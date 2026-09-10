import logging
import inspect
from typing import List, Dict, Any, Optional

from azure.core.credentials import AzureKeyCredential
from azure.search.documents.aio import SearchClient
from azure.search.documents.models import VectorizedQuery
from openai import AsyncAzureOpenAI

from api.config import config

logger = logging.getLogger(f"lia.{__name__}")


class BookSearchClient:
    """Client for searching books in Azure Search."""

    def __init__(self):
        # ===== Azure Search =====
        self.endpoint = config.search_books_url
        self.index = config.search_books_index_name
        self.key = config.search_books_query_key

        # allow book-specific semantic config, otherwise use the default one
        self.semantic_config = config.search_books_semantic_config or None

        # ===== OpenAI Embedding =====
        self.OPENAI_API_ENDPOINT = config.search_books_embedding_endpoint
        self.OPENAI_API_KEY = config.search_books_embedding_key
        self.OPENAI_API_VERSION = config.search_books_embedding_api_version
        self.OPENAI_MODEL = config.search_books_embedding_model

        self.text_fields = [
            "text",
            "book_title",
            "book_authors",
            "chapter_title",
        ]
        self.vector_field = "text_vector"

        self.select_fields = [
            "id",
            "text",
            "book_title",
            "book_authors",
            "chapter_title",
            "book_publisher",
            "book_published_date",
            "book_isbn",
            "book_subjects",
            "chunk_name",
            "paragraph_ids",
        ]

        # Azure Search client
        self.client = SearchClient(
            endpoint=self.endpoint,
            index_name=self.index,
            credential=AzureKeyCredential(self.key),
        )

        # Embedding client — extract base URL from full deployment endpoint
        embed_base = self.OPENAI_API_ENDPOINT
        embed_api_version = self.OPENAI_API_VERSION or "2023-05-15"
        if "/openai/" in embed_base:
            if "api-version=" in embed_base:
                embed_api_version = embed_base.split("api-version=")[-1].split("&")[0]
            embed_base = embed_base.split("/openai/")[0]
        self.embedding_client = AsyncAzureOpenAI(
            azure_endpoint=embed_base,
            api_version=embed_api_version,
            api_key=self.OPENAI_API_KEY,
        )
        logger.info("Azure Book Search Client initialized")

    def _build_vector_queries(self, vec, k: int):
        """Build vector query for Azure Search."""
        if not vec:
            return None
        import numpy as np

        vec32 = np.asarray(vec, dtype=np.float32).tolist()
        return [
            VectorizedQuery(
                vector=vec32,
                k_nearest_neighbors=k,
                fields=self.vector_field,
            )
        ]

    async def search(
        self,
        query: str,
        *,
        top_k: int = 8,
        query_vec: Optional[list[float]] = None,
        search_in_title_desc: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Search for books.
        
        Args:
            query: Search query text
            top_k: Number of results to return
            query_vec: Pre-computed embedding vector (optional)
            search_in_title_desc: Whether to search in title/metadata (True) or just text (False)
            
        Returns:
            List of search results
        """
        # Determine search fields
        search_fields = self.text_fields if search_in_title_desc else ["text"]

        # Generate embedding if not provided
        if not query_vec:
            response = await self.embedding_client.embeddings.create(
                input=query,
                model=self.OPENAI_MODEL,
            )
            query_vec = response.data[0].embedding

        vector_queries = self._build_vector_queries(query_vec, top_k)
        use_semantic = self.semantic_config is not None

        results = await self.client.search(
            search_text=query or "*",
            top=top_k,
            search_fields=search_fields,
            vector_queries=vector_queries,
            query_type="semantic" if use_semantic else "simple",
            semantic_configuration_name=self.semantic_config if use_semantic else None,
            select=",".join(self.select_fields),
        )

        out: List[Dict[str, Any]] = []
        async for r in results:
            score = r.get("@search.score")
            item = {
                "id": r.get("id"),
                "source": "book",
                "score": score,
                # Content
                "text": r.get("text"),
                # Unified field names (matching LetterSearchClient)
                "title": r.get("book_title"),
                "description": r.get("chapter_title"),
                "metadata": {
                    "book_title": r.get("book_title"),
                    "book_authors": r.get("book_authors"),
                    "chapter_title": r.get("chapter_title"),
                    "book_publisher": r.get("book_publisher"),
                    "book_published_date": r.get("book_published_date"),
                    "book_isbn": r.get("book_isbn"),
                    "subjects": r.get("book_subjects"),
                    "chunk_name": r.get("chunk_name"),
                    "paragraph_ids": r.get("paragraph_ids"),
                },
            }
            out.append(item)

        # print("Get search amount:", len(out))
        return out

    async def close(self):
        await self._close_client(self.client)
        await self._close_client(self.embedding_client)

    async def _close_client(self, client):
        if client is None:
            return
        close_fn = getattr(client, "close", None)
        if close_fn is None:
            return
        result = close_fn()
        if inspect.isawaitable(result):
            await result
