"""
Phase 1: Theodore Roosevelt Association Cyclopedia hub.

1) Fetch the hub page (Browse TRA Cyclopedia).
2) Extract every Cyclopedia browse link (content.aspx ... module_id=...).
3) GET each target and report HTTP status, HTML <title>, and main <h1>.

Requires: ``pip install -r requirements-tra-scrape.txt`` from the project folder
``TRA (Theodore Roosevelt Association Cyclopedia)`` (same folder as this file).

Respect the site: default delay between requests; do not disable for bulk runs
without permission from the site owner.
"""

from __future__ import annotations

import argparse
import re
import time
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

BASE = "https://www.theodoreroosevelt.org"
HUB_URL = (
    f"{BASE}/content.aspx?page_id=22&club_id=991271&module_id=339179"
)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def module_id_from_url(url: str) -> str | None:
    q = parse_qs(urlparse(url).query)
    mids = q.get("module_id")
    return mids[0] if mids else None


def extract_cyclopedia_links(html: str) -> list[tuple[str, str]]:
    """
    Return list of (absolute_url, link_text) for items under
    'Browse TRA Cyclopedia by:' on the hub page.
    """
    soup = BeautifulSoup(html, "html.parser")
    h4 = None
    for el in soup.find_all("h4"):
        if "Browse TRA Cyclopedia" in el.get_text():
            h4 = el
            break
    if h4 is None:
        raise RuntimeError("Could not find hub heading 'Browse TRA Cyclopedia'")

    ul = h4.find_next("ul")
    if ul is None:
        raise RuntimeError("No <ul> found after Cyclopedia browse heading")

    out: list[tuple[str, str]] = []
    seen_modules: set[str] = set()

    for li in ul.find_all("li", recursive=False):
        candidates: list[str] = []
        for a in li.find_all("a", href=True):
            href = a["href"].strip()
            if not href or href.startswith("#"):
                continue
            abs_url = urljoin(BASE + "/", href)
            if "theodoreroosevelt.org" not in urlparse(abs_url).netloc:
                continue
            if "/content.aspx" not in urlparse(abs_url).path:
                continue
            mid = module_id_from_url(abs_url)
            if not mid:
                continue
            candidates.append(abs_url)

        if not candidates:
            continue

        # Prefer the last content.aspx link in the <li> (matches visible label on hub).
        chosen = candidates[-1]
        mid = module_id_from_url(chosen)
        assert mid
        label = (li.get_text(" ", strip=True) or "").strip()
        if mid in seen_modules:
            continue
        seen_modules.add(mid)
        out.append((chosen, label))

    return out


def page_summary(session: requests.Session, url: str) -> tuple[int, str, str]:
    r = session.get(url, timeout=45)
    status = r.status_code
    title = ""
    h1 = ""
    if r.ok and r.text:
        s = BeautifulSoup(r.text, "html.parser")
        t = s.find("title")
        title = t.get_text(strip=True) if t else ""
        h1el = s.select_one("#content_column h1")
        if h1el is None:
            h1el = s.find("h1")
        h1 = h1el.get_text(" ", strip=True) if h1el else ""
    return status, title, h1


def main() -> None:
    p = argparse.ArgumentParser(description="TRA Cyclopedia hub link discovery (phase 1)")
    p.add_argument(
        "--hub",
        default=HUB_URL,
        help="Hub URL (default: official Cyclopedia browse page)",
    )
    p.add_argument(
        "--delay",
        type=float,
        default=1.0,
        help="Seconds to sleep between each child GET (default: 1.0)",
    )
    p.add_argument(
        "--no-fetch-children",
        action="store_true",
        help="Only parse the hub; do not request child pages",
    )
    args = p.parse_args()

    session = requests.Session()
    session.headers.update(DEFAULT_HEADERS)

    hub = session.get(args.hub, timeout=45)
    hub.raise_for_status()

    links = extract_cyclopedia_links(hub.text)
    print(f"Hub: {args.hub}")
    print(f"Found {len(links)} Cyclopedia browse targets:\n")
    for i, (url, label) in enumerate(links, 1):
        mid = module_id_from_url(url) or "?"
        print(f"{i:2}. [{mid}] {label}")
        print(f"    {url}")

    if args.no_fetch_children:
        return

    print("\n--- Probing each page (GET) ---\n")
    for url, label in links:
        status, title, h1 = page_summary(session, url)
        print(f"module={module_id_from_url(url)} status={status}")
        print(f"  label: {label}")
        print(f"  title: {title}")
        print(f"  h1:    {h1}")
        print()
        time.sleep(max(0.0, args.delay))


if __name__ == "__main__":
    main()
