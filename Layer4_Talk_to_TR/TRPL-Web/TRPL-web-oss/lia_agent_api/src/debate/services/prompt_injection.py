# -*- coding: utf-8 -*-
"""L1 pattern and L2 model-based prompt-injection detector.

Runs on each visitor input. In observe mode, detections are logged and
auto-filed. In enforce mode, callers can use the sanitized input and private
directive returned by this module.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from api.config import config
from debate.services.private_config import PrivateConfigError, load_private_json
from debate.services.shared import get_openai_client

logger = logging.getLogger(f"lia.{__name__}")

_REGEX_FLAGS = {
    "IGNORECASE": re.IGNORECASE,
    "MULTILINE": re.MULTILINE,
    "DOTALL": re.DOTALL,
    "VERBOSE": re.VERBOSE,
}


def _required_string(values: dict, key: str) -> str:
    value = values.get(key)
    if not isinstance(value, str) or not value.strip():
        raise PrivateConfigError(
            f"Prompt-injection private configuration requires {key!r}."
        )
    return value


def _required_string_list(values: dict, key: str) -> list[str]:
    value = values.get(key)
    if not isinstance(value, list) or not value:
        raise PrivateConfigError(
            f"Prompt-injection private configuration requires a non-empty {key!r} list."
        )
    result = [str(item).strip() for item in value if str(item).strip()]
    if not result:
        raise PrivateConfigError(
            f"Prompt-injection private configuration requires values in {key!r}."
        )
    return result


def _required_confidence(values: dict, key: str) -> float:
    try:
        confidence = float(values[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise PrivateConfigError(
            f"Prompt-injection private configuration requires numeric {key!r}."
        ) from exc
    if not 0.0 <= confidence <= 1.0:
        raise PrivateConfigError(
            f"Prompt-injection private configuration requires {key!r} "
            "between 0 and 1."
        )
    return confidence


def _compile_patterns(values: dict) -> list[tuple[str, re.Pattern]]:
    pattern_values = values.get("patterns")
    if not isinstance(pattern_values, list) or not pattern_values:
        raise PrivateConfigError(
            "Prompt-injection private configuration requires patterns."
        )

    compiled: list[tuple[str, re.Pattern]] = []
    for item in pattern_values:
        if not isinstance(item, dict):
            raise PrivateConfigError("Each private prompt-injection pattern must be an object.")
        category = _required_string(item, "category").lower()
        expression = _required_string(item, "expression")
        flag_names = item.get("flags", [])
        if not isinstance(flag_names, list):
            raise PrivateConfigError("Prompt-injection pattern flags must be a list.")
        flags = 0
        for flag_name in flag_names:
            try:
                flags |= _REGEX_FLAGS[str(flag_name).upper()]
            except KeyError as exc:
                raise PrivateConfigError(
                    f"Unsupported prompt-injection regex flag: {flag_name!r}."
                ) from exc
        try:
            compiled.append((category, re.compile(expression, flags)))
        except re.error as exc:
            raise PrivateConfigError(
                f"Invalid private prompt-injection expression for {category!r}."
            ) from exc
    return compiled


if config.prompt_injection_enabled:
    _PRIVATE_CONFIG = load_private_json(
        "LIA_PROMPT_INJECTION_CONFIG",
        "LIA_PROMPT_INJECTION_CONFIG_FILE",
        dict,
    )
    _CATEGORIES = frozenset(
        category.lower()
        for category in _required_string_list(_PRIVATE_CONFIG, "categories")
    )
    _CLEAN_CATEGORY = _required_string(_PRIVATE_CONFIG, "clean_category").lower()
    if _CLEAN_CATEGORY not in _CATEGORIES:
        raise PrivateConfigError(
            "Prompt-injection clean_category must be included in categories."
        )

    _L1_PATTERNS = _compile_patterns(_PRIVATE_CONFIG)
    for _pattern_category, _ in _L1_PATTERNS:
        if _pattern_category not in _CATEGORIES:
            raise PrivateConfigError(
                "Every private prompt-injection pattern category must be configured."
            )

    _L2_SYSTEM = _required_string(_PRIVATE_CONFIG, "classifier_prompt")
    _REDACTION_MARKER = _required_string(_PRIVATE_CONFIG, "redaction_marker")
    _L1_CONFIDENCE = _required_confidence(_PRIVATE_CONFIG, "l1_confidence")
    _L2_DEFAULT_CONFIDENCE = _required_confidence(
        _PRIVATE_CONFIG, "l2_default_confidence"
    )

    _directive_values = _PRIVATE_CONFIG.get("directives")
    if not isinstance(_directive_values, dict) or not _directive_values:
        raise PrivateConfigError(
            "Prompt-injection private configuration requires directives."
        )
    _DIRECTIVES = {
        str(category).lower(): str(directive)
        for category, directive in _directive_values.items()
        if str(directive).strip()
    }
    _DEFAULT_DIRECTIVE = _required_string(
        _PRIVATE_CONFIG, "default_directive"
    ).lower()
    if _DEFAULT_DIRECTIVE not in _DIRECTIVES:
        raise PrivateConfigError(
            "Prompt-injection default_directive must identify a configured directive."
        )

    _PROVIDER_FILTER_SIGNALS = tuple(
        signal.lower()
        for signal in _required_string_list(
            _PRIVATE_CONFIG, "provider_filter_signals"
        )
    )

    _provider_filter_result = _PRIVATE_CONFIG.get("provider_filter_result")
    if not isinstance(_provider_filter_result, dict):
        raise PrivateConfigError(
            "Prompt-injection private configuration requires provider_filter_result."
        )
    _PROVIDER_FILTER_CATEGORY = _required_string(
        _provider_filter_result, "category"
    ).lower()
    if _PROVIDER_FILTER_CATEGORY not in _CATEGORIES:
        raise PrivateConfigError(
            "Prompt-injection provider filter category must be configured."
        )
    _PROVIDER_FILTER_SANITIZED_TEXT = _required_string(
        _provider_filter_result, "sanitized_text"
    )
    _PROVIDER_FILTER_REASON = _required_string(_provider_filter_result, "reason")
    _PROVIDER_FILTER_LAYER = _required_string(_provider_filter_result, "layer")
    _PROVIDER_FILTER_CONFIDENCE = _required_confidence(
        _provider_filter_result, "confidence"
    )
else:
    _CATEGORIES = frozenset({"clean"})
    _CLEAN_CATEGORY = "clean"
    _L1_PATTERNS = []
    _L2_SYSTEM = ""
    _REDACTION_MARKER = ""
    _L1_CONFIDENCE = 0.0
    _L2_DEFAULT_CONFIDENCE = 0.0
    _DIRECTIVES = {"clean": ""}
    _DEFAULT_DIRECTIVE = "clean"
    _PROVIDER_FILTER_SIGNALS = ()
    _PROVIDER_FILTER_CATEGORY = "clean"
    _PROVIDER_FILTER_SANITIZED_TEXT = ""
    _PROVIDER_FILTER_REASON = ""
    _PROVIDER_FILTER_LAYER = ""
    _PROVIDER_FILTER_CONFIDENCE = 0.0


class DetectionResult:
    __slots__ = ("category", "sanitized_text", "confidence", "reason", "layer")

    def __init__(
        self,
        category: str,
        sanitized_text: str,
        confidence: float,
        reason: str,
        layer: str,
    ):
        self.category = (
            category if category in _CATEGORIES else _CLEAN_CATEGORY
        )
        self.sanitized_text = sanitized_text
        self.confidence = confidence
        self.reason = reason
        self.layer = layer

    def is_injection(self) -> bool:
        return self.category != _CLEAN_CATEGORY

    def to_dict(self) -> dict:
        return {
            "category": self.category,
            "sanitized_text": self.sanitized_text,
            "confidence": self.confidence,
            "reason": self.reason,
            "layer": self.layer,
        }


def _redact_match(text: str, match: re.Match) -> str:
    return text[: match.start()] + _REDACTION_MARKER + text[match.end() :]


def detect_l1(text: str) -> Optional[DetectionResult]:
    """Run the deployment-configured synchronous pattern prefilter."""
    for category, pattern in _L1_PATTERNS:
        match = pattern.search(text)
        if match:
            return DetectionResult(
                category=category,
                sanitized_text=_redact_match(text, match),
                confidence=_L1_CONFIDENCE,
                reason=f"L1 pattern matched: {match.group(0)!r}",
                layer="L1",
            )
    return None


async def detect_l2(text: str, timeout: float) -> Optional[DetectionResult]:
    """Call the deployment-configured model detector."""
    try:
        client = get_openai_client()
        response = await asyncio.wait_for(
            asyncio.to_thread(
                client.chat.completions.create,
                model=config.prompt_injection_model,
                messages=[
                    {"role": "system", "content": _L2_SYSTEM},
                    {"role": "user", "content": text},
                ],
                max_tokens=200,
                response_format={"type": "json_object"},
            ),
            timeout=timeout,
        )
        content = response.choices[0].message.content or "{}"
        data = json.loads(content)
        category = str(data.get("category", _CLEAN_CATEGORY)).lower()
        if category == _CLEAN_CATEGORY:
            return None
        return DetectionResult(
            category=category,
            sanitized_text=str(data.get("sanitized_text", text)),
            confidence=float(data.get("confidence", _L2_DEFAULT_CONFIDENCE)),
            reason=str(data.get("reason", "")),
            layer="L2",
        )
    except asyncio.TimeoutError:
        logger.warning("[PI] L2 timeout (%.1fs) on %r", timeout, text[:80])
        return None
    except (json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
        logger.warning("[PI] L2 parse error: %s on %r", exc, text[:80])
        return None
    except Exception as exc:  # noqa: BLE001
        message = str(exc).lower()
        if any(signal in message for signal in _PROVIDER_FILTER_SIGNALS):
            logger.warning("[PI] L2 provider filter flagged input on %r", text[:80])
            return DetectionResult(
                category=_PROVIDER_FILTER_CATEGORY,
                sanitized_text=_PROVIDER_FILTER_SANITIZED_TEXT,
                confidence=_PROVIDER_FILTER_CONFIDENCE,
                reason=_PROVIDER_FILTER_REASON,
                layer=_PROVIDER_FILTER_LAYER,
            )
        logger.error("[PI] L2 crashed: %s", exc, exc_info=True)
        return None


def get_directive(category: str) -> str:
    return _DIRECTIVES.get(category, _DIRECTIVES[_DEFAULT_DIRECTIVE])


REPORTS_DIR = Path(
    os.getenv("JAILBREAK_REPORTS_DIR", str(Path.home() / "jailbreak_reports"))
)


async def check_input(
    text: str,
    *,
    session_id: Optional[str] = None,
) -> Optional[DetectionResult]:
    """Run configured input detection and optionally report a detection."""
    if not config.prompt_injection_enabled:
        return None
    text = (text or "").strip()
    if not text:
        return None

    result: Optional[DetectionResult] = None

    if config.prompt_injection_use_l1:
        result = detect_l1(text)
        if result:
            logger.warning(
                "[PI] L1 caught %s (conf=%.2f) on %r",
                result.category,
                result.confidence,
                text[:120],
            )

    if result is None and config.prompt_injection_use_l2:
        result = await detect_l2(text, config.prompt_injection_l2_timeout)
        if result:
            logger.warning(
                "[PI] L2 caught %s (conf=%.2f) on %r - %s",
                result.category,
                result.confidence,
                text[:120],
                result.reason,
            )

    if result and config.prompt_injection_auto_report:
        try:
            _auto_report(text, result, session_id)
        except Exception as exc:  # noqa: BLE001
            logger.error("[PI] auto-report failed: %s", exc, exc_info=True)

    return result


def _auto_report(
    text: str,
    result: DetectionResult,
    session_id: Optional[str],
) -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.utcnow()
    report_id = f"{now.strftime('%Y-%m-%d_%H%M%S')}_auto_{uuid.uuid4().hex[:6]}"
    record = {
        "id": report_id,
        "submitted_at": now.isoformat(timespec="seconds") + "Z",
        "note": (
            f"[{result.layer}/{result.category} "
            f"conf={result.confidence:.2f}] {result.reason}"
        ),
        "transcript": [{"role": "user", "text": text}],
        "room_name": session_id,
        "tester_label": "[auto-detected]",
    }
    path = REPORTS_DIR / f"{report_id}.json"
    with path.open("w", encoding="utf-8") as report_file:
        json.dump(record, report_file, ensure_ascii=False, indent=2)
    logger.info("[PI] auto-report saved: %s", report_id)
