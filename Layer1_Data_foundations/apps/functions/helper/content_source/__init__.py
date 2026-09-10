"""Provider-neutral content-source contracts and the shipped synthetic adapter."""

from .contracts import (
    AssetContent,
    ContentAsset,
    ContentMetadata,
    ContentRecord,
    ContentRecordQuery,
    ContentSourceAdapter,
    ContentSourceError,
    ContentSourceErrorCode,
    JsonValue,
    OpaquePage,
    RightsMetadata,
)
from .mapper import (
    build_gallery_projection,
    map_asset_to_canonical,
    map_record_to_canonical,
)
from .pagination import (
    advance_opaque_cursor,
    advance_sync_orchestration,
    advance_sync_query_state,
    cursor_fingerprint,
    normalize_sync_query_state,
    retry_sync_failure_chain,
    sync_query_activity_params,
    sync_query_state_from_failure,
)
from .registry import get_content_source_adapter
from .synthetic_adapter import SyntheticContentSourceAdapter, generate_synthetic_png

__all__ = [
    "AssetContent",
    "ContentAsset",
    "ContentMetadata",
    "ContentRecord",
    "ContentRecordQuery",
    "ContentSourceAdapter",
    "ContentSourceError",
    "ContentSourceErrorCode",
    "JsonValue",
    "OpaquePage",
    "RightsMetadata",
    "SyntheticContentSourceAdapter",
    "advance_opaque_cursor",
    "advance_sync_orchestration",
    "advance_sync_query_state",
    "build_gallery_projection",
    "cursor_fingerprint",
    "get_content_source_adapter",
    "generate_synthetic_png",
    "map_asset_to_canonical",
    "map_record_to_canonical",
    "normalize_sync_query_state",
    "retry_sync_failure_chain",
    "sync_query_activity_params",
    "sync_query_state_from_failure",
]
