"""Structured, feed and corpus providers against a fake fetcher with real-shaped API responses."""
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from neuroscifi.webcortex import providers
from neuroscifi.webcortex.config import WebConfig
from neuroscifi.webcortex.providers.corpus import CorpusProvider
from neuroscifi.webcortex.providers.feeds import FeedProvider, parse_feed_minimal
from neuroscifi.webcortex.providers.openwebsearch import NotLoopback, OpenWebSearchProvider
from neuroscifi.webcortex.providers.structured import (ArxivProvider, CrossrefProvider, GitHubReleasesProvider,
                                                       NpmProvider, OpenAlexProvider, OSVProvider, PubMedProvider,
                                                       PyPIProvider, WikipediaProvider, parse_github_query,
                                                       parse_osv_query, parse_pypi_query, pubmed_date)
from neuroscifi.webcortex.search import ProviderError, ProviderRegistry
from neuroscifi.webcortex.types import Budget, FetchError, FetchResult, Query, QueryPlan, SearchResult, Usage

NOW = 1795132800.0   # 2026-11-20T00:00:00Z


class FakeFetcher:
    """Implements the SafeFetcher.fetch contract with canned bodies keyed by URL; records every request."""

    def __init__(self, routes=None):
        self.routes = dict(routes or {})
        self.requests = []

    def fetch(self, url, *, method="GET", body=None, headers=None, purpose="page", budget=None, usage=None,
              conditional=True):
        self.requests.append({"url": url, "method": method, "body": body, "headers": headers or {},
                              "purpose": purpose})
        if budget is not None and usage is not None:
            usage.charge(budget, requests=1)
        route = self.routes.get(url)
        if route is None:
            raise FetchError("http_error", url, "HTTP 404", status=404)
        if isinstance(route, BaseException):
            raise route
        if callable(route):
            route = route(url, body, headers or {})
        status, payload, ctype = route if isinstance(route, tuple) else (200, route, "application/json")
        if isinstance(payload, (dict, list)):
            payload = json.dumps(payload)
        if isinstance(payload, str):
            payload = payload.encode()
        return FetchResult(url=url, final_url=url, status=status, headers={"content-type": ctype},
                           content_type=ctype, body=payload, retrieved_at=NOW)

    @property
    def urls(self):
        return [r["url"] for r in self.requests]


def qs(url):
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


# ---- Wikipedia ---------------------------------------------------------------------------------------------------

WIKI = {"batchcomplete": "", "continue": {"sroffset": 2, "continue": "-||"}, "query": {"searchinfo": {"totalhits": 812},
        "search": [
            {"ns": 0, "title": "Dendritic computation", "pageid": 23514590, "snippet":
             '<span class="searchmatch">Dendritic</span> <span class="searchmatch">computation</span> is the ability'
             ' of the dendrites &amp; neurons', "timestamp": "2026-09-14T08:12:03Z"},
            {"ns": 0, "title": "Neuron (software)", "pageid": 2117, "snippet": "simulation", "timestamp":
             "2025-01-02T00:00:00Z"}]}}


def test_wikipedia():
    url = ("https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch=dendritic+computation&format=json"
           "&srlimit=20&srprop=snippet%7Ctimestamp")
    f = FakeFetcher({url: WIKI})
    out = WikipediaProvider(f).search(Query("dendritic computation", "encyclopedia"), 50)
    assert f.urls == [url] and f.requests[0]["purpose"] == "api"
    assert [r.url for r in out] == ["https://en.wikipedia.org/wiki/Dendritic_computation",
                                    "https://en.wikipedia.org/wiki/Neuron_(software)"]
    assert out[0].snippet == "Dendritic computation is the ability of the dendrites & neurons"
    assert out[0].kind == "encyclopedia" and out[0].published_at == 1789373523.0
    assert out[0].metadata["pageid"] == 23514590 and out[0].metadata["last_edited"] == "2026-09-14T08:12:03Z"


def test_wikipedia_language_and_api_error():
    f = FakeFetcher()
    url_ru = ("https://ru.wikipedia.org/w/api.php?action=query&list=search&srsearch=%D0%BD%D0%B5%D0%B9%D1%80%D0%BE%D0%BD"
              "&format=json&srlimit=3&srprop=snippet%7Ctimestamp")
    f.routes[url_ru] = {"error": {"code": "internal_api_error", "info": "x"}}
    with pytest.raises(ProviderError) as err:
        WikipediaProvider(f).search(Query("нейрон", language="ru"), 3)
    assert err.value.code == "bad_response" and f.urls == [url_ru]


# ---- arXiv -------------------------------------------------------------------------------------------------------

ARXIV = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <title type="html">ArXiv Query: search_query=all:dendritic AND all:computation</title>
  <opensearch:totalResults>2</opensearch:totalResults>
  <entry>
    <id>http://arxiv.org/abs/2611.01234v2</id>
    <updated>2026-11-15T10:00:00Z</updated>
    <published>2026-11-10T09:30:00Z</published>
    <title>Dendritic Computation in
      Cortical Pyramidal Neurons</title>
    <summary>  We show that active dendrites
      perform nonlinear integration.</summary>
    <author><name>Ada Lovelace</name></author>
    <author><name>Santiago Ramón y Cajal</name></author>
    <arxiv:doi>10.1234/neuro.2026.5</arxiv:doi>
    <link href="http://arxiv.org/abs/2611.01234v2" rel="alternate" type="text/html"/>
    <arxiv:primary_category term="q-bio.NC"/>
    <category term="q-bio.NC" scheme="http://arxiv.org/schemas/atom"/>
    <category term="cs.NE" scheme="http://arxiv.org/schemas/atom"/>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2401.00001v1</id>
    <updated>2024-01-01T00:00:00Z</updated>
    <published>2024-01-01T00:00:00Z</published>
    <title>Old dendrites</title>
    <summary>Older.</summary>
    <author><name>B</name></author>
  </entry>
</feed>"""


def test_arxiv_relevance_and_fields():
    url = ("https://export.arxiv.org/api/query?search_query=all%3Adendritic+AND+all%3Acomputation&sortBy=relevance"
           "&sortOrder=descending&max_results=5")
    f = FakeFetcher({url: (200, ARXIV, "application/atom+xml")})
    out = ArxivProvider(f).search(Query("dendritic computation", "academic"), 5)
    assert f.urls == [url]
    assert [r.url for r in out] == ["https://arxiv.org/abs/2611.01234", "https://arxiv.org/abs/2401.00001"]
    r = out[0]
    assert r.title == "Dendritic Computation in Cortical Pyramidal Neurons" and r.kind == "academic"
    assert r.published_at == 1794303000.0
    m = r.metadata
    assert m["arxiv_id"] == "2611.01234" and m["version"] == "v2" and m["doi"] == "10.1234/neuro.2026.5"
    assert m["authors"] == ["Ada Lovelace", "Santiago Ramón y Cajal"] and m["categories"] == ["q-bio.NC", "cs.NE"]
    assert m["abstract"] == "We show that active dendrites perform nonlinear integration."
    assert m["updated"] == "2026-11-15T10:00:00Z"


def test_arxiv_since_sorts_by_date_and_filters():
    url = ("https://export.arxiv.org/api/query?search_query=all%3Adendritic&sortBy=submittedDate"
           "&sortOrder=descending&max_results=5")
    f = FakeFetcher({url: (200, ARXIV, "application/atom+xml")})
    out = ArxivProvider(f).search(Query("dendritic", "academic", since=NOW - 30 * 86400), 5)
    assert f.urls == [url] and [r.metadata["arxiv_id"] for r in out] == ["2611.01234"]


def test_arxiv_refuses_doctype_and_reports_api_errors():
    bomb = '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">]><feed>&lol;</feed>'
    f = FakeFetcher()
    p = ArxivProvider(f)
    f.routes[("https://export.arxiv.org/api/query?search_query=all%3Ax&sortBy=relevance&sortOrder=descending"
              "&max_results=1")] = (200, bomb, "application/atom+xml")
    with pytest.raises(ProviderError, match="DOCTYPE"):
        p.search(Query("x"), 1)
    err_feed = ('<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/api/errors#incorrect_id'
                '</id><summary>incorrect id format</summary></entry></feed>')
    f.routes[("https://export.arxiv.org/api/query?search_query=all%3Ay&sortBy=relevance&sortOrder=descending"
              "&max_results=1")] = (200, err_feed, "application/atom+xml")
    with pytest.raises(ProviderError, match="incorrect id format"):
        p.search(Query("y"), 1)
    assert p.search(Query("AND OR"), 1) == []        # nothing searchable: no request


# ---- OpenAlex / Crossref / PubMed ----------------------------------------------------------------------------------

OPENALEX = {"meta": {"count": 1}, "results": [{
    "id": "https://openalex.org/W4390000001", "doi": "https://doi.org/10.1038/S41586-026-0001-X",
    "display_name": "Dendrites as <i>computational</i> units", "publication_date": "2026-10-02",
    "type": "article", "cited_by_count": 17,
    "abstract_inverted_index": {"Dendrites": [0], "compute": [1, 4], "and": [3], "locally": [2], "globally": [5]},
    "open_access": {"is_oa": True, "oa_url": "https://europepmc.org/articles/PMC1"},
    "primary_location": {"landing_page_url": "https://www.nature.com/articles/x", "source": {"display_name":
                                                                                            "Nature"}},
    "authorships": [{"author": {"display_name": "Ada Lovelace"}}]},
    {"id": "https://openalex.org/W2", "doi": None, "display_name": "No DOI", "publication_date": "2026-01-01",
     "primary_location": {"landing_page_url": "https://example.org/paper"}}]}


def test_openalex():
    url = ("https://api.openalex.org/works?search=dendritic+computation&per-page=4"
           "&filter=from_publication_date%3A2026-08-22")
    f = FakeFetcher({url: OPENALEX})
    out = OpenAlexProvider(f).search(Query("dendritic computation", "academic", since=NOW - 90 * 86400), 4)
    assert f.urls == [url]
    assert out[0].url == "https://doi.org/10.1038/s41586-026-0001-x"
    assert out[0].title == "Dendrites as computational units"
    assert out[0].snippet == "Dendrites compute locally and compute globally"
    m = out[0].metadata
    assert m["cited_by_count"] == 17 and m["venue"] == "Nature" and m["type"] == "article"
    assert m["open_access_url"] == "https://europepmc.org/articles/PMC1" and m["authors"] == ["Ada Lovelace"]
    assert out[1].url == "https://example.org/paper" and out[1].metadata["doi"] is None


CROSSREF = {"status": "ok", "message-type": "work-list", "message": {"total-results": 1, "items": [
    {"DOI": "10.1038/S41586-026-0001-X", "title": ["Dendrites as computational units"],
     "issued": {"date-parts": [[2026, 10, 2]]}, "container-title": ["Nature"], "type": "journal-article",
     "author": [{"given": "Ada", "family": "Lovelace"}], "publisher": "Springer",
     "abstract": "<jats:p>Active dendrites matter.</jats:p>"},
    {"DOI": "not-a-doi", "title": ["bad"]},
    {"DOI": "10.5555/partial", "title": ["Year only"], "issued": {"date-parts": [[2025]]}}]}}


def test_crossref():
    url = "https://api.crossref.org/works?query=dendritic+computation&rows=3&filter=from-pub-date%3A2025-11-20"
    f = FakeFetcher({url: CROSSREF})
    out = CrossrefProvider(f).search(Query("dendritic computation", "academic", since=NOW - 365 * 86400), 3)
    assert f.urls == [url]
    assert [r.url for r in out] == ["https://doi.org/10.1038/s41586-026-0001-x", "https://doi.org/10.5555/partial"]
    assert out[0].metadata["venue"] == "Nature" and out[0].metadata["issued"] == "2026-10-02"
    assert out[0].snippet == "Active dendrites matter." and out[0].metadata["authors"] == ["Ada Lovelace"]
    assert out[1].metadata["issued"] == "2025-01-01"


ESEARCH = {"header": {"type": "esearch"}, "esearchresult": {"count": "2", "retmax": "2", "idlist": ["39000001",
                                                                                                 "38000002"]}}
ESUMMARY = {"header": {"type": "esummary"}, "result": {"uids": ["39000001", "38000002"],
            "39000001": {"uid": "39000001", "pubdate": "2026 Oct 3", "epubdate": "2026 Sep 30",
                         "source": "Neuron", "fulljournalname": "Neuron", "title": "Dendritic spikes in vivo.",
                         "authors": [{"name": "Lovelace A", "authtype": "Author"}],
                         "articleids": [{"idtype": "pubmed", "value": "39000001"},
                                        {"idtype": "doi", "value": "10.1016/j.neuron.2026.01.001"}],
                         "pubtype": ["Journal Article"]},
            "38000002": {"uid": "38000002", "pubdate": "2024 Spring", "source": "J Neurosci",
                         "title": "Older."}}}


def test_pubmed_two_step_with_dates():
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    s_url = (f"{base}/esearch.fcgi?db=pubmed&term=dendritic+spikes&retmode=json&retmax=2&mindate=2026%2F08%2F22"
             "&maxdate=3000%2F12%2F31&datetype=pdat")
    m_url = f"{base}/esummary.fcgi?db=pubmed&id=39000001%2C38000002&retmode=json"
    f = FakeFetcher({s_url: ESEARCH, m_url: ESUMMARY})
    out = PubMedProvider(f).search(Query("dendritic spikes", "academic", since=NOW - 90 * 86400), 2)
    assert f.urls == [s_url, m_url]
    assert [r.url for r in out] == ["https://pubmed.ncbi.nlm.nih.gov/39000001/"]    # 2024 one filtered by since
    m = out[0].metadata
    assert m["venue"] == "Neuron" and m["doi"] == "https://doi.org/10.1016/j.neuron.2026.01.001"
    assert out[0].published_at == 1790985600.0


def test_pubmed_dates_and_empty():
    assert pubmed_date("2026 Oct 3") == 1790985600.0
    assert pubmed_date("2024 Mar-Apr") == pubmed_date("2024 Mar")
    assert pubmed_date("2024") is not None and pubmed_date("Spring") is None
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    f = FakeFetcher({f"{base}/esearch.fcgi?db=pubmed&term=zzz&retmode=json&retmax=3":
                     {"esearchresult": {"idlist": ["123; DROP", "../x"]}}})
    assert PubMedProvider(f).search(Query("zzz"), 3) == [] and len(f.urls) == 1   # invalid ids never fetched


# ---- packages ----------------------------------------------------------------------------------------------------

def _files(t, yanked=False):
    return [{"filename": "x.whl", "upload_time_iso_8601": t, "yanked": yanked},
            {"filename": "x.tar.gz", "upload_time_iso_8601": t.replace("00.0", "30.0"), "yanked": yanked}]


PYPI = {"info": {"name": "zstandard", "version": "0.25.0", "requires_python": ">=3.9",
                 "home_page": "", "project_urls": {"Homepage": "https://github.com/indygreg/python-zstandard"},
                 "summary": "Zstandard bindings for Python", "yanked": False},
        "releases": {"0.22.0": _files("2023-11-01T10:00:00.000000Z"), "0.23.0": _files("2024-07-15T10:00:00.000000Z"),
                     "0.24.0": _files("2025-08-17T10:00:00.000000Z", yanked=True),
                     "0.25.0": _files("2025-09-14T10:00:00.000000Z"), "0.21.0": _files("2023-04-01T10:00:00.000000Z"),
                     "0.20.0": _files("2023-02-01T10:00:00.000000Z"), "0.1.0": []},
        "urls": _files("2025-09-14T10:00:00.000000Z")}


def test_pypi():
    f = FakeFetcher({"https://pypi.org/pypi/zstandard/json": PYPI})
    p = PyPIProvider(f)
    assert p.accepts(Query("pypi:zstandard==0.24.0")) and p.accepts(Query("zstandard"))
    assert not p.accepts(Query("is zstandard fast"))
    out = p.search(Query("pypi:zstandard==0.24.0", "technical"), 5)
    assert len(out) == 1
    r = out[0]
    assert r.url == "https://pypi.org/project/zstandard/" and r.kind == "package"
    m = r.metadata
    assert m["ecosystem"] == "PyPI" and m["latest"] == "0.25.0" and m["requires_python"] == ">=3.9"
    assert m["released_at"] == 1757844000.0 == r.published_at and m["yanked"] is False
    assert m["homepage"] == "https://github.com/indygreg/python-zstandard"
    assert [v["version"] for v in m["versions_recent"]] == ["0.25.0", "0.24.0", "0.23.0", "0.22.0", "0.21.0"]
    assert m["versions_recent"][1]["yanked"] is True and m["versions_recent"][0]["released"] == "2025-09-14"
    assert m["requested"] == "0.24.0" and m["requested_yanked"] is True and m["requested_is_latest"] is False


def test_pypi_missing_and_injection():
    f = FakeFetcher()
    assert PyPIProvider(f).search(Query("pypi:does-not-exist"), 5) == []      # 404 -> nothing, not an error
    for evil in ("pypi:../../admin", "pypi:a/b", "pypi:x?y=1", "pypi:-bad", "pypi:name==1.0/../x"):
        assert parse_pypi_query(evil) is None
        assert PyPIProvider(f).search(Query(evil), 5) == []
    assert f.urls == ["https://pypi.org/pypi/does-not-exist/json"]


NPM_FULL = {"name": "@scope/pkg", "description": "A <b>thing</b>", "dist-tags": {"latest": "2.1.0", "next": "3.0.0-rc.1"},
            "time": {"created": "2020-01-01T00:00:00.000Z", "modified": "2026-11-01T00:00:00.000Z",
                     "1.0.0": "2021-01-01T00:00:00.000Z", "2.0.0": "2025-01-01T00:00:00.000Z",
                     "2.1.0": "2026-10-30T12:00:00.000Z", "3.0.0-rc.1": "2026-11-01T00:00:00.000Z"},
            "versions": {"2.1.0": {"homepage": "https://example.org", "deprecated": None}}}


def test_npm_full_and_abbreviated_fallback():
    url = "https://registry.npmjs.org/@scope%2Fpkg"
    f = FakeFetcher({url: NPM_FULL})
    out = NpmProvider(f).search(Query("npm:@scope/pkg", "technical"), 5)
    assert f.urls == [url]
    m = out[0].metadata
    assert m["latest"] == "2.1.0" and out[0].published_at == 1793361600.0 and m["abbreviated"] is False
    assert [v["version"] for v in m["versions_recent"]][:2] == ["3.0.0-rc.1", "2.1.0"]
    assert out[0].url == "https://www.npmjs.com/package/@scope/pkg"

    calls = []

    def big_then_small(u, body, headers):
        calls.append(headers.get("Accept"))
        if headers.get("Accept") is None:
            raise FetchError("too_large", u, "body exceeds 5000000 bytes")
        return {"name": "@scope/pkg", "modified": "2026-11-01T00:00:00.000Z", "dist-tags": {"latest": "2.1.0"},
                "versions": {"2.1.0": {}}}

    f2 = FakeFetcher({url: big_then_small})
    out = NpmProvider(f2).search(Query("npm:@scope/pkg"), 5)
    assert calls == [None, "application/vnd.npm.install-v1+json"]
    assert out[0].metadata["abbreviated"] is True and out[0].metadata["latest"] == "2.1.0"
    assert out[0].published_at is None


OSV = {"vulns": [{
    "id": "GHSA-abcd-1234-efgh", "summary": "Decompression bomb in <code>zstandard</code>",
    "details": "A crafted frame can exhaust memory.", "aliases": ["CVE-2026-1111"],
    "modified": "2026-11-02T00:00:00Z", "published": "2026-11-01T00:00:00Z",
    "severity": [{"type": "CVSS_V3", "score": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:N/A:H"}],
    "database_specific": {"severity": "HIGH"},
    "affected": [{"package": {"ecosystem": "PyPI", "name": "zstandard"},
                  "ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": "0.25.1"}]}]},
                 {"package": {"ecosystem": "PyPI", "name": "other"},
                  "ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": "9.9"}]}]}]},
    {"id": "../../etc", "summary": "bad id"}]}


def test_osv():
    url = "https://api.osv.dev/v1/query"
    f = FakeFetcher({url: OSV})
    p = OSVProvider(f)
    out = p.search(Query("osv:PyPI:zstandard@0.25.0", "technical"), 5)
    req = f.requests[0]
    assert req["method"] == "POST" and req["purpose"] == "api"
    assert json.loads(req["body"]) == {"package": {"name": "zstandard", "ecosystem": "PyPI"}, "version": "0.25.0"}
    assert req["headers"]["Content-Type"] == "application/json"
    assert len(out) == 1
    r = out[0]
    assert r.url == "https://osv.dev/vulnerability/GHSA-abcd-1234-efgh" and r.kind == "advisory"
    assert r.title == "Decompression bomb in zstandard" and r.published_at == 1793491200.0
    assert r.metadata["aliases"] == ["CVE-2026-1111"] and r.metadata["fixed_versions"] == ["0.25.1"]
    assert r.metadata["severity_label"] == "HIGH" and r.metadata["severity"][0]["type"] == "CVSS_V3"
    f.routes[url] = {}
    assert p.search(Query("osv:npm:@scope/pkg"), 5) == []
    assert json.loads(f.requests[1]["body"]) == {"package": {"name": "@scope/pkg", "ecosystem": "npm"}}


def test_osv_query_parsing():
    assert parse_osv_query("osv:PyPI:zstandard@0.25.0") == ("PyPI", "zstandard", "0.25.0")
    assert parse_osv_query("osv:crates.io:serde") == ("crates.io", "serde", None)
    assert parse_osv_query("osv:Go:golang.org/x/net@v0.17.0") == ("Go", "golang.org/x/net", "v0.17.0")
    assert parse_osv_query("osv:Unknown:x") is None and parse_osv_query("zstandard") is None
    assert parse_osv_query('osv:PyPI:x"}@1') is None


GH = [{"tag_name": "v2.2.0", "name": "2.2.0", "html_url": "https://github.com/acme/tool/releases/tag/v2.2.0",
       "published_at": "2026-11-01T00:00:00Z", "prerelease": False, "draft": False, "body": "Fixes  bugs."},
      {"tag_name": "v2.3.0-rc1", "name": "RC", "html_url": "https://github.com/acme/tool/releases/tag/v2.3.0-rc1",
       "published_at": "2026-11-10T00:00:00Z", "prerelease": True, "draft": False, "body": ""},
      {"tag_name": "evil", "html_url": "https://evil.example/x", "published_at": None},
      {"tag_name": "draft", "draft": True, "html_url": "https://github.com/acme/tool/releases/tag/draft"}]


RELEASE_MD = """# Tool 2.5.5 Release Notes

*Released on 2026-11-15.*

Tool 2.5.5 is a patch release that fixes bugs discovered after the 2.5.4
release. It is unusually large for a patch release due to bugs discovered
during continuing work on strings for the upcoming 2.6.0 release, ongoing
threading work, the certificate compromise fixes, and updates in the CI
infrastructure.

This release supports Python versions 3.12-3.15.

## Improvements

- `tool.linalg.svd` and `tool.linalg.lstsq` now raise for inputs containing
  infinities instead of returning **garbage**.
  ([gh-33298](https://github.com/acme/tool/pull/33298))
- The minimum supported version of the build_helper package is now 3.1.

```python
tool.svd(x)
```
"""


def test_github_release_body_is_the_whole_plain_text_document_and_the_snippet_never_cuts_a_word():
    """RT4-04: the release body was cut to the 400-character snippet (mid-word: "inste") with markdown kept
    ("# NumPy 2.5.5 Release Notes *Released ...*", "## Improvements - `numpy...`") and used as document text."""
    url = "https://api.github.com/repos/acme/tool/releases?per_page=5"
    rel = dict(GH[0], tag_name="v2.5.5", name="v2.5.5", body=RELEASE_MD + "\n" + "Further notes follow. " * 2000)
    out = GitHubReleasesProvider(FakeFetcher({url: [rel]})).search(Query("github:acme/tool", "technical"), 5)
    body = out[0].metadata["body"]
    lines = body.split("\n")
    assert lines[:4] == [
        "Tool 2.5.5 Release Notes", "Released on 2026-11-15.",
        "Tool 2.5.5 is a patch release that fixes bugs discovered after the 2.5.4 release. It is unusually large for a "
        "patch release due to bugs discovered during continuing work on strings for the upcoming 2.6.0 release, "
        "ongoing threading work, the certificate compromise fixes, and updates in the CI infrastructure.",
        "This release supports Python versions 3.12-3.15."]
    assert "tool.linalg.svd and tool.linalg.lstsq now raise for inputs containing infinities instead of returning " \
           "garbage. (gh-33298)" in lines
    assert "The minimum supported version of the build_helper package is now 3.1." in lines   # snake_case kept
    assert "tool.svd(x)" in lines and not any(m in body for m in ("#", "**", "`", "](", "```"))
    assert len(body) <= 20_000 and body.endswith("Further notes follow.")        # bounded at a word boundary
    snippet = out[0].snippet
    assert len(snippet) <= 400 and snippet.endswith("…") and snippet[:-1] in " ".join(lines)
    assert snippet[:-1].rstrip().split()[-1] in " ".join(lines).split()          # a whole word, never "inste"


def test_github_releases():
    url = "https://api.github.com/repos/acme/tool/releases?per_page=5"
    f = FakeFetcher({url: GH})
    out = GitHubReleasesProvider(f).search(Query("github:acme/tool", "technical"), 5)
    assert f.urls == [url] and f.requests[0]["headers"] == {"Accept": "application/vnd.github+json"}
    assert [r.metadata["tag"] for r in out] == ["v2.2.0", "v2.3.0-rc1"]
    assert out[0].title == "acme/tool v2.2.0: 2.2.0" and out[0].snippet == "Fixes bugs." and out[0].kind == "release"
    assert out[1].metadata["prerelease"] is True and out[1].published_at == 1794268800.0
    for evil in ("github:acme/../x", "github:a/b/c", "github:../x", "github:acme", "github:-x-/y z"):
        assert parse_github_query(evil) is None or ".." not in parse_github_query(evil)[1]
    assert parse_github_query("github:acme/..") is None and parse_github_query("github:acme/b/c") is None
    assert GitHubReleasesProvider(f).search(Query("github:nobody/nothing"), 5) == []      # 404


# ---- shared error handling ---------------------------------------------------------------------------------------

CASES = [
    (WikipediaProvider, Query("x"), "https://en.wikipedia.org/w/api.php"),
    (OpenAlexProvider, Query("x", "academic"), "https://api.openalex.org/works"),
    (CrossrefProvider, Query("x", "academic"), "https://api.crossref.org/works"),
    (PubMedProvider, Query("x", "academic"), "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"),
    (PyPIProvider, Query("pypi:x"), "https://pypi.org/pypi/x/json"),
    (NpmProvider, Query("npm:x"), "https://registry.npmjs.org/x"),
    (OSVProvider, Query("osv:PyPI:x"), "https://api.osv.dev/v1/query"),
    (GitHubReleasesProvider, Query("github:a/b"), "https://api.github.com/repos/a/b/releases"),
]


class PrefixFetcher(FakeFetcher):
    def __init__(self, prefix, response):
        super().__init__()
        self.prefix, self.response = prefix, response

    def fetch(self, url, **kw):
        assert url.startswith(self.prefix), url
        self.routes = {url: self.response}
        return super().fetch(url, **kw)


@pytest.mark.parametrize("cls,query,prefix", CASES, ids=[c[0].__name__ for c in CASES])
def test_bad_json_is_provider_error(cls, query, prefix):
    with pytest.raises(ProviderError) as err:
        cls(PrefixFetcher(prefix, (200, "{not json", "application/json"))).search(query, 3)
    assert err.value.code == "bad_response" and err.value.retryable is False and err.value.provider == cls.name


@pytest.mark.parametrize("cls,query,prefix", CASES, ids=[c[0].__name__ for c in CASES])
def test_fetch_errors_become_provider_errors(cls, query, prefix):
    with pytest.raises(ProviderError) as err:
        cls(PrefixFetcher(prefix, FetchError("rate_limited", prefix, "backoff", retry_after=30))).search(query, 3)
    assert err.value.code == "rate_limited" and err.value.retryable is True


def test_all_structured_requests_are_api_purpose_and_charge_budget():
    f = FakeFetcher({"https://pypi.org/pypi/zstandard/json": PYPI})
    usage = Usage()
    PyPIProvider(f).search(Query("zstandard"), 1, budget=Budget(), usage=usage)
    assert usage.requests == 1 and {r["purpose"] for r in f.requests} == {"api"}


# ---- feeds -------------------------------------------------------------------------------------------------------

RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>Lab news</title>
<item><title>Dendritic computation breakthrough</title><link>https://lab.example/a</link>
<pubDate>Wed, 18 Nov 2026 10:00:00 GMT</pubDate><description>&lt;p&gt;New dendritic results.&lt;/p&gt;</description></item>
<item><title>Dendritic archive</title><link>https://lab.example/old</link>
<pubDate>Mon, 01 Jan 2024 10:00:00 GMT</pubDate><description>Old dendritic computation.</description></item>
<item><title>Unrelated cooking</title><link>https://lab.example/c</link></item>
<item><title>javascript</title><link>javascript:alert(1)</link></item>
</channel></rss>"""
ATOM = """<?xml version="1.0" encoding="utf-8"?><feed xmlns="http://www.w3.org/2005/Atom"><title>T</title>
<entry><title>Computation in dendrites, a primer</title><link rel="alternate" href="https://blog.example/p"/>
<updated>2026-11-19T00:00:00Z</updated><summary>Primer.</summary></entry></feed>"""


def test_feed_provider_ranks_filters_and_tolerates_bad_feeds():
    f = FakeFetcher({"https://lab.example/rss": (200, RSS, "application/rss+xml"),
                     "https://blog.example/atom": (200, ATOM, "application/atom+xml"),
                     "https://bad.example/feed": (200, '<!DOCTYPE x [<!ENTITY a "b">]><rss/>', "text/xml")})
    p = FeedProvider(["https://lab.example/rss", "https://blog.example/atom", "https://bad.example/feed",
                      "file:///etc/passwd"], fetcher=f, clock=lambda: NOW)
    out = p.search(Query("dendritic computation"), 10)
    assert {r["purpose"] for r in f.requests} == {"feed"} and "file:///etc/passwd" not in f.urls
    # terms match as stems ("dendrites" ~ "dendritic"): the primer matches both terms and is the newest
    assert [r.url for r in out] == ["https://blog.example/p", "https://lab.example/a", "https://lab.example/old"]
    assert out[1].snippet == "New dendritic results." and out[1].kind == "feed"
    out = p.search(Query("dendritic computation", since=NOW - 30 * 86400), 10)
    assert [r.url for r in out] == ["https://blog.example/p", "https://lab.example/a"]


def test_feed_parser_refuses_doctype_and_all_failing_raises():
    with pytest.raises(ValueError):
        parse_feed_minimal(b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]><r>&x;</r>',
                           "https://x")
    f = FakeFetcher({"https://a.example/f": FetchError("blocked_robots", "https://a.example/f", "disallowed")})
    with pytest.raises(ProviderError, match="blocked_robots"):
        FeedProvider(["https://a.example/f"], fetcher=f).search(Query("x"), 3)
    with pytest.raises(ProviderError):
        FeedProvider(["https://a.example/f"]).search(Query("x"), 3)


# ---- corpus ------------------------------------------------------------------------------------------------------

def test_corpus_bm25():
    docs = [{"url": "https://a.org/1", "title": "Dendritic computation in neurons", "snippet": "dendrites compute",
             "published_at": NOW - 86400},
            {"url": "https://b.org/2", "title": "Cooking pasta", "snippet": "water and salt"},
            {"url": "https://c.org/3", "title": "Neurons", "snippet": "dendritic trees, old", "published_at": 1.0},
            SearchResult(url="https://d.org/4", title="computation", snippet="", provider="x")]
    p = CorpusProvider(docs, name="bench", kinds={"web", "academic"})
    out = p.search(Query("dendritic computation"), 10)
    assert out[0].url == "https://a.org/1"                      # both terms beat either one alone
    assert {r.url for r in out[1:]} == {"https://c.org/3", "https://d.org/4"}
    assert {r.provider for r in out} == {"bench"} and [r.rank for r in out] == [1, 2, 3]
    assert [r.url for r in p.search(Query("dendritic", since=NOW - 7 * 86400), 10)] == ["https://a.org/1"]
    assert [r.url for r in p.search(Query("dendritic", site="c.org"), 10)] == ["https://c.org/3"]
    assert p.search(Query("quantum"), 10) == []


def test_corpus_through_registry():
    docs = [{"url": f"https://s{i}.org/", "title": f"topic {i} dendrites", "snippet": ""} for i in range(5)]
    reg = ProviderRegistry([CorpusProvider(docs)])
    out = reg.search(QueryPlan(queries=(Query("dendrites"),)), budget=Budget(), usage=Usage())
    assert len(out.results) == 5 and out.results[0].score > 0


# ---- build -------------------------------------------------------------------------------------------------------

def test_build_from_config():
    f = FakeFetcher()
    built = providers.build(WebConfig(), f)
    assert [p.name for p in built] == ["openwebsearch", "wikipedia", "arxiv", "openalex", "crossref", "pubmed",
                                       "pypi", "npm", "osv", "github"]
    assert isinstance(built[0], OpenWebSearchProvider) and built[0].engines == ["duckduckgo", "brave", "startpage",
                                                                                "bing"]
    assert all(p.fetcher is f for p in built[1:])
    cfg = WebConfig(providers=["arxiv", "nonsense", "arxiv", "osv"], openwebsearch_url="http://localhost:9")
    assert [p.name for p in providers.build(cfg, f)] == ["arxiv", "osv"]
    assert providers.unknown(cfg) == ["nonsense"]
    with pytest.raises(NotLoopback):
        providers.build(WebConfig(openwebsearch_url="http://search.example.com"), f)
    names = [p.name for p in built]
    ProviderRegistry(built)          # unique names
    assert len(set(names)) == len(names)


# ---- integration: real SafeFetcher + FixtureWeb ------------------------------------------------------------------

def test_pypi_and_osv_through_safefetcher(web):
    from neuroscifi.webcortex.net import NetPolicy, SafeFetcher
    web.add("pypi.org", "/pypi/zstandard/json", json.dumps(PYPI), content_type="application/json")
    web.add("api.osv.dev", "/v1/query", json.dumps(OSV), content_type="application/json")
    fetcher = SafeFetcher(NetPolicy(min_domain_interval=0.0), resolver=web.resolver, connector=web.connector)

    class FixturePyPI(PyPIProvider):
        BASE = "http://pypi.org"

    class FixtureOSV(OSVProvider):
        BASE = "http://api.osv.dev"

    reg = ProviderRegistry([FixturePyPI(fetcher), FixtureOSV(fetcher)])
    usage = Usage()
    plan = QueryPlan(queries=(Query("pypi:zstandard", "technical"), Query("osv:PyPI:zstandard@0.25.0", "technical")))
    out = reg.search(plan, budget=Budget(), usage=usage)
    assert {r.kind for r in out.results} == {"package", "advisory"}
    pkg = next(r for r in out.results if r.kind == "package")
    assert pkg.url == "https://pypi.org/project/zstandard/" and pkg.metadata["latest"] == "0.25.0"
    assert usage.queries == 2 and usage.requests == 2          # api purpose: no robots.txt requests
    osv_req = web.requests_to("api.osv.dev")[0]
    assert osv_req["method"] == "POST" and json.loads(osv_req["body"])["version"] == "0.25.0"
    assert not web.requests_to("pypi.org", "/robots.txt")


def test_wikipedia_backoff_after_one_429_does_not_open_the_circuit(web):
    """OBS-01: one Wikipedia 429 (Retry-After) puts the host in backoff; the searches refused meanwhile with 'host
    backoff in force' are reported but must not open the provider's circuit; it answers once the backoff ends."""
    from neuroscifi.webcortex.net import NetPolicy, SafeFetcher
    now = [NOW]
    web.add("en.wikipedia.org", "/w/api.php", b"slow down", status=429, content_type="text/plain",
            headers={"Retry-After": "120"})
    fetcher = SafeFetcher(NetPolicy(min_domain_interval=0.0), resolver=web.resolver, connector=web.connector,
                          clock=lambda: now[0])

    class FixtureWikipedia(WikipediaProvider):
        BASE = "http://{lang}.wikipedia.org"

    reg = ProviderRegistry([FixtureWikipedia(fetcher)], clock=lambda: now[0], failure_threshold=2)
    errors = []
    for _ in range(4):
        out = reg.search(QueryPlan(queries=(Query("eiffel tower", "encyclopedia"),)), budget=Budget(), usage=Usage())
        errors.append(out.failures[0]["error"])
    assert "host backoff in force" in errors[-1] and len(web.requests_to("en.wikipedia.org")) == 1
    state = next(s for s in reg.status() if s["name"] == "wikipedia")
    assert state["state"] == "closed" and state["failures"] == 0, state


# ---- live --------------------------------------------------------------------------------------------------------

@pytest.mark.live
@pytest.mark.parametrize("cls,query,check", [
    (ArxivProvider, Query("dendritic computation", "academic"), lambda rs: rs and rs[0].metadata["arxiv_id"]),
    (OpenAlexProvider, Query("dendritic computation", "academic"), lambda rs: rs and rs[0].title),
    (PyPIProvider, Query("pypi:numpy", "technical"),
     lambda rs: rs and rs[0].metadata["latest"] and rs[0].metadata["released_at"]),
    (OSVProvider, Query("osv:PyPI:zstandard@0.25.0", "technical"), lambda rs: isinstance(rs, list)),
], ids=["arxiv", "openalex", "pypi-numpy", "osv-zstandard"])
def test_live_structured(cls, query, check):
    from neuroscifi.webcortex.net import NetPolicy, SafeFetcher
    fetcher = SafeFetcher(NetPolicy())
    usage = Usage()
    results = cls(fetcher).search(query, 3, budget=Budget(), usage=usage)
    print(f"\nLIVE {cls.name}: {len(results)} result(s); requests={usage.requests}")
    for r in results[:3]:
        print("  ", r.url, "|", r.title[:80], "|", {k: r.metadata.get(k) for k in
                                                     ("latest", "released_at", "arxiv_id", "doi", "id")})
    assert check(results)


def test_build_adds_the_feed_provider_only_when_feeds_exist():
    """R3: FeedProvider was never built. build() adds it for the owner's feeds; one fetch per feed per run."""
    f = FakeFetcher({"https://lab.example/rss": (200, RSS, "application/rss+xml")})
    cfg = WebConfig(providers=["openwebsearch", "wikipedia"])
    assert [p.name for p in providers.build(cfg, f)] == ["openwebsearch", "wikipedia"]
    built = providers.build(cfg, f, feeds=["https://lab.example/rss"], clock=lambda: NOW)
    assert [p.name for p in built] == ["openwebsearch", "wikipedia", "feeds"] and "feeds" in providers.KNOWN
    feed = built[-1]
    assert feed.search(Query("dendritic computation"), 5) and feed.search(Query("dendritic results"), 5)
    assert len(f.requests) == 1                                   # memoised within the run, not one per query
    assert providers.unknown(WebConfig(providers=["feeds", "nope"])) == ["nope"]


# ---- review remediation (V1): XML guards in every encoding, OSV GIT ranges -------------------------------------

UTF16_LAUGHS = '<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE r [<!ENTITY a "aaaaaaaaaa">' + "".join(
    f'<!ENTITY {chr(98 + i)} "' + ("&" + chr(97 + i) + ";") * 10 + '">' for i in range(8)) + \
    ']><feed xmlns="http://www.w3.org/2005/Atom"><entry><title>&i;</title>' \
    '<link href="http://x.example/1"/></entry></feed>'
UTF16_XXE = '<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/hostname">]>' \
    '<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>&x;</title><link href="http://x.example/1"/>' \
    '</entry></feed>'
UTF16_OK = '<?xml version="1.0" encoding="UTF-16"?><feed xmlns="http://www.w3.org/2005/Atom"><entry>' \
    '<title>Wide dendrites</title><link href="http://x.example/1"/></entry></feed>'


@pytest.mark.parametrize("doc", [UTF16_LAUGHS, UTF16_XXE])
def test_minimal_feed_parser_refuses_utf16_declarations(doc):
    with pytest.raises(ValueError, match="DOCTYPE"):
        parse_feed_minimal(doc.encode("utf-16"), "http://feeds.example/")
    assert [i["url"] for i in parse_feed_minimal(UTF16_OK.encode("utf-16"), "http://feeds.example/")] == \
        ["http://x.example/1"]


@pytest.mark.parametrize("doc", [UTF16_LAUGHS, UTF16_XXE])
def test_arxiv_refuses_utf16_declarations(doc):
    url = ("https://export.arxiv.org/api/query?search_query=all%3Ax&sortBy=relevance&sortOrder=descending"
           "&max_results=1")
    f = FakeFetcher({url: (200, doc.encode("utf-16"), "application/atom+xml")})
    with pytest.raises(ProviderError, match="DOCTYPE") as info:
        ArxivProvider(f).search(Query("x"), 1)
    assert info.value.code == "bad_response"


def test_osv_fixed_versions_exclude_git_commit_hashes():
    url = "https://api.osv.dev/v1/query"
    vuln = json.loads(json.dumps(OSV["vulns"][0]))
    vuln["affected"][0]["ranges"].append({"type": "GIT", "repo": "https://github.com/x/zstandard",
                                          "events": [{"introduced": "0"},
                                                     {"fixed": "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b"}]})
    out = OSVProvider(FakeFetcher({url: {"vulns": [vuln]}})).search(Query("osv:PyPI:zstandard@0.25.0"), 5)
    assert out[0].metadata["fixed_versions"] == ["0.25.1"]
    assert {r["type"] for r in out[0].metadata["affected_ranges"]} == {"ECOSYSTEM", "GIT"}


# ---- UAT RC1 regressions (W-10 Wikipedia snippets, W-14 / W-13 feeds) ------------------------------------------

def test_wikipedia_hits_are_snippet_only_and_article_returns_the_lead():
    url = ("https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch=dendritic+computation&format=json"
           "&srlimit=20&srprop=snippet%7Ctimestamp")
    f = FakeFetcher({url: WIKI})
    out = WikipediaProvider(f).search(Query("dendritic computation", "encyclopedia"), 50)
    assert all(r.metadata["snippet_only"] is True for r in out)
    art = ("https://en.wikipedia.org/w/api.php?action=query&prop=extracts%7Crevisions&explaintext=1&exintro=1"
           "&rvprop=timestamp&redirects=1&titles=Eiffel+Tower&format=json&formatversion=2")
    f.routes[art] = {"batchcomplete": True, "query": {"pages": [{
        "pageid": 9232, "ns": 0, "title": "Eiffel Tower",
        "extract": "The Eiffel Tower is a wrought-iron lattice tower on the Champ de Mars in Paris, France. " * 400,
        "revisions": [{"timestamp": "2026-11-18T10:00:00Z"}]}]}}
    budget, usage = Budget(max_requests=5), Usage()
    got = WikipediaProvider(f).article("Eiffel Tower", budget=budget, usage=usage, max_chars=500)
    assert f.requests[-1]["purpose"] == "api" and usage.requests == 1
    assert got["title"] == "Eiffel Tower" and got["url"] == "https://en.wikipedia.org/wiki/Eiffel_Tower"
    assert got["extract"].startswith("The Eiffel Tower is a wrought-iron lattice tower on the Champ de Mars in Paris")
    assert len(got["extract"]) <= 500 and got["last_edited"] == "2026-11-18T10:00:00Z"
    missing = art.replace("Eiffel+Tower", "Nope")
    f.routes[missing] = {"query": {"pages": [{"ns": 0, "title": "Nope", "missing": True}]}}
    with pytest.raises(ProviderError) as err:
        WikipediaProvider(f).article("Nope")
    assert err.value.code == "not_found"
    with pytest.raises(ProviderError) as err:
        WikipediaProvider(f).article("a|b")
    assert err.value.code == "bad_request"


FEED_MIXED = """<?xml version="1.0"?><rss version="2.0"><channel><title>Mixed</title>
<item><title>Injection molding plant opens</title><link>https://news.example/molding</link>
<pubDate>Wed, 18 Nov 2026 10:00:00 GMT</pubDate><description>A new injection molding plant.</description></item>
<item><title>Prompt injection in LLM agents</title><link>https://news.example/prompt</link>
<pubDate>Wed, 18 Nov 2026 11:00:00 GMT</pubDate><description>Prompt injection attacks on agents.</description></item>
<item><title>Quillstream community call</title><link>https://news.example/qs</link>
<pubDate>Wed, 18 Nov 2026 12:00:00 GMT</pubDate><description>Notes from the call.</description></item>
</channel></rss>"""


def test_feed_items_need_two_query_terms_or_every_entity():
    f = FakeFetcher({"https://news.example/rss": (200, FEED_MIXED, "application/rss+xml")})
    p = FeedProvider(["https://news.example/rss"], fetcher=f, clock=lambda: NOW)
    # one shared word ("injection") is not relevance: the molding item stays out
    assert [r.url for r in p.search(Query("latest prompt injection attacks this week"), 10)] == \
        ["https://news.example/prompt"]
    assert p.search(Query("injection molding"), 10)[0].url == "https://news.example/molding"
    assert [r.url for r in p.search(Query("Quillstream release"), 10)] == ["https://news.example/qs"]  # entity
    assert p.search(Query("release plan"), 10) == []
    assert p.search(Query("last week"), 10) == []                       # nothing topical to match


def test_feed_partial_failures_are_recorded_as_attempts():
    f = FakeFetcher({"https://news.example/rss": (200, FEED_MIXED, "application/rss+xml"),
                     "https://down.example/rss": FetchError("timeout", "https://down.example/rss", "read timed out")})
    p = FeedProvider(["https://news.example/rss", "https://down.example/rss"], fetcher=f, clock=lambda: NOW)
    reg = ProviderRegistry([p], clock=lambda: NOW)
    out = reg.search(QueryPlan(queries=(Query("prompt injection attacks", "news"),)), budget=Budget(), usage=Usage())
    assert [r.url for r in out.results] == ["https://news.example/prompt"]
    feeds = {a["engine"]: a for a in out.attempts if a["engine"]}
    assert feeds["https://news.example/rss"]["ok"] and feeds["https://news.example/rss"]["n"] == 1
    assert not feeds["https://down.example/rss"]["ok"] and "timeout" in feeds["https://down.example/rss"]["error"]
    assert [a["engine"] for a in out.failures] == ["https://down.example/rss"]


def test_registry_clients_mark_the_sentence_they_write():
    # RT-W-03: the "Latest release ..." sentence is the client's template over the record, not the package's text;
    # the npm description is kept as the record's summary (ecosystem relevance reads it, RT-W-04).
    r = PyPIProvider(FakeFetcher({"https://pypi.org/pypi/zstandard/json": PYPI})).search(Query("pypi:zstandard"), 5)[0]
    assert r.metadata["provider_text"] == "Latest release 0.25.0 uploaded 2025-09-14; requires Python >=3.9."
    assert r.snippet.startswith(r.metadata["provider_text"] + " Zstandard bindings")
    url = "https://registry.npmjs.org/@scope%2Fpkg"
    n = NpmProvider(FakeFetcher({url: NPM_FULL})).search(Query("npm:@scope/pkg"), 5)[0]
    assert n.metadata["provider_text"] == "Latest release 2.1.0 published 2026-10-30."
    assert n.metadata["summary"] == "A thing" and n.snippet == "Latest release 2.1.0 published 2026-10-30. A thing"
