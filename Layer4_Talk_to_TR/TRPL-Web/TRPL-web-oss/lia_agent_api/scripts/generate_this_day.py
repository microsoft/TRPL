#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Generate "This Day in TR History" data using LLM.

For each calendar day (Jan 1 - Dec 31), asks the LLM what notable events
happened during Theodore Roosevelt's life (1858-1919) on that date.

Output: storys/this_day_in_history.json
Format:
  {
    "01-01": [
      {"year": 1907, "event": "I issued an executive order on...", "category": "presidency"}
    ],
    ...
  }

Usage:
    cd lia_agent_api/
    PYTHONPATH=src .venv/bin/python scripts/generate_this_day.py
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

OUTPUT_FILE = Path(__file__).resolve().parents[2] / "storys" / "this_day_in_history.json"

SYSTEM = """\
You are a Theodore Roosevelt historian. For any given calendar date,
generate 1-2 plausible "anniversary thoughts" TR might have on that day.

The goal: every day of the year should give TR something to "be thinking about."

You can use:
1. Specific dated events you know happened on that exact date
2. Approximate events from that month/season of his life
3. Themes that fit that time of year (winter ranching, summer in the Badlands,
   autumn campaigns, spring conservation work, etc.)
4. General reflections tied to his life's rhythm during that period

Even when you can't pin a specific date, generate something thematic and
authentic to TR's voice. The reflection should feel like TR genuinely
"thinking about something" on that day.

For each item, write a 1-sentence first-person reflection from TR's voice.
Vivid, specific, hook-like. This will be used as a museum exhibit prompt.

Return JSON:
{
  "events": [
    {
      "year": 1884,
      "event": "I lost my mother and my wife on the same day, and the light went out of my life.",
      "category": "personal | presidency | adventure | early_life | military",
      "specific_date": true
    },
    {
      "year": 1885,
      "event": "It was around this time of year that I rode the long winter rounds at my Dakota ranch.",
      "category": "adventure",
      "specific_date": false
    }
  ]
}

ALWAYS return at least 1 event. Mark specific_date true if you know the exact date,
false if it's seasonal/thematic.

CRITICAL: Never reference TR's death (January 6, 1919) or events after his
presidency that involve dying/funeral. TR is portrayed as ALIVE in the museum,
mid-thought. Skip his death entirely. If a date is around January 6, write
about something else from his life instead.
"""


async def get_events_for_date(month: int, day: int, llm_config: dict) -> list[dict]:
    from debate.services.llm_streaming import stream_json_field

    months = ["January", "February", "March", "April", "May", "June",
              "July", "August", "September", "October", "November", "December"]
    date_str = f"{months[month-1]} {day}"

    payload = {"date": date_str}
    try:
        result = await stream_json_field(
            user_messages=[{"role": "user", "content": json.dumps(payload)}],
            llm_config=llm_config,
            field_name="events",
            on_field_chunk=None,
            system_message=SYSTEM,
        )
        return result.get("events", [])
    except Exception as e:
        print(f"  ERROR for {date_str}: {e}")
        return []


def days_in_month(month: int) -> int:
    if month == 2:
        return 29  # always check Feb 29 too
    if month in (4, 6, 9, 11):
        return 30
    return 31


async def main():
    from api.config import config

    llm_config = config.llm_config_large.model_dump()  # use large for accuracy

    # Resume from existing file if present
    existing = {}
    if OUTPUT_FILE.exists():
        with open(OUTPUT_FILE, encoding="utf-8") as f:
            existing = json.load(f)
        print(f"Resuming with {len(existing)} dates already done.")

    results = dict(existing)
    total_events = 0

    for month in range(1, 13):
        for day in range(1, days_in_month(month) + 1):
            key = f"{month:02d}-{day:02d}"
            if key in results:
                total_events += len(results[key])
                continue

            print(f"  [{key}] ", end="", flush=True)

            events = []
            for attempt in range(3):
                try:
                    events = await get_events_for_date(month, day, llm_config)
                    break
                except Exception as e:
                    if "Rate limit" in str(e) and attempt < 2:
                        wait = (attempt + 1) * 5
                        print(f"(rate limit, wait {wait}s)", end=" ", flush=True)
                        await asyncio.sleep(wait)
                    else:
                        print(f"FAILED: {e}")
                        break

            results[key] = events
            total_events += len(events)
            if events:
                print(f"{len(events)} event(s): {events[0].get('event', '')[:60]}")
            else:
                print("(none)")

            # Incremental save
            with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                json.dump(results, f, ensure_ascii=False, indent=2)

            await asyncio.sleep(0.5)  # gentle pacing

    print(f"\nDone! {len(results)} dates, {total_events} total events.")
    print(f"Saved to {OUTPUT_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
