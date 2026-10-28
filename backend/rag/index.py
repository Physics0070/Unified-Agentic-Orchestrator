"""Hybrid retrieval over one knowledge workspace.

Three stages, each of which can be turned off and measured on its own:

1. **Keyword leg** - BM25 over stemmed tokens. Exact terms, names, error codes
   and numbers: the things a vector search blurs.
2. **Semantic leg** - sentence embeddings when `sentence-transformers` is
   installed (the same optional extra the metrics use). Without it the leg falls
   back to character n-gram TF-IDF, which tolerates typos and morphology but
   does not understand synonyms - and the result says which one ran, so nobody
   believes they got meaning-based search when they got fuzzy-keyword search.
3. **Fusion + rerank** - the two ranked lists are merged with Reciprocal Rank
   Fusion (no score calibration needed between a BM25 score and a cosine), then
   the best candidates are re-scored on how completely they cover the query
   (idf-weighted term coverage and adjacent-pair matches) and near-duplicate
   overlap chunks are dropped.

The index is in memory, per workspace. Documents (name + text) are also written
to the EventStore's database (see backend/rag/store.py) and the index is rebuilt
lazily from there on first access, so uploads survive serverless cold starts.
If that storage is unavailable the workspace still works in memory and the API
reports `persistent: false` rather than pretending.
"""

import logging
import math
import re
import threading
import uuid
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, datetime

from backend.config import settings
from backend.rag.chunker import chunk_text
from backend.rag.store import KnowledgeStore, KnowledgeStoreError

log = logging.getLogger("aco.rag")

_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    "a an the and or but if then else of to in on at by for with from as is are was were be "
    "been being it its this that these those i you he she we they them his her our your their "
    "do does did done have has had not no so than too very can could should would will just "
    "about into over under again also there here what which who whom how when where why".split()
)
_SUFFIXES = ("ingly", "ing", "edly", "ed", "ies", "es", "ly", "s")

RRF_K = 60
LEG_DEPTH = 50
DEDUPE_JACCARD = 0.8


def _stem(tok: str) -> str:
    if len(tok) <= 4 or tok.isdigit():
        return tok
    for suf in _SUFFIXES:
        if tok.endswith(suf) and len(tok) - len(suf) >= 3:
            return tok[: -len(suf)] + ("y" if suf == "ies" else "")
    return tok


def tokenize(text: str) -> list[str]:
    return [_stem(t) for t in _TOKEN.findall(text.lower()) if t not in _STOP and len(t) > 1]


class KnowledgeError(ValueError):
    """A knowledge-workspace limit or input problem. Message is safe to show."""


@dataclass
class Chunk:
    idx: int
    doc_id: str
    doc_name: str
    ordinal: int
    text: str
    tf: Counter = field(default_factory=Counter)
    length: int = 0


@dataclass
class DocInfo:
    doc_id: str
    name: str
    chars: int
    chunks: int
    added_at: str

    def to_dict(self) -> dict:
        return {"doc_id": self.doc_id, "name": self.name, "chars": self.chars,
                "chunks": self.chunks, "added_at": self.added_at}


class Workspace:
    def __init__(self, workspace_id: str, store: KnowledgeStore | None = None) -> None:
        self.workspace_id = workspace_id
        self._store = store
        self.persistent = False
        self.docs: dict[str, DocInfo] = {}
        self.chunks: list[Chunk] = []
        self._lock = threading.RLock()
        self._dirty = True
        self._df: Counter = Counter()
        self._avgdl = 0.0
        self._tfidf = None         # (vectorizer, matrix) - fuzzy leg
        self._embeddings = None    # numpy matrix aligned with self.chunks - semantic leg

    # ------------------------------------------------------------- persistence

    def load_from_store(self) -> None:
        """Rebuild documents and chunks from durable storage (cold start)."""
        if self._store is None:
            return
        try:
            rows = self._store.load(self.workspace_id)
        except KnowledgeStoreError:
            self._store = None  # degrade to memory-only; summary() says persistent=False
            return
        with self._lock:
            for r in rows:
                pieces = chunk_text(r["text"])
                if pieces:
                    self._index_locked(r["doc_id"], r["name"], pieces)
                    self.docs[r["doc_id"]] = DocInfo(
                        r["doc_id"], r["name"], r["chars"], len(pieces), r["added_at"])
            self.persistent = True
            self._invalidate()

    def _index_locked(self, doc_id: str, name: str, pieces: list[str]) -> None:
        for i, piece in enumerate(pieces):
            toks = tokenize(piece)
            self.chunks.append(Chunk(
                idx=len(self.chunks), doc_id=doc_id, doc_name=name, ordinal=i,
                text=piece, tf=Counter(toks), length=len(toks),
            ))

    # ------------------------------------------------------------- mutation

    def add_document(self, name: str, text: str) -> DocInfo:
        pieces = chunk_text(text)
        if not pieces:
            raise KnowledgeError("the document produced no usable text")
        with self._lock:
            if len(self.docs) >= settings.knowledge_max_docs:
                raise KnowledgeError(
                    f"workspace is full ({settings.knowledge_max_docs} documents); "
                    "delete one first")
            if len(self.chunks) + len(pieces) > settings.knowledge_max_chunks:
                raise KnowledgeError("workspace is full; delete a document first")
            doc_id = uuid.uuid4().hex[:12]
            info = DocInfo(doc_id, name, len(text), len(pieces),
                           datetime.now(UTC).isoformat(timespec="seconds"))
            # Re-uploading a name replaces it instead of double-counting.
            replaced = [d.doc_id for d in self.docs.values() if d.name == name]
            if self._store is not None:
                try:  # durable first: a failed save must not look like success
                    self._store.replace(self.workspace_id, replaced,
                                        {**info.to_dict(), "text": text})
                except KnowledgeStoreError as e:
                    raise KnowledgeError("could not save the document to storage") from e
            for old in replaced:
                self._remove_locked(old)
            self._index_locked(doc_id, name, pieces)
            self.docs[doc_id] = info
            self._invalidate()
            return info

    def remove_document(self, doc_id: str) -> bool:
        with self._lock:
            if doc_id not in self.docs:
                return False
            if self._store is not None:
                try:
                    self._store.replace(self.workspace_id, [doc_id], None)
                except KnowledgeStoreError as e:
                    raise KnowledgeError("could not delete the document from storage") from e
            return self._remove_locked(doc_id)

    def _remove_locked(self, doc_id: str) -> bool:
        if doc_id not in self.docs:
            return False
        del self.docs[doc_id]
        self.chunks = [c for c in self.chunks if c.doc_id != doc_id]
        for i, c in enumerate(self.chunks):
            c.idx = i
        self._invalidate()
        return True

    def _invalidate(self) -> None:
        self._dirty = True
        self._tfidf = None
        self._embeddings = None

    # ------------------------------------------------------------- indexing

    def _refresh_stats(self) -> None:
        if not self._dirty:
            return
        self._df = Counter()
        for c in self.chunks:
            self._df.update(c.tf.keys())
        self._avgdl = (sum(c.length for c in self.chunks) / len(self.chunks)) if self.chunks else 0
        self._dirty = False

    def _idf(self, term: str) -> float:
        n = len(self.chunks)
        df = self._df.get(term, 0)
        return math.log(1 + (n - df + 0.5) / (df + 0.5))

    # ------------------------------------------------------------- the legs

    def _bm25(self, q_terms: list[str]) -> list[tuple[int, float]]:
        k1, b = 1.5, 0.75
        scores: dict[int, float] = {}
        for term in set(q_terms):
            idf = self._idf(term)
            for c in self.chunks:
                f = c.tf.get(term)
                if not f:
                    continue
                denom = f + k1 * (1 - b + b * c.length / (self._avgdl or 1))
                scores[c.idx] = scores.get(c.idx, 0.0) + idf * f * (k1 + 1) / denom
        return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:LEG_DEPTH]

    def _semantic_backend(self) -> str:
        from backend.metrics.metrics import embeddings_available

        return "embeddings" if embeddings_available() else "char-ngram-tfidf"

    def _semantic(self, query: str) -> tuple[list[tuple[int, float]], str]:
        import numpy as np

        backend = self._semantic_backend()
        if backend == "embeddings":
            from backend.metrics.metrics import embed

            if self._embeddings is None:
                self._embeddings = embed([c.text for c in self.chunks])
            sims = self._embeddings @ embed([query])[0]
        else:
            from sklearn.feature_extraction.text import TfidfVectorizer

            if self._tfidf is None:
                vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), sublinear_tf=True)
                self._tfidf = (vec, vec.fit_transform([c.text for c in self.chunks]))
            vec, matrix = self._tfidf
            sims = np.asarray((matrix @ vec.transform([query]).T).todense()).ravel()
        order = np.argsort(-sims)[:LEG_DEPTH]
        return [(int(i), float(sims[i])) for i in order if sims[i] > 0.0], backend

    # --------------------------------------------------------------- search

    def search(
        self, query: str, *, top_k: int = 5, mode: str = "hybrid", rerank: bool = True,
    ) -> dict:
        with self._lock:
            return self._search_locked(query, top_k, mode, rerank)

    def _search_locked(self, query: str, top_k: int, mode: str, rerank: bool) -> dict:
        top_k = max(1, min(top_k, 20))
        if mode not in ("hybrid", "keyword", "semantic"):
            mode = "hybrid"
        base = {"query": query, "mode": mode, "reranked": False, "hits": [],
                "semantic_backend": None, "documents": len(self.docs),
                "chunks": len(self.chunks)}
        q_terms = tokenize(query)
        if not self.chunks or not q_terms:
            return base
        self._refresh_stats()

        bm25 = self._bm25(q_terms) if mode in ("hybrid", "keyword") else []
        sem: list[tuple[int, float]] = []
        backend = None
        if mode in ("hybrid", "semantic"):
            sem, backend = self._semantic(query)

        bm25_rank = {i: r for r, (i, _) in enumerate(bm25, start=1)}
        sem_rank = {i: r for r, (i, _) in enumerate(sem, start=1)}
        bm25_score, sem_score = dict(bm25), dict(sem)

        fused: dict[int, float] = {}
        for ranks in (bm25_rank, sem_rank):
            for i, r in ranks.items():
                fused[i] = fused.get(i, 0.0) + 1.0 / (RRF_K + r)
        if not fused:
            return {**base, "semantic_backend": backend}

        pool = sorted(fused, key=fused.get, reverse=True)[: max(top_k * 4, 20)]
        top_fused = fused[pool[0]]

        rerank_score: dict[int, float] = {}
        if rerank:
            q_unique = list(dict.fromkeys(q_terms))
            q_bigrams = list(zip(q_terms, q_terms[1:], strict=False))
            idf_total = sum(self._idf(t) for t in q_unique) or 1.0
            for i in pool:
                c = self.chunks[i]
                cover = sum(self._idf(t) for t in q_unique if t in c.tf) / idf_total
                bigram = 0.0
                if q_bigrams:
                    seq = tokenize(c.text)
                    pairs = set(zip(seq, seq[1:], strict=False))
                    bigram = sum(1 for bg in q_bigrams if bg in pairs) / len(q_bigrams)
                rerank_score[i] = 0.5 * cover + 0.2 * bigram + 0.3 * (fused[i] / top_fused)
            pool.sort(key=lambda i: rerank_score[i], reverse=True)

        chosen: list[int] = []
        chosen_sets: list[set[str]] = []
        for i in pool:
            toks = set(self.chunks[i].tf)
            if any(
                len(toks & s) / max(1, len(toks | s)) >= DEDUPE_JACCARD for s in chosen_sets
            ):
                continue
            chosen.append(i)
            chosen_sets.append(toks)
            if len(chosen) == top_k:
                break

        hits = []
        for i in chosen:
            c = self.chunks[i]
            hits.append({
                "doc_id": c.doc_id, "doc_name": c.doc_name, "ordinal": c.ordinal,
                "text": c.text,
                "score": round(rerank_score.get(i, fused[i]), 4),
                "scores": {
                    "bm25": round(bm25_score[i], 4) if i in bm25_score else None,
                    "semantic": round(sem_score[i], 4) if i in sem_score else None,
                    "fused": round(fused[i], 5),
                    "rerank": round(rerank_score[i], 4) if i in rerank_score else None,
                },
                "ranks": {"bm25": bm25_rank.get(i), "semantic": sem_rank.get(i)},
            })
        return {**base, "semantic_backend": backend, "reranked": rerank, "hits": hits}

    def summary(self) -> dict:
        with self._lock:
            return {"workspace_id": self.workspace_id,
                    "documents": [d.to_dict() for d in self.docs.values()],
                    "chunks": len(self.chunks),
                    "persistent": self.persistent,
                    "semantic_backend": self._semantic_backend()}


_WORKSPACE_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
MAX_WORKSPACES = 200


class KnowledgeBase:
    """Workspaces by id, least-recently-used evicted past MAX_WORKSPACES so an
    open endpoint cannot be used to grow memory without bound."""

    def __init__(self, store: "KnowledgeStore | None | str" = "auto") -> None:
        # "auto": use the EventStore's database per current settings; None: memory only.
        self._store_arg = store
        self._spaces: OrderedDict[str, Workspace] = OrderedDict()
        self._lock = threading.Lock()

    def _resolve_store(self) -> KnowledgeStore | None:
        if self._store_arg != "auto":
            return self._store_arg  # type: ignore[return-value]
        url, token = settings.turso_database_url, settings.turso_auth_token
        try:
            return KnowledgeStore(url, token)
        except KnowledgeStoreError:
            return None

    def get(self, workspace_id: str, *, create: bool = True) -> Workspace | None:
        if not _WORKSPACE_ID.match(workspace_id or ""):
            raise KnowledgeError("workspace_id must be 8-64 letters, digits, '-' or '_'")
        with self._lock:
            ws = self._spaces.get(workspace_id)
            if ws is None:
                # Cold start (or evicted): rebuild from durable storage.
                cand = Workspace(workspace_id, self._resolve_store())
                cand.load_from_store()
                if create or cand.docs:
                    ws = cand
                    self._spaces[workspace_id] = ws
                    while len(self._spaces) > MAX_WORKSPACES:
                        self._spaces.popitem(last=False)
            if ws is not None:
                self._spaces.move_to_end(workspace_id)
            return ws


knowledge_base = KnowledgeBase()
