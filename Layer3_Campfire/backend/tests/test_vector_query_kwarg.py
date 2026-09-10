"""Regression test: VectorizedQuery's k must reach the SDK.

azure-search-documents 11.6 renamed the candidate-count kwarg from `k`
to `k_nearest_neighbors`. Passing the old name silently fell through to
**kwargs and was dropped during serialization, so the carefully-tuned
vector_k in base.py wasn't actually being applied. This test pins the
fix so a future SDK bump or careless refactor can't re-introduce it.
"""

from chatbot.VectorSearch.base import _BaseKBSearchClient


class _StubClient(_BaseKBSearchClient):
    def _map_result(self, r):  # type: ignore[override]
        return r


def _make_client() -> _StubClient:
    return _StubClient(
        endpoint="https://test.search.windows.net",
        api_key="k",
        index_name="letter-idx",
        semantic_config_name=None,
        openai_model="text-embedding-3-large",
        search_fields=["record_ocr_text"],
        vector_field="record_ocr_text_vector",
    )


def test_build_vector_queries_sets_k_nearest_neighbors():
    """The constructed VectorizedQuery must carry the k value on the
    attribute the SDK serializer recognizes. If this attribute is None,
    the SDK reverts to its server-side default and ignores vector_k."""
    client = _make_client()
    queries = client._build_vector_queries([0.1, 0.2, 0.3], k=42)
    assert queries is not None
    [query] = queries
    assert query.k_nearest_neighbors == 42, (
        "VectorizedQuery.k_nearest_neighbors must be set — the SDK drops "
        "any unrecognized kwarg (including the historical name `k=`) "
        "silently during serialization."
    )


def test_build_vector_queries_returns_none_for_empty_vec():
    """The empty-vector early return guards against unnecessary SDK calls
    when embedding generation fails and base.py falls back to BM25."""
    client = _make_client()
    assert client._build_vector_queries([], k=10) is None
