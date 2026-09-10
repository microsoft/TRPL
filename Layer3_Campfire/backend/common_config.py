import logging
import os

VALID_CAMPFIRE_RAG_MODES = {"cloud", "degraded"}
CAMPFIRE_RAG_MODE = os.getenv("CAMPFIRE_RAG_MODE", "cloud").strip().lower()
if CAMPFIRE_RAG_MODE not in VALID_CAMPFIRE_RAG_MODES:
    raise RuntimeError(
        "CAMPFIRE_RAG_MODE must be one of: "
        + ", ".join(sorted(VALID_CAMPFIRE_RAG_MODES))
    )
CLOUD_RAG_ENABLED = CAMPFIRE_RAG_MODE == "cloud"


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if value is None:
        raise RuntimeError(f"{name} environment variable is required")
    return value


def _cloud_required_env(name: str) -> str:
    if CLOUD_RAG_ENABLED:
        return _required_env(name)
    return os.getenv(name, "")


# Environment variables
AZURE_OPENAI_ENDPOINT = _cloud_required_env("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_BASE_URL = f"{AZURE_OPENAI_ENDPOINT}/openai/v1/"
AZURE_OPENAI_API_KEY = _cloud_required_env("AZURE_OPENAI_API_KEY")

_GPT52_CHAT = "gpt-5.2-chat"
_GPT41_MINI = "gpt-4.1-mini"
_GPT54_MINI = "gpt-5.4-mini"
_GPT55 = "gpt-5.5"
_GPT_CHAT_LATEST = "gpt-chat-latest"

_REASONING_EFFORTS: dict[str, str] = {
    _GPT54_MINI: "low",
    _GPT_CHAT_LATEST: "medium",
    _GPT55: "medium",
}

# Model overrides for A/B testing (env var → production default)
GPT_CHAT_MODEL = os.getenv("GPT_CHAT_MODEL", _GPT_CHAT_LATEST)
GPT_SCOPE_MODEL = os.getenv("GPT_SCOPE_MODEL", _GPT54_MINI)
GPT_SEARCH_QUERY_MODEL = os.getenv("GPT_SEARCH_QUERY_MODEL", _GPT54_MINI)
GPT_FOLLOWUP_MODEL = os.getenv("GPT_FOLLOWUP_MODEL", _GPT54_MINI)
# must match baseline judge model for valid comparisons
GPT_EVALUATION_MODEL = os.getenv("GPT_EVALUATION_MODEL", _GPT54_MINI)
AZURE_OPENAI_EMBEDDING_MODEL = os.getenv(
    "AZURE_OPENAI_EMBEDDING_MODEL", "text-embedding-3-large"
)


def reasoning_kwargs(model: str, api: str = "chat") -> dict:
    """Reasoning-effort kwargs for models that support it; empty otherwise.

    `api="chat"` returns chat.completions-style `reasoning_effort`.
    `api="responses"` returns Responses-API-style `reasoning={"effort": ...}`.
    """
    effort = _REASONING_EFFORTS.get(model)
    if not effort:
        return {}
    if api == "responses":
        return {"reasoning": {"effort": effort}}
    return {"reasoning_effort": effort}


AZURE_SEARCH_ENDPOINT = _cloud_required_env("AZURE_SEARCH_ENDPOINT")
AZURE_SEARCH_API_KEY = _cloud_required_env("AZURE_SEARCH_API_KEY")
AZURE_SEARCH_BOOK_INDEX = _cloud_required_env("AZURE_SEARCH_BOOK_INDEX")
AZURE_SEARCH_BOOK_SEMANTIC_CONFIG = os.getenv("AZURE_SEARCH_BOOK_SEMANTIC_CONFIG")
AZURE_SEARCH_LETTER_INDEX = _cloud_required_env("AZURE_SEARCH_LETTER_INDEX")
AZURE_SEARCH_LETTER_SEMANTIC_CONFIG = os.getenv("AZURE_SEARCH_LETTER_SEMANTIC_CONFIG")

AZURE_STORAGE_KEY = os.getenv("AZURE_STORAGE_KEY")
# Canonical storage account to serve blobs from. When set, blob URLs from the
# Search index are normalized to this account's host before signing (the index
# may store URLs pointing at a different account, e.g. staging).
AZURE_STORAGE_ACCOUNT = os.getenv("AZURE_STORAGE_ACCOUNT")

# API Authentication
RAG_API_KEY = _required_env("RAG_API_KEY")

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", None)
REDIS_SSL = os.getenv("REDIS_SSL", "true").lower() in ("true", "1", "yes")
_redis_scheme = "rediss" if REDIS_SSL else "redis"
_redis_auth = f":{REDIS_PASSWORD}@" if REDIS_PASSWORD else ""
REDIS_URL_CHAT_STORE = f"{_redis_scheme}://{_redis_auth}{REDIS_HOST}:{REDIS_PORT}/0"

# Azure Search constants
SEARCH_TOP_K = int(os.getenv("SEARCH_TOP_K", "10"))

# Router override for evaluations (forces book agent to get citations)
FORCE_BOOK_AGENT = os.getenv("FORCE_BOOK_AGENT", "false").lower() in ("true", "1", "yes")

# Number of leading characters of the user prompt to include in fact-check logs.
FACT_CHECK_LOG_PROMPT_CHARS = int(os.getenv("FACT_CHECK_LOG_PROMPT_CHARS", "150"))

# Max total answer-generation attempts before giving up and returning a safe
# fallback message. 1 initial attempt + (FACT_CHECK_MAX_ATTEMPTS - 1) retries.
FACT_CHECK_MAX_ATTEMPTS = int(os.getenv("FACT_CHECK_MAX_ATTEMPTS", "3"))

AZURE_SEARCH_VECTOR_K = int(os.getenv("AZURE_SEARCH_VECTOR_K", "50"))
AZURE_SEARCH_VECTOR_WEIGHT = float(os.getenv("AZURE_SEARCH_VECTOR_WEIGHT", "1.0"))
AZURE_SEARCH_VECTOR_FILTER_MODE = os.getenv("AZURE_SEARCH_VECTOR_FILTER_MODE", "preFilter")
AZURE_SEARCH_TIMEOUT = float(os.getenv("AZURE_SEARCH_TIMEOUT", "5.0"))
AZURE_SEARCH_MAX_RETRIES = int(os.getenv("AZURE_SEARCH_MAX_RETRIES", "2"))
AZURE_SEARCH_LETTER_DATE_FROM = os.getenv("AZURE_SEARCH_LETTER_DATE_FROM")
AZURE_SEARCH_LETTER_DATE_TO = os.getenv("AZURE_SEARCH_LETTER_DATE_TO")
AZURE_SEARCH_BOOK_DATE_FROM = os.getenv("AZURE_SEARCH_BOOK_DATE_FROM")
AZURE_SEARCH_BOOK_DATE_TO = os.getenv("AZURE_SEARCH_BOOK_DATE_TO")
KB_INDEX_VERSION = os.getenv("KB_INDEX_VERSION", "v1")

# LRU Cache
ENABLE_KB_EMB_CACHE = os.getenv("ENABLE_KB_EMB_CACHE", "false").lower() == "true"
ENABLE_KB_RESULT_CACHE = os.getenv("ENABLE_KB_RESULT_CACHE", "false").lower() == "true"

KB_EMB_CACHE_CAPACITY = int(os.getenv("KB_EMB_CACHE_CAPACITY", "2000"))
KB_RESULT_CACHE_CAPACITY = int(os.getenv("KB_RESULT_CACHE_CAPACITY", "500"))
KB_RESULT_CACHE_THRESHOLD = float(os.getenv("KB_RESULT_CACHE_THRESHOLD", "0.915"))
KB_CACHE_DIR = os.getenv("KB_CACHE_DIR", ".")
KB_RESULT_CACHE_FLUSH_INTERVAL_SEC = float(os.getenv("KB_RESULT_CACHE_FLUSH_INTERVAL_SEC", "3.0"))
KB_RESULT_CACHE_FLUSH_EVERY_N = int(os.getenv("KB_RESULT_CACHE_FLUSH_EVERY_N", "10"))


def setup_logging():
    from observability import PII_FILTER, JsonFormatter

    level = os.getenv("LOG_LEVEL", "INFO").upper()

    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    handler.addFilter(PII_FILTER)

    root = logging.getLogger()
    # Drop any handlers installed by an earlier import path so we don't end up
    # double-emitting. Azure Monitor attaches its own handler later, after
    # configure_azure_monitor() runs in main.py — that one is preserved.
    for h in list(root.handlers):
        root.removeHandler(h)
    root.setLevel(level)
    root.addHandler(handler)

    # Quiet noisy third-party libs
    logging.getLogger("azure").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


# Initialize logging on import
setup_logging()
