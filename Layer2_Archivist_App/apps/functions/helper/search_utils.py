# pylint: disable=too-many-arguments, too-many-positional-arguments

"""Utilities for working with Azure Cognitive Search."""
import logging
from typing import List, Dict, Any

from azure.search.documents import SearchClient
from azure.search.documents.models import IndexingResult
from azure.core.exceptions import HttpResponseError
from helper.search_document_util import SearchDocumentReader
from helper.credential import get_search_credential

class SearchError(Exception):
    """Custom exception for search operations."""


def _upload_batch_with_retry(
        client: SearchClient,
        batch: List[Dict[str, Any]],
        min_batch_size: int = 1) -> List[IndexingResult]:
    """Upload a batch of documents with automatic retry on payload too large errors.
    
    If the batch is too large, it will be split in half and retried recursively.
    
    Args:
        client: Azure Search client
        batch: List of documents to upload
        min_batch_size: Minimum batch size before giving up
        
    Returns:
        List[IndexingResult]: Results of indexing operations
        
    Raises:
        SearchError: If indexing fails even with single document batches
    """
    if not batch:
        return []
    
    try:
        result = client.upload_documents(documents=batch)
        logging.info("Indexed batch of %d documents", len(batch))
        return list(result)
    except (HttpResponseError, Exception) as e:
        error_str = str(e).lower()
        # Check for payload too large error
        if 'too large' in error_str or 'request entity too large' in error_str or '413' in error_str:
            if len(batch) <= min_batch_size:
                # Single document is too large - log and skip it
                logging.exception(
                    "Single document too large to index, skipping. Document ID: %s",
                    batch[0].get('id', 'unknown') if batch else 'unknown'
                )
                raise SearchError(
                    f"Document too large to index: {batch[0].get('id', 'unknown')}"
                ) from e
            
            # Split batch in half and retry
            mid = len(batch) // 2
            logging.warning(
                "Batch of %d documents too large, splitting into batches of %d and %d",
                len(batch), mid, len(batch) - mid
            )
            
            results = []
            results.extend(_upload_batch_with_retry(client, batch[:mid], min_batch_size))
            results.extend(_upload_batch_with_retry(client, batch[mid:], min_batch_size))
            return results
        else:
            # Re-raise other errors
            raise


def index_documents(
        document_id: str,
        endpoint: str,
        index_name: str,
        documents: List[Dict[str, Any]],
        batch_size: int = 100) -> List[IndexingResult]:
    """Index documents into Azure Cognitive Search (managed identity / DefaultAzureCredential).

    Args:
        document_id: The parent document ID (used to delete existing chunks)
        endpoint: Search service endpoint URL
        index_name: Name of the search index
        documents: List of documents to index
        batch_size: Maximum documents per batch (default reduced to 100 for safety)
        
    Returns:
        List[IndexingResult]: Results of indexing operations
        
    Raises:
        SearchError: If indexing fails
    """
    if not documents:
        return []

    try:
        credential = get_search_credential()
        client = SearchClient(endpoint=endpoint,
                            index_name=index_name,
                            credential=credential)

        doc_reader = SearchDocumentReader(endpoint, index_name)

        chunk_ids = doc_reader.get_all_chunks_for_document(
            base_doc_id = document_id
        )

        if chunk_ids:
            # Delete in smaller batches to avoid size issues
            delete_batch_size = 500
            for i in range(0, len(chunk_ids), delete_batch_size):
                delete_batch = [{"id": cid} for cid in chunk_ids[i:i + delete_batch_size]]
                client.delete_documents(documents=delete_batch)
                logging.info("Deleted batch of %d existing chunks", len(delete_batch))

        results = []
        for i in range(0, len(documents), batch_size):
            batch = documents[i:i + batch_size]
            try:
                batch_results = _upload_batch_with_retry(client, batch)
                results.extend(batch_results)
            except SearchError:
                # Re-raise SearchError as-is
                raise
            except Exception as e:
                logging.exception("Failed to index batch starting at %d: %s", i, str(e))
                raise SearchError(f"Failed to index batch: {str(e)}") from e

        return results

    except SearchError:
        # Re-raise SearchError without wrapping
        raise
    except Exception as e:
        raise SearchError(f"Failed to index documents: {str(e)}") from e
