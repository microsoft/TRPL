"""
Phase 3: Crawl per-letter Index pages linked from the Index A–Z hub (module 339476).

Reads ``structured.index_letter_links`` from ``module_339476/module_339476.json`` when
present; otherwise fetches the hub page and parses letter targets.

Each letter page is saved under the same nested layout as speech children::

  <outdir>/module_339476/module_<letter_id>/module_<letter_id>.json
  <outdir>/module_339476/module_<letter_id>/module_<letter_id>.fragment.html

Updates ``manifest.json`` by removing prior rows with ``parent_module_id`` 339476,
then inserting the new letter rows immediately after the Index hub row.

Requires: ``pip install -r requirements-tra-scrape.txt`` from the project folder.

Keep ``PYTHONPATH`` including the project directory (e.g. run from
``d:\\Temp\\TRA (Theodore Roosevelt Association Cyclopedia)`` so phase1/phase2 imports resolve).
Default ``--outdir`` is ``tra_cyclopedia_out`` next to these scripts::

  cd "d:\\Temp\\TRA (Theodore Roosevelt Association Cyclopedia)"
  python tra_cyclopedia_phase3_index_letters.py
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

from tra_cyclopedia_phase2_scrape import (
    DEFAULT_SCRAPE_OUTDIR,
    INDEX_HUB_MODULE,
    extract_index_letter_links,
    find_module_json_on_disk,
    get_html,
    main_article_root,
    persist_record,
    scrape_page,
    session,
)

INDEX_HUB_URL = (
    "https://www.theodoreroosevelt.org/content.aspx?"
    "page_id=22&club_id=991271&module_id=" + INDEX_HUB_MODULE
)


def load_letter_targets(outdir: Path, s: Any) -> list[dict[str, str]]:
    hub_path = find_module_json_on_disk(outdir, INDEX_HUB_MODULE)
    if hub_path is not None:
        data = json.loads(hub_path.read_text(encoding="utf-8"))
        links = data.get("structured", {}).get("index_letter_links", [])
        if links:
            return links
    html = get_html(s, INDEX_HUB_URL)
    root = main_article_root(BeautifulSoup(html, "html.parser"))
    if root is None:
        raise RuntimeError("Could not parse Index hub HTML")
    return extract_index_letter_links(root, INDEX_HUB_URL)["index_letter_links"]


def merge_manifest(
    outdir: Path, new_rows: list[dict[str, Any]], letter_order: list[str]
) -> None:
    man_path = outdir / "manifest.json"
    order_of = {mid: i for i, mid in enumerate(letter_order)}

    def sort_key(row: dict[str, Any]) -> int:
        return order_of.get(row.get("module_id", ""), 9999)

    new_sorted = sorted(new_rows, key=sort_key)

    if not man_path.exists():
        man_path.write_text(
            json.dumps(new_sorted, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return

    rows: list[dict[str, Any]] = json.loads(man_path.read_text(encoding="utf-8"))
    filtered = [r for r in rows if r.get("parent_module_id") != INDEX_HUB_MODULE]

    out: list[dict[str, Any]] = []
    inserted = False
    for r in filtered:
        out.append(r)
        if r.get("module_id") == INDEX_HUB_MODULE and not inserted:
            out.extend(new_sorted)
            inserted = True

    if not inserted:
        out.extend(new_sorted)

    man_path.write_text(
        json.dumps(out, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="TRA Cyclopedia Index letter pages (phase 3)")
    ap.add_argument(
        "--outdir",
        type=Path,
        default=DEFAULT_SCRAPE_OUTDIR,
        help="Same output tree as phase 2 (default: project folder / tra_cyclopedia_out)",
    )
    ap.add_argument(
        "--delay",
        type=float,
        default=1.5,
        help="Seconds between letter-page GETs",
    )
    ap.add_argument(
        "--no-manifest",
        action="store_true",
        help="Write JSON/HTML only; do not change manifest.json",
    )
    args = ap.parse_args()

    s = session()
    targets = load_letter_targets(args.outdir, s)
    letter_order = [t.get("module_id", "") for t in targets if t.get("module_id")]

    args.outdir.mkdir(parents=True, exist_ok=True)

    manifest_rows: list[dict[str, Any]] = []
    for t in targets:
        mid = (t.get("module_id") or "").strip()
        url = (t.get("url") or "").strip()
        text = (t.get("text") or "").strip() or f"module {mid}"
        if not mid or not url:
            continue
        label = f"Index letter page: {text}"
        print(f"Fetching index letter module {mid} ({label}) ...")
        rec = scrape_page(s, url, label, parent_module_id=INDEX_HUB_MODULE)
        manifest_rows.append(persist_record(args.outdir, rec))
        time.sleep(max(0.0, args.delay))

    if not args.no_manifest:
        merge_manifest(args.outdir, manifest_rows, letter_order)

    print(
        f"Wrote {len(manifest_rows)} index letter bundle(s) under "
        f"{args.outdir.resolve() / ('module_' + INDEX_HUB_MODULE)}"
    )


if __name__ == "__main__":
    main()
