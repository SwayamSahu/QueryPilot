"""Schema & metric retrieval: give the model only the relevant slice of the catalog.

Two interchangeable backends:
  * TfidfRetriever     - scikit-learn word + char n-gram TF-IDF. Offline, zero setup (default).
  * EmbeddingRetriever - sentence-transformers embeddings stored in Postgres/pgvector.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

from querypilot.config import get_settings
from querypilot.semantic.catalog import Catalog, get_catalog

log = logging.getLogger(__name__)


@dataclass
class Doc:
    doc_id: str  # metric:revenue | dim:region | table:sales.orders | column:sales.orders.order_date
    kind: str
    title: str
    text: str


@dataclass
class Hit:
    doc: Doc
    score: float


def build_docs(cat: Catalog, values: dict[str, list[str]] | None = None) -> list[Doc]:
    """`values` (tenant-scoped dimension values) makes 'Electronics' or 'APAC' retrieve their dimension."""
    docs: list[Doc] = []
    values = values or {}
    for m in cat.metrics.values():
        docs.append(Doc(f"metric:{m.key}", "metric", m.label,
                        f"{m.key.replace('_', ' ')} {m.label}. {m.description} Also called: {', '.join(m.synonyms)}."))
    for d in cat.dimensions.values():
        vals = values.get(d.key) or []
        docs.append(Doc(f"dim:{d.key}", "dimension", d.label,
                        f"{d.key} {d.label} (group by / filter). Also called: {', '.join(d.synonyms)}."
                        + (f" Values: {', '.join(vals[:40])}." if vals else "")))
    for t, info in cat.tables.items():
        cols = info.get("columns", {})
        docs.append(Doc(f"table:{t}", "table", t, f"{t}: {info.get('description', '')} Columns: {', '.join(cols)}."))
        for c, desc in cols.items():
            docs.append(Doc(f"column:{t}.{c}", "column", f"{t}.{c}", f"{t}.{c} {c.replace('_', ' ')}: {desc}"))
    return docs


QUOTAS = {"metric": 3, "dimension": 3}  # remaining slots go to physical schema docs (tables/columns)


def typed_topk(hits: list[Hit], k: int) -> list[Hit]:
    """Typed retrieval: guarantee metric and dimension candidates are not crowded out by schema docs."""
    out: list[Hit] = []
    for kind, q in QUOTAS.items():
        out += [h for h in hits if h.doc.kind == kind][:q]
    rest = [h for h in hits if h not in out]
    out += rest[: max(0, k - len(out))]
    return sorted(out, key=lambda h: -h.score)[:k]


class TfidfRetriever:
    name = "tfidf"

    def __init__(self, cat: Catalog, values: dict[str, list[str]] | None = None):
        self.docs = build_docs(cat, values)
        corpus = [d.text.lower() for d in self.docs]
        self.word = TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True).fit(corpus)
        self.char = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), sublinear_tf=True).fit(corpus)
        self.mw, self.mc = self.word.transform(corpus), self.char.transform(corpus)

    def search(self, query: str, k: int = 8, kinds: set[str] | None = None) -> list[Hit]:
        q = query.lower()
        s = 0.6 * (self.mw @ self.word.transform([q]).T).toarray().ravel() + 0.4 * (self.mc @ self.char.transform([q]).T).toarray().ravel()
        order = np.argsort(-s)
        hits = [Hit(self.docs[i], float(s[i])) for i in order if (kinds is None or self.docs[i].kind in kinds)]
        return hits[:k] if kinds else typed_topk(hits, k)


class EmbeddingRetriever:
    """Requires `pip install sentence-transformers` and network access to download the model once."""

    name = "embedding"

    def __init__(self, cat: Catalog, model_name: str, store=None):
        from sentence_transformers import SentenceTransformer  # optional dependency

        self.cat = cat
        self.docs = build_docs(cat)
        self.model = SentenceTransformer(model_name)
        self.store = store
        self.emb = self.model.encode([d.text for d in self.docs], normalize_embeddings=True)
        if store is not None:
            store.upsert(cat.version, self.docs, self.emb)

    def search(self, query: str, k: int = 8, kinds: set[str] | None = None) -> list[Hit]:
        qv = self.model.encode([query], normalize_embeddings=True)[0]
        if self.store is not None:
            pairs = self.store.search(self.cat.version, qv, k * 3)
            by_id = {d.doc_id: d for d in self.docs}
            hits = [Hit(by_id[i], s) for i, s in pairs if i in by_id]
        else:
            s = self.emb @ qv
            hits = [Hit(self.docs[i], float(s[i])) for i in np.argsort(-s)]
        return [h for h in hits if h.doc.kind in kinds][:k] if kinds else typed_topk(hits, k)


class PgVectorStore:
    def __init__(self, engine):
        self.engine = engine

    def upsert(self, version: str, docs: list[Doc], emb: np.ndarray) -> None:
        from sqlalchemy import text

        with self.engine.begin() as conn:
            conn.execute(text("DELETE FROM qp_ops.catalog_embeddings WHERE catalog_version <> :v"), {"v": version})
            for d, e in zip(docs, emb):
                conn.execute(text("INSERT INTO qp_ops.catalog_embeddings (doc_id, catalog_version, kind, content, embedding) "
                                  "VALUES (:i, :v, :k, :c, CAST(:e AS vector)) ON CONFLICT (doc_id) DO UPDATE SET "
                                  "catalog_version = EXCLUDED.catalog_version, content = EXCLUDED.content, embedding = EXCLUDED.embedding"),
                             {"i": d.doc_id, "v": version, "k": d.kind, "c": d.text, "e": "[" + ",".join(f"{x:.6f}" for x in e) + "]"})

    def search(self, version: str, qv: np.ndarray, k: int) -> list[tuple[str, float]]:
        from sqlalchemy import text

        with self.engine.connect() as conn:
            rows = conn.execute(text("SELECT doc_id, 1 - (embedding <=> CAST(:q AS vector)) AS score FROM qp_ops.catalog_embeddings "
                                     "WHERE catalog_version = :v ORDER BY embedding <=> CAST(:q AS vector) LIMIT :k"),
                                {"q": "[" + ",".join(f"{x:.6f}" for x in qv) + "]", "v": version, "k": k}).all()
        return [(r[0], float(r[1])) for r in rows]


_tenant_retrievers: dict[int, TfidfRetriever] = {}


def retriever_for(tenant_id: int, values: dict[str, list[str]]):
    """Per-tenant TF-IDF index (includes that tenant's dimension values); embedding backend is shared."""
    base = get_retriever()
    if not isinstance(base, TfidfRetriever):
        return base
    if tenant_id not in _tenant_retrievers:
        _tenant_retrievers[tenant_id] = TfidfRetriever(get_catalog(), values)
    return _tenant_retrievers[tenant_id]


@lru_cache
def get_retriever():
    s = get_settings()
    cat = get_catalog()
    if s.retriever == "embedding":
        try:
            from querypilot.ops.store import app_engine

            return EmbeddingRetriever(cat, s.embed_model, PgVectorStore(app_engine()))
        except Exception as e:  # noqa: BLE001 - degrade gracefully, but say so
            log.warning("embedding retriever unavailable (%s); falling back to TF-IDF", e)
    return TfidfRetriever(cat)
