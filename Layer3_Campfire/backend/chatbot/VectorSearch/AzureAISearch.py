import logging
from typing import Any

from chatbot.utils.blob import sign_full_blob_url_shared_key
from common_config import (
    AZURE_OPENAI_EMBEDDING_MODEL,
    AZURE_SEARCH_API_KEY,
    AZURE_SEARCH_BOOK_DATE_FROM,
    AZURE_SEARCH_BOOK_DATE_TO,
    AZURE_SEARCH_BOOK_INDEX,
    AZURE_SEARCH_BOOK_SEMANTIC_CONFIG,
    AZURE_SEARCH_ENDPOINT,
    AZURE_SEARCH_LETTER_DATE_FROM,
    AZURE_SEARCH_LETTER_DATE_TO,
    AZURE_SEARCH_LETTER_INDEX,
    AZURE_SEARCH_LETTER_SEMANTIC_CONFIG,
)

from .base import _BaseKBSearchClient

logger = logging.getLogger(__name__)


def _build_letter_date_filter(date_from: str | None, date_to: str | None) -> str | None:
    if not date_from and not date_to:
        return None
    parts = []
    if date_from:
        parts.append(f"portal_publish_date ge '{date_from}'")
    if date_to:
        parts.append(f"portal_publish_date le '{date_to}'")
    return " and ".join(parts)


def _build_book_date_filter(date_from: str | None, date_to: str | None) -> str | None:
    if not date_from and not date_to:
        return None
    parts = []
    if date_from:
        parts.append(f"book_published_date ge '{date_from}'")
    if date_to:
        parts.append(f"book_published_date le '{date_to}'")
    return " and ".join(parts)


class LetterSearchClient(_BaseKBSearchClient):
    def __init__(self):
        super().__init__(
            endpoint=AZURE_SEARCH_ENDPOINT,
            api_key=AZURE_SEARCH_API_KEY,
            index_name=AZURE_SEARCH_LETTER_INDEX,
            semantic_config_name=AZURE_SEARCH_LETTER_SEMANTIC_CONFIG,
            openai_model=AZURE_OPENAI_EMBEDDING_MODEL,
            search_fields=["record_ocr_text", "title", "creator", "recipient", "description"],
            vector_field="record_ocr_text_vector",
            date_filter=_build_letter_date_filter(AZURE_SEARCH_LETTER_DATE_FROM, AZURE_SEARCH_LETTER_DATE_TO),
        )

    def _map_result(self, r: dict[str, Any]) -> dict[str, Any]:
        urls = list(r.get("trpl_file_url") or [])
        signed_urls: list[str] = []
        for url in urls:
            raw = (url or "").strip()
            if not raw:
                continue
            try:
                signed = sign_full_blob_url_shared_key(raw)
                signed_urls.append(signed)
            except Exception:
                logger.warning("URL signing failed; using raw url. url=%r", raw, exc_info=True)
                signed_urls.append(raw)

        return {
            "id": r.get("id"),
            "source": "letter",
            "text": r.get("record_ocr_text"),
            "title": r.get("title"),
            "description": r.get("description"),
            "creator": r.get("creator"),
            "recipient": r.get("recipient"),
            "creation_date": r.get("creation_date"),
            "production_method": r.get("production_method"),
            "resource_type": r.get("resource_type"),
            "collection": r.get("collection"),
            "repository": r.get("repository"),
            "period": r.get("period"),
            "record_id": r.get("record_id"),
            "source_record_id": r.get("source_record_id"),
            "citation": r.get("citation"),
            "copyright_notes": r.get("copyright_notes"),
            "portal_publish_date": r.get("portal_publish_date"),
            "trc_url": r.get("trc_url"),
            "trpl_file_url": signed_urls,
            # Retrieval scores from Azure (kept internal — never sent to the
            # frontend). Surfaced here so the agent's tracer can roll them
            # into span attributes for the observability dashboard.
            "similarity_score": r.get("@search.score"),
            "reranker_score": r.get("@search.reranker_score"),
            # "selected_metadata_json": r.get("selected_metadata_json"),
            "ai_generated_fields": list(r.get("ai_generated_fields") or []),
        }

    def format_source_for_llm(self, src: dict[str, Any]) -> str:
        text = ""
        text += "Title: " + (src.get("title") or "").strip().replace("\n", " ") + "\n"
        text += (
            ("Description: " + (src.get("description") or "").strip().replace("\n", " ") + "\n")
            if src.get("description")
            else ""
        )
        text += (
            ("Creator: " + (src.get("creator") or "").strip().replace("\n", " ") + "\n") if src.get("creator") else ""
        )
        text += (
            ("Recipient: " + (src.get("recipient") or "").strip().replace("\n", " ") + "\n")
            if src.get("recipient")
            else ""
        )
        text += "Content: " + (src.get("text") or "").strip().replace("\n", " ") + "\n"
        return text


class BookSearchClient(_BaseKBSearchClient):
    def __init__(self):
        super().__init__(
            endpoint=AZURE_SEARCH_ENDPOINT,
            api_key=AZURE_SEARCH_API_KEY,
            index_name=AZURE_SEARCH_BOOK_INDEX,
            semantic_config_name=AZURE_SEARCH_BOOK_SEMANTIC_CONFIG or AZURE_SEARCH_LETTER_SEMANTIC_CONFIG,
            openai_model=AZURE_OPENAI_EMBEDDING_MODEL,
            # Only these fields are marked searchable in the epub-documents index.
            # Other fields (book_description, book_publisher, etc.) are retrievable but not searchable.
            # To add fields here, first update the index schema in Azure Portal.
            search_fields=["text", "book_title", "book_authors", "chapter_title"],
            vector_field="text_vector",
            date_filter=_build_book_date_filter(AZURE_SEARCH_BOOK_DATE_FROM, AZURE_SEARCH_BOOK_DATE_TO),
        )

    def _map_result(self, r: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": r.get("id"),
            "source": "book",
            "text": r.get("text"),
            "title": r.get("book_title"),
            "description": r.get("chapter_title"),
            "book_title": r.get("book_title"),
            "book_authors": r.get("book_authors"),
            "chapter_title": r.get("chapter_title"),
            "book_publisher": r.get("book_publisher"),
            "book_published_date": r.get("book_published_date"),
            "book_isbn": r.get("book_isbn"),
            "book_subjects": r.get("book_subjects"),
            "paragraph_ids": list(r.get("paragraph_ids") or []),
            "book_description": r.get("book_description"),
            "book_language": r.get("book_language"),
            "chapter_id": r.get("chapter_id"),
            "record_id": r.get("record_id"),
            "similarity_score": r.get("@search.score"),
            "reranker_score": r.get("@search.reranker_score"),
        }

    def format_source_for_llm(self, d: dict[str, Any]) -> str:
        text = ""
        text += "Title: " + (d.get("book_title") or "").strip().replace("\n", " ") + "\n"
        text += (
            ("Description: " + (d.get("book_description") or "").strip().replace("\n", " ") + "\n")
            if d.get("book_description")
            else ""
        )
        text += (
            ("Authors: " + ", ".join(d.get("book_authors") or []).strip().replace("\n", " ") + "\n")
            if d.get("book_authors")
            else ""
        )
        text += (
            ("Chapter: " + (d.get("chapter_title") or "").strip().replace("\n", " ") + "\n")
            if d.get("chapter_title")
            else ""
        )
        text += "Content: " + (d.get("text") or "").strip().replace("\n", " ") + "\n"
        return text


letter_search_client: LetterSearchClient = LetterSearchClient()
book_search_client: BookSearchClient = BookSearchClient()
