"""Extraction: FetchResult bodies -> untrusted `Document`s, plus feed/sitemap parsing and URL normalisation.

Contract (ARCHITECTURE §4.1)
- HTML is parsed with `scrapling.parser.Selector(..., huge_tree=False)` (bounded tree size, no network, no scripts);
  Markdown comes from `markdownify` over the cleaned main content. `engine="trafilatura"` (optional dependency,
  imported lazily) or `"auto"` may use trafilatura for the main text, but only on the already-cleaned HTML, so its
  output obeys the same hidden-content rules.
- Hidden content is removed before any text is taken and counted in `flags["hidden_removed"]`: `hidden` attribute,
  `aria-hidden="true"`, inline `display:none` / `visibility:hidden` / `opacity:0` / `font-size:0` / off-screen
  positioning, hiding class names (hidden, sr-only, visually-hidden, d-none, ...), `<template>`, and zero-width or
  bidi-control characters. Boilerplate (script, style, noscript, svg, nav, header, footer, aside, form, iframe ...)
  is removed and counted in `flags["boilerplate_removed"]`. Page text is data: nothing here executes or follows it.
- JavaScript shells (little visible text plus scripts, an empty #root/#app/#__next mount, or a <noscript> asking for
  JavaScript) set `flags["js_suspected"]` so the orchestrator may escalate to browser rendering.
- Dates: JSON-LD, then meta (article:*, citation_*, Dublin Core, itemprop), then `<time datetime>`, then the HTTP
  Last-Modified header; `metadata["date_source"]` names the source used for `published_at`.
- A page's `<link rel=canonical>` is honoured only when absolute http(s) and on the same registrable domain
  (an off-site canonical could otherwise poison de-duplication); it is recorded in metadata either way.
- PDFs via pypdf (page and character bounds; encrypted or unreadable files raise FetchError('decode_error')).
- Feeds and sitemaps are parsed with xml.etree after refusing any DOCTYPE/ENTITY declaration (billion laughs, XXE)
  and any non-ASCII-compatible encoding (so the refusal check cannot be bypassed with UTF-16).
"""
from __future__ import annotations

import codecs
from datetime import datetime, timezone
import email.utils
import hashlib
import html as htmlmod
import io
import ipaddress
import json
import re
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
import xml.etree.ElementTree as ET

import lxml.etree
import lxml.html
import markdownify
from scrapling.parser import Selector

from .types import Document, FetchError, FetchResult

MAX_TEXT = 500_000
MAX_MARKDOWN = 500_000
MAX_LINKS = 200
MAX_FEED_ITEMS = 500
MAX_SITEMAP_URLS = 50_000
JS_TEXT_THRESHOLD = 200
AUTO_TRAFILATURA_BELOW = 300
HTML_TYPES = ("text/html", "application/xhtml+xml")
XML_TYPES = ("application/xml", "text/xml", "application/atom+xml", "application/rss+xml", "application/rdf+xml")

_ZERO_WIDTH = re.compile("[​-‏‪-‮⁠-⁤⁦-⁩﻿­]")
_BOILERPLATE = {"script", "style", "noscript", "svg", "nav", "header", "footer", "aside", "form", "iframe",
                "object", "embed", "canvas", "button", "select", "textarea", "input", "dialog", "link", "meta"}
_HIDING_CLASSES = {"hidden", "hide", "d-none", "sr-only", "visually-hidden", "visuallyhidden", "screen-reader-text",
                   "invisible", "is-hidden", "u-hidden"}
_HIDDEN_STYLE = re.compile(r"(display\s*:\s*none|visibility\s*:\s*(hidden|collapse)|opacity\s*:\s*0(\.0+)?\s*(;|$|!)"
                           r"|font-size\s*:\s*0(px|em|rem|pt|%)?\s*(;|$|!)|(left|top|text-indent)\s*:\s*-\d{3,}"
                           r"|clip\s*:\s*rect\(\s*0)", re.I)
_BLOCKS = {"p", "div", "section", "article", "main", "h1", "h2", "h3", "h4", "h5", "h6", "li", "ul", "ol", "dl",
           "dt", "dd", "pre", "blockquote", "table", "tr", "td", "th", "figure", "figcaption", "br", "hr",
           "address", "details", "summary", "body", "caption"}
_JS_TYPES = {"text/javascript", "application/javascript", "module", "text/ecmascript", "application/ecmascript", ""}
_JS_ROOTS = ("root", "app", "__next", "__nuxt", "svelte", "react-root")
_DOI = re.compile(r"\b(10\.\d{4,9}/[^\s\"'<>]+)", re.I)
_TRACKING = {"fbclid", "gclid", "dclid", "gbraid", "wbraid", "msclkid", "yclid", "igshid", "ref", "ref_src",
             "_ga", "_gl", "mkt_tok", "spm"}
_MONTHS = {m: i for i, names in enumerate(
    [("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"), ("may",), ("jun", "june"),
     ("jul", "july"), ("aug", "august"), ("sep", "sept", "september"), ("oct", "october"), ("nov", "november"),
     ("dec", "december")], start=1) for m in names}


# ---- URLs and domains -----------------------------------------------------------------------------------------

def registrable_domain(host) -> str:
    """eTLD+1 of a host via the Public Suffix List (`tld`), else its last two labels; IPs are returned as is."""
    host = str(host or "").strip().lower()
    if "://" in host:
        host = urlsplit(host).hostname or ""
    host = host.strip("[]").rstrip(".")
    if not host:
        return ""
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass
    try:
        from tld import get_fld
        fld = get_fld(host, fix_protocol=True, fail_silently=True)
    except Exception:          # the PSL package is optional; malformed hosts fall back below
        fld = None
    if fld:
        return fld.lower()
    labels = [label for label in host.split(".") if label]
    return ".".join(labels[-2:])


def canonical_url(url) -> str:
    """Comparison form: lower-case scheme/host, no 'www.', no default port, fragment, tracking params or userinfo;
    remaining query parameters sorted; trailing slash removed except for the root path."""
    url = str(url or "").strip()
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return url
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").rstrip(".")
    if not scheme or not host:
        return url
    if not host.isascii():
        try:
            host = host.encode("idna").decode("ascii")
        except UnicodeError:
            pass
    host = host.lower()
    if host.startswith("www."):
        host = host[4:]
    if ":" in host:
        host = f"[{host}]"
    if port is not None and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        host = f"{host}:{port}"
    path = parts.path or "/"
    if len(path) > 1:
        path = path.rstrip("/") or "/"
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not (k.lower().startswith(("utm_", "mc_")) or k.lower() in _TRACKING)]
    return urlunsplit((scheme, host, path, urlencode(sorted(query)), ""))


def _absolute(base: str, href) -> str | None:
    href = str(href or "").strip()
    if not href or href.startswith(("javascript:", "mailto:", "data:", "#")):
        return None
    try:
        joined = urljoin(base, href).split("#", 1)[0]
        parts = urlsplit(joined)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname or len(joined) > 2048:
        return None
    return joined


# ---- dates ------------------------------------------------------------------------------------------------------

def _to_epoch(value: datetime) -> float | None:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    if not 1900 <= value.year <= 2200:
        return None
    return value.timestamp()


def _parse_date(value) -> float | None:
    """ISO-8601 variants, RFC 2822, '2026/11/03', 'YYYY-MM', 'YYYY', '3 November 2026', 'November 3, 2026'."""
    if value is None:
        return None
    text = " ".join(str(value).split())[:120]
    if not text:
        return None
    iso = re.sub(r"\s*(UTC|GMT|Z)$", "+00:00", text)
    try:
        return _to_epoch(datetime.fromisoformat(iso))
    except ValueError:
        pass
    match = re.match(r"^(\d{4})[-/.](\d{1,2})(?:[-/.](\d{1,2}))?(?:[T ](\d{1,2}):(\d{2})(?::(\d{2}))?)?", text)
    if match:
        y, mo, d, hh, mm, ss = match.groups()
        try:
            return _to_epoch(datetime(int(y), int(mo), int(d or 1), int(hh or 0), int(mm or 0), int(ss or 0)))
        except ValueError:
            return None
    if re.fullmatch(r"\d{4}", text):
        return _to_epoch(datetime(int(text), 1, 1))
    try:
        parsed = email.utils.parsedate_to_datetime(text)
        if parsed is not None:
            return _to_epoch(parsed)
    except (TypeError, ValueError, IndexError):
        pass
    for pattern, order in ((r"(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})", "dmy"),
                           (r"([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})", "mdy")):
        match = re.search(pattern, text)
        if match:
            a, b, year = match.groups()
            day, month = (a, b) if order == "dmy" else (b, a)
            month_number = _MONTHS.get(month.lower())
            if month_number:
                try:
                    return _to_epoch(datetime(int(year), month_number, int(day)))
                except ValueError:
                    return None
    return None


# ---- HTML -------------------------------------------------------------------------------------------------------

def _decode(body: bytes, headers: dict | None) -> str:
    charset = None
    content_type = (headers or {}).get("content-type", "")
    match = re.search(r"charset=[\"']?([\w.:-]+)", content_type, re.I)
    if match:
        charset = match.group(1)
    elif body[:3] == codecs.BOM_UTF8:
        charset = "utf-8-sig"
    else:
        match = re.search(rb"<meta[^>]{0,200}charset=[\"']?([\w.:-]+)", body[:4096], re.I)
        if match:
            charset = match.group(1).decode("ascii", "ignore")
    try:
        codecs.lookup(charset or "utf-8")
    except LookupError:
        charset = None
    return body.decode(charset or "utf-8", "replace")


def _is_hidden(el, tag: str) -> bool:
    if tag == "template" or el.get("hidden") is not None:
        return True
    if (el.get("aria-hidden") or "").strip().lower() == "true":
        return True
    style = el.get("style")
    if style and _HIDDEN_STYLE.search(style):
        return True
    classes = (el.get("class") or "").lower().split()
    if tag == "input" and (el.get("type") or "").lower() == "hidden":
        return True
    return any(c in _HIDING_CLASSES for c in classes)


def _clean(root) -> tuple[int, int]:
    """Remove hidden and boilerplate subtrees in place (tails kept). Returns (hidden, boilerplate) counts."""
    hidden = boilerplate = 0
    stack = [root]
    while stack:
        el = stack.pop()
        for child in list(el):
            if not isinstance(child.tag, str):          # comments, processing instructions
                child.drop_tree()
                continue
            tag = child.tag.lower()
            if _is_hidden(child, tag):
                child.drop_tree()
                hidden += 1
            elif tag in _BOILERPLATE:
                child.drop_tree()
                boilerplate += 1
            else:
                stack.append(child)
    return hidden, boilerplate


def _text_of(node) -> str:
    """Visible text of a cleaned subtree; block elements become line breaks; whitespace collapsed per line."""
    parts = []
    for event, el in lxml.etree.iterwalk(node, events=("start", "end")):
        tag = el.tag.lower() if isinstance(el.tag, str) else ""
        if event == "start":
            if tag in _BLOCKS:
                parts.append("\n")
            if el.text and isinstance(el.tag, str):
                parts.append(el.text)
        else:
            if tag in _BLOCKS:
                parts.append("\n")
            if el is not node and el.tail:
                parts.append(el.tail)
    lines = (" ".join(line.split()) for line in "".join(parts).split("\n"))
    return "\n".join(line for line in lines if line)


def _length(node) -> int:
    return len(" ".join(node.text_content().split()))


def _main_node(root):
    body = root.find(".//body")
    body = body if body is not None else root
    total = _length(body)
    candidates = root.xpath("//article | //main | //*[@role='main']")
    if candidates:
        best = max(candidates, key=_length)
        size = _length(best)
        if size >= 0.3 * total or size >= 500:
            return best
    scores: dict = {}
    for p in root.iter("p", "pre", "blockquote"):
        size = _length(p)
        if size < 25:
            continue
        parent = p.getparent()
        if parent is None:
            continue
        scores[parent] = scores.get(parent, 0.0) + size
        grand = parent.getparent()
        if grand is not None:
            scores[grand] = scores.get(grand, 0.0) + size / 2
    if not scores:
        return body
    best = max(scores, key=scores.get)
    return best if _length(best) >= 0.2 * total else body


def _fragment_text(fragment: str) -> str:
    """Plain text of an HTML fragment (feed summaries) with the same hidden-content removal as pages."""
    if "<" not in fragment:
        return " ".join(htmlmod.unescape(_ZERO_WIDTH.sub("", fragment)).split())
    sel = Selector(f"<html><body><div>{_ZERO_WIDTH.sub('', fragment)}</div></body></html>", url="",
                   huge_tree=False)
    root = sel._root
    _clean(root)
    return " ".join(_text_of(root).split())


def _json_ld_objects(sel) -> list[dict]:
    found = []
    for raw in sel.css('script[type="application/ld+json"]::text').getall()[:20]:
        if len(raw) > 1_000_000:
            continue
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        stack = [(data, 0)]
        while stack and len(found) < 200:
            item, depth = stack.pop(0)
            if depth > 6:
                continue
            if isinstance(item, list):
                stack.extend((x, depth + 1) for x in item[:100])
            elif isinstance(item, dict):
                found.append(item)
                for key in ("@graph", "mainEntity", "mainEntityOfPage", "itemListElement"):
                    if isinstance(item.get(key), (list, dict)):
                        stack.append((item[key], depth + 1))
    return found


def _names(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return _names(value.get("name"))
    if isinstance(value, list):
        return [n for v in value[:20] for n in _names(v)]
    return []


def _meta_map(sel) -> dict[str, list[str]]:
    metas: dict[str, list[str]] = {}
    for meta in sel.css("meta"):
        attrs = meta.attrib
        content = attrs.get("content")
        if content is None:
            continue
        for key in ("name", "property", "itemprop", "http-equiv"):
            name = attrs.get(key)
            if name:
                metas.setdefault(name.strip().lower(), []).append(content.strip())
    return metas


def _first(metas, *names) -> tuple[str | None, str | None]:
    for name in names:
        for value in metas.get(name, ()):
            if value:
                return value, name
    return None, None


def _trafilatura(cleaned_html: str, url: str) -> str | None:
    try:
        import trafilatura
    except ImportError:
        return None
    try:
        text = trafilatura.extract(cleaned_html, url=url or None, output_format="txt", include_comments=False)
    except Exception:          # optional engine on untrusted input: any failure falls back to the heuristic
        return None
    return text.strip() if text else None


def _apply_last_modified(doc: Document, headers: dict | None):
    if doc.published_at is not None:
        return
    value = (headers or {}).get("last-modified")
    when = _parse_date(value) if value else None
    if when is not None:
        doc.published_at = when
        doc.metadata["date_source"] = "http-last-modified"
        if doc.modified_at is None:
            doc.modified_at = when


def extract_html(body: bytes, url: str, *, retrieved_at, retrieval_mode, engine="auto", headers=None,
                 final_url=None) -> Document:
    """`headers` (lower-case FetchResult headers) supply the charset and Last-Modified; `final_url` is the URL
    after redirects (defaults to `url`)."""
    final_url = final_url or url
    text_in = _decode(bytes(body), headers)
    zero_width = len(_ZERO_WIDTH.findall(text_in))
    text_in = _ZERO_WIDTH.sub("", text_in)
    text_in = re.sub(r"^\s*<\?xml[^>]*\?>", "", text_in)
    if not text_in.strip():
        text_in = "<html><body></body></html>"
    sel = Selector(text_in, url=final_url, huge_tree=False)
    metas = _meta_map(sel)
    ld = _json_ld_objects(sel)
    metadata: dict = {}

    title = " ".join((sel.css("title::text").get() or "").split())
    if not title:
        title = (_first(metas, "og:title", "twitter:title", "citation_title")[0] or "").strip()
    if not title:
        title = " ".join(" ".join(sel.css("h1 ::text").getall()).split())
    title = title[:500]

    language = None
    html_el = sel.css("html")
    if html_el:
        language = (html_el[0].attrib.get("lang") or html_el[0].attrib.get("xml:lang") or "").strip() or None
    if not language:
        language = _first(metas, "content-language", "og:locale", "dc.language")[0]
    language = language[:35] if language else None

    host = urlsplit(final_url).hostname or ""
    domain = registrable_domain(host)
    canonical = canonical_url(final_url)
    for link in sel.css("link[rel][href]"):
        if "canonical" in (link.attrib.get("rel") or "").lower().split():
            target = _absolute(final_url, link.attrib.get("href"))
            if target:
                metadata["canonical_declared"] = target
                if registrable_domain(urlsplit(target).hostname or "") == domain:
                    canonical = canonical_url(target)
                else:
                    metadata["canonical_offsite"] = True
            break

    # authors
    authors: list[str] = []
    for obj in ld:
        authors.extend(_names(obj.get("author")))
    for name in ("author", "citation_author", "dc.creator", "article:author", "parsely-author"):
        authors.extend(metas.get(name, ()))
    authors = list(dict.fromkeys(" ".join(a.split())[:200] for a in authors if a and a.strip()))[:20]

    # dates
    published = modified = None
    source = None
    for obj in ld:
        if published is None and obj.get("datePublished"):
            published = _parse_date(obj.get("datePublished"))
            source = "json-ld" if published is not None else None
        if modified is None and obj.get("dateModified"):
            modified = _parse_date(obj.get("dateModified"))
    if published is None:
        for name in ("article:published_time", "citation_publication_date", "citation_date", "citation_online_date",
                     "dc.date", "dc.date.issued", "dcterms.issued", "dcterms.created", "dc.date.created",
                     "datepublished", "pubdate", "publish-date", "publication_date", "sailthru.date",
                     "parsely-pub-date", "date"):
            for value in metas.get(name, ()):
                published = _parse_date(value)
                if published is not None:
                    source = f"meta:{name}"
                    break
            if published is not None:
                break
    if published is None:
        times = sel.css("time[itemprop=datePublished][datetime]") or sel.css("time[pubdate][datetime]") \
            or sel.css("time[datetime]")
        for el in times[:5]:
            published = _parse_date(el.attrib.get("datetime"))
            if published is not None:
                source = "time-element"
                break
    if modified is None:
        for name in ("article:modified_time", "og:updated_time", "dcterms.modified", "datemodified",
                     "last-modified"):
            value, _ = _first(metas, name)
            modified = _parse_date(value) if value else None
            if modified is not None:
                break
    if source:
        metadata["date_source"] = source

    # scholarly / typing metadata
    doi, _ = _first(metas, "citation_doi", "prism.doi", "bepress_citation_doi", "dc.identifier", "dc.identifier.doi")
    if doi and not _DOI.search(doi):
        doi = None
    venue, _ = _first(metas, "citation_journal_title", "citation_conference_title", "prism.publicationname")
    og_type, _ = _first(metas, "og:type")
    description, _ = _first(metas, "description", "og:description", "citation_abstract", "dc.description")
    if venue:
        metadata["venue"] = venue[:300]
    if og_type:
        metadata["og_type"] = og_type[:50]
    if description:
        metadata["description"] = _fragment_text(description)[:2000]
    for obj in ld:
        kind = obj.get("@type")
        if kind:
            metadata.setdefault("schema_type", kind if isinstance(kind, str) else ",".join(map(str, kind))[:100])
            break

    # JavaScript shell signals, measured before scripts are removed
    root = sel._root            # lxml tree behind scrapling's Selector (pinned scrapling==0.5.1)
    scripts = sum(1 for el in root.iter("script")
                  if (el.get("type") or "text/javascript").strip().lower() in _JS_TYPES)
    mount = any(len(root.xpath(f"//*[@id='{name}']")) for name in _JS_ROOTS) or bool(root.xpath("//*[@data-reactroot]"))
    noscript = " ".join(" ".join(n.text_content().split()) for n in root.xpath("//noscript")).lower()
    asks_js = "javascript" in noscript and ("enable" in noscript or "requires" in noscript or "need" in noscript)

    base = final_url
    base_el = root.xpath("//base[@href]")
    if base_el:
        base = _absolute(final_url, base_el[0].get("href")) or final_url

    hidden, boilerplate = _clean(root)
    body_el = root.find(".//body")
    visible = _text_of(body_el if body_el is not None else root)
    main = _main_node(root)
    text = _text_of(main)
    used_engine = "scrapling"
    if engine in ("trafilatura", "auto") and (engine == "trafilatura" or len(text) < AUTO_TRAFILATURA_BELOW):
        alt = _trafilatura(lxml.html.tostring(root, encoding="unicode"), final_url)
        if alt and (engine == "trafilatura" or len(alt) > len(text)):
            text, used_engine = alt, "trafilatura"
    metadata["engine"] = used_engine

    links = []
    for a in main.iter("a"):
        target = _absolute(base, a.get("href"))
        if target and target not in links:
            links.append(target)
            if len(links) >= MAX_LINKS:
                break
    if not doi:
        for candidate in links + [text[:20000]]:
            match = _DOI.search(candidate)
            if match:
                doi = match.group(1)
                break
    if doi:
        metadata["doi"] = re.sub(r"^(doi:|https?://(dx\.)?doi\.org/)", "", _DOI.search(doi).group(1),
                                 flags=re.I).rstrip(".,;)")

    for el in main.iter("a", "img"):
        attr = "href" if el.tag == "a" else "src"
        if el.get(attr):
            target = _absolute(base, el.get(attr))
            if target:
                el.set(attr, target)
            else:
                del el.attrib[attr]
    markdown = markdownify.markdownify(lxml.html.tostring(main, encoding="unicode"), heading_style="ATX")
    markdown = re.sub(r"\n{3,}", "\n\n", markdown).strip()[:MAX_MARKDOWN]

    js_suspected = len(visible) < JS_TEXT_THRESHOLD and (scripts > 0 or mount or asks_js)
    flags = {"hidden_removed": hidden + zero_width, "boilerplate_removed": boilerplate,
             "js_suspected": bool(js_suspected)}
    if zero_width:
        flags["zero_width_removed"] = zero_width

    doc = Document(url=url, final_url=final_url, canonical_url=canonical, domain=domain, title=title,
                   text=text[:MAX_TEXT], retrieved_at=retrieved_at, retrieval_mode=retrieval_mode, kind="page",
                   content_type="text/html", content_sha256=hashlib.sha256(bytes(body)).hexdigest(),
                   markdown=markdown, published_at=published, modified_at=modified, authors=authors,
                   language=language, links=links, flags=flags, metadata=metadata)
    _apply_last_modified(doc, headers)
    return doc


# ---- PDF --------------------------------------------------------------------------------------------------------

def _pdf_date(value) -> float | None:
    if isinstance(value, datetime):
        return _to_epoch(value)
    return None


def extract_pdf(body: bytes, url: str, *, retrieved_at, max_pages=30, retrieval_mode="live",
                final_url=None) -> Document:
    from pypdf import PdfReader
    final_url = final_url or url
    try:
        reader = PdfReader(io.BytesIO(bytes(body)), strict=False)
        if reader.is_encrypted:
            raise FetchError("decode_error", url, "encrypted PDF")
        pages = len(reader.pages)
        chunks, size = [], 0
        for page in reader.pages[:max(0, int(max_pages))]:
            piece = page.extract_text() or ""
            chunks.append(piece)
            size += len(piece)
            if size >= MAX_TEXT:
                break
        info = reader.metadata
        title = (info.title if info and info.title else "") or ""
        author = (info.author if info and info.author else "") or ""
        created = _pdf_date(info.creation_date) if info else None
        changed = _pdf_date(info.modification_date) if info else None
    except FetchError:
        raise
    except Exception as error:       # pypdf raises many types on malformed input; all mean "cannot decode"
        raise FetchError("decode_error", url, f"unreadable PDF ({type(error).__name__})") from None
    text = "\n\n".join(c.strip() for c in chunks if c.strip())
    text = _ZERO_WIDTH.sub("", text)[:MAX_TEXT]
    if not title:
        title = next((line.strip() for line in text.splitlines() if line.strip()), "")[:300]
    metadata = {"pages": pages, "pages_read": min(pages, int(max_pages))}
    if created is not None:
        metadata["date_source"] = "pdf-creation-date"
    match = _DOI.search(text[:20000])
    if match:
        metadata["doi"] = match.group(1).rstrip(".,;)")
    host = urlsplit(final_url).hostname or ""
    return Document(url=url, final_url=final_url, canonical_url=canonical_url(final_url),
                    domain=registrable_domain(host), title=" ".join(str(title).split())[:500], text=text,
                    retrieved_at=retrieved_at, retrieval_mode=retrieval_mode, kind="pdf",
                    content_type="application/pdf", content_sha256=hashlib.sha256(bytes(body)).hexdigest(),
                    markdown=text[:MAX_MARKDOWN], published_at=created, modified_at=changed,
                    authors=[" ".join(str(author).split())[:200]] if str(author).strip() else [],
                    flags={"truncated": pages > int(max_pages) or size >= MAX_TEXT}, metadata=metadata)


# ---- XML: feeds and sitemaps ------------------------------------------------------------------------------------

def _safe_xml(body: bytes, url: str):
    body = bytes(body)
    if not body.strip():
        raise FetchError("decode_error", url, "empty XML document")
    if b"\x00" in body[:1024]:
        raise FetchError("decode_error", url, "XML in a non-ASCII-compatible encoding is refused")
    if re.search(rb"<!\s*(DOCTYPE|ENTITY)", body, re.I):
        raise FetchError("decode_error", url, "XML with DOCTYPE/ENTITY declarations is refused")
    try:
        return ET.fromstring(body)
    except ET.ParseError as error:
        raise FetchError("decode_error", url, f"malformed XML ({error.code})") from None


def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1].lower() if isinstance(tag, str) else ""


def _child(el, *names):
    for child in el:
        if _local(child.tag) in names:
            return child
    return None


def _child_text(el, *names) -> str:
    for name in names:
        child = _child(el, name)
        if child is not None:
            text = "".join(child.itertext()).strip()
            if text:
                return text
    return ""


def _feed(body: bytes, url: str):
    root = _safe_xml(body, url)
    kind = _local(root.tag)
    items_out = []
    if kind == "rss":
        channel = _child(root, "channel")
        container = channel if channel is not None else root
        title = _child_text(container, "title")
        entries = [el for el in container if _local(el.tag) == "item"]
    elif kind == "feed":
        title = _child_text(root, "title")
        entries = [el for el in root if _local(el.tag) == "entry"]
    elif kind == "rdf":
        channel = _child(root, "channel")
        title = _child_text(channel, "title") if channel is not None else ""
        entries = [el for el in root if _local(el.tag) == "item"]
    else:
        return None, []
    for entry in entries[:MAX_FEED_ITEMS]:
        link = None
        if kind == "feed":
            for el in entry:
                if _local(el.tag) == "link" and (el.get("rel") or "alternate") == "alternate" and el.get("href"):
                    link = el.get("href")
                    break
        else:
            link = _child_text(entry, "link")
            if not link:
                guid = _child(entry, "guid")
                if guid is not None and (guid.get("isPermaLink") or "true").lower() != "false":
                    link = (guid.text or "").strip()
        target = _absolute(url, link)
        if not target:
            continue
        published = _parse_date(_child_text(entry, "pubdate", "published", "date", "issued", "updated", "created",
                                            "modified"))
        summary = _child_text(entry, "description", "summary", "content", "encoded")
        items_out.append({"title": _fragment_text(_child_text(entry, "title"))[:500], "url": target,
                          "published_at": published, "summary": _fragment_text(summary)[:2000]})
    return title, items_out


def parse_feed(body: bytes, url: str) -> list[dict]:
    """Items of an RSS 2.0, Atom or RDF (RSS 1.0) feed: [{title, url, published_at, summary}]."""
    return _feed(body, url)[1]


def parse_sitemap(body: bytes, url: str) -> list[dict]:
    """Entries of a <urlset> ([{url, lastmod}]) or <sitemapindex> ([{url, lastmod, sitemap: True}])."""
    root = _safe_xml(body, url)
    kind = _local(root.tag)
    if kind not in ("urlset", "sitemapindex"):
        return []
    entry_tag = "url" if kind == "urlset" else "sitemap"
    out = []
    for el in root:
        if _local(el.tag) != entry_tag:
            continue
        target = _absolute(url, _child_text(el, "loc"))
        if not target:
            continue
        row = {"url": target, "lastmod": _parse_date(_child_text(el, "lastmod"))}
        if kind == "sitemapindex":
            row["sitemap"] = True
        out.append(row)
        if len(out) >= MAX_SITEMAP_URLS:
            break
    return out


# ---- dispatch ---------------------------------------------------------------------------------------------------

def extract(result: FetchResult, *, engine="auto") -> Document:
    body = bytes(result.body)
    content_type = (result.content_type or "").lower()
    headers = result.headers or {}
    final = result.final_url or result.url
    common = {"retrieved_at": result.retrieved_at}
    if content_type == "application/pdf" or body[:5] == b"%PDF-":
        doc = extract_pdf(body, result.url, retrieval_mode=result.retrieval_mode, final_url=final, **common)
    elif content_type in HTML_TYPES or (not content_type and b"<html" in body[:2048].lower()):
        doc = extract_html(body, result.url, retrieval_mode=result.retrieval_mode, engine=engine, headers=headers,
                           final_url=final, **common)
        doc.content_type = content_type or "text/html"
    else:
        host = urlsplit(final).hostname or ""
        text = _ZERO_WIDTH.sub("", _decode(body, headers))
        kind, title, links = "text", "", []
        if content_type in XML_TYPES or (content_type == "text/plain" and text.lstrip().startswith("<?xml")):
            feed_title, items = _feed(body, final)
            if feed_title is not None:
                kind, title = "feed", feed_title
                links = [item["url"] for item in items][:MAX_LINKS]
                text = "\n".join(f"{item['title']}. {item['summary']}".strip() for item in items)
            else:
                root = _safe_xml(body, final)
                text = "\n".join(" ".join(t.split()) for t in root.itertext() if t.strip())
        elif content_type == "application/json" or content_type.endswith("+json"):
            kind = "json"
            try:
                text = json.dumps(json.loads(text), ensure_ascii=False, indent=1)
            except ValueError:
                pass
        if not title:
            title = next((line.strip() for line in text.splitlines() if line.strip()), "")[:200] \
                if kind == "text" else final
        doc = Document(url=result.url, final_url=final, canonical_url=canonical_url(final),
                       domain=registrable_domain(host), title=title[:500], text=text[:MAX_TEXT],
                       retrieved_at=result.retrieved_at, retrieval_mode=result.retrieval_mode, kind=kind,
                       content_type=content_type or "text/plain",
                       content_sha256=hashlib.sha256(body).hexdigest(), markdown="", links=links)
        _apply_last_modified(doc, headers)
    if result.truncated:
        doc.flags["truncated"] = True
    elif "truncated" not in doc.flags:
        doc.flags["truncated"] = False
    return doc
