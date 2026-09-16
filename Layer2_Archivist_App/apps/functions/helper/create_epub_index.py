# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Create Azure AI Search index for EPUB documents.

This script creates the 'epub-documents' search index with:
- Full-text search on content and metadata
- Vector search using text-embedding-3-large (3072 dimensions)
- Semantic search with prioritized fields
- Book-idx compatible schema for consistency with book_processing

Uses the shared Layer 2 Search credential helper: local API-key files are
supported when ``ENVIRONMENT=local`` and Azure deployments use managed identity.
The managed identity needs a control-plane role on the search service (for
example Search Service Contributor).

Usage:
    export AZURE_SEARCH_ENDPOINT=https://your-search.search.windows.net
    python -m helper.create_epub_index

    # Delete the index
    python -m helper.create_epub_index --delete --endpoint ...

    python -m helper.create_epub_index --help
"""

import os
import logging
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    SearchableField,
    SimpleField,
    SearchField,
    SearchFieldDataType,
    VectorSearch,
    HnswAlgorithmConfiguration,
    VectorSearchProfile,
    SearchIndex,
    SemanticSearch,
    SemanticConfiguration,
    SemanticPrioritizedFields,
    SemanticField,
)
from helper.credential import get_search_credential

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Constants
INDEX_NAME = 'epub-documents'
EMBEDDING_DIM = 3072  # text-embedding-3-large


def create_epub_search_index(
    endpoint: str | None = None,
    index_name: str = INDEX_NAME
) -> bool:
    """
    Create the EPUB search index in Azure AI Search.

    Args:
        endpoint: Azure Search endpoint (defaults to env var)
        index_name: Name of the index to create

    Returns:
        True if index created successfully, False otherwise
    """
    endpoint = endpoint or os.getenv("AZURE_SEARCH_ENDPOINT")

    if not endpoint:
        logger.error("Missing AZURE_SEARCH_ENDPOINT")
        return False

    try:
        index_client = SearchIndexClient(
            endpoint=endpoint,
            credential=get_search_credential(),
        )
        
        # Define fields (book-idx compatible schema from book_processing)
        # Reference: trpl-ai4g-lab/book_processing/08_create_book_index.py
        fields = [
            # Primary key
            SimpleField(
                name="id",
                type=SearchFieldDataType.String,
                key=True
            ),
            
            # Content for full-text + semantic search
            SearchableField(
                name="text",
                type=SearchFieldDataType.String
            ),
            
            # Vector field for vector search (3072 dims for text-embedding-3-large)
            SearchField(
                name="text_vector",
                type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
                vector_search_dimensions=EMBEDDING_DIM,
                vector_search_profile_name="epubHnswProfile"
            ),
            
            # Metadata fields (searchable) - matches book_processing
            SearchableField(
                name="book_title",
                type=SearchFieldDataType.String,
                filterable=True,
                sortable=True,
                facetable=True
            ),
            SearchableField(
                name="book_authors",
                type=SearchFieldDataType.String,
                filterable=True,
                sortable=True,
                facetable=True
            ),
            SearchableField(
                name="chapter_title",
                type=SearchFieldDataType.String,
                filterable=True,
                sortable=True,
                facetable=True
            ),
            
            # Metadata fields (not searchable) - matches book_processing
            SimpleField(
                name="book_publisher",
                type=SearchFieldDataType.String,
                filterable=True
            ),
            SimpleField(
                name="book_published_date",
                type=SearchFieldDataType.String,
                filterable=True
            ),
            SimpleField(
                name="book_isbn",
                type=SearchFieldDataType.String,
                filterable=True
            ),
            SimpleField(
                name="book_subjects",
                type=SearchFieldDataType.String
            ),
            
            # Debugging fields - matches book_processing
            SimpleField(
                name="chunk_name",
                type=SearchFieldDataType.String,
                filterable=True
            ),
            SimpleField(
                name="paragraph_ids",
                type=SearchFieldDataType.Collection(SearchFieldDataType.String)
            ),
            
            # Additional fields from book_processing reference (06_upload_to_cosmos.py)
            SimpleField(
                name="book_description",
                type=SearchFieldDataType.String
            ),
            SimpleField(
                name="book_language",
                type=SearchFieldDataType.String,
                filterable=True
            ),
            SimpleField(
                name="chapter_id",
                type=SearchFieldDataType.String,
                filterable=True
            ),
            SimpleField(
                name="token_count",
                type=SearchFieldDataType.Int32,
                filterable=True,
                sortable=True
            ),
            SimpleField(
                name="overlap_previous",
                type=SearchFieldDataType.Boolean,
                filterable=True
            ),
            SimpleField(
                name="overlap_next",
                type=SearchFieldDataType.Boolean,
                filterable=True
            ),
            SimpleField(
                name="chunk_order_within_chapter",
                type=SearchFieldDataType.Int32,
                filterable=True,
                sortable=True
            ),
            SimpleField(
                name="chunk_order_within_book",
                type=SearchFieldDataType.Int32,
                filterable=True,
                sortable=True
            ),
            
            # EPUB-specific fields (additional to book_processing schema)
            SimpleField(
                name="record_id",
                type=SearchFieldDataType.String,
                filterable=True
            ),
            SimpleField(
                name="blob_url",
                type=SearchFieldDataType.String
            ),
        ]
        
        # Vector search configuration (HNSW for ANN search)
        # Matches book_processing reference configuration
        vector_search = VectorSearch(
            algorithms=[
                HnswAlgorithmConfiguration(name="epubHnsw"),
            ],
            profiles=[
                VectorSearchProfile(
                    name="epubHnswProfile",
                    algorithm_configuration_name="epubHnsw",
                )
            ],
        )
        
        # Semantic search configuration
        # Matches book_processing reference: book-semantic-config
        semantic_search = SemanticSearch(
            configurations=[
                SemanticConfiguration(
                    name="epub-semantic-config",
                    prioritized_fields=SemanticPrioritizedFields(
                        content_fields=[
                            SemanticField(field_name="text")
                        ],
                        keywords_fields=[
                            SemanticField(field_name="book_title"),
                            SemanticField(field_name="book_authors"),
                            SemanticField(field_name="chapter_title"),
                        ],
                    ),
                )
            ]
        )
        
        # Create the index
        index = SearchIndex(
            name=index_name,
            fields=fields,
            vector_search=vector_search,
            semantic_search=semantic_search,
        )
        
        # Create or update the index
        result = index_client.create_or_update_index(index)
        logger.info("Index '%s' created/updated successfully.", result.name)
        logger.info("  - Fields: %d", len(result.fields))
        logger.info("  - Vector search profiles: %d", len(result.vector_search.profiles))
        logger.info("  - Semantic configurations: %d", len(result.semantic_search.configurations))
        
        return True
        
    except Exception as e:
        logger.exception("Failed to create index '%s': %s", index_name, str(e))
        return False


def delete_epub_search_index(
    endpoint: str | None = None,
    index_name: str = INDEX_NAME
) -> bool:
    """
    Delete the EPUB search index.

    Args:
        endpoint: Azure Search endpoint
        index_name: Name of the index to delete

    Returns:
        True if deleted successfully, False otherwise
    """
    endpoint = endpoint or os.getenv("AZURE_SEARCH_ENDPOINT")

    if not endpoint:
        logger.error("Missing AZURE_SEARCH_ENDPOINT")
        return False

    try:
        index_client = SearchIndexClient(
            endpoint=endpoint,
            credential=get_search_credential(),
        )
        index_client.delete_index(index_name)
        logger.info("Index '%s' deleted successfully.", index_name)
        return True
    except Exception as e:
        logger.exception("Failed to delete index '%s': %s", index_name, str(e))
        return False


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Create or delete the EPUB search index in Azure AI Search"
    )
    parser.add_argument(
        "--endpoint",
        type=str,
        default=os.getenv("AZURE_SEARCH_ENDPOINT"),
        help="Azure AI Search endpoint URL (or set AZURE_SEARCH_ENDPOINT env var)"
    )
    parser.add_argument(
        "--index-name",
        type=str,
        default=INDEX_NAME,
        help=f"Name of the index (default: {INDEX_NAME})"
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Delete the index instead of creating it"
    )

    args = parser.parse_args()

    if not args.endpoint:
        parser.error("--endpoint is required (or set AZURE_SEARCH_ENDPOINT)")

    if args.delete:
        delete_epub_search_index(
            endpoint=args.endpoint,
            index_name=args.index_name
        )
    else:
        create_epub_search_index(
            endpoint=args.endpoint,
            index_name=args.index_name
        )
