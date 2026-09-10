"""Fixed registry for shipped content-source adapters."""

from __future__ import annotations

import os
from typing import Callable

from .contracts import ContentSourceAdapter, ContentSourceError, ContentSourceErrorCode
from .synthetic_adapter import SyntheticContentSourceAdapter


_ADAPTER_FACTORIES: dict[str, Callable[[], ContentSourceAdapter]] = {
    "synthetic": SyntheticContentSourceAdapter,
}


def get_content_source_adapter(name: str | None = None) -> ContentSourceAdapter:
    """Resolve only explicitly shipped adapters; never import a caller-selected module."""

    selected = (name or os.getenv("CONTENT_SOURCE_ADAPTER") or "synthetic").strip().lower()
    factory = _ADAPTER_FACTORIES.get(selected)
    if factory is None:
        raise ContentSourceError(
            ContentSourceErrorCode.UNSUPPORTED_ADAPTER,
            "The configured content-source adapter is not shipped by this repository",
            details={"adapter": selected, "available": sorted(_ADAPTER_FACTORIES)},
        )
    return factory()
