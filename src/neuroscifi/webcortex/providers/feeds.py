"""RSS/Atom feeds as a search provider: fetch each configured feed (purpose='feed', robots apply), keep items that
share words with the query, rank by lexical overlap and recency, honour `query.since`.

Parsing uses `extract.parse_feed` when the extraction module is installed; otherwise a minimal local parser over
`safety.safe_xml_parse` (DOCTYPE/ENTITY declarations refused in any encoding, UTF-16 included; no entity
expansion) that reads RSS 2.0, RSS 1.0 (RDF) and Atom items.
A provider instance (one per research run or tick) fetches each feed at most once per MEMO_SECONDS, not once per
planned query.
"""
from __future__ import annotations

from email.utils import parsedate_to_datetime
import math
import re
import threading
import time

from ..safety import safe_xml_parse
from ..search import ProviderError, parse_iso_date, strip_html
from ..types import FetchError, Query, SearchResult

_STOP = frozenset("a an and are as at be by for from has have how in is it its of on or that the this to was "
                  "were what when where which who why will with".split())
HALF_LIFE_DAYS = 14.0
MEMO_SECONDS = 300.0


class _HTTPError(Exception):
    pass


def words(text: str) -> list[str]:
    return [w for w in re.findall(r"[^\W_]+", (text or "").lower()) if w not in _STOP]


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
        if self.fetcher is None:
            raise ProviderError(self.name, "no fetcher configured", retryable=False, code="disabled")
        limit = max(1, min(int(limit), 20))
        qwords = set(words(query.text))
        if not qwords or not self.feed_urls:
            return []
        parse = _parser()
        now = self._clock()
        scored, errors = [], []
        for feed_url in self.feed_urls:
            try:
                items = self._items(feed_url, parse, budget, usage)
            except FetchError as exc:
                errors.append(f"{feed_url}: {exc.code}")
                continue
            except _HTTPError as exc:
                errors.append(f"{feed_url}: {exc}")
                continue
            except Exception as exc:   # any parser failure on untrusted bytes is a per-feed error, recorded
                errors.append(f"{feed_url}: unparseable feed: {exc}"[:200])
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
                overlap = len(qwords & set(words(f"{item.get('title', '')} {item.get('summary', '')}")))
                if not overlap:
                    continue
                age_days = max(0.0, (now - published) / 86400.0) if published is not None else None
                recency = math.exp(-age_days * math.log(2) / HALF_LIFE_DAYS) if age_days is not None else 0.0
                score = overlap / len(qwords) + 0.5 * recency
                scored.append((score, published or 0.0, url, item, feed_url))
        if errors and len(errors) == len(self.feed_urls):
            raise ProviderError(self.name, "every feed failed: " + "; ".join(errors)[:280])
        scored.sort(key=lambda t: (-t[0], -t[1], t[2]))
        out, seen = [], set()
        for score, _, url, item, feed_url in scored:
            if url in seen:
                continue
            seen.add(url)
            out.append(SearchResult(url=url, title=str(item.get("title") or url)[:500],
                                    snippet=str(item.get("summary") or "")[:400], provider=self.name,
                                    rank=len(out) + 1, kind="feed", published_at=item.get("published_at"),
                                    metadata={"feed": feed_url, "match": round(score, 4)}))
            if len(out) >= limit:
                break
        return out
