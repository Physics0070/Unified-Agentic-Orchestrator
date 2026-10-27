"""Knowledge workspace: upload documents, retrieve from them with hybrid search."""

from backend.rag.index import KnowledgeError, Workspace, knowledge_base, tokenize
from backend.rag.parsers import SUPPORTED, ParseError, parse_document


def render_context(hits: list[dict]) -> str:
    """Retrieved chunks as prompt text, labelled so the model can cite them.

    Folded into the question the same way web-search results are (see
    backend/chat/session.py): whatever text the answer was built from is what
    gets stored and replayed, so retrieval needs no replay machinery of its own.
    """
    if not hits:
        return ""
    lines = ["Excerpts from the user's uploaded documents (answer from these when they are "
             "relevant, and cite the document name; say so if they do not cover the question):"]
    for i, h in enumerate(hits, start=1):
        lines.append(f"[{i}] {h['doc_name']} (part {h['ordinal'] + 1}):\n{h['text']}")
    return "\n\n".join(lines)


__all__ = [
    "SUPPORTED",
    "KnowledgeError",
    "ParseError",
    "Workspace",
    "knowledge_base",
    "parse_document",
    "render_context",
    "tokenize",
]
