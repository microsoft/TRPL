#!/usr/bin/env python3
"""Create ignored Layer 4 cloud-text settings from the working Layer 3 setup."""

from __future__ import annotations

import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
LAYER3_ENV = ROOT / "Layer3_Campfire" / "backend" / ".env.local"
LAYER4_LOCAL_ENV = (
    ROOT
    / "Layer4_Talk_to_TR"
    / "TRPL-Web"
    / "TRPL-web-oss"
    / "lia_agent_api"
    / ".env.local"
)
LAYER4_CLOUD_ENV = LAYER4_LOCAL_ENV.with_name(".env.cloud.local")
GUARDRAIL_DIR = LAYER4_LOCAL_ENV.parent / "private" / "guardrails"


def _read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        name, separator, value = line.partition("=")
        if separator and name and not name.startswith("#"):
            values[name] = value
    return values


def _required(values: dict[str, str], name: str, source: Path) -> str:
    value = values.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is missing from {source}")
    return value


def _openai_v1_url(endpoint: str) -> str:
    base = endpoint.rstrip("/")
    if "/openai/" in base:
        return base + "/"
    return base + "/openai/v1/"


def main() -> int:
    layer3 = _read_env(LAYER3_ENV)
    layer4 = _read_env(LAYER4_LOCAL_ENV)
    required_guardrails = {
        "LIA_ROOSEVELT_GUARDRAILS_FILE": GUARDRAIL_DIR
        / "roosevelt-guardrails.txt",
        "LIA_THREAT_PATTERNS_FILE": GUARDRAIL_DIR / "threat-patterns.json",
    }
    for path in required_guardrails.values():
        if not path.is_file() or not path.read_text(encoding="utf-8").strip():
            raise RuntimeError(
                f"Populate deployment-only guardrail file before cloud startup: {path}"
            )

    openai_endpoint = _required(layer3, "AZURE_OPENAI_ENDPOINT", LAYER3_ENV)
    openai_key = _required(layer3, "AZURE_OPENAI_API_KEY", LAYER3_ENV)
    search_endpoint = _required(layer3, "AZURE_SEARCH_ENDPOINT", LAYER3_ENV)
    search_key = _required(layer3, "AZURE_SEARCH_API_KEY", LAYER3_ENV)
    embedding_model = layer3.get(
        "AZURE_OPENAI_EMBEDDING_MODEL", "text-embedding-3-large"
    ).strip()
    embedding_api_version = "2024-02-01"
    embedding_endpoint = (
        f"{openai_endpoint.rstrip('/')}/openai/deployments/{embedding_model}"
        f"/embeddings?api-version={embedding_api_version}"
    )

    values = {
        "LIA_RUNTIME_MODE": "cloud",
        "APP_ENV": "local",
        "HOST": "0.0.0.0",
        "PORT": "8010",
        "CLIENT_API_KEYS": _required(layer4, "CLIENT_API_KEYS", LAYER4_LOCAL_ENV),
        "WS_AUTH_ENABLED": "true",
        "TTS_ENABLED": "false",
        "STORYS_SELF_ROUTING": "true",
        "STORYS_KB_ACK_PROBABILITY": "0",
        "PROMPT_INJECTION_ENABLED": "false",
        "OUTPUT_REVIEWER_ENABLED": "false",
        "LLM_BASE_URL": _openai_v1_url(openai_endpoint),
        "LLM_API_KEY": openai_key,
        "LLM_SMALL_MODEL": _required(layer3, "GPT_SCOPE_MODEL", LAYER3_ENV),
        "LLM_SMALL_INPUT_PRICE_1K": "0",
        "LLM_SMALL_OUTPUT_PRICE_1K": "0",
        "LLM_MID_MODEL": _required(layer3, "GPT_SEARCH_QUERY_MODEL", LAYER3_ENV),
        "LLM_MID_INPUT_PRICE_1K": "0",
        "LLM_MID_OUTPUT_PRICE_1K": "0",
        "LLM_LARGE_MODEL": _required(layer3, "GPT_CHAT_MODEL", LAYER3_ENV),
        "LLM_LARGE_INPUT_PRICE_1K": "0",
        "LLM_LARGE_OUTPUT_PRICE_1K": "0",
        "SEARCH_ARCHIVES_QUERY_KEY": search_key,
        "SEARCH_ARCHIVES_URL": search_endpoint,
        "SEARCH_ARCHIVES_INDEX_NAME": _required(
            layer3, "AZURE_SEARCH_LETTER_INDEX", LAYER3_ENV
        ),
        "SEARCH_ARCHIVES_SEMANTIC_CONFIG": layer3.get(
            "AZURE_SEARCH_LETTER_SEMANTIC_CONFIG", ""
        ),
        "SEARCH_ARCHIVES_EMBEDDING_MODEL": embedding_model,
        "SEARCH_ARCHIVES_EMBEDDING_KEY": openai_key,
        "SEARCH_ARCHIVES_EMBEDDING_ENDPOINT": embedding_endpoint,
        "SEARCH_ARCHIVES_EMBEDDING_API_VERSION": embedding_api_version,
        "SEARCH_BOOKS_QUERY_KEY": search_key,
        "SEARCH_BOOKS_URL": search_endpoint,
        "SEARCH_BOOKS_INDEX_NAME": _required(
            layer3, "AZURE_SEARCH_BOOK_INDEX", LAYER3_ENV
        ),
        "SEARCH_BOOKS_SEMANTIC_CONFIG": layer3.get(
            "AZURE_SEARCH_BOOK_SEMANTIC_CONFIG", ""
        ),
        "SEARCH_BOOKS_EMBEDDING_MODEL": embedding_model,
        "SEARCH_BOOKS_EMBEDDING_KEY": openai_key,
        "SEARCH_BOOKS_EMBEDDING_ENDPOINT": embedding_endpoint,
        "SEARCH_BOOKS_EMBEDDING_API_VERSION": embedding_api_version,
    }
    for name, path in required_guardrails.items():
        values[name] = f"/run/secrets/layer4-guardrails/{path.name}"

    content = "\n".join(f"{name}={value}" for name, value in values.items()) + "\n"
    descriptor = os.open(
        LAYER4_CLOUD_ENV,
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
        0o600,
    )
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        file.write(content)
    LAYER4_CLOUD_ENV.chmod(0o600)
    print(f"Wrote ignored Layer 4 cloud-text settings: {LAYER4_CLOUD_ENV}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
