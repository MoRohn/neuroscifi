"""RSS/Atom feeds as a search provider: fetch each configured feed (purpose='feed', robots apply), keep the items
relevant to the query, rank by lexical overlap and recency, honour `query.since`.

Relevance (W-14: an owner's feeds must not pollute unrelated research): query terms are the query's topical words
(stopwords, cue and temporal words and years dropped) compared as 5-letter stems. An item is relevant only when it
matches at least min(2, number of terms) distinct terms, or every word of every named entity in the query, and its
BM25 score (over the items of this search, smoothed with FEED_BM25_BACKGROUND absent documents so a tiny feed does
not inflate rare-word weights) reaches FEED_BM25_FLOOR. One shared word is not enough.

Each feed is one attempt in `last_attempts` (thread-local, like open-webSearch's engines): a feed that failed while
others answered is recorded there (the registry lists it as a failed attempt), never dropped silently.

Parsing uses `extract.parse_feed` when the extraction module is installed; otherwise a minimal local parser over
`safety.safe_xml_parse` (DOCTYPE/ENTITY declarations refused in any encoding, UTF-16 included; no entity
expansion) that reads RSS 2.0, RSS 1.0 (RDF) and Atom items.
A provider instance (one per research run or tick) fetches each feed at most once per MEMO_SECONDS, not once per
planned query.
"""
from __future__ import annotations

from collections import Counter
from email.utils import parsedate_to_datetime
import math
import re
import threading
import time

from ..safety import safe_xml_parse
from ..search import ProviderError, parse_iso_date, strip_html
from ..types import FetchError, Query, SearchResult, Stopped

_STOP = frozenset("a an and are as at be by for from has have how in is it its of on or that the this to was "
                  "were what when where which who why will with".split())
HALF_LIFE_DAYS = 14.0
MEMO_SECONDS = 300.0
FEED_BM25_FLOOR = 1.0
FEED_BM25_BACKGROUND = 10
_K1, _B = 1.5, 0.75


class _HTTPError(Exception):
    pass


def words(text: str) -> list[str]:
    return [w for w in re.findall(r"[^\W_]+", (text or "").lower()) if w not in _STOP]


def _stems(text: str) -> list[str]:
    from ..intent import META, STOPWORDS, _TEMPORAL
    out = []
    for w in words(text):
        if w in STOPWORDS or w in META or w in _TEMPORAL or re.fullmatch(r"(?:19|20)\d{2}", w):
            continue
        out.append(w[:5])
    return out


def _entity_stems(text: str) -> list[set]:
    from ..intent import extract_entities
    return [set(_stems(e)) for e in extract_entities(text or "") if _stems(e)]


def _bm25(query_terms, docs: list[list[str]]) -> list[float]:
    n = len(docs) + FEED_BM25_BACKGROUND
    avg = sum(len(d) for d in docs) / len(docs) if docs else 0.0
    df = Counter(t for d in docs for t in set(d))
    out = []
    for doc in docs:
        tf = Counter(doc)
        norm = _K1 * (1 - _B + _B * len(doc) / avg) if avg else _K1
        out.append(sum(math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5)) * tf[t] * (_K1 + 1) / (tf[t] + norm)
                       for t in query_terms if t in tf))
    return out


def relevant(query_text: str, item_text: str, score: float) -> bool:
    """The W-14 relevance rule for one feed item (see the module docstring); `score` is its BM25 score."""
    terms = set(_stems(query_text))
    if not terms or score < FEED_BM25_FLOOR:
        return False
    have = set(_stems(item_text))
    entities = _entity_stems(query_text)
    return len(terms & have) >= min(2, len(terms)) or (bool(entities) and all(e <= have for e in entities))


def _date(text) -> float | None:
    if not text:
        return None
    text = str(text).strip()
    ts = parse_iso_date(text)
    if ts is not None:
        return ts
    try:
        return parsedate_to_datetime(text).timestamp()
    except (TypeError, ValueError, IndexError, OverflowError):
        return None


def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1].lower() if isinstance(tag, str) else ""


def parse_feed_minimal(body: bytes, url: str) -> list[dict]:
    """Safe minimal RSS/Atom parser -> [{title, url, published_at, summary}]. Raises ValueError on refusal."""
    root = safe_xml_parse(body, on_refuse=lambda detail: ValueError(f"feed refused: {detail}"))
    items = []
    for el in root.iter():
        if _local(el.tag) not in ("item", "entry"):
            continue
        fields: dict = {}
        for child in el:
            name = _local(child.tag)
            if name == "link":
                href = child.get("href")
                if href and child.get("rel", "alternate") == "alternate":
                    fields.setdefault("link", href)
                elif (child.text or "").strip():
                    fields.setdefault("link", child.text.strip())
            elif name not in fields:
                fields[name] = "".join(child.itertext()).strip()
        link = fields.get("link") or fields.get("guid") or fields.get("id") or ""
        if not link.startswith(("http://", "https://")):
            continue
        items.append({"title": strip_html(fields.get("title"))[:500], "url": link,
                      "published_at": _date(fields.get("pubdate") or fields.get("published") or fields.get("date")
                                            or fields.get("updated")),
                      "summary": strip_html(fields.get("description") or fields.get("summary")
                                            or fields.get("content"))[:2000]})
    return items


def _parser():
    try:
        from ..extract import parse_feed   # Lane A's parser when installed
        return parse_feed
    except Exception:
        return parse_feed_minimal


class FeedProvider:
    name = "feeds"
    kinds = frozenset({"news", "web"})

    def __init__(self, feed_urls, *, fetcher=None, clock=time.time, name: str = "feeds"):
        self.feed_urls = [str(u) for u in feed_urls if str(u).startswith(("http://", "https://"))]
        self.fetcher, self._clock, self.name = fetcher, clock, name
        self._memo: dict = {}     # feed url -> (parsed at, items): one fetch per feed per MEMO_SECONDS, not per query
        self._lock = threading.Lock()
        self._local = threading.local()

    @property
    def last_attempts(self) -> list[dict]:
        """Per-feed attempts of this thread's most recent search: {engine: feed url, ok, n, error, latency}."""
        return list(getattr(self._local, "attempts", []))

    def _items(self, feed_url, parse, budget, usage):
        """Parsed items of one feed (memoised MEMO_SECONDS). Raises FetchError / ValueError-like parser errors."""
        now = self._clock()
        with self._lock:
            hit = self._memo.get(feed_url)
        if hit is not None and now - hit[0] < MEMO_SECONDS:
            return hit[1]
        result = self.fetcher.fetch(feed_url, purpose="feed", budget=budget, usage=usage)
        if result.status >= 400:
            raise _HTTPError(f"HTTP {result.status}")
        items = parse(result.body, result.final_url or feed_url)
        with self._lock:
            self._memo[feed_url] = (now, items)
        return items

    def search(self, query: Query, limit: int, *, budget=None, usage=None) -> list[SearchResult]:
        attempts: list[dict] = []
        self._local.attempts = attempts
        if self.fetcher is None:
            raise ProviderError(self.name, "no fetcher configured", retryable=False, code="disabled")
        limit = max(1, min(int(limit), 20))
        qterms = set(_stems(query.text))
        if not qterms or not self.feed_urls:
            return []
        parse = _parser()
        now = self._clock()
        candidates, errors = [], []
        for feed_url in self.feed_urls:
            started = time.perf_counter()
            error = None
            try:
                items = self._items(feed_url, parse, budget, usage)
            except FetchError as exc:
                error = f"{exc.code}: {exc.detail}"[:200] if exc.detail else exc.code
            except _HTTPError as exc:
                error = str(exc)
            except Stopped:            # the owner's stop (the search's gate): not a feed's failure
                raise
            except Exception as exc:   # any parser failure on untrusted bytes is a per-feed error, recorded
                error = f"unparseable feed: {exc}"[:200]
            attempt = {"engine": feed_url[:200], "ok": error is None, "n": 0, "error": error,
                       "latency": round(time.perf_counter() - started, 4)}
            attempts.append(attempt)
            if error is not None:
                errors.append(f"{feed_url}: {error}")
                continue
            for item in items:
                url = item.get("url") or ""
                if not url.startswith(("http://", "https://")):
                    continue
                if query.site and query.site.lower() not in url.lower():
                    continue
                published = item.get("published_at")
                if query.since is not None and published is not None and published < query.since:
                    continue
                text = f"{item.get('title', '')} {item.get('summary', '')}"
                if qterms & set(_stems(text)):
                    candidates.append((text, url, item, feed_url, attempt))
        if errors and len(errors) == len(self.feed_urls):
            raise ProviderError(self.name, "every feed failed: " + "; ".join(errors)[:280])
        bm25 = _bm25(qterms, [_stems(c[0]) for c in candidates])
        scored = []
        for (text, url, item, feed_url, attempt), score in zip(candidates, bm25):
            if not relevant(query.text, text, score):
                continue
            published = item.get("published_at")
            overlap = len(qterms & set(_stems(text)))
            age_days = max(0.0, (now - published) / 86400.0) if published is not None else None
            recency = math.exp(-age_days * math.log(2) / HALF_LIFE_DAYS) if age_days is not None else 0.0
            scored.append((overlap / len(qterms) + 0.5 * recency, published or 0.0, url, item, feed_url, attempt,
                           score))
        scored.sort(key=lambda t: (-t[0], -t[1], t[2]))
        out, seen = [], set()
        for match, _, url, item, feed_url, attempt, score in scored:
            if url in seen:
                continue
            seen.add(url)
            attempt["n"] += 1
            out.append(SearchResult(url=url, title=str(item.get("title") or url)[:500],
                                    snippet=str(item.get("summary") or "")[:400], provider=self.name,
                                    rank=len(out) + 1, kind="feed", published_at=item.get("published_at"),
                                    metadata={"feed": feed_url, "match": round(match, 4), "bm25": round(score, 4)}))
            if len(out) >= limit:
                break
        return out
