#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Generate one-line summaries for each story using LLM.
Reads each story .txt file, sends the narrative to LLM, gets a 1-sentence summary.
Saves results to storys/quiz_stories/_one_liners.json

Usage:
    cd lia_agent_api/
    PYTHONPATH=src .venv/bin/python scripts/generate_story_summaries.py
"""
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

STORYS_DIR = Path(__file__).resolve().parents[2] / "storys" / "quiz_stories"
OUTPUT_FILE = STORYS_DIR / "_one_liners.json"

SYSTEM = """\
You are generating a one-line story summary for a museum exhibit about Theodore Roosevelt.
The summary should be:
- One sentence, max 20 words
- Written from TR's first-person perspective ("I...")
- Vivid and hook-like — makes someone want to hear the full story
- No spoilers — tease, don't tell

Examples:
- "I lost my mother and wife on the same day — then I rode West into the wilderness."
- "A dead seal in a market changed my life when I was seven years old."
- "I was shot in the chest and kept giving my speech for ninety more minutes."
- "My children smuggled a pony into the White House elevator."

Return ONLY the one-line summary, nothing else.
"""


async def summarize_one(narrative: str, llm_config: dict) -> str:
    from debate.services.llm_streaming import stream_text_response

    # Truncate very long narratives to first ~2000 chars
    if len(narrative) > 3000:
        narrative = narrative[:2000] + "\n...[truncated]"

    result = await stream_text_response(
        user_messages=[{"role": "user", "content": f"Summarize this story in one line:\n\n{narrative}"}],
        llm_config=llm_config,
        on_text_chunk=None,
        system_message=SYSTEM,
    )
    return result.strip().strip('"')


def read_narrative(filepath: Path) -> str:
    """Read story file and strip the evidence/audit section."""
    text = filepath.read_text(encoding="utf-8")
    marker = "=" * 40
    parts = text.split(marker)
    narrative_parts = []
    for part in parts:
        if part.strip().upper().startswith("EVIDENCE SOURCES"):
            break
        narrative_parts.append(part)
    return marker.join(narrative_parts).strip()


async def main():
    from api.config import config

    # Use mid model (cheaper, faster) for summaries
    llm_config = config.llm_config_mid.model_dump()

    # Load summary index
    with open(STORYS_DIR / "_summary.json", encoding="utf-8") as f:
        catalog = json.load(f)

    # Load existing one-liners if any (to resume)
    existing = {}
    if OUTPUT_FILE.exists():
        with open(OUTPUT_FILE, encoding="utf-8") as f:
            for entry in json.load(f):
                existing[entry["file"]] = entry

    results = []
    skipped = 0

    for i, entry in enumerate(catalog):
        filename = entry.get("file", "")
        title = entry.get("title", "untitled")

        # Skip if already done
        if filename in existing:
            results.append(existing[filename])
            skipped += 1
            continue

        filepath = STORYS_DIR / filename
        if not filepath.exists():
            print(f"  [{i+1}/{len(catalog)}] SKIP (file missing): {filename}")
            continue

        narrative = read_narrative(filepath)
        if not narrative:
            print(f"  [{i+1}/{len(catalog)}] SKIP (empty): {filename}")
            continue

        print(f"  [{i+1}/{len(catalog)}] {title[:60]}...", end=" ", flush=True)

        for attempt in range(3):
            try:
                one_liner = await summarize_one(narrative, llm_config)
                break
            except Exception as e:
                if "Rate limit" in str(e) and attempt < 2:
                    wait = (attempt + 1) * 5
                    print(f"(rate limit, wait {wait}s)", end=" ", flush=True)
                    await asyncio.sleep(wait)
                else:
                    one_liner = ""
                    print(f"ERROR: {e}", end=" ", flush=True)
                    break

        print(f"→ {one_liner[:80]}")

        results.append({
            "file": filename,
            "title": title,
            "quiz": entry.get("quiz", ""),
            "one_liner": one_liner,
        })

        # Save incrementally
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)

        await asyncio.sleep(1)  # rate limit pacing

    if skipped:
        print(f"\nSkipped {skipped} already-done stories.")
    print(f"Done! {len(results)} stories saved to {OUTPUT_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
