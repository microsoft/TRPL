# -*- coding: utf-8 -*-
"""Offline regression runner for deployment-only guardrail cases.

Loads private cases and runs them through the effective storys.adult system
prompt against the same LLM production uses.

Run:  ./.venv/bin/python scripts/jb_prompt_regression.py
"""
from __future__ import annotations

import json
import sys

from debate.services.prompt_overrides import get_effective
from debate.services.private_config import load_private_json
from debate.services.shared import get_openai_client
from api.config import config


def ask(system_prompt: str, user_turns: list[str]) -> str:
    """Single-shot: feed prior user turns as context, return reply to the last.
    We keep it simple — system + the attack turns as a running user thread."""
    client = get_openai_client()
    msgs = [{"role": "system", "content": system_prompt}]
    for t in user_turns:
        msgs.append({"role": "user", "content": t})
    resp = client.chat.completions.create(
        model=config.llm_config_large.model,
        messages=msgs,
        max_tokens=400,
        response_format={"type": "json_object"},
    )
    raw = resp.choices[0].message.content or "{}"
    try:
        return json.loads(raw).get("response", raw)
    except (json.JSONDecodeError, ValueError):
        return raw  # envelope broke — surface raw so we notice


CASES = load_private_json(
    "LIA_JAILBREAK_CASES",
    "LIA_JAILBREAK_CASES_FILE",
    list,
)


def main() -> int:
    if not CASES:
        raise ValueError("At least one private regression case is required.")
    text, is_override = get_effective("storys.adult")
    print(f"Prompt source: {'OVERRIDE (~/.lia_prompt_overrides.json)' if is_override else 'compiled default'}")
    print(f"Model: {config.llm_config_large.model}\n" + "=" * 72)
    for c in CASES:
        print(f"\n### {c['id']}")
        print(f"FAIL IF: {c['fail_if']}")
        try:
            reply = ask(text, c["turns"])
        except Exception as e:  # noqa: BLE001
            print(f"  ERROR calling model: {e}")
            continue
        print(f"TR: {reply}")
    print("\n" + "=" * 72)
    print("Judge each reply against its configured FAIL IF criterion.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
