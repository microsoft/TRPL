# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Phase 2: Download each TRA Cyclopedia browse page and save extracted content.

After the Speeches hub (module 339335), follows each on-site ``content.aspx`` speech
transcript link (same ``page_id`` / ``club_id``) and saves ``module_<id>.json`` for
those pages. Legacy ``lookup.asp`` URLs are not fetched (different app; often audio).

Patterns (by page structure / module_id):
  - prose: long article (Introduction, About, Bibliography, Chronology, Family, Genealogy)
  - papers_toc: Governor Public Papers 1899 / 1900 — large <ul> of plain-text items
  - index_hub: Index A–Z hub — letter links to per-letter module pages + term lists as text
  - index_letter_page: Per-letter index (children of 339476) — ``cyclopedia_entries`` (heading,
    body, anchor_id, letter), plus ``cyclopedia_entries_map`` (anchor/slug keys) and
    ``cyclopedia_entries_by_heading`` (display titles as keys).
  - prose: Adds ``document_sections`` / ``_map`` / ``_by_heading`` from ``h2``–``h4`` blocks.
  - papers_toc: Adds ``toc_entries`` list and ``toc_entries_map``.
  - speeches_sections: Each section includes a ``body`` string; ``speech_sections_map`` maps title→body.
  - speeches_sections: Speeches — <h3> sections with date/place blurbs and occasional links

Requires: ``pip install -r requirements-tra-scrape.txt`` (from this project folder).

These scripts live under ``d:\\Temp\\TRA (Theodore Roosevelt Association Cyclopedia)\\``.
Default output directory is ``tra_cyclopedia_out`` beside them.

Run (from that folder, or pass absolute paths)::

  cd "d:\\Temp\\TRA (Theodore Roosevelt Association Cyclopedia)"
  python tra_cyclopedia_phase2_scrape.py
  python tra_cyclopedia_phase2_scrape.py --run-index-letters

Output layout (under ``--outdir``):

  module_<id>/module_<id>.json
  module_<id>/module_<id>.fragment.html

Child pages (e.g. speech transcripts) nest under the parent hub folder:

  module_<parent>/module_<child>/module_<child>.json
  module_<parent>/module_<child>/module_<child>.fragment.html
"""

from __future__ import annotations

import argparse
import json
import random
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup, NavigableString

from tra_cyclopedia_phase1_hub import (
    DEFAULT_HEADERS,
    HUB_URL,
    extract_cyclopedia_links,
    module_id_from_url,
)

# Project root: folder named "TRA (Theodore Roosevelt Association Cyclopedia)" that holds these scripts.
TRA_CYCOPE_PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_SCRAPE_OUTDIR = TRA_CYCOPE_PROJECT_DIR / "tra_cyclopedia_out"

PAPERS_MODULES = frozenset({"339186", "339187"})
INDEX_HUB_MODULE = "339476"
SPEECHES_MODULE = "339335"


@dataclass
class PageRecord:
    module_id: str
    url: str
    hub_label: str
    h1: str
    pattern: str
    plain_text: str
    html_fragment: str
    structured: dict[str, Any]
    parent_module_id: str | None = None


def session() -> requests.Session:
    s = requests.Session()
    s.headers.update(DEFAULT_HEADERS)
    return s


def get_html(s: requests.Session, url: str, *, attempts: int = 4) -> str:
    """GET with retries for flaky connections / rate limits."""
    last_err: Exception | None = None
    for i in range(attempts):
        try:
            r = s.get(url, timeout=60)
            r.raise_for_status()
            return r.text
        except (requests.exceptions.ConnectionError, requests.exceptions.ChunkedEncodingError) as e:
            last_err = e
            time.sleep(1.5 + i * 1.5 + random.random())
    assert last_err is not None
    raise last_err


def main_article_root(soup: BeautifulSoup) -> BeautifulSoup | None:
    col = soup.select_one("#content_column")
    if col is None:
        return None
    tq = col.select_one("div.column.threequarter")
    return tq or col


def clone_for_export(root: BeautifulSoup) -> BeautifulSoup:
    """Detach a copy and drop script/style for safer HTML export."""
    node = BeautifulSoup(str(root), "html.parser")
    for tag in node.find_all(["script", "style"]):
        tag.decompose()
    return node


def headings_outline(root: BeautifulSoup) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for tag in root.find_all(["h2", "h3", "h4"]):
        name = tag.name
        if not name:
            continue
        level = int(name[1])
        txt = tag.get_text(" ", strip=True)
        if txt:
            out.append({"level": level, "text": txt, "class": tag.get("class")})
    return out


def _slugify_key(s: str, max_len: int = 96) -> str:
    s = re.sub(r"\s+", "_", (s or "").strip())
    s = re.sub(r"[^\w\-\.]+", "", s, flags=re.UNICODE)
    return (s[:max_len] if s else "entry").strip("_") or "entry"


def extract_papers_toc(root: BeautifulSoup) -> dict[str, Any]:
    uls = root.find_all("ul")
    best_ul = None
    best_n = 0
    for ul in uls:
        n = len(ul.find_all("li", recursive=False))
        if n > best_n:
            best_n = n
            best_ul = ul
    items: list[str] = []
    if best_ul:
        for li in best_ul.find_all("li", recursive=False):
            t = li.get_text(" ", strip=True)
            if t:
                items.append(t)
    entries_map: dict[str, str] = {}
    for i, t in enumerate(items):
        k = _slugify_key(t)
        key = k
        m = 1
        while key in entries_map:
            m += 1
            key = f"{k}_{m}"
        entries_map[key] = t

    return {
        "toc_item_count": len(items),
        "toc_items": items,
        "toc_entries": [{"title": t} for t in items],
        "toc_entries_map": entries_map,
    }


def extract_index_letter_links(root: BeautifulSoup, page_url: str) -> dict[str, Any]:
    links_out: list[dict[str, str]] = []
    for a in root.find_all("a", href=True):
        text = a.get_text(" ", strip=True)
        href = a["href"].strip()
        if not text or not href:
            continue
        abs_url = urljoin(page_url, href)
        if "/content.aspx" not in urlparse(abs_url).path:
            continue
        mid = module_id_from_url(abs_url)
        if not mid:
            continue
        if re.match(r"^Index\s+[A-Z]\b", text):
            links_out.append({"text": text, "url": abs_url, "module_id": mid})
    # de-dupe by module_id keeping first
    seen: set[str] = set()
    deduped: list[dict[str, str]] = []
    for row in links_out:
        mid = row["module_id"]
        if mid in seen:
            continue
        seen.add(mid)
        deduped.append(row)
    return {"index_letter_links": deduped}


def _dedupe_links(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    best: dict[str, dict[str, str]] = {}
    for row in rows:
        u = row["url"]
        prev = best.get(u)
        if prev is None or len(row["text"]) > len(prev["text"]):
            best[u] = row
    return list(best.values())


def _h3_speech_links(h3: BeautifulSoup, title: str, page_url: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for a in h3.find_all("a", href=True):
        href = a["href"].strip()
        if not href:
            continue
        abs_url = urljoin(page_url, href)
        text = a.get_text(" ", strip=True)
        if not text:
            text = title
        rows.append({"text": text, "url": abs_url})
    return _dedupe_links(rows)


def _direct_div_blurb_and_links(
    div: BeautifulSoup, page_url: str
) -> tuple[list[str], list[dict[str, str]]]:
    """Text and links from a wrapper <div>, stopping at the first nested <h3>."""
    blurbs: list[str] = []
    links: list[dict[str, str]] = []
    parts: list[str] = []
    for child in div.children:
        if isinstance(child, NavigableString):
            t = str(child).strip()
            if t:
                parts.append(t)
            continue
        name = child.name
        if name == "h3":
            break
        if name == "p":
            t = child.get_text(" ", strip=True)
            if t:
                parts.append(t)
            for a in child.find_all("a", href=True):
                links.append(
                    {
                        "text": a.get_text(" ", strip=True) or t,
                        "url": urljoin(page_url, a["href"].strip()),
                    }
                )
        elif name == "br":
            parts.append("")
        elif name == "img":
            continue
        elif name == "div":
            b2, l2 = _direct_div_blurb_and_links(child, page_url)
            blurbs.extend(b2)
            links.extend(l2)
        elif name in ("strong", "span"):
            t = child.get_text(" ", strip=True)
            if t:
                parts.append(t)
            for a in child.find_all("a", href=True):
                links.append(
                    {
                        "text": a.get_text(" ", strip=True) or t,
                        "url": urljoin(page_url, a["href"].strip()),
                    }
                )
    joined = " ".join(x for x in parts if x).strip()
    if joined:
        blurbs.append(" ".join(joined.split()))
    return blurbs, _dedupe_links(links)


def extract_speeches(root: BeautifulSoup, page_url: str) -> dict[str, Any]:
    sections: list[dict[str, Any]] = []
    for h3 in root.find_all("h3"):
        if "IndexSubTitle" in (h3.get("class") or []):
            continue
        title = h3.get_text(" ", strip=True)
        if not title:
            continue
        blurbs: list[str] = []
        links: list[dict[str, str]] = _h3_speech_links(h3, title, page_url)
        n = h3.next_sibling
        steps = 0
        while n is not None and steps < 60:
            steps += 1
            name = getattr(n, "name", None)
            if name == "h3":
                break
            if name in ("h2", "h4"):
                break
            if name == "p":
                t = n.get_text(" ", strip=True)
                if t:
                    blurbs.append(t)
                for a in n.find_all("a", href=True):
                    links.append(
                        {
                            "text": a.get_text(" ", strip=True) or t,
                            "url": urljoin(page_url, a["href"].strip()),
                        }
                    )
            elif name == "div":
                b2, l2 = _direct_div_blurb_and_links(n, page_url)
                blurbs.extend(b2)
                links.extend(l2)
            n = n.next_sibling
        body = "\n\n".join(x for x in blurbs if x)
        sections.append(
            {
                "title": title,
                "blurbs": blurbs,
                "body": body,
                "links": _dedupe_links(links),
            }
        )
    return {"speech_sections": sections}


def _is_tra_cyclopedia_content_url(abs_url: str) -> bool:
    p = urlparse(abs_url)
    if "theodoreroosevelt.org" not in p.netloc.lower():
        return False
    if "/content.aspx" not in p.path.lower():
        return False
    q = parse_qs(p.query)
    if q.get("page_id") != ["22"]:
        return False
    if q.get("club_id") != ["991271"]:
        return False
    return bool(q.get("module_id"))


def speech_content_targets_from_structured(structured: dict[str, Any]) -> list[dict[str, str]]:
    """
    On-site speech transcript pages linked from the Speeches hub (content.aspx, same club/page).
    One row per module_id (first section title wins if duplicates).
    """
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for sec in structured.get("speech_sections", []):
        title = sec.get("title") or ""
        for link in sec.get("links", []):
            u = (link.get("url") or "").strip()
            if not u or not _is_tra_cyclopedia_content_url(u):
                continue
            mid = module_id_from_url(u) or ""
            if not mid or mid == SPEECHES_MODULE:
                continue
            if mid in seen:
                continue
            seen.add(mid)
            rows.append(
                {
                    "module_id": mid,
                    "url": u,
                    "section_title": title,
                }
            )
    return rows


def classify_pattern(module_id: str, parent_module_id: str | None = None) -> str:
    if (
        parent_module_id == INDEX_HUB_MODULE
        and module_id
        and module_id != INDEX_HUB_MODULE
    ):
        return "index_letter_page"
    if module_id in PAPERS_MODULES:
        return "papers_toc"
    if module_id == INDEX_HUB_MODULE:
        return "index_hub"
    if module_id == SPEECHES_MODULE:
        return "speeches_sections"
    return "prose"


def _index_h4_is_letter_navigation(h4: BeautifulSoup) -> bool:
    """The A–Z strip is an ``h4`` full of single-letter ``content.aspx`` links."""
    links = h4.find_all("a", href=True)
    if len(links) < 10:
        return False
    short = sum(1 for a in links if len(a.get_text(strip=True)) <= 2)
    return short >= 8


def _h4_topic_plain_text(h4: BeautifulSoup) -> str:
    h = BeautifulSoup(str(h4), "html.parser").find("h4")
    if h is None:
        return ""
    for a in list(h.find_all("a")):
        a.decompose()
    return h.get_text(" ", strip=True)


def _h4_anchor_id(h4: BeautifulSoup) -> str | None:
    a = h4.find("a", id=True) or h4.find("a", attrs={"name": True})
    if a is None:
        return None
    return (a.get("id") or a.get("name") or "").strip() or None


def _cyclopedia_entries_to_map(entries: list[dict[str, Any]]) -> dict[str, str]:
    """Stable keys: anchor_id when present, else slugified heading with numeric suffix."""
    out: dict[str, str] = {}
    used: set[str] = set()
    for e in entries:
        body = e.get("body") or ""
        aid = (e.get("anchor_id") or "").strip()
        base = aid if aid else _slugify_key(e.get("heading") or "")
        key = base
        n = 1
        while key in used:
            n += 1
            key = f"{base}_{n}"
        used.add(key)
        out[key] = body
    return out


def _body_content_scope(rr: BeautifulSoup) -> BeautifulSoup:
    """Prefer the main transcript/article container over chrome-only paragraphs."""
    ts = rr.select_one("#TextSizeModify")
    if ts is not None:
        return ts
    ic = rr.select_one("div.inner-column")
    if ic is not None:
        return ic
    return rr


def _pack_doc_section(
    title: str | None, lev: int | None, chunks: list[str]
) -> dict[str, Any]:
    body = "\n\n".join(chunks)
    return {
        "heading": title,
        "level": lev,
        "paragraphs": chunks,
        "body": body,
    }


def _finalize_document_section_maps(
    sections: list[dict[str, Any]],
) -> dict[str, Any]:
    doc_map: dict[str, str] = {}
    used: set[str] = set()
    for i, sec in enumerate(sections):
        h = sec.get("heading")
        key = _slugify_key(h) if h else f"section_{i}"
        k = key
        m = 1
        while k in used:
            m += 1
            k = f"{key}_{m}"
        used.add(k)
        if sec.get("body"):
            doc_map[k] = sec["body"]

    by_heading: dict[str, str] = {}
    for sec in sections:
        h = sec.get("heading")
        if not h:
            continue
        key = h
        n = 1
        while key in by_heading:
            n += 1
            key = f"{h}_{n}"
        by_heading[key] = sec.get("body") or ""

    return {
        "document_sections": sections,
        "document_sections_map": doc_map,
        "document_sections_by_heading": by_heading,
    }


def extract_prose_document_sections(root: BeautifulSoup) -> dict[str, Any]:
    """
    Split prose pages into heading → paragraphs + body (``h2``/``h3``/``h4`` in ``resp-row``).
    When there are no subsection headings, uses the page ``h1`` as the section title and
    collects paragraphs from ``#TextSizeModify`` when present (speech transcripts), else
    the main ``inner-column``, to avoid pulling unrelated footer text.
    """
    rr = root.select_one("div.resp-row") or root
    scope = _body_content_scope(rr)
    hs = rr.select("h2, h3, h4")
    sections: list[dict[str, Any]] = []

    if not hs:
        h1_el = rr.find("h1")
        title = h1_el.get_text(" ", strip=True) if h1_el else None
        lev = 1 if h1_el else None
        ps = [
            p.get_text(" ", strip=True)
            for p in scope.find_all("p")
            if p.get_text(strip=True)
        ]
        if not ps and h1_el:
            for sib in h1_el.find_next_siblings():
                if getattr(sib, "name", None) != "div":
                    continue
                block = sib.get_text("\n", strip=True)
                if not block:
                    continue
                parts = [x.strip() for x in re.split(r"\n{2,}", block) if x.strip()]
                ps = parts if len(parts) > 1 else [block]
                break
        if not ps:
            blob = scope.get_text("\n", strip=True)
            if title and blob.startswith(title):
                blob = blob[len(title) :].strip()
            if blob:
                ps = [blob]
        if ps:
            sections.append(_pack_doc_section(title or None, lev, ps))
        return _finalize_document_section_maps(sections)

    for h in hs:
        lev = int(h.name[1])  # type: ignore[union-attr]
        title = h.get_text(" ", strip=True)
        body_chunks: list[str] = []
        n = h.next_sibling
        while n is not None:
            nm = getattr(n, "name", None)
            if nm in ("h2", "h3", "h4"):
                if int(nm[1]) <= lev:
                    break
            if nm == "p":
                t = n.get_text(" ", strip=True)
                if t:
                    body_chunks.append(t)
            elif nm == "div":
                for p in n.find_all("p"):
                    t = p.get_text(" ", strip=True)
                    if t:
                        body_chunks.append(t)
            n = n.next_sibling
        if title or body_chunks:
            sections.append(_pack_doc_section(title or None, lev, body_chunks))

    return _finalize_document_section_maps(sections)


def extract_index_letter_page(root: BeautifulSoup, page_url: str) -> dict[str, Any]:
    """
    Per-letter Cyclopedia index: each topic is an ``h4`` followed by one or more ``p>``
    blocks (TR quotations). Output mirrors that structure for JSON consumers.
    """
    page_mid = module_id_from_url(page_url) or ""
    rr = root.select_one("div.resp-row") or root

    letter_nav_text = ""
    for h4 in root.find_all("h4"):
        if _index_h4_is_letter_navigation(h4):
            letter_nav_text = h4.get_text(" ", strip=True)
            break

    current_letter = ""
    cyclopedia_entries: list[dict[str, Any]] = []
    for el in rr.find_all(["h2", "h4"]):
        if el.name == "h2":
            current_letter = el.get_text(" ", strip=True)
            continue
        if el.name == "h4" and _index_h4_is_letter_navigation(el):
            continue
        if el.name != "h4":
            continue

        heading = _h4_topic_plain_text(el)
        if not heading:
            continue
        anchor_id = _h4_anchor_id(el)

        body_paras: list[str] = []
        n = el.next_sibling
        while n is not None:
            nm = getattr(n, "name", None)
            if nm in ("h2", "h4"):
                break
            if nm == "p":
                t = n.get_text(" ", strip=True)
                if t:
                    body_paras.append(t)
            n = n.next_sibling

        body = "\n\n".join(body_paras)
        cyclopedia_entries.append(
            {
                "letter": current_letter or None,
                "heading": heading,
                "anchor_id": anchor_id,
                "body": body,
            }
        )

    cross: list[dict[str, str]] = []
    seen_mid: set[str] = set()
    for a in root.find_all("a", href=True):
        u = urljoin(page_url, a["href"].strip())
        if not _is_tra_cyclopedia_content_url(u):
            continue
        mid = module_id_from_url(u) or ""
        if not mid or mid == INDEX_HUB_MODULE or mid == page_mid:
            continue
        if mid in seen_mid:
            continue
        seen_mid.add(mid)
        cross.append(
            {
                "text": a.get_text(" ", strip=True),
                "url": u,
                "module_id": mid,
            }
        )

    entries_map = _cyclopedia_entries_to_map(cyclopedia_entries)

    by_heading: dict[str, str] = {}
    for e in cyclopedia_entries:
        h = (e.get("heading") or "").strip() or "untitled"
        key = h
        n = 1
        while key in by_heading:
            n += 1
            key = f"{h}_{n}"
        by_heading[key] = e.get("body") or ""

    return {
        "letter_nav_text": letter_nav_text,
        "letter_cross_links": cross,
        "cyclopedia_entries": cyclopedia_entries,
        "cyclopedia_entries_map": entries_map,
        "cyclopedia_entries_by_heading": by_heading,
        "cyclopedia_entry_count": len(cyclopedia_entries),
    }


def scrape_page(
    s: requests.Session,
    url: str,
    hub_label: str,
    *,
    parent_module_id: str | None = None,
) -> PageRecord:
    html = get_html(s, url)
    soup = BeautifulSoup(html, "html.parser")
    root = main_article_root(soup)
    if root is None:
        raise RuntimeError(f"No main article container for {url}")

    h1_el = root.find("h1")
    h1 = h1_el.get_text(" ", strip=True) if h1_el else ""

    mid = module_id_from_url(url) or ""
    pattern = classify_pattern(mid, parent_module_id)

    export_root = clone_for_export(root)
    plain = export_root.get_text("\n", strip=True)
    html_fragment = str(export_root)

    structured: dict[str, Any] = {"headings_outline": headings_outline(export_root)}

    if pattern == "papers_toc":
        structured.update(extract_papers_toc(export_root))
    elif pattern == "index_hub":
        structured.update(extract_index_letter_links(export_root, url))
    elif pattern == "index_letter_page":
        structured.update(extract_index_letter_page(export_root, url))
    elif pattern == "speeches_sections":
        structured.update(extract_speeches(export_root, url))
        structured["speech_content_targets"] = speech_content_targets_from_structured(
            structured
        )
        sm: dict[str, str] = {}
        for sec in structured.get("speech_sections", []):
            t = sec.get("title")
            if t:
                sm[t] = sec.get("body", "")
        structured["speech_sections_map"] = sm
    elif pattern == "prose":
        structured.update(extract_prose_document_sections(export_root))

    # Extract images from the HTML fragment
    images: list[dict[str, str]] = []
    for img_tag in export_root.find_all("img"):
        src = img_tag.get("src", "").strip()
        if not src:
            continue
        # Normalize protocol-relative URLs
        if src.startswith("//"):
            src = "https:" + src
        elif not src.startswith(("http://", "https://")):
            src = urljoin(url, src)
        alt = img_tag.get("alt", "").strip()
        images.append({"src": src, "alt": alt})
    if images:
        structured["images"] = images

    # Extract audio/document download links (docs.ashx)
    audio_links: list[dict[str, str]] = []
    for a_tag in export_root.find_all("a", href=True):
        href = a_tag["href"].strip()
        if "docs.ashx" in href:
            if not href.startswith(("http://", "https://")):
                href = urljoin("https://www.theodoreroosevelt.org/", href)
            link_text = a_tag.get_text(" ", strip=True)
            audio_links.append({"url": href, "text": link_text})
    if audio_links:
        structured["audio_links"] = audio_links

    structured["extraction_summary"] = {
        "pattern": pattern,
        "h1": h1,
        "paragraph_count": len(export_root.find_all("p")),
    }
    if pattern == "prose" and structured.get("document_sections"):
        structured["extraction_summary"]["document_section_count"] = len(
            structured["document_sections"]
        )
    if pattern == "index_letter_page" and structured.get("cyclopedia_entries"):
        structured["extraction_summary"]["indexed_topic_count"] = len(
            structured["cyclopedia_entries"]
        )
    if pattern == "index_hub":
        structured["extraction_summary"]["index_letter_link_count"] = len(
            structured.get("index_letter_links", [])
        )
    if pattern == "papers_toc":
        structured["extraction_summary"]["toc_entry_count"] = structured.get(
            "toc_item_count", 0
        )
    if pattern == "speeches_sections":
        structured["extraction_summary"]["speech_section_count"] = len(
            structured.get("speech_sections", [])
        )

    return PageRecord(
        module_id=mid,
        url=url,
        hub_label=hub_label,
        h1=h1,
        pattern=pattern,
        plain_text=plain,
        html_fragment=html_fragment,
        structured=structured,
        parent_module_id=parent_module_id,
    )


def record_to_jsonable(rec: PageRecord) -> dict[str, Any]:
    d = asdict(rec)
    if d.get("parent_module_id") is None:
        d.pop("parent_module_id", None)
    return d


def module_bundle_dir(
    outdir: Path, module_id: str, parent_module_id: str | None = None
) -> Path:
    """
    Directory for one scraped module's artifacts.

    Top-level:  <outdir>/module_<id>/
    Child:      <outdir>/module_<parent>/module_<id>/
    """
    if parent_module_id:
        return outdir / f"module_{parent_module_id}" / f"module_{module_id}"
    return outdir / f"module_{module_id}"


def find_module_json_on_disk(outdir: Path, module_id: str) -> Path | None:
    """Prefer nested layout; fall back to legacy flat ``module_<id>.json`` in outdir."""
    nested = module_bundle_dir(outdir, module_id, None) / f"module_{module_id}.json"
    if nested.exists():
        return nested
    flat = outdir / f"module_{module_id}.json"
    if flat.exists():
        return flat
    return None


def persist_record(outdir: Path, rec: PageRecord) -> dict[str, Any]:
    mid = rec.module_id
    bundle_dir = module_bundle_dir(outdir, mid, rec.parent_module_id)
    bundle_dir.mkdir(parents=True, exist_ok=True)
    out_path = bundle_dir / f"module_{mid}.json"
    out_path.write_text(
        json.dumps(record_to_jsonable(rec), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    html_path = bundle_dir / f"module_{mid}.fragment.html"
    html_path.write_text(rec.html_fragment, encoding="utf-8")
    row: dict[str, Any] = {
        "module_id": rec.module_id,
        "hub_label": rec.hub_label,
        "pattern": rec.pattern,
        "h1": rec.h1,
        "output_dir": str(bundle_dir.resolve()),
        "json": str(out_path.resolve()),
        "html_fragment": str(html_path.resolve()),
    }
    if rec.parent_module_id:
        row["parent_module_id"] = rec.parent_module_id
    return row


def main() -> None:
    ap = argparse.ArgumentParser(description="TRA Cyclopedia phase-2 scrape")
    ap.add_argument("--hub", default=HUB_URL)
    ap.add_argument(
        "--outdir",
        type=Path,
        default=DEFAULT_SCRAPE_OUTDIR,
        help="Output directory (created if missing; default: project folder / tra_cyclopedia_out)",
    )
    ap.add_argument(
        "--delay",
        type=float,
        default=1.5,
        help="Delay between page GETs (default: 1.5)",
    )
    ap.add_argument(
        "--modules",
        help="Comma-separated module_id list to scrape (default: all hub links)",
    )
    ap.add_argument(
        "--no-speech-children",
        action="store_true",
        help="Do not follow Speeches hub links to per-speech content.aspx pages",
    )
    ap.add_argument(
        "--prune-legacy-flat",
        action="store_true",
        help=(
            "Remove top-level module_*.json / module_*.fragment.html when the same "
            "filename exists under a module_* subfolder (old flat layout duplicates)"
        ),
    )
    ap.add_argument(
        "--run-index-letters",
        action="store_true",
        help=(
            "After phase 2 finishes, run tra_cyclopedia_phase3_index_letters.py "
            "with the same --outdir"
        ),
    )
    args = ap.parse_args()

    s = session()
    hub_html = get_html(s, args.hub)
    links = extract_cyclopedia_links(hub_html)

    allow: set[str] | None = None
    if args.modules:
        allow = {x.strip() for x in args.modules.split(",") if x.strip()}

    args.outdir.mkdir(parents=True, exist_ok=True)

    hub_order = [module_id_from_url(u) or "" for u, _ in links]

    manifest: list[dict[str, Any]] = []
    written: set[str] = set()
    speech_child_order: dict[str, int] = {}

    for url, label in links:
        mid = module_id_from_url(url) or ""
        if allow is not None and mid not in allow:
            continue
        print(f"Fetching module {mid} ({label}) ...")
        rec = scrape_page(s, url, label)
        manifest.append(persist_record(args.outdir, rec))
        written.add(mid)
        time.sleep(max(0.0, args.delay))

    if not args.no_speech_children:
        hub_json = find_module_json_on_disk(args.outdir, SPEECHES_MODULE)
        targets: list[dict[str, str]] = []
        if hub_json is not None:
            try:
                hub_data = json.loads(hub_json.read_text(encoding="utf-8"))
                targets = hub_data.get("structured", {}).get(
                    "speech_content_targets", []
                )
            except json.JSONDecodeError:
                targets = []
        for i, t in enumerate(targets):
            cmid = t.get("module_id", "")
            curl = (t.get("url") or "").strip()
            ctitle = t.get("section_title", "")
            if not cmid or not curl or cmid in written:
                continue
            label = f"Speech content: {ctitle}" if ctitle else f"Speech content (module {cmid})"
            print(f"Fetching speech child module {cmid} ({label}) ...")
            crec = scrape_page(
                s,
                curl,
                label,
                parent_module_id=SPEECHES_MODULE,
            )
            new_struct = {
                **crec.structured,
                "speech_hub_section_title": ctitle,
            }
            resolved = (
                f"Speech transcript: {crec.h1}"
                if (crec.h1 or "").strip()
                else label
            )
            crec = replace(
                crec,
                hub_label=resolved,
                structured=new_struct,
            )
            manifest.append(persist_record(args.outdir, crec))
            written.add(cmid)
            speech_child_order[cmid] = i
            time.sleep(max(0.0, args.delay))

    manifest_path = args.outdir / "manifest.json"
    if allow is not None and manifest_path.exists():
        try:
            prev_rows: list[dict[str, Any]] = json.loads(
                manifest_path.read_text(encoding="utf-8")
            )
        except json.JSONDecodeError:
            prev_rows = []
        for row in prev_rows:
            mid = row.get("module_id", "")
            if mid and mid not in written:
                manifest.append(row)

    order_index = {m: i for i, m in enumerate(hub_order) if m}

    def manifest_sort_key(row: dict[str, Any]) -> tuple[int, int]:
        mid = row.get("module_id", "")
        if row.get("parent_module_id") == SPEECHES_MODULE:
            return (1, speech_child_order.get(mid, 9999))
        return (0, order_index.get(mid, 9999))

    manifest.sort(key=manifest_sort_key)

    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    if args.prune_legacy_flat:
        pruned = 0
        for p in list(args.outdir.iterdir()):
            if not p.is_file():
                continue
            if not p.name.startswith("module_"):
                continue
            if not (p.suffix == ".json" or p.name.endswith(".fragment.html")):
                continue
            dup = [q for q in args.outdir.rglob(p.name) if q.is_file() and q.resolve() != p.resolve()]
            if dup:
                p.unlink()
                pruned += 1
        if pruned:
            print(f"Pruned {pruned} legacy flat file(s) from output root.")

    print(f"Wrote {len(written)} page bundle(s); manifest has {len(manifest)} entries.")
    print(f"Output directory: {args.outdir.resolve()}")

    if args.run_index_letters:
        root = TRA_CYCOPE_PROJECT_DIR
        phase3 = root / "tra_cyclopedia_phase3_index_letters.py"
        if not phase3.is_file():
            print(f"ERROR: phase 3 script not found: {phase3}", file=sys.stderr)
            sys.exit(1)
        r = subprocess.run(
            [sys.executable, str(phase3), "--outdir", str(args.outdir)],
            cwd=str(root),
        )
        if r.returncode != 0:
            sys.exit(r.returncode)


if __name__ == "__main__":
    main()
