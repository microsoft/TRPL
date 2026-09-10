# -*- coding: utf-8 -*-
import datetime
import hashlib
import re
from typing import Any, Tuple

# Hard token regex: extracts identifier-like signals used to gate semantic
# cache hits. The point is to catch identifiers an answer must be about
# (article numbers, ticket IDs, UUIDs, etc.) so we don't return one
# identifier's content for a query about another.
#
# Order matters: longer/more-specific patterns are listed first so that
# alternation prefers the full identifier (e.g. "PROJ-1234-5678") over a
# trailing digit run ("5678"). The optional separator in the letters+digits
# pattern catches hyphenated tickets like "ABC-123" that fall under the 8-char
# long-token threshold.
_HARD_TOKEN_RE = re.compile(
    r"(\b[A-Za-z0-9_-]{8,}\b|"      # Long tokens (UUID fragments, hashes, multi-part IDs)
    r"\b[A-Za-z]{2,}[-_]?\d{2,}\b|" # Letters + optional separator + digits (PROJ-1234, ABC123)
    r"\b\d{3,}\b)"                  # >=3 digits (IDs, dates, clauses, amounts)
)


def normalize_query(q: str) -> str:
    """Normalizes the query string by trimming and collapsing whitespace."""
    q = (q or "").strip()
    q = re.sub(r"\s+", " ", q)
    return q

def sha256_text(s: str) -> str:
    """Returns the SHA256 hex digest of a string."""
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def extract_hard_tokens(q: str) -> Tuple[str, ...]:
    """
    Extracts 'hard signals' (identifiers/numbers) from the query.
    Used as a lexical gate during semantic cache hits to prevent false positives.
    """
    tokens = [m.group(0) for m in _HARD_TOKEN_RE.finditer(q or "")]
    return tuple(sorted(set(tokens)))

def to_jsonable(x: Any) -> Any:
    """
    Recursively converts data to JSON-safe types (handles datetime, sets, numpy).
    Ensures disk persistence doesn't fail.
    """
    if x is None or isinstance(x, (str, int, float, bool)):
        return x
    if isinstance(x, (datetime.datetime, datetime.date)):
        return x.isoformat()
    if isinstance(x, (list, tuple)):
        return [to_jsonable(i) for i in x]
    if isinstance(x, set):
        return [to_jsonable(i) for i in sorted(list(x))]
    if isinstance(x, dict):
        return {str(k): to_jsonable(v) for k, v in x.items()}
    if hasattr(x, "tolist"):  # numpy handling
        try:
            return x.tolist()
        except Exception:
            pass
    # Fallback: convert to string to prevent crash
    return str(x)