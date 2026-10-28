"""Knowledge uploads survive a cold start: sqlite file backend, no network."""

import pytest

from backend.rag import KnowledgeError
from backend.rag.index import KnowledgeBase
from backend.rag.store import KnowledgeStore, KnowledgeStoreError

DOC = (
    "Optimistic locking assumes conflicts are rare. Each row carries a version number; "
    "an update succeeds only if the version is unchanged, otherwise the writer retries."
)


def _kb(tmp_path):
    return KnowledgeBase(store=KnowledgeStore(f"file:{tmp_path}/sub/k.db"))


def test_upload_survives_new_instance(tmp_path):
    kb1 = _kb(tmp_path)
    ws = kb1.get("workspace-1")
    info = ws.add_document("locking.txt", DOC)
    assert ws.summary()["persistent"] is True

    ws2 = _kb(tmp_path).get("workspace-1", create=False)  # cold start
    assert ws2 is not None
    s = ws2.summary()
    assert s["persistent"] is True
    assert [d["doc_id"] for d in s["documents"]] == [info.doc_id]
    assert s["documents"][0]["name"] == "locking.txt"
    hits = ws2.search("optimistic locking version number")["hits"]
    assert hits and hits[0]["doc_name"] == "locking.txt"


def test_delete_persists(tmp_path):
    ws = _kb(tmp_path).get("workspace-1")
    keep = ws.add_document("keep.txt", DOC)
    gone = ws.add_document("gone.txt", "Pessimistic locking uses SELECT FOR UPDATE to queue.")
    assert ws.remove_document(gone.doc_id)

    ws2 = _kb(tmp_path).get("workspace-1", create=False)
    assert [d["doc_id"] for d in ws2.summary()["documents"]] == [keep.doc_id]
    assert not ws2.remove_document(gone.doc_id)


def test_reupload_same_name_replaces_durably(tmp_path):
    ws = _kb(tmp_path).get("workspace-1")
    ws.add_document("a.txt", DOC)
    ws.add_document("a.txt", DOC + " Retries are bounded.")
    docs = _kb(tmp_path).get("workspace-1", create=False).summary()["documents"]
    assert len(docs) == 1


def test_workspaces_isolated_and_unknown_is_none(tmp_path):
    _kb(tmp_path).get("workspace-1").add_document("a.txt", DOC)
    kb = _kb(tmp_path)
    assert kb.get("workspace-2", create=False) is None
    assert kb.get("workspace-2").summary()["documents"] == []


def test_limits_still_enforced_after_reload(tmp_path, monkeypatch):
    from backend.config import settings

    _kb(tmp_path).get("workspace-1").add_document("a.txt", DOC)
    monkeypatch.setattr(settings, "knowledge_max_docs", 1)
    ws = _kb(tmp_path).get("workspace-1")
    with pytest.raises(KnowledgeError, match="full"):
        ws.add_document("b.txt", "Another document with enough words to be chunked properly.")


def test_unusable_storage_degrades_to_memory(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    # parent "directory" is a regular file, so sqlite cannot open it
    ws = KnowledgeBase(store=KnowledgeStore(f"file:{blocker}/k.db")).get("workspace-1")
    ws.add_document("a.txt", DOC)
    assert ws.summary()["persistent"] is False
    assert ws.search("optimistic locking")["hits"]


def test_save_failure_is_not_silent(tmp_path, monkeypatch):
    ws = _kb(tmp_path).get("workspace-1")

    def boom(*a, **k):
        raise KnowledgeStoreError("down")

    monkeypatch.setattr(ws._store, "replace", boom)
    with pytest.raises(KnowledgeError):
        ws.add_document("a.txt", DOC)
    assert ws.summary()["documents"] == []


def test_api_roundtrip_is_persistent(tmp_path):
    from fastapi.testclient import TestClient

    from backend.main import app
    from backend.rag import knowledge_base

    c = TestClient(app)
    r = c.post("/api/knowledge/upload", data={"workspace_id": "api-ws-test"},
               files={"file": ("n.txt", DOC.encode(), "text/plain")})
    assert r.status_code == 200 and r.json()["persistent"] is True
    knowledge_base._spaces.clear()  # simulate cold start
    r = c.post("/api/knowledge/query", json={"workspace_id": "api-ws-test", "query": "optimistic"})
    assert r.json()["hits"]
    doc_id = r.json()["hits"][0]["doc_id"]
    assert c.delete(f"/api/knowledge/documents/{doc_id}",
                    params={"workspace_id": "api-ws-test"}).status_code == 200
    knowledge_base._spaces.clear()
    assert c.get("/api/knowledge/documents", params={"workspace_id": "api-ws-test"}
                 ).json()["documents"] == []
