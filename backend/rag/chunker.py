"""Split a document into overlapping, paragraph-aware chunks.

Chunks end on a paragraph or sentence boundary where one is available, because
a chunk cut mid-sentence retrieves badly and reads worse when it is quoted back
as a source. The overlap means a fact that straddles a boundary still lives
whole in one of the two chunks.
"""

import re

TARGET_CHARS = 900
OVERLAP_CHARS = 150
MIN_CHARS = 40

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def _pieces(text: str) -> list[str]:
    """Paragraphs, with any paragraph larger than the target split by sentence
    (and, for a wall of text with no punctuation, by hard length)."""
    out: list[str] = []
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        if len(para) <= TARGET_CHARS:
            out.append(para)
            continue
        buf = ""
        for sent in _SENTENCE_END.split(para):
            if len(sent) > TARGET_CHARS:
                if buf:
                    out.append(buf)
                    buf = ""
                out.extend(sent[i:i + TARGET_CHARS] for i in range(0, len(sent), TARGET_CHARS))
                continue
            if buf and len(buf) + len(sent) + 1 > TARGET_CHARS:
                out.append(buf)
                buf = sent
            else:
                buf = f"{buf} {sent}".strip()
        if buf:
            out.append(buf)
    return out


def chunk_text(text: str) -> list[str]:
    chunks: list[str] = []
    current = ""
    for piece in _pieces(text):
        if current and len(current) + len(piece) + 2 > TARGET_CHARS:
            chunks.append(current)
            # Carry the tail of the previous chunk forward as overlap.
            tail = current[-OVERLAP_CHARS:]
            cut = tail.find(" ")
            current = (tail[cut + 1:] if cut != -1 else tail) + "\n\n" + piece
        else:
            current = f"{current}\n\n{piece}" if current else piece
    if current:
        chunks.append(current)
    return [c.strip() for c in chunks if len(c.strip()) >= MIN_CHARS or len(chunks) == 1]
