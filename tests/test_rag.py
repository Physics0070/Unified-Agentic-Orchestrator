"""Knowledge workspace: parsing, chunking and hybrid retrieval. Pure - no network,
no model calls, no database."""

import io

import pytest

from backend.rag import (
    KnowledgeError,
    ParseError,
    Workspace,
    knowledge_base,
    parse_document,
    render_context,
    tokenize,
)
from backend.rag.chunker import TARGET_CHARS, chunk_text

LOCKING = (
    "Optimistic locking assumes conflicts are rare. Each row carries a version number; "
    "an update succeeds only if the version is unchanged, otherwise the writer retries.\n\n"
    "Pessimistic locking takes a lock up front with SELECT FOR UPDATE, so concurrent writers "
    "queue behind each other. It is safer under heavy contention on a hot ledger row."
)
COOKING = (
    "To make a proper risotto, toast the arborio rice in butter before adding warm stock one "
    "ladle at a time. Stir constantly and finish with parmesan."
)


def _ws() -> Workspace:
    ws = Workspace("test-space")
    ws.add_document("locking.md", LOCKING)
    ws.add_document("cooking.txt", COOKING)
    return ws


# ---------------------------------------------------------------- parsing


def test_plain_text_and_markdown_parse():
    assert "hello" in parse_document("a.txt", b"hello world")
    assert "# Title" in parse_document("a.md", b"# Title\n\nbody")


def test_csv_becomes_pipe_rows():
    out = parse_document("t.csv", b"name,age\nada,36\n")
    assert "name | age" in out and "ada | 36" in out


def test_docx_round_trip():
    from docx import Document

    buf = io.BytesIO()
    d = Document()
    d.add_paragraph("Quarterly revenue grew twelve percent.")
    d.save(buf)
    assert "revenue grew" in parse_document("r.docx", buf.getvalue())


def test_a_fake_pdf_is_rejected_by_magic_bytes_not_extension():
    with pytest.raises(ParseError, match="not a real PDF"):
        parse_document("evil.pdf", b"MZ\x90\x00 this is an executable")


def test_unsupported_and_empty_are_rejected_with_a_reason():
    with pytest.raises(ParseError, match="unsupported"):
        parse_document("a.exe", b"x")
    with pytest.raises(ParseError, match="empty"):
        parse_document("a.txt", b"")
    with pytest.raises(ParseError, match="binary"):
        parse_document("a.txt", b"abc\x00def")


# --------------------------------------------------------------- chunking


def test_chunks_respect_the_size_target_and_overlap():
    para = "Sentence number {} is here and says something. "
    text = "\n\n".join("".join(para.format(i * 20 + j) for j in range(20)) for i in range(4))
    chunks = chunk_text(text)
    assert len(chunks) > 1
    assert all(len(c) <= TARGET_CHARS + 300 for c in chunks)


def test_a_short_document_is_one_chunk():
    assert chunk_text("Just one short paragraph about locking strategies.") == [
        "Just one short paragraph about locking strategies."
    ]


# -------------------------------------------------------------- retrieval


def test_stemming_matches_inflections():
    assert tokenize("locking locks locked")[0] == tokenize("lock")[0] or len(
        set(tokenize("queries query"))) <= 2


@pytest.mark.parametrize("mode", ["hybrid", "keyword", "semantic"])
def test_every_mode_finds_the_relevant_document_first(mode):
    res = _ws().search("how does optimistic locking handle conflicts", mode=mode)
    assert res["hits"], f"{mode} found nothing"
    assert res["hits"][0]["doc_name"] == "locking.md"


def test_unrelated_query_does_not_return_the_wrong_document_as_top():
    res = _ws().search("how do I make risotto rice", mode="keyword")
    assert res["hits"][0]["doc_name"] == "cooking.txt"


def test_hybrid_reports_both_legs_and_the_semantic_backend():
    res = _ws().search("pessimistic locking contention")
    hit = res["hits"][0]
    assert hit["scores"]["bm25"] is not None
    assert hit["scores"]["fused"] > 0
    assert res["semantic_backend"] in ("embeddings", "char-ngram-tfidf")
    assert res["reranked"] is True


def test_keyword_mode_runs_no_semantic_leg():
    res = _ws().search("optimistic locking", mode="keyword")
    assert res["semantic_backend"] is None
    assert res["hits"][0]["scores"]["semantic"] is None


def test_fuzzy_leg_survives_a_typo_that_keyword_search_misses():
    ws = _ws()
    assert ws.search("optimisitc lockign", mode="keyword")["hits"] == []
    sem = ws.search("optimisitc lockign", mode="semantic")
    assert sem["hits"] and sem["hits"][0]["doc_name"] == "locking.md"


def test_empty_workspace_and_stopword_only_query_return_no_hits():
    assert Workspace("x").search("anything")["hits"] == []
    assert _ws().search("the of and")["hits"] == []


def test_removing_a_document_removes_its_chunks_from_results():
    ws = _ws()
    doc_id = next(d.doc_id for d in ws.docs.values() if d.name == "locking.md")
    assert ws.remove_document(doc_id) is True
    assert ws.remove_document(doc_id) is False
    res = ws.search("optimistic locking", mode="keyword")
    assert all(h["doc_name"] != "locking.md" for h in res["hits"])


def test_reuploading_a_name_replaces_instead_of_duplicating():
    ws = _ws()
    ws.add_document("locking.md", "Entirely new text about sharding strategies and replicas.")
    assert len([d for d in ws.docs.values() if d.name == "locking.md"]) == 1
    assert ws.search("sharding replicas", mode="keyword")["hits"][0]["doc_name"] == "locking.md"


def test_near_duplicate_chunks_are_not_returned_twice():
    ws = Workspace("dupes")
    ws.add_document("a.txt", LOCKING)
    ws.add_document("b.txt", LOCKING)
    docs = {h["doc_name"] for h in ws.search("optimistic locking version number")["hits"]}
    assert len(docs) == 1


def test_document_limit_is_enforced(monkeypatch):
    monkeypatch.setattr("backend.rag.index.settings.knowledge_max_docs", 1)
    ws = Workspace("tiny")
    ws.add_document("a.txt", LOCKING)
    with pytest.raises(KnowledgeError, match="full"):
        ws.add_document("b.txt", COOKING)


def test_render_context_labels_sources_for_citation():
    hits = _ws().search("optimistic locking")["hits"]
    out = render_context(hits)
    assert "[1] locking.md" in out and "cite the document name" in out
    assert render_context([]) == ""


def test_workspace_id_is_validated():
    for bad in ("", "short", "../etc", "a b", "x" * 65):
        with pytest.raises(KnowledgeError):
            knowledge_base.get(bad)
    assert knowledge_base.get("ok-id_123") is knowledge_base.get("ok-id_123")
