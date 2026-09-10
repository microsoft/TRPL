# Layer 1 content-source adapter contract

This TRPL reference implementation uses a provider-neutral interface between
source collections and the Layer 1 ingestion pipeline. The interface documents
the contract used by the published TRPL showcase; no feature evolution is
planned, and security maintenance will preserve the documented behavior. It is
not a generic-platform compatibility claim.

Only the deterministic `synthetic` adapter is shipped. An operator integrating
an external source must implement the contract from that source's authoritative
documentation and review the implementation for security, privacy, rights, and
operational suitability.

## Contract

The Python definitions are in
`Layer1_Data_foundations/apps/functions/helper/content_source/contracts.py`.

| Type | Required behavior |
| --- | --- |
| `ContentRecordQuery` | Page size `1..500`, optional opaque cursor, optional collection IDs, and optional inclusive updated-from/updated-to datetimes |
| `OpaquePage[T]` | Ordered items, adapter-owned next cursor or `None`, and an optional total |
| `ContentRecord` | Stable record/source IDs, title, type, summary, collection ID, reserved/public record URL, publication flag, created/updated datetimes, metadata, rights, and asset IDs |
| `ContentAsset` | Stable asset/record IDs, filename, media type, sequence, metadata, and rights; no remote content URL |
| `AssetContent` | Adapter-returned filename, media type, and bytes |
| `ContentMetadata` | JSON-compatible values keyed by display name |
| `RightsMetadata` | Status, statement, license URL, credit line, and restrictions |
| `ContentSourceError` | Error code, safe message, retryability, and structured details |

`ContentSourceAdapter` provides `query_records`, `get_record`, `query_assets`,
`get_asset`, and `get_asset_content`. Cursors are opaque: callers pass them back
unchanged and must not derive offsets or provider requests from them.
Record ingestion stores the next cursor in Durable state and makes one adapter
call per page. Asset ingestion drains every cursor page before one record update;
cursor cycles, duplicate asset IDs, malformed cursors, and adapter failures are
structured errors rather than empty successful pages.
The legacy `parallel_batches` request/configuration field is accepted for caller
compatibility but normalized to `1`; opaque cursor chains cannot be fetched by
inventing parallel offsets.

The canonical mapper in `content_source/mapper.py` is the only mapping into the
downstream record shape. It preserves source identity and rights, orders assets
by `sequence`, and creates the derived gallery projection.

## Implementing an external adapter

1. Implement every `ContentSourceAdapter` method in operator-owned code.
2. Convert the authoritative provider payload into the typed contracts at the
   adapter boundary. Reject missing identity, metadata, rights, or content.
3. Translate provider failures to `ContentSourceError`; do not expose secrets,
   credentials, request headers, or private payloads in messages/details.
4. Return content bytes from `get_asset_content`. Do not return URLs for the
   pipeline to fetch, follow redirects, or access arbitrary hosts.
5. Treat pagination cursors as opaque and deterministic for the lifetime of a
   query. Apply collection and update-window filters consistently to every page.
   Process a page regardless of whether `total` is present, retain the returned
   cursor in Durable state, reject cursor cycles, and stop only at a `None` cursor.
6. Add the implementation through a reviewed, fixed factory entry in
   `content_source/registry.py`. Never import a module named by request data or
   configuration.
7. Add contract tests for pagination, filters, missing records/assets, malformed
   data, rights, structured errors, canonical mapping, and offline behavior.

The tracked template selects `CONTENT_SOURCE_ADAPTER=synthetic`. This repository
does not accept a module path, class name, endpoint, or credential for an
unshipped implementation.

## Synthetic reference flow

The canonical pack is
`Layer1_Data_foundations/apps/functions/helper/content_source/data/fictional-content-source-pack.json`.
It is inside the Azure Functions deployment root and has
three wholly invented records, people, institutions, identifiers, collections,
rights statements, and deterministic PNG generator parameters. The adapter emits
supported `.png` content bytes at runtime; no media files or SVG rasterizer are
shipped. Layer 3's
`public/data/home-gallery-artifacts.json` is a derived projection and must not be
edited independently.

Run the no-network path from the repository root:

```bash
python3 scripts/verify_offline_content_flow.py
```

The command queries records/assets/content through the synthetic adapter, maps
canonical records, blocks name resolution, and compares the gallery projection.

## Evidence and deliberate limits

| Inferred behavior | Repository evidence and coverage |
| --- | --- |
| Record query, collection/update filters, advisory total, and cursor paging | `function_app.py`; content-source pagination/filter/orchestration tests |
| Record identity, metadata, timestamps, and canonical storage | `content_source_client.py`; `content_source/mapper.py`; mapper tests |
| Asset listing, ordering, metadata, content bytes, and rights | asset pipeline stages in `function_app.py`; adapter asset/content tests |
| Structured retry/error categories | `contracts.py`; invalid-cursor and missing-asset tests |
| Downstream approval export | `apps/content-export-api/api/routes/records.py` and `tests/test_routes_records.py` |
| Offline gallery derivation | canonical sample pack, `verify_offline_content_flow.py`, frontend gallery tests, and public-verifier tests |

Repository evidence did not establish a safe public HTTP contract for the former
private integration. No guessed endpoint, credential, search/facet identifier,
payload compatibility layer, or provider-returned URL transport is included.

Existing private deployments must re-ingest through an explicitly implemented
adapter. No state migration is provided; any migration requires a separately
reviewed specification from the deployment owner.

Security fixes are reviewed on a best-effort basis. There is no support,
response-time, or operational SLA.
