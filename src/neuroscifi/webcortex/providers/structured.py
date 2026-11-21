"""Structured, key-free public APIs (ADR-025 fallback): encyclopedia, scholarly, package, advisory, release.

Every request goes through the injected fetcher's contract
`fetcher.fetch(url, *, method="GET", body=None, headers=None, purpose="api", budget=None, usage=None) -> FetchResult`
(net.SafeFetcher in production; it raises types.FetchError). Network failures become `ProviderError` with the fetch
code; unparseable bodies become `ProviderError(code='bad_response')`. Names that end up in a URL path (package,
repository) are validated against strict patterns first; free text is URL-encoded. `limit` is clamped to 1..20.
Popularity fields (cited_by_count) are kept as metadata only and never used as evidence of truth. All returned
text is untrusted data.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import re
from urllib.parse import quote, urlencode

from ..safety import safe_xml_parse
from ..search import ProviderError, date_string, parse_iso_date, strip_html
from ..types import FetchError, Query, SearchResult

MAX_LIMIT = 20
_SNIPPET = 400
_RETRYABLE = {"timeout", "connect_failed", "dns_failure", "rate_limited", "http_error", "tls_error"}


def _clamp(limit) -> int:
    return max(1, min(int(limit), MAX_LIMIT))


def _terms(text: str, n: int = 8) -> list[str]:
    return re.findall(r"[^\W_][\w.\-]*", text or "")[:n]


def _first(value):
    if isinstance(value, list):
        return value[0] if value else None
    return value


class _Api:
    """Shared fetch + decode with uniform error mapping. `BASE` (class attribute) is the API origin; result URLs
    stay on the public canonical hosts whatever BASE is (tests point BASE at FixtureWeb over plain HTTP)."""
    name = "api"
    kinds = frozenset()

    def __init__(self, fetcher):
        self.fetcher = fetcher

    def _fetch(self, url, *, budget, usage, method="GET", body=None, headers=None, missing_ok=False):
        try:
            result = self.fetcher.fetch(url, method=method, body=body, headers=headers, purpose="api",
                                        budget=budget, usage=usage)
        except FetchError as exc:
            if missing_ok and exc.status == 404:
                return None
            raise ProviderError(self.name, f"{exc.detail or exc}"[:300], code=exc.code,
                                retryable=exc.code in _RETRYABLE) from None
        if missing_ok and result.status == 404:
            return None
        if result.status >= 400:
            raise ProviderError(self.name, f"HTTP {result.status} from {url}"[:300], code="http_error",
                                retryable=result.status == 429 or result.status >= 500)
        return result

    def _json(self, url, *, budget, usage, **kw):
        result = self._fetch(url, budget=budget, usage=usage, **kw)
        if result is None:
            return None
        try:
            data = json.loads(result.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise ProviderError(self.name, f"unparseable JSON: {exc}"[:300], code="bad_response",
                                retryable=False) from None
        if not isinstance(data, (dict, list)):
            raise ProviderError(self.name, "JSON is not an object", code="bad_response", retryable=False)
        return data

    def _bad(self, detail: str):
        return ProviderError(self.name, detail[:300], code="bad_response", retryable=False)


def _safe_xml(body: bytes, provider: str):
    """Root of an untrusted XML response (safety.safe_xml_parse: DOCTYPE/ENTITY refused in any encoding)."""
    return safe_xml_parse(body, on_refuse=lambda detail: ProviderError(
        provider, f"unparseable XML: {detail}"[:300], code="bad_response", retryable=False))


# ---- encyclopedia ----------------------------------------------------------------------------------------------

_WIKI_TITLE = re.compile(r"[^\x00-\x1f\x7f#<>\[\]{}|]{1,255}")
ARTICLE_MAX_CHARS = 20_000


class WikipediaProvider(_Api):
    """Search hits are discovery hints only: a search snippet is a highlighted fragment ("She married the Eiffel
    Tower in"), so every result carries metadata `snippet_only: True` and must never be read as the document.
    `article(title)` returns the article's plain-text lead (the document to read instead; or fetch the page URL)."""
    name = "wikipedia"
    kinds = frozenset({"encyclopedia", "web"})
    BASE = "https://{lang}.wikipedia.org"

    @staticmethod
    def _lang(language) -> str:
        return language if isinstance(language, str) and re.fullmatch(r"[a-z]{2,3}", language) else "en"

    @staticmethod
    def page_url(title: str, lang: str = "en") -> str:
        return f"https://{lang}.wikipedia.org/wiki/{quote(title.replace(' ', '_'), safe='()_,:')}"

    def article(self, title: str, *, budget=None, usage=None, lang: str = "en",
                max_chars: int = ARTICLE_MAX_CHARS) -> dict:
        """The plain-text lead section of one article: {title, extract, last_edited, url} (one 'api' request,
        redirects followed, text bounded to `max_chars`). ProviderError code 'not_found' when there is no such
        article, 'bad_response' for an unexpected answer; FetchError codes as for search."""
        title = " ".join(str(title or "").split())
        if not _WIKI_TITLE.fullmatch(title):
            raise ProviderError(self.name, f"invalid article title {title[:80]!r}", code="bad_request",
                                retryable=False)
        lang = self._lang(lang)
        params = {"action": "query", "prop": "extracts|revisions", "explaintext": 1, "exintro": 1,
                  "rvprop": "timestamp", "redirects": 1, "titles": title, "format": "json", "formatversion": 2}
        data = self._json(f"{self.BASE.format(lang=lang)}/w/api.php?{urlencode(params)}", budget=budget,
                          usage=usage)
        pages = (data.get("query") or {}).get("pages") if isinstance(data, dict) else None
        if isinstance(pages, dict):                                   # formatversion 1 shape
            pages = list(pages.values())
        if not isinstance(pages, list) or not pages or not isinstance(pages[0], dict):
            if isinstance(data, dict) and data.get("error"):
                raise self._bad(f"API error: {str(data['error'])[:200]}")
            raise self._bad("missing query.pages")
        page = pages[0]
        if page.get("missing") is not None or page.get("invalid") is not None or "extract" not in page:
            raise ProviderError(self.name, f"no article titled {title[:80]!r}", code="not_found", retryable=False)
        found = str(page.get("title") or title)
        revisions = page.get("revisions") if isinstance(page.get("revisions"), list) else []
        edited = revisions[0].get("timestamp") if revisions and isinstance(revisions[0], dict) else None
        extract = str(page.get("extract") or "").strip()[:max(0, int(max_chars))]
        return {"title": found, "extract": extract, "last_edited": edited if isinstance(edited, str) else None,
                "url": self.page_url(found, lang)}

    def search(self, query: Query, limit: int, *, budget=None, usage=None) -> list[SearchResult]:
        lang = self._lang(query.language)
        base = self.BASE.format(lang=lang)
        params = {"action": "query", "list": "search", "srsearch": query.text[:300], "format": "json",
                  "srlimit": _clamp(limit), "srprop": "snippet|timestamp"}
        data = self._json(f"{base}/w/api.php?{urlencode(params)}", budget=budget, usage=usage)
        hits = (data.get("query") or {}).get("search") if isinstance(data, dict) else None
        if hits is None:
            if isinstance(data, dict) and data.get("error"):
                raise self._bad(f"API error: {str(data['error'])[:200]}")
            raise self._bad("missing query.search")
        out = []
        for hit in hits:
            if not isinstance(hit, dict) or not hit.get("title"):
                continue
            title = str(hit["title"])
            edited = parse_iso_date(hit.get("timestamp"))
            out.append(SearchResult(
                url=self.page_url(title, lang), title=title,
                snippet=strip_html(hit.get("snippet"))[:_SNIPPET], provider=self.name, rank=len(out) + 1,
                kind="encyclopedia", published_at=edited,
                metadata={"pageid": hit.get("pageid"), "last_edited": hit.get("timestamp"), "language": lang,
                          "snippet_only": True}))
        return out[:_clamp(limit)]


# ---- scholarly ---------------------------------------------------------------------------------------------------

_ATOM = "{http://www.w3.org/2005/Atom}"
_ARXIV = "{http://arxiv.org/schemas/atom}"
_ARXIV_ID = re.compile(r"arxiv\.org/abs/([^\s/]+(?:/\d+)?)$")


class ArxivProvider(_Api):
    name = "arxiv"
    kinds = frozenset({"academic"})
    BASE = "https://export.arxiv.org"

    def search(self, query: Query, limit: int, *, budget=None, usage=None) -> list[SearchResult]:
        terms = [t for t in _terms(query.text) if t.upper() not in ("AND", "OR", "ANDNOT")]
        if not terms:
            return []
        n = _clamp(limit)
        params = {"search_query": " AND ".join(f"all:{t}" for t in terms),
                  "sortBy": "submittedDate" if query.since else "relevance", "sortOrder": "descending",
                  "max_results": n}
        result = self._fetch(f"{self.BASE}/api/query?{urlencode(params)}", budget=budget, usage=usage)
        root = _safe_xml(result.body, self.name)
        out = []
        for entry in root.iter(f"{_ATOM}entry"):
            ident = (entry.findtext(f"{_ATOM}id") or "").strip()
            if "/api/errors" in ident:
                raise self._bad(f"arXiv API error: {(entry.findtext(f'{_ATOM}summary') or '').strip()[:200]}")
            m = _ARXIV_ID.search(ident)
            if not m:
                continue
            full = m.group(1)
            vm = re.search(r"v(\d+)$", full)
            arxiv_id = full[: vm.start()] if vm else full
            published = parse_iso_date((entry.findtext(f"{_ATOM}published") or "").strip())
            if query.since is not None and published is not None and published < query.since:
                continue
            abstract = " ".join((entry.findtext(f"{_ATOM}summary") or "").split())
            title = " ".join((entry.findtext(f"{_ATOM}title") or "").split())
            out.append(SearchResult(
                url=f"https://arxiv.org/abs/{arxiv_id}", title=title or arxiv_id, snippet=abstract[:_SNIPPET],
                provider=self.name, rank=len(out) + 1, kind="academic", published_at=published,
                metadata={"arxiv_id": arxiv_id, "version": f"v{vm.group(1)}" if vm else None,
                          "authors": [" ".join((a.findtext(f"{_ATOM}name") or "").split())
                                      for a in entry.findall(f"{_ATOM}author")][:50],
                          "abstract": abstract[:5000],
                          "updated": (entry.findtext(f"{_ATOM}updated") or "").strip() or None,
                          "categories": [c.get("term") for c in entry.findall(f"{_ATOM}category") if c.get("term")],
                          "doi": (entry.findtext(f"{_ARXIV}doi") or "").strip() or None,
                          "venue": "arXiv", "preprint": True}))
        return out[:n]


def _inverted_abstract(index) -> str:
    if not isinstance(index, dict):
        return ""
    slots: dict[int, str] = {}
    for word, positions in index.items():
        for pos in positions if isinstance(positions, list) else []:
            if isinstance(pos, int) and 0 <= pos < 5000:
                slots[pos] = str(word)
    return " ".join(slots[i] for i in sorted(slots))


def _doi_url(doi) -> str | None:
    if not isinstance(doi, str):
        return None
    doi = doi.strip()
    doi = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:\s*)", "", doi, flags=re.I)
    if not re.fullmatch(r"10\.\d{4,9}/\S{1,300}", doi):
        return None
    return "https://doi.org/" + quote(doi.lower(), safe="/:;()._-")


class OpenAlexProvider(_Api):
    name = "openalex"
    kinds = frozenset({"academic"})
    BASE = "https://api.openalex.org"

    def search(self, query: Query, limit: int, *, budget=None, usage=None) -> list[SearchResult]:
        n = _clamp(limit)
        params = {"search": query.text[:300], "per-page": n}
        if query.since is not None:
            params["filter"] = f"from_publication_date:{date_string(query.since)}"
        data = self._json(f"{self.BASE}/works?{urlencode(params)}", budget=budget, usage=usage)
        works = data.get("results") if isinstance(data, dict) else None
        if not isinstance(works, list):
            raise self._bad("missing results")
        out = []
        for w in works:
            if not isinstance(w, dict):
                continue
            doi = _doi_url(w.get("doi"))
            location = w.get("primary_location") if isinstance(w.get("primary_location"), dict) else {}
            source = location.get("source") if isinstance(location.get("source"), dict) else {}
            oa = w.get("open_access") if isinstance(w.get("open_access"), dict) else {}
            url = doi or location.get("landing_page_url") or w.get("id")
            if not isinstance(url, str) or not url.startswith(("http://", "https://")):
                continue
            abstract = _inverted_abstract(w.get("abstract_inverted_index"))
            title = strip_html(w.get("display_name") or w.get("title") or "")
            published = parse_iso_date(w.get("publication_date"))
            out.append(SearchResult(
                url=url, title=title or url, snippet=abstract[:_SNIPPET], provider=self.name, rank=len(out) + 1,
                kind="academic", published_at=published,
                metadata={"openalex_id": w.get("id"), "doi": doi, "publication_date": w.get("publication_date"),
                          "type": w.get("type"), "cited_by_count": w.get("cited_by_count"),
                          "abstract": abstract[:5000], "venue": source.get("display_name"),
                          "open_access_url": oa.get("oa_url"),
                          "authors": [str((a.get("author") or {}).get("display_name"))
                                      for a in (w.get("authorships") or [])[:50] if isinstance(a, dict)]}))
        return out[:n]


def _date_parts(value) -> float | None:
    parts = (value or {}).get("date-parts") if isinstance(value, dict) else None
    if not parts or not isinstance(parts[0], list) or not parts[0] or parts[0][0] is None:
        return None
    y, m, d = (list(parts[0]) + [1, 1])[:3]
    try:
        return datetime(int(y), int(m or 1), int(d or 1), tzinfo=timezone.utc).timestamp()
    except (TypeError, ValueError):
        return None


class CrossrefProvider(_Api):
    name = "crossref"
    kinds = frozenset({"academic"})
    BASE = "https://api.crossref.org"

    def search(self, query: Query, limit: int, *, budget=None, usage=None) -> list[SearchResult]:
        n = _clamp(limit)
        params = {"query": query.text[:300], "rows": n}
        if query.since is not None:
            params["filter"] = f"from-pub-date:{date_string(query.since)}"
        data = self._json(f"{self.BASE}/works?{urlencode(params)}", budget=budget, usage=usage)
        items = (data.get("message") or {}).get("items") if isinstance(data, dict) else None
        if not isinstance(items, list):
            raise self._bad("missing message.items")
        out = []
        for item in items:
            if not isinstance(item, dict):
                continue
            url = _doi_url(item.get("DOI"))
            if url is None:
                continue
            issued = _date_parts(item.get("issued")) or _date_parts(item.get("published"))
            title = strip_html(_first(item.get("title")) or "")
            abstract = strip_html(item.get("abstract") or "")
            authors = [" ".join(filter(None, (a.get("given"), a.get("family"))))
                       for a in (item.get("author") or [])[:50] if isinstance(a, dict)]
            out.append(SearchResult(
                url=url, title=title or url, snippet=abstract[:_SNIPPET], provider=self.name, rank=len(out) + 1,
                kind="academic", published_at=issued,
                metadata={"doi": url, "issued": date_string(issued) if issued is not None else None,
                          "venue": strip_html(_first(item.get("container-title")) or "") or None,
                          "type": item.get("type"), "authors": authors, "abstract": abstract[:5000] or None,
                          "publisher": item.get("publisher")}))
        return out[:n]


_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                       "dec"), start=1)}
_SEASONS = {"spring": 3, "summer": 6, "fall": 9, "autumn": 9, "winter": 12}


def pubmed_date(text) -> float | None:
    """'2024 Mar 5', '2024 Mar', '2024 Mar-Apr', '2024 Spring', '2024' -> Unix seconds (first day if partial)."""
    if not isinstance(text, str):
        return None
    m = re.match(r"\s*(\d{4})(?:\s+([A-Za-z]+))?(?:[-\s/]+(\d{1,2})\b)?", text)
    if not m:
        return None
    year, word, day = int(m.group(1)), (m.group(2) or "").lower(), m.group(3)
    month = _MONTHS.get(word[:3]) or _SEASONS.get(word) or 1
    try:
        return datetime(year, month, int(day) if day and _MONTHS.get(word[:3]) else 1,
                        tzinfo=timezone.utc).timestamp()
    except ValueError:
        return datetime(year, month, 1, tzinfo=timezone.utc).timestamp()


class PubMedProvider(_Api):
    name = "pubmed"
    kinds = frozenset({"academic"})
    BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

    def search(self, query: Query, limit: int, *, budget=None, usage=None) -> list[SearchResult]:
        n = _clamp(limit)
        params = {"db": "pubmed", "term": query.text[:300], "retmode": "json", "retmax": n}
        if query.since is not None:
            params.update({"mindate": date_string(query.since).replace("-", "/"), "maxdate": "3000/12/31",
                           "datetype": "pdat"})
        found = self._json(f"{self.BASE}/esearch.fcgi?{urlencode(params)}", budget=budget, usage=usage)
        ids = (found.get("esearchresult") or {}).get("idlist") if isinstance(found, dict) else None
        if not isinstance(ids, list):
            raise self._bad("missing esearchresult.idlist")
        ids = [str(i) for i in ids if re.fullmatch(r"\d{1,10}", str(i))][:n]
        if not ids:
            return []
        params = {"db": "pubmed", "id": ",".join(ids), "retmode": "json"}
        summary = self._json(f"{self.BASE}/esummary.fcgi?{urlencode(params)}", budget=budget, usage=usage)
        result = summary.get("result") if isinstance(summary, dict) else None
        if not isinstance(result, dict):
            raise self._bad("missing result")
        out = []
        for pmid in ids:
            doc = result.get(pmid)
            if not isinstance(doc, dict):
                continue
            published = pubmed_date(doc.get("pubdate")) or pubmed_date(doc.get("epubdate"))
            if query.since is not None and published is not None and published < query.since:
                continue
            doi = next((_doi_url(a.get("value")) for a in doc.get("articleids") or []
                        if isinstance(a, dict) and a.get("idtype") == "doi"), None)
            journal = doc.get("fulljournalname") or doc.get("source")
            authors = [a.get("name") for a in (doc.get("authors") or [])[:50] if isinstance(a, dict)]
            title = strip_html(doc.get("title") or "")
            out.append(SearchResult(
                url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/", title=title or f"PMID {pmid}",
                snippet=f"{journal or 'PubMed'}, {doc.get('pubdate') or 'undated'}"[:_SNIPPET],
                provider=self.name, rank=len(out) + 1, kind="academic", published_at=published,
                metadata={"pmid": pmid, "doi": doi, "venue": journal, "source": doc.get("source"),
                          "pubdate": doc.get("pubdate"), "authors": authors,
                          "pubtype": doc.get("pubtype")}))
        return out[:n]


# ---- packages, advisories, releases -------------------------------------------------------------------------------

PYPI_NAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,98}[A-Za-z0-9])?")
NPM_NAME = re.compile(r"(?:@[a-z0-9][a-z0-9._~-]{0,100}/)?[a-z0-9][a-z0-9._~-]{0,100}")
VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+_~!-]{0,63}")
GH_OWNER = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")
GH_REPO = re.compile(r"[A-Za-z0-9._-]{1,100}")
OSV_ECOSYSTEMS = {"pypi": "PyPI", "npm": "npm", "crates.io": "crates.io", "go": "Go", "maven": "Maven",
                  "rubygems": "RubyGems", "nuget": "NuGet", "packagist": "Packagist", "hex": "Hex", "pub": "Pub"}
OSV_NAME = re.compile(r"[A-Za-z0-9@][A-Za-z0-9._\-/:@+]{0,213}")
OSV_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{1,63}")


def parse_pypi_query(text: str):
    """'pypi:<name>[==version]' or a bare package name -> (name, version|None); None if not a package query."""
    text = (text or "").strip()
    if text.lower().startswith("pypi:"):
        text = text[5:].strip()
    elif " " in text:
        return None
    name, _, version = text.partition("==")
    if not PYPI_NAME.fullmatch(name) or (version and not VERSION.fullmatch(version)):
        return None
    return name, version or None


class PyPIProvider(_Api):
    name = "pypi"
    kinds = frozenset({"technical"})
    BASE = "https://pypi.org"

    def accepts(self, query: Query) -> bool:
        return parse_pypi_query(query.text) is not None

    def search(self, query: Query, limit: int, *, budget=None, usage=None) -> list[SearchResult]:
        parsed = parse_pypi_query(query.text)
        if parsed is None:
            return []
        name, wanted = parsed
        data = self._json(f"{self.BASE}/pypi/{quote(name)}/json", budget=budget, usage=usage, missing_ok=True)
        if data is None:
            return []
        info = data.get("info") if isinstance(data, dict) else None
        releases = data.get("releases") if isinstance(data, dict) else None
        if not isinstance(info, dict) or not isinstance(releases, dict):
            raise self._bad("missing info/releases")
        latest = str(info.get("version") or "")

        def released(files):
            times = [parse_iso_date(f.get("upload_time_iso_8601")) for f in files or [] if isinstance(f, dict)]
            times = [t for t in times if t is not None]
            return min(times) if times else None

        def yanked(files):
            files = [f for f in files or [] if isinstance(f, dict)]
            return bool(files) and all(f.get("yanked") for f in files)

        history = sorted(((released(files), v, yanked(files)) for v, files in releases.items() if files),
                         key=lambda t: (t[0] is not None, t[0] or 0.0))
        latest_files = data.get("urls") if isinstance(data.get("urls"), list) else releases.get(latest)
        released_at = released(latest_files)
        recent = [{"version": v, "released_at": t, "released": date_string(t) if t else None, "yanked": y}
                  for t, v, y in history[-5:]][::-1]
        project_urls = info.get("project_urls") if isinstance(info.get("project_urls"), dict) else {}
        homepage = info.get("home_page") or project_urls.get("Homepage") or project_urls.get("homepage")
        metadata = {"package": info.get("name") or name, "ecosystem": "PyPI", "latest": latest,
                    "released_at": released_at, "yanked": bool(info.get("yanked")) or yanked(latest_files),
                    "requires_python": info.get("requires_python"), "homepage": homepage,
                    "versions_recent": recent, "summary": strip_html(info.get("summary") or "")[:300]}
        if wanted:
            files = releases.get(wanted)
            metadata.update({"requested": wanted, "requested_exists": files is not None,
                             "requested_released_at": released(files), "requested_yanked": yanked(files),
                             "requested_is_latest": wanted == latest})
        when = date_string(released_at) if released_at else "unknown date"
        # written here, not by the package: claims.extract_claims makes it a template, never a quote (RT-W-03)
        metadata["provider_text"] = f"Latest release {latest} uploaded {when}; requires Python " \
                                    f"{info.get('requires_python') or 'any'}."
        return [SearchResult(
            url=f"https://pypi.org/project/{quote(metadata['package'])}/",
            title=f"{metadata['package']} {latest} (PyPI)",
            snippet=f"{metadata['provider_text']} {metadata['summary']}"[:_SNIPPET],
            provider=self.name, rank=1, kind="package", published_at=released_at, metadata=metadata)]


def parse_npm_query(text: str) -> str | None:
    text = (text or "").strip()
    if text.lower().startswith("npm:"):
        text = text[4:].strip()
    elif " " in text:
        return None
    return text if len(text) <= 214 and NPM_NAME.fullmatch(text) else None


class NpmProvider(_Api):
    name = "npm"
    kinds = frozenset({"technical"})
    BASE = "https://registry.npmjs.org"

    def accepts(self, query: Query) -> bool:
        return parse_npm_query(query.text) is not None

    def search(self, query: Query, limit: int, *, budget=None, usage=None) -> list[SearchResult]:
        name = parse_npm_query(query.text)
        if name is None:
            return []
        url = f"{self.BASE}/" + quote(name, safe="@")
        try:
            data = self._json(url, budget=budget, usage=usage, missing_ok=True)
            abbreviated = False
        except ProviderError as exc:
            if exc.code not in ("too_large", "bad_response"):
                raise
            # full documents of very popular packages exceed the size limit: abbreviated metadata has no `time`
            data = self._json(url, budget=budget, usage=usage, missing_ok=True,
                              headers={"Accept": "application/vnd.npm.install-v1+json"})
            abbreviated = True
        if data is None:
            return []
        tags = data.get("dist-tags") if isinstance(data, dict) else None
        if not isinstance(tags, dict) or not tags.get("latest"):
            raise self._bad("missing dist-tags.latest")
        latest = str(tags["latest"])
        times = data.get("time") if isinstance(data.get("time"), dict) else {}
        released_at = parse_iso_date(times.get(latest))
        recent = sorted(((parse_iso_date(t), v) for v, t in times.items() if v not in ("created", "modified")),
                        key=lambda p: p[0] or 0.0)[-5:][::-1]
        info = (data.get("versions") or {}).get(latest) if isinstance(data.get("versions"), dict) else None
        info = info if isinstance(info, dict) else {}
        deprecated = info.get("deprecated")
        metadata = {"package": name, "ecosystem": "npm", "latest": latest, "released_at": released_at,
                    "modified": data.get("modified") or times.get("modified"), "abbreviated": abbreviated,
                    "deprecated": str(deprecated)[:300] if deprecated else None,
                    "dist_tags": {str(k): str(v) for k, v in list(tags.items())[:10]},
                    "versions_recent": [{"version": v, "released_at": t, "released": date_string(t) if t else None}
                                        for t, v in recent],
                    "homepage": info.get("homepage") or data.get("homepage"),
                    "summary": strip_html(data.get("description") or "")[:300]}
        when = date_string(released_at) if released_at else "unknown date"
        metadata["provider_text"] = f"Latest release {latest} published {when}."     # a template (RT-W-03)
        return [SearchResult(
            url=f"https://www.npmjs.com/package/{quote(name, safe='@/')}", title=f"{name} {latest} (npm)",
            snippet=f"{metadata['provider_text']} {metadata['summary']}"[:_SNIPPET],
            provider=self.name, rank=1, kind="package", published_at=released_at, metadata=metadata)]


def parse_osv_query(text: str):
    """'osv:<ecosystem>:<name>[@<version>]' -> (ecosystem, name, version|None) or None."""
    text = (text or "").strip()
    if not text.lower().startswith("osv:"):
        return None
    eco, sep, rest = text[4:].partition(":")
    ecosystem = OSV_ECOSYSTEMS.get(eco.strip().lower())
    if not sep or ecosystem is None:
        return None
    name, version = rest.strip(), None
    if "@" in name[1:]:
        at = name.rindex("@")
        name, version = name[:at], name[at + 1:]
    if not OSV_NAME.fullmatch(name) or (version is not None and not VERSION.fullmatch(version)):
        return None
    return ecosystem, name, version


class OSVProvider(_Api):
    name = "osv"
    kinds = frozenset({"technical"})
    BASE = "https://api.osv.dev"

    def accepts(self, query: Query) -> bool:
        return parse_osv_query(query.text) is not None

    def search(self, query: Query, limit: int, *, budget=None, usage=None) -> list[SearchResult]:
        parsed = parse_osv_query(query.text)
        if parsed is None:
            return []
        ecosystem, name, version = parsed
        body = {"package": {"name": name, "ecosystem": ecosystem}}
        if version:
            body["version"] = version
        data = self._json(f"{self.BASE}/v1/query", budget=budget, usage=usage, method="POST",
                          body=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        if not isinstance(data, dict):
            raise self._bad("response is not an object")
        vulns = data.get("vulns") or []
        if not isinstance(vulns, list):
            raise self._bad("vulns is not a list")
        out = []
        for v in vulns:
            if not isinstance(v, dict) or not OSV_ID.fullmatch(str(v.get("id") or "")):
                continue
            ranges, fixed = [], []
            for aff in v.get("affected") or []:
                if not isinstance(aff, dict):
                    continue
                pkg = aff.get("package") if isinstance(aff.get("package"), dict) else {}
                if pkg and str(pkg.get("name", "")).lower() != name.lower():
                    continue
                for rng in aff.get("ranges") or []:
                    events = [e for e in (rng.get("events") or []) if isinstance(e, dict)] \
                        if isinstance(rng, dict) else []
                    ranges.append({"type": rng.get("type"), "events": events[:20]})
                    if str(rng.get("type") or "").upper() != "GIT":   # GIT "fixed" values are commit hashes
                        fixed += [str(e["fixed"]) for e in events if e.get("fixed")]
            severity = [{"type": s.get("type"), "score": s.get("score")} for s in v.get("severity") or []
                        if isinstance(s, dict)]
            db_sev = (v.get("database_specific") or {}).get("severity") if isinstance(
                v.get("database_specific"), dict) else None
            vid = str(v["id"])
            out.append(SearchResult(
                url=f"https://osv.dev/vulnerability/{vid}", title=strip_html(v.get("summary") or vid)[:300],
                snippet=strip_html(v.get("details") or v.get("summary") or "")[:_SNIPPET], provider=self.name,
                rank=len(out) + 1, kind="advisory", published_at=parse_iso_date(v.get("published")),
                metadata={"id": vid, "aliases": [str(a) for a in v.get("aliases") or []][:20],
                          "severity": severity, "severity_label": db_sev, "affected_ranges": ranges[:20],
                          "fixed_versions": sorted(set(fixed)), "modified": v.get("modified"),
                          "published": v.get("published"), "package": name, "ecosystem": ecosystem,
                          "queried_version": version}))
        return out[:_clamp(limit)]


def parse_github_query(text: str):
    text = (text or "").strip()
    if not text.lower().startswith("github:"):
        return None
    owner, sep, repo = text[7:].strip().partition("/")
    if not sep or not GH_OWNER.fullmatch(owner) or not GH_REPO.fullmatch(repo) or repo in (".", ".."):
        return None
    return owner, repo


class GitHubReleasesProvider(_Api):
    name = "github"
    kinds = frozenset({"technical"})
    BASE = "https://api.github.com"

    def accepts(self, query: Query) -> bool:
        return parse_github_query(query.text) is not None

    def search(self, query: Query, limit: int, *, budget=None, usage=None) -> list[SearchResult]:
        parsed = parse_github_query(query.text)
        if parsed is None:
            return []
        owner, repo = parsed
        data = self._json(f"{self.BASE}/repos/{owner}/{repo}/releases?per_page=5", budget=budget,
                          usage=usage, missing_ok=True, headers={"Accept": "application/vnd.github+json"})
        if data is None:
            return []
        if not isinstance(data, list):
            raise self._bad("releases is not a list")
        out = []
        for rel in data:
            if not isinstance(rel, dict) or rel.get("draft"):
                continue
            url = rel.get("html_url")
            if not isinstance(url, str) or not url.startswith("https://github.com/"):
                continue
            tag = str(rel.get("tag_name") or "")[:100]
            name = strip_html(rel.get("name") or "")[:200]
            out.append(SearchResult(
                url=url, title=f"{owner}/{repo} {tag}" + (f": {name}" if name and name != tag else ""),
                snippet=" ".join(str(rel.get("body") or "").split())[:_SNIPPET], provider=self.name,
                rank=len(out) + 1, kind="release", published_at=parse_iso_date(rel.get("published_at")),
                metadata={"repo": f"{owner}/{repo}", "tag": tag, "name": name,
                          "prerelease": bool(rel.get("prerelease")), "published_at": rel.get("published_at")}))
        return out[:_clamp(limit)]


__all__ = ["WikipediaProvider", "ArxivProvider", "OpenAlexProvider", "CrossrefProvider", "PubMedProvider",
           "PyPIProvider", "NpmProvider", "OSVProvider", "GitHubReleasesProvider", "parse_pypi_query",
           "parse_npm_query", "parse_osv_query", "parse_github_query", "pubmed_date"]
