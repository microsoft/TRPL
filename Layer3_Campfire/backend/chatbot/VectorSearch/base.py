# -*- coding: utf-8 -*-
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
from azure.core.credentials import AzureKeyCredential
from azure.search.documents.aio import SearchClient
from azure.search.documents.models import QueryType, VectorizedQuery
from common_config import (
    AZURE_OPENAI_API_KEY,
    AZURE_OPENAI_BASE_URL,
    AZURE_SEARCH_MAX_RETRIES,
    AZURE_SEARCH_TIMEOUT,
    AZURE_SEARCH_VECTOR_FILTER_MODE,
    AZURE_SEARCH_VECTOR_K,
    AZURE_SEARCH_VECTOR_WEIGHT,
    ENABLE_KB_EMB_CACHE,
    ENABLE_KB_RESULT_CACHE,
    KB_CACHE_DIR,
    KB_EMB_CACHE_CAPACITY,
    KB_INDEX_VERSION,
    KB_RESULT_CACHE_CAPACITY,
    KB_RESULT_CACHE_FLUSH_EVERY_N,
    KB_RESULT_CACHE_FLUSH_INTERVAL_SEC,
    KB_RESULT_CACHE_THRESHOLD,
)
from openai import AsyncOpenAI
from pricing import CostAccumulator
from tenacity import AsyncRetrying, RetryError, stop_after_attempt, wait_exponential

from .cache import EmbeddingLRUCache, VectorizedLRUCache
from .utils import extract_hard_tokens, normalize_query, sha256_text

logger = logging.getLogger(__name__)


class _BaseKBSearchClient:
    """
    Universal Search Logic:
    1) Exact LRU Hit (Identical query + signature)
    2) Embedding (with embedding LRU)
    3) Semantic LRU Hit (Vector similarity within bucket + Hard token gate)
    4) Azure Search Hybrid (BM25 + Vector) + Optional Semantic
    5) Write back to cache
    """

    def __init__(
        self,
        *,
        endpoint: str,
        api_key: str,
        index_name: str,
        semantic_config_name: str | None,
        openai_model: str,
        search_fields: List[str],
        vector_field: str,
        date_filter: str | None = None,
    ) -> None:
        # Azure Search
        self.endpoint = endpoint
        self.index = index_name
        self.key = api_key

        # Semantic config
        self.semantic_config = semantic_config_name

        # OpenAI Embedding
        self.OPENAI_MODEL = openai_model

        # Field Config
        self.search_fields = search_fields
        self.vector_field = vector_field

        self.client = SearchClient(
            endpoint=self.endpoint,
            index_name=self.index,
            credential=AzureKeyCredential(self.key),
        )

        # OpenAI SDK Setup
        self.embedding_client = AsyncOpenAI(base_url=AZURE_OPENAI_BASE_URL, api_key=AZURE_OPENAI_API_KEY)

        # filter applied to every search call on this index 
        self._date_filter = date_filter

        # Industrial defaults (Override via ENV)
        self._vector_k_default = AZURE_SEARCH_VECTOR_K
        self._vector_weight = AZURE_SEARCH_VECTOR_WEIGHT
        self._vector_filter_mode = AZURE_SEARCH_VECTOR_FILTER_MODE
        self._timeout = AZURE_SEARCH_TIMEOUT
        self._max_retries = AZURE_SEARCH_MAX_RETRIES
        self._index_version = KB_INDEX_VERSION
        # LRU Cache Switches
        self._enable_emb_cache = ENABLE_KB_EMB_CACHE
        self._enable_result_cache = ENABLE_KB_RESULT_CACHE

        # Embedding LRU
        self._emb_cache = EmbeddingLRUCache(capacity=KB_EMB_CACHE_CAPACITY) if self._enable_emb_cache else None

        # Result LRU (Persisted per index)
        if self._enable_result_cache:
            cache_dir = Path(KB_CACHE_DIR)
            persist_path = cache_dir / f"{index_name}.json"

            self._result_cache = VectorizedLRUCache(
                dim=3072,
                capacity=KB_RESULT_CACHE_CAPACITY,
                threshold=KB_RESULT_CACHE_THRESHOLD,
                persist_path=str(persist_path),
                flush_interval_sec=KB_RESULT_CACHE_FLUSH_INTERVAL_SEC,
                flush_every_n=KB_RESULT_CACHE_FLUSH_EVERY_N,
            )
        else:
            self._result_cache = None

    async def print_document_count(self):
        try:
            return await self.client.get_document_count()
        except Exception as e:
            logger.warning("Failed to get document count (not affecting search): %s", e)

    async def get_document(self, document_id: str) -> Dict[str, Any]:
        """Fetch a single document by its key and return the mapped result."""
        raw = await self.client.get_document(key=document_id)
        return self._map_result(dict(raw))

    # ---- Subclass Responsibility: Map Azure doc to existing output structure
    def _map_result(self, r: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError

    async def _embed_query(
        self,
        query: str,
        cost: Optional[CostAccumulator] = None,
    ) -> List[float]:
        qn = normalize_query(query)
        if not qn:
            return []

        # Check embedding cache if enabled. Cache hits skip the API call, so
        # we deliberately do NOT charge the accumulator here.
        if self._emb_cache is not None:
            cached = self._emb_cache.get(qn)
            if cached is not None:
                return cached

        # Embedding Call (Fail fast, rely on fallback)
        resp = await self.embedding_client.embeddings.create(input=qn, model=self.OPENAI_MODEL)
        vec = resp.data[0].embedding
        vec32 = np.asarray(vec, dtype=np.float32).tolist()

        if cost is not None:
            usage = getattr(resp, "usage", None)
            tokens = getattr(usage, "prompt_tokens", None) or getattr(usage, "total_tokens", 0) or 0
            if tokens:
                cost.add_embedding(self.OPENAI_MODEL, tokens)

        # Store in cache if enabled
        if self._emb_cache is not None:
            self._emb_cache.set(qn, vec32)
        return vec32

    def _build_vector_queries(self, vec: List[float], k: int) -> Optional[List[VectorizedQuery]]:
        if not vec:
            return None
        vec32 = np.asarray(vec, dtype=np.float32).tolist()

        return [
            # azure-search-documents 11.6 renamed the candidate-count kwarg
            # from `k` to `k_nearest_neighbors`. The SDK silently swallows
            # the old name into **kwargs, then logs "k is not a known
            # attribute ... and will be ignored" during serialization — so
            # vector_k tuning was being dropped and Azure used its default.
            VectorizedQuery(
                vector=vec32,
                k_nearest_neighbors=k,
                fields=self.vector_field,
                weight=self._vector_weight,
                exhaustive=False,
            )
        ]

    async def _azure_search_with_retry(
        self,
        search_text: str,
        top_k: int,
        search_fields: list[str],
        vector_queries: list[VectorizedQuery] | None,
        query_type: QueryType,
        use_semantic: bool,
    ) -> Any:
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(self._max_retries + 1), wait=wait_exponential(multiplier=1, min=4, max=10)
            ):
                with attempt:
                    return await self.client.search(
                        search_text=search_text,
                        top=top_k,
                        search_fields=search_fields,
                        vector_queries=list(vector_queries) if vector_queries else None,
                        query_type=query_type,
                        semantic_configuration_name=self.semantic_config if use_semantic else None,
                        vector_filter_mode=self._vector_filter_mode,
                        filter=self._date_filter,
                        timeout=self._timeout,
                    )
        except RetryError as e:
            raise Exception(f"Azure Search failed after {self._max_retries} retries: {e}") from e

    def _make_bucket_id(
        self,
        *,
        search_fields: List[str],
        top_k: int,
        vector_k: int,
        use_semantic: bool,
    ) -> str:
        """
        Bucket: Constrains 'reusable semantic hits' to the same strategy space.
        Prevents cross-strategy or cross-version pollution.
        """
        raw = "|".join(
            [
                f"idx={self.index}",
                f"idxv={self._index_version}",
                f"fields={','.join(search_fields)}",
                f"top={top_k}",
                f"vk={vector_k}",
                f"vw={self._vector_weight}",
                f"sem={int(use_semantic)}",
                f"semcfg={self.semantic_config or ''}",
            ]
        )
        return sha256_text(raw)

    def _make_exact_key(
        self,
        *,
        query_norm: str,
        bucket_id: str,
    ) -> str:
        """Exact key: Hit only if strict query and bucket match."""
        return sha256_text(f"q={query_norm}|b={bucket_id}")

    # =========================
    # Search Interface (Signature invariant)
    # =========================
    async def search(
        self,
        query: str,
        *,
        top_k: int = 8,
        query_vec: Optional[List[float]] = None,
        cost: Optional[CostAccumulator] = None,
    ) -> List[Dict[str, Any]]:
        start_time = time.time()

        query_norm = normalize_query(query)
        hard_tokens = extract_hard_tokens(query_norm)

        # 1) Select fields
        search_fields = self.search_fields

        # 2) Semantic Toggle
        use_semantic = (self.semantic_config is not None) and bool(query_norm) and (query_norm != "*")
        logger.info(
            "Semantic search: %s (config=%s, query_norm=%s)",
            "ENABLED" if use_semantic else "DISABLED",
            self.semantic_config or "None",
            query_norm[:50] if query_norm else "None",
        )

        # 3) Vector Candidates
        vector_k = max(int(top_k), int(self._vector_k_default))
        if use_semantic:
            vector_k = max(vector_k, 50)

        # 4) Bucket & Keys
        bucket_id = self._make_bucket_id(
            search_fields=search_fields,
            top_k=int(top_k),
            vector_k=int(vector_k),
            use_semantic=use_semantic,
        )
        exact_key = self._make_exact_key(query_norm=query_norm or "*", bucket_id=bucket_id)

        # 5) L1: Exact cache
        if self._result_cache is not None:
            cached = self._result_cache.get(exact_key)
            if cached is not None:
                logger.debug("Exact cache hit: reason L1")
                logger.debug("Search total time: %.3f sec", time.time() - start_time)
                return cached

        # 6) Embedding (Use provided or generate)
        vec: List[float] = []
        if query_vec:
            vec = np.asarray(query_vec, dtype=np.float32).tolist()
        else:
            try:
                vec = await self._embed_query(query_norm, cost=cost)
            except Exception:
                # Fallback to pure BM25 if embedding fails
                vec = []

        # 7) L2: Semantic cache (Similarity + Hard Token Gate)
        if vec and self._result_cache is not None:
            hit = self._result_cache.semantic_lookup(
                vec,
                bucket_id=bucket_id,
                __hard_tokens__=hard_tokens,
            )
            if hit is not None:
                hit_key, _score, cached_hard_tokens = hit
                got = self._result_cache.get(hit_key)
                if got is not None:
                    logger.debug("Semantic cache hit: reason L2")
                    logger.debug("Search total time: %.3f sec", time.time() - start_time)
                    # Cache the exact_key for next time. Use the *cached*
                    # entry's hard_tokens (not the current query's) so the
                    # re-stored entry's gate reflects the coverage of the
                    # underlying data, not just this one query's identifiers.
                    self._result_cache.set_entry(
                        exact_key,
                        got,
                        vec,
                        bucket_id=bucket_id,
                        hard_tokens=cached_hard_tokens,
                    )
                    return got

        # 8) Real Azure Search Call
        vector_queries = self._build_vector_queries(vec, vector_k) if vec else None

        search_text = query_norm or "*"
        query_type = QueryType.SEMANTIC if use_semantic else QueryType.SIMPLE

        results = await self._azure_search_with_retry(
            search_text=search_text,
            top_k=int(top_k),
            search_fields=search_fields,
            vector_queries=vector_queries,
            query_type=query_type,
            use_semantic=use_semantic,
        )

        out: List[Dict[str, Any]] = []
        async for r in results:
            out.append(self._map_result(dict(r)))

        # 9) Write back to cache
        if self._result_cache is not None:
            if vec:
                self._result_cache.set_entry(
                    exact_key,
                    out,
                    vec,
                    bucket_id=bucket_id,
                    hard_tokens=hard_tokens,
                )
            else:
                # Cache even without vector (Exact match only)
                self._result_cache.set_entry(
                    exact_key,
                    out,
                    [0.0] * 3072,  # Placeholder, not used for semantic lookup
                    bucket_id=bucket_id,
                    hard_tokens=hard_tokens,
                )

        logger.debug("Azure Search executed: reason L3")
        logger.debug("Search total time: %.3f sec", time.time() - start_time)
        return out
