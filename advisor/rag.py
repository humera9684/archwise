"""Retrieval-augmented explanations over a curated cloud-documentation knowledge base.

`knowledge/*.md` holds short notes per service family. Each file has front matter (title,
source URL, tags) and `##` sections; every section is one retrievable passage.

Retrieval is hybrid:
  * BM25 keyword ranking (pure Python, always available), and
  * dense embeddings from Ollama (`OLLAMA_EMBED_MODEL`, default nomic-embed-text), used
    automatically when Ollama is running; ranks are fused with reciprocal-rank fusion.
If embeddings are unavailable the retriever silently uses BM25 alone.

The numbers an architecture is priced with never come from here: the engine owns those.
Passages only supply qualitative facts (limits, risks, compatibility) that the LLM must cite.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

KNOWLEDGE_DIR = Path(os.getenv("ARCHWISE_KNOWLEDGE_DIR",
                               Path(__file__).resolve().parent.parent / "knowledge"))
EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")
RRF_K = 60
_TOKEN = re.compile(r"[a-z0-9][a-z0-9\-\.]*[a-z0-9]|[a-z0-9]")
_STOP = frozenset("a an and are as at be by for from has have how in is it its of on or that the "
                  "this to was were with you your can not no but if than then so such".split())


@dataclass(frozen=True)
class Passage:
    id: str
    title: str
    section: str
    text: str
    source: str
    tags: tuple[str, ...]

    @property
    def search_text(self) -> str:
        return f"{self.title} {self.section} {' '.join(self.tags)} {self.text}"


def tokenize(text: str) -> list[str]:
    toks = []
    for t in _TOKEN.findall(text.lower()):
        if t in _STOP:
            continue
        toks.append(t)
        if "-" in t:                       # "multi-az" also matches "multi" and "az"
            toks.extend(p for p in t.split("-") if p and p not in _STOP)
    return toks


def _parse(path: Path) -> list[Passage]:
    raw = path.read_text(encoding="utf-8")
    meta: dict[str, str] = {}
    body = raw
    if raw.startswith("---"):
        _, fm, body = raw.split("---", 2)
        for line in fm.strip().splitlines():
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip()
    title, source = meta.get("title", path.stem), meta.get("source", "")
    tags = tuple(meta.get("tags", "").split())
    out = []
    for block in re.split(r"^## ", body, flags=re.M)[1:]:
        heading, _, text = block.partition("\n")
        text = " ".join(text.split())
        if text:
            out.append(Passage(f"{path.stem}#{len(out) + 1}", title, heading.strip(), text, source, tags))
    return out


def load_corpus(directory: Path | None = None) -> list[Passage]:
    d = directory or KNOWLEDGE_DIR
    return [p for f in sorted(d.glob("*.md")) for p in _parse(f)] if d.is_dir() else []


class BM25:
    def __init__(self, docs: list[list[str]], k1: float = 1.4, b: float = 0.75):
        self.k1, self.b = k1, b
        self.tf = [Counter(d) for d in docs]
        self.len = [len(d) for d in docs]
        self.avg = (sum(self.len) / len(docs)) if docs else 0.0
        df: Counter = Counter()
        for c in self.tf:
            df.update(c.keys())
        n = len(docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def scores(self, query: list[str]) -> list[float]:
        out = []
        for tf, ln in zip(self.tf, self.len):
            s = 0.0
            for t in query:
                f = tf.get(t)
                if f:
                    s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * ln / (self.avg or 1)))
            out.append(s)
        return out


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class OllamaEmbedder:
    """Embeddings from Ollama's /api/embed endpoint, cached on disk by content hash."""

    def __init__(self, model: str = EMBED_MODEL):
        self.model = model
        base = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1").rsplit("/v1", 1)[0]
        self.url = base + "/api/embed"
        cache_dir = Path(os.getenv("ARCHWISE_CACHE_DIR", Path(__file__).resolve().parent.parent / ".cache"))
        self.cache_file = cache_dir / "rag_embeddings.json"
        try:
            self.cache: dict[str, list[float]] = json.loads(self.cache_file.read_text())
        except (OSError, ValueError):
            self.cache = {}

    def _key(self, text: str) -> str:
        return hashlib.sha256(f"{self.model}\n{text}".encode()).hexdigest()

    def embed(self, texts: list[str]) -> list[list[float]]:
        import httpx
        missing = [t for t in dict.fromkeys(texts) if self._key(t) not in self.cache]
        for i in range(0, len(missing), 16):
            batch = missing[i:i + 16]
            r = httpx.post(self.url, json={"model": self.model, "input": batch}, timeout=60)
            r.raise_for_status()
            for t, v in zip(batch, r.json()["embeddings"]):
                self.cache[self._key(t)] = v
        if missing:
            try:
                self.cache_file.parent.mkdir(parents=True, exist_ok=True)
                self.cache_file.write_text(json.dumps(self.cache))
            except OSError:
                pass
        return [self.cache[self._key(t)] for t in texts]


class Retriever:
    def __init__(self, passages: list[Passage], embedder: OllamaEmbedder | None = None):
        self.passages = passages
        self.bm25 = BM25([tokenize(p.search_text) for p in passages])
        self.embedder = embedder
        self.vectors: list[list[float]] | None = None
        if embedder and passages:
            try:
                self.vectors = embedder.embed([p.search_text for p in passages])
            except Exception:  # model not pulled, Ollama stopped, ...
                self.vectors = None

    @property
    def backend(self) -> str:
        return f"hybrid (BM25 + {self.embedder.model} embeddings)" if self.vectors else "BM25 keyword"

    def search(self, query: str, k: int = 3) -> list[tuple[Passage, float]]:
        if not self.passages:
            return []
        sparse = self.bm25.scores(tokenize(query))
        rankings = [sorted(range(len(sparse)), key=lambda i: -sparse[i])]
        if self.vectors and self.embedder:
            try:
                q = self.embedder.embed([query])[0]
                dense = [_cosine(q, v) for v in self.vectors]
                rankings.append(sorted(range(len(dense)), key=lambda i: -dense[i]))
            except Exception:
                pass
        fused: Counter = Counter()
        for ranking in rankings:
            for rank, i in enumerate(ranking):
                if len(rankings) == 1 and sparse[i] <= 0:
                    continue                      # keyword-only: ignore non-matches
                fused[i] += 1 / (RRF_K + rank + 1)
        top = fused.most_common(k)
        return [(self.passages[i], s) for i, s in top]


_RETRIEVER: Retriever | None = None


def get_retriever(force: bool = False) -> Retriever:
    global _RETRIEVER
    if force or _RETRIEVER is None:
        use_embeddings = os.getenv("ARCHWISE_RAG_EMBEDDINGS", "auto").lower() != "off"
        embedder = None
        if use_embeddings:
            from .llm import ollama_running
            embedder = OllamaEmbedder() if ollama_running() else None
        _RETRIEVER = Retriever(load_corpus(), embedder)
    return _RETRIEVER


# ---------------------------------------------------------------------------
# Turning an evaluation into retrieval queries
# ---------------------------------------------------------------------------
def _arch_query(a) -> str:
    return " ".join([a.cloud.label, a.pattern.value.replace("_", " ")] + [c.service for c in a.components])


def _facets(*, global_users: bool, availability: float | None, spiky: bool, fast: bool) -> list[str]:
    q = []
    if global_users or (availability or 0) >= 99.95:
        q.append("availability multi-region failover SLA users on several continents")
    if spiky:
        q.append("traffic spikes seasonal peak cost always-on versus serverless quotas")
    if fast:
        q.append("p95 latency cold starts caching database round trips")
    return q


def queries_for(obj) -> list[str]:
    """Retrieval queries derived from the engine's output (picks, warnings, requirement facets)."""
    from .models import Recommendation
    queries: list[str] = []
    if isinstance(obj, Recommendation):
        req = obj.requirements
        for p in obj.picks:
            queries.append(_arch_query(p.architecture))
            queries += [f"{w.name} {p.architecture.pattern.value}" for w in p.architecture.warnings]
        queries += _facets(global_users=req.global_users, availability=req.availability_target,
                           spiky=req.traffic_pattern.value == "spiky", fast=req.p95_latency_ms <= 300)
        return queries
    # ScenarioReport
    intake = obj.intake
    by_id = {a.id: a for r in obj.recommendations.values() for a in r.all_candidates}
    opts = {o.key: o for o in obj.options}
    for p in obj.picks:
        o = opts[p.option_key]
        arch = next((by_id[t.architecture_id] for t in o.per_tier.values() if t.architecture_id in by_id), None)
        queries.append(_arch_query(arch) if arch else f"{o.cloud.label} {o.pattern.value.replace('_', ' ')}")
    queries += _facets(global_users=intake.global_users,
                       availability=99.95 if intake.downtime_tolerance.value == "never" else None,
                       spiky=intake.growth_pattern.value in ("seasonal", "new_startup"),
                       fast=intake.response_time.value == "milliseconds")
    return queries


def passages_for(obj, limit: int = 8, per_query: int = 3) -> list[Passage]:
    """Distinct passages most relevant to an evaluation, best-first, at most `limit`."""
    r = get_retriever()
    seen: dict[str, Passage] = {}
    ranked = [r.search(q, per_query) for q in queries_for(obj)]
    # Round-robin across queries so every pick and requirement facet is represented.
    for depth in range(per_query):
        for hits in ranked:
            if depth < len(hits):
                seen.setdefault(hits[depth][0].id, hits[depth][0])
    return list(seen.values())[:limit]
