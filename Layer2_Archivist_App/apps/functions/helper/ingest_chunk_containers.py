# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Ingest chunk containers from Cosmos DB into Azure AI Search indexes.

Default mappings:
- record-chunks-test -> documents
- epub-chunks -> epub-documents

Uses DefaultAzureCredential for both Cosmos DB and Search.

Required environment variables:
- COSMOS_DB_ENDPOINT
- COSMOS_DB_DATABASE_NAME
- AZURE_SEARCH_ENDPOINT

Usage:
    python helper/ingest_chunk_containers.py
    python helper/ingest_chunk_containers.py --only records
    python helper/ingest_chunk_containers.py --batch-size 200 --limit 1000
    python helper/ingest_chunk_containers.py --dry-run
"""

import argparse
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Tuple

from azure.cosmos import CosmosClient
from azure.identity import AzureCliCredential, DefaultAzureCredential
from azure.search.documents import SearchClient


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


SCALAR_STRING = "string"
SCALAR_INT = "int"
SCALAR_BOOL = "bool"
ARRAY_STRING = "array_string"
ARRAY_FLOAT = "array_float"


DOCUMENTS_SCHEMA = {
    "id": SCALAR_STRING,
    "record_ocr_text": SCALAR_STRING,
    "record_ocr_text_vector": ARRAY_FLOAT,
    "creator": SCALAR_STRING,
    "recipient": SCALAR_STRING,
    "creation_date": SCALAR_STRING,
    "title": SCALAR_STRING,
    "description": SCALAR_STRING,
    "production_method": SCALAR_STRING,
    "resource_type": SCALAR_STRING,
    "collection": SCALAR_STRING,
    "repository": SCALAR_STRING,
    "period": SCALAR_STRING,
    "record_id": SCALAR_STRING,
    "source_record_id": SCALAR_STRING,
    "chunk_name": SCALAR_STRING,
    "token_count": SCALAR_INT,
    "chunk_order": SCALAR_INT,
    "citation": SCALAR_STRING,
    "copyright_notes": SCALAR_STRING,
    "portal_publish_date": SCALAR_STRING,
    "trc_url": SCALAR_STRING,
    "trpl_file_url": ARRAY_STRING,
    "selected_metadata_json": SCALAR_STRING,
    "ai_generated_fields": ARRAY_STRING,
}


EPUB_DOCUMENTS_SCHEMA = {
    "id": SCALAR_STRING,
    "text": SCALAR_STRING,
    "text_vector": ARRAY_FLOAT,
    "book_title": SCALAR_STRING,
    "book_authors": SCALAR_STRING,
    "chapter_title": SCALAR_STRING,
    "book_publisher": SCALAR_STRING,
    "book_published_date": SCALAR_STRING,
    "book_isbn": SCALAR_STRING,
    "book_subjects": SCALAR_STRING,
    "chunk_name": SCALAR_STRING,
    "paragraph_ids": ARRAY_STRING,
    "book_description": SCALAR_STRING,
    "book_language": SCALAR_STRING,
    "chapter_id": SCALAR_STRING,
    "token_count": SCALAR_INT,
    "overlap_previous": SCALAR_BOOL,
    "overlap_next": SCALAR_BOOL,
    "chunk_order_within_chapter": SCALAR_INT,
    "chunk_order_within_book": SCALAR_INT,
    "record_id": SCALAR_STRING,
    "blob_url": SCALAR_STRING,
}


@dataclass
class Mapping:
    """Maps a Cosmos DB source container to a target Azure AI Search index."""

    key: str
    source_container: str
    target_index: str
    schema: Dict[str, str]
    query: str


MAPPINGS = [
    Mapping(
        key="records",
        source_container="record-chunks-test",
        target_index="documents",
        schema=DOCUMENTS_SCHEMA,
        query="SELECT * FROM c WHERE IS_DEFINED(c.record_ocr_text_vector)",
    ),
    Mapping(
        key="epub",
        source_container="epub-chunks",
        target_index="epub-documents",
        schema=EPUB_DOCUMENTS_SCHEMA,
        query="SELECT * FROM c WHERE IS_DEFINED(c.text_vector)",
    ),
]


def _to_string(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(str(item) for item in value if item is not None)
    if isinstance(value, dict):
        for key in ("label", "value", "name", "text"):
            if key in value and value[key] is not None:
                return str(value[key])
    return str(value)


def _to_int(value: Any) -> int:
    if value is None or value == "":
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "y")
    if isinstance(value, (int, float)):
        return value != 0
    return bool(value)


def _to_array_string(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item is not None]
    if isinstance(value, str):
        return [value] if value else []
    return [str(value)]


def _to_array_float(value: Any) -> List[float]:
    if value is None:
        return []
    if not isinstance(value, list):
        return []

    out: List[float] = []
    for item in value:
        try:
            out.append(float(item))
        except (TypeError, ValueError):
            continue
    return out


def _sanitize_document(raw_doc: Dict[str, Any], schema: Dict[str, str]) -> Dict[str, Any]:
    doc: Dict[str, Any] = {}
    for field_name, field_type in schema.items():
        value = raw_doc.get(field_name)
        if field_type == SCALAR_STRING:
            doc[field_name] = _to_string(value)
        elif field_type == SCALAR_INT:
            doc[field_name] = _to_int(value)
        elif field_type == SCALAR_BOOL:
            doc[field_name] = _to_bool(value)
        elif field_type == ARRAY_STRING:
            doc[field_name] = _to_array_string(value)
        elif field_type == ARRAY_FLOAT:
            doc[field_name] = _to_array_float(value)

    return doc


def _iter_batches(items: Iterable[Dict[str, Any]], batch_size: int) -> Iterable[List[Dict[str, Any]]]:
    batch: List[Dict[str, Any]] = []
    for item in items:
        batch.append(item)
        if len(batch) >= batch_size:
            yield batch
            batch = []
    if batch:
        yield batch


def _ingest_mapping(
    cosmos_client: CosmosClient,
    credential: Any,
    search_endpoint: str,
    database_name: str,
    mapping: Mapping,
    batch_size: int,
    limit: int,
    dry_run: bool,
) -> Tuple[int, int, int]:
    db_client = cosmos_client.get_database_client(database_name)
    container_client = db_client.get_container_client(mapping.source_container)

    search_client = SearchClient(
        endpoint=search_endpoint,
        index_name=mapping.target_index,
        credential=credential,
    )

    logger.info(
        "Starting ingest: %s -> %s",
        mapping.source_container,
        mapping.target_index,
    )

    source_items = container_client.query_items(
        query=mapping.query,
        enable_cross_partition_query=True,
    )

    processed = 0
    uploaded = 0
    failed = 0
    clean_items: List[Dict[str, Any]] = []

    for raw_doc in source_items:
        if 0 < limit <= processed:
            break

        clean_doc = _sanitize_document(raw_doc, mapping.schema)
        if not clean_doc.get("id"):
            failed += 1
            continue

        clean_items.append(clean_doc)
        processed += 1

    logger.info("Prepared %d documents for index '%s'", processed, mapping.target_index)

    if dry_run:
        logger.info("Dry run enabled; no documents uploaded for '%s'", mapping.target_index)
        return processed, uploaded, failed

    for batch in _iter_batches(clean_items, batch_size):
        try:
            results = search_client.upload_documents(documents=batch)
            batch_success = sum(1 for result in results if getattr(result, "succeeded", False))
            batch_failed = len(batch) - batch_success

            uploaded += batch_success
            failed += batch_failed
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.exception(
                "Batch upload failed for index '%s' (batch size: %d): %s",
                mapping.target_index,
                len(batch),
                exc,
            )
            failed += len(batch)

    return processed, uploaded, failed


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest Cosmos chunk containers into Search indexes")
    parser.add_argument(
        "--credential",
        choices=["default", "cli"],
        default="default",
        help="Credential source: default (managed identity chain) or cli (az login user)",
    )
    parser.add_argument(
        "--only",
        choices=["all", "records", "epub"],
        default="all",
        help="Run only one mapping or both (default: all)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Search upload batch size (default: 100)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Optional max number of documents per mapping (0 = no limit)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Prepare and count documents without uploading",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()

    cosmos_endpoint = os.getenv("COSMOS_DB_ENDPOINT")
    cosmos_database_name = os.getenv("COSMOS_DB_DATABASE_NAME")
    search_endpoint = os.getenv("AZURE_SEARCH_ENDPOINT")

    missing = []
    if not cosmos_endpoint:
        missing.append("COSMOS_DB_ENDPOINT")
    if not cosmos_database_name:
        missing.append("COSMOS_DB_DATABASE_NAME")
    if not search_endpoint:
        missing.append("AZURE_SEARCH_ENDPOINT")

    if missing:
        logger.error("Missing required environment variables: %s", ", ".join(missing))
        return 1

    if args.credential == "cli":
        credential = AzureCliCredential()
    else:
        credential = DefaultAzureCredential()

    cosmos_client = CosmosClient(url=cosmos_endpoint, credential=credential)

    mappings_to_run = [
        mapping for mapping in MAPPINGS if args.only in ("all", mapping.key)
    ]

    total_processed = 0
    total_uploaded = 0
    total_failed = 0

    for mapping in mappings_to_run:
        processed, uploaded, failed = _ingest_mapping(
            cosmos_client=cosmos_client,
            credential=credential,
            search_endpoint=search_endpoint,
            database_name=cosmos_database_name,
            mapping=mapping,
            batch_size=args.batch_size,
            limit=args.limit,
            dry_run=args.dry_run,
        )
        total_processed += processed
        total_uploaded += uploaded
        total_failed += failed

        logger.info(
            "Completed %s: processed=%d uploaded=%d failed=%d",
            mapping.key,
            processed,
            uploaded,
            failed,
        )

    logger.info(
        "Done. total_processed=%d total_uploaded=%d total_failed=%d",
        total_processed,
        total_uploaded,
        total_failed,
    )

    return 0 if total_failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
