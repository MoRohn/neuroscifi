"""Offline provider: BM25 over title + snippet of a fixed result list (tests and the offline benchmark)."""
from __future__ import annotations

from dataclasses import fields, replace
import math
from collections import Counter

from ..types import QUERY_KINDS, Query, SearchResult
from .feeds import words

_RESULT_FIELDS = {f.name for f in fields(SearchResult)}


class CorpusProvider:
    general_web = False

    def __init__(self, results, name: str = "corpus", kinds=frozenset(QUERY_KINDS), *, k1: float = 1.5,
                 b: float = 0.75):
        self.name, self.kinds, self.k1, self.b = name, frozenset(kinds), k1, b
        self.results = []
        for r in results:
            if isinstance(r, dict):
                raw = {k: v for k, v in r.items() if k in _RESULT_FIELDS}
                raw.setdefault("title", "")
                raw.setdefault("snippet", "")
                raw.setdefault("provider", name)
                r = SearchResult(**raw)
            self.results.append(r)
        self._docs = [words(f"{r.title} {r.snippet}") for r in self.results]
        self._tf = [Counter(d) for d in self._docs]
        self._avg = (sum(len(d) for d in self._docs) / len(self._docs)) if self._docs else 0.0
        df = Counter(w for d in self._docs for w in set(d))
        n = len(self._docs)
        self._idf = {w: math.log(1 + (n - c + 0.5) / (c + 0.5)) for w, c in df.items()}

    def scores(self, text: str) -> list[float]:
        q = words(text)
        out = []
        for doc, tf in zip(self._docs, self._tf):
            norm = self.k1 * (1 - self.b + self.b * len(doc) / self._avg) if self._avg else self.k1
            out.append(sum(self._idf.get(w, 0.0) * tf[w] * (self.k1 + 1) / (tf[w] + norm) for w in q if w in tf))
        return out

    def search(self, query: Query, limit: int, *, budget=None, usage=None) -> list[SearchResult]:
        limit = max(1, min(int(limit), 20))
        ranked = []
        for i, (score, r) in enumerate(zip(self.scores(query.text), self.results)):
            if score <= 0:
                continue
            if query.since is not None and r.published_at is not None and r.published_at < query.since:
                continue
            if query.site and query.site.lower() not in r.url.lower():
                continue
            ranked.append((-score, i, r))
        ranked.sort()
        return [replace(r, provider=self.name, rank=n, score=0.0)
                for n, (_, _, r) in enumerate(ranked[:limit], start=1)]
