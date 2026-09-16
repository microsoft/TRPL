# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Utility module for interacting with Azure Cognitive Search documents.

Provides a SearchDocumentReader class that can retrieve individual documents
or all chunked documents associated with a base document ID.
"""

import logging
import re
from typing import Iterable, List, Optional, Set

from azure.search.documents import SearchClient
from azure.core.exceptions import HttpResponseError, ResourceNotFoundError, ServiceRequestError
from helper.credential import get_search_credential

logger = logging.getLogger(__name__)


def sanitize_document_id_for_search(doc_id: str) -> str:
    """
    Normalize Cosmos document id for Azure Search document keys.

    Must stay identical to ``helper.cosmos_ingest.sanitize_document_id`` (chunk ids use this).
    """
    if doc_id is None:
        return ""
    return re.sub(r"[^a-zA-Z0-9_\-=]", "_", str(doc_id))


def _odata_string_literal(value: str) -> str:
    """Escape a string for use inside an OData ``eq`` / ``ge`` literal."""
    return "'" + str(value).replace("'", "''") + "'"


class SearchDocumentReader:
    """
    Reader utility for Azure Cognitive Search documents.

    Encapsulates logic for connecting to a search index and retrieving
    documents or grouped chunks by base document ID.
    """

    def __init__(self, endpoint: str, index_name: str):
        """
        Initialize the SearchDocumentReader.

        Args:
            endpoint (str): The Azure Cognitive Search service endpoint.
            index_name (str): The name of the search index.
        """
        self.endpoint = endpoint
        self.index_name = index_name
        credential = get_search_credential()
        self.client = SearchClient(
            endpoint=endpoint,
            index_name=index_name,
            credential=credential
        )
        logger.debug("SearchDocumentReader connected to %s/%s", endpoint, index_name)

    def get_document_by_id(self, doc_id: str):
        """
        Retrieve a single document by its ID.

        Args:
            doc_id (str): The document ID.

        Returns:
            dict | None: The document as a dictionary with embedding replaced
            by a placeholder string, or None if not found or on error.
        """
        try:
            result = self.client.get_document(key=doc_id)
            embedding_length = len(result['embedding']) if result['embedding'] else 0
            result['embedding'] = f"<vector with {embedding_length} dimensions>"

            return dict(result)
        except ResourceNotFoundError as e:
            logger.debug("Document %s not found: %s", doc_id, str(e))
            return None
        except HttpResponseError as e:
            logger.warning("HTTP error retrieving document %s: %s", doc_id, str(e))
            return None

    def _paged_search_chunk_ids(self, filter_expr: str) -> List[str]:
        """Run filtered search (``*``) and collect ``id`` fields with paging."""
        chunk_ids: List[str] = []
        skip = 0
        page_size = 100
        while True:
            results = self.client.search(
                search_text="*",
                filter=filter_expr,
                top=page_size,
                skip=skip,
                include_total_count=False,
            )
            page_chunks = list(results)
            if not page_chunks:
                break
            for result in page_chunks:
                rid = result.get("id")
                if rid:
                    chunk_ids.append(str(rid))
            if len(page_chunks) < page_size:
                break
            skip += page_size
        return chunk_ids

    def _chunk_ids_for_id_prefix(self, prefix_base: str) -> List[str]:
        """Chunk rows whose key id starts with ``{prefix_base}_chunk_`` (lexicographic range)."""
        if not prefix_base:
            return []
        p = prefix_base + "_chunk_"
        # OData: compare id as string; '~' sorts above digits/letters used in ids
        filt = f"id ge {_odata_string_literal(p)} and id lt {_odata_string_literal(p + '~')}"
        try:
            return self._paged_search_chunk_ids(filt)
        except (ResourceNotFoundError, ServiceRequestError, HttpResponseError) as e:
            logger.warning("Chunk id-prefix search failed for base %r: %s", prefix_base, e)
            return []

    def _chunk_ids_for_record_id(self, record_id: str) -> List[str]:
        """Chunk rows indexed with ``record_id`` equal to the Cosmos document id (filterable)."""
        if not record_id:
            return []
        filt = f"record_id eq {_odata_string_literal(record_id)}"
        try:
            return self._paged_search_chunk_ids(filt)
        except (ResourceNotFoundError, ServiceRequestError, HttpResponseError) as e:
            logger.warning("Chunk record_id search failed for %r: %s", record_id, e)
            return []

    @staticmethod
    def _merge_unique_preserve_order(parts: Iterable[List[str]]) -> List[str]:
        seen: Set[str] = set()
        out: List[str] = []
        for lst in parts:
            for cid in lst:
                s = str(cid).strip()
                if s and s not in seen:
                    seen.add(s)
                    out.append(s)
        return out

    def get_all_chunks_for_document(self, base_doc_id: str) -> List[str]:
        """
        Retrieve all chunk IDs for a given Cosmos document id.

        Ingestion stores chunk keys as ``{sanitize_document_id(doc_id)}_chunk_{n}`` and sets
        ``record_id`` to the raw Cosmos id (see ``cosmos_ingest``). This method unions:

        - id-prefix range for the **raw** id (legacy / no-op when same as sanitized),
        - id-prefix range for the **sanitized** id (current ingest),
        - ``record_id eq '<raw id>'`` as a fallback when prefix range misses rows.

        Returns:
            Chunk id list sorted by trailing chunk index when parseable.
        """
        if not base_doc_id or not str(base_doc_id).strip():
            return []
        raw = str(base_doc_id).strip()
        safe = sanitize_document_id_for_search(raw)

        merged = self._merge_unique_preserve_order(
            [
                self._chunk_ids_for_id_prefix(raw),
                self._chunk_ids_for_id_prefix(safe) if safe != raw else [],
                self._chunk_ids_for_record_id(raw),
            ]
        )

        def get_chunk_number(chunk_id: str) -> int:
            try:
                if "_chunk_" not in chunk_id:
                    return 0
                return int(chunk_id.split("_chunk_")[-1])
            except (ValueError, IndexError):
                return 0

        merged.sort(key=get_chunk_number)
        if not merged:
            logger.warning(
                "Search index: no chunk rows found for Cosmos document id %r "
                "(tried id-prefix raw, id-prefix sanitized %r, record_id filter)",
                raw,
                safe,
            )
        else:
            logger.info(
                "Search index: found %d chunk row(s) for document %r",
                len(merged),
                raw,
            )
        return merged
