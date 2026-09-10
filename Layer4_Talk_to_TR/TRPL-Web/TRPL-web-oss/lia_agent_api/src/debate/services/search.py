import inspect
import logging

from azure.core.credentials import AzureKeyCredential
from openai import AsyncAzureOpenAI
from azure.search.documents.aio import SearchClient
from azure.search.documents.models import VectorizedQuery

from api.config import config


logger = logging.getLogger(f"lia.{__name__}")


_openai_client = None


def _parse_embedding_endpoint(endpoint: str) -> tuple[str, str]:
    """Extract base URL and api-version from full Azure deployment URL.

    Input:  https://xxx.cognitiveservices.azure.com/openai/deployments/model/embeddings?api-version=2023-05-15
    Output: ("https://xxx.cognitiveservices.azure.com", "2023-05-15")
    """
    base = endpoint.split("/openai/")[0] if "/openai/" in endpoint else endpoint
    api_version = ""
    if "api-version=" in endpoint:
        api_version = endpoint.split("api-version=")[-1].split("&")[0]
    return base, api_version


async def get_openai_client():
    global _openai_client
    if _openai_client is None:
        base, api_version = _parse_embedding_endpoint(
            config.search_archives_embedding_endpoint
        )
        _openai_client = AsyncAzureOpenAI(
            azure_endpoint=base,
            api_version=api_version
            or config.search_archives_embedding_api_version
            or "2023-05-15",
            api_key=config.search_archives_embedding_key,
        )
    return _openai_client


_search_client = None


async def get_search_client():
    global _search_client
    if _search_client is None:
        cred = AzureKeyCredential(config.search_archives_query_key)
        _search_client = SearchClient(
            endpoint=config.search_archives_url,
            index_name=config.search_archives_index_name,
            credential=cred,
        )
    return _search_client


async def close_search_clients():
    global _search_client, _openai_client
    await _close_client(_search_client)
    await _close_client(_openai_client)
    _search_client = None
    _openai_client = None


async def _close_client(client):
    if client is None:
        return
    close_fn = getattr(client, "close", None)
    if close_fn is None:
        return
    result = close_fn()
    if inspect.isawaitable(result):
        await result


class SearchService:

    async def search(self, query: str):
        embedding = await self._embed_text(query)
        search_client = await get_search_client()

        results = await search_client.search(
            search_text=query,
            search_fields=["record_ocr_text"],
            vector_queries=[
                VectorizedQuery(
                    vector=embedding,
                    fields="record_ocr_text_vector",
                )
            ],
            top=10,
            select=[
                "title",
                "description",
                "resource_type",
                "recipient",
                "creation_date",
                "creator",
                "production_method",
                "record_ocr_text",
            ],
        )

        return [result async for result in results]

    async def _embed_text(self, text: str):
        openai_client = await get_openai_client()
        response = await openai_client.embeddings.create(
            input=text,
            model=config.search_archives_embedding_model,
        )
        return response.data[0].embedding
