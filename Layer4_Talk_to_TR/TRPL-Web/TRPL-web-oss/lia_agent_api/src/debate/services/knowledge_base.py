import asyncio
import logging
from typing import List, Dict, Any

from debate.services.search import SearchService
from debate.services.book_search import BookSearchClient

logger = logging.getLogger(f"lia.{__name__}")


class KnowledgeBaseService:
    """
    Unified knowledge base service that searches both letters and books.
    """

    def __init__(
        self,
        *,
        letter_search: SearchService | None = None,
        book_search: BookSearchClient | None = None,
    ):
        self.letter_search = letter_search or SearchService()
        self.book_search = book_search or BookSearchClient()
        logger.info("KnowledgeBaseService initialized")

    async def search(
        self,
        query: str,
        *,
        top_k: int = 5,
        search_letters: bool = True,
        search_books: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Search across letters and books.
        
        Args:
            query: Search query text
            top_k: Number of results to return per source
            search_letters: Whether to search letters
            search_books: Whether to search books
            
        Returns:
            Combined list of search results from both sources
        """
        results = []

        tasks: list[tuple[str, asyncio.Task]] = []
        # TODO: handle top_k for letters. Same for both? Separate param?
        if search_letters:
            tasks.append(("letters", asyncio.create_task(self.letter_search.search(query))))
        if search_books:
            tasks.append(("books", asyncio.create_task(self.book_search.search(query, top_k=top_k))))

        if tasks:
            task_results = await asyncio.gather(
                *(task for _, task in tasks),
                return_exceptions=True,
            )

            for (label, _), result in zip(tasks, task_results):
                if isinstance(result, Exception):
                    logger.error(f"Error searching {label}: {result}", exc_info=True)
                    continue
                if label == "letters":
                    letter_results = result
                    # Convert to unified format
                    for r in letter_results[:top_k]:
                        results.append({
                            "source": "letter",
                            "score": r.get("@search.score", 0),
                            "title": r.get("title", ""),
                            "description": r.get("description", ""),
                            "text": r.get("record_ocr_text", ""),
                            "metadata": {
                                "creation_date": r.get("creation_date"),
                                "creator": r.get("creator"),
                                "recipient": r.get("recipient"),
                                "resource_type": r.get("resource_type"),
                                "trc_url": r.get("trc_url"),
                            },
                        })
                    logger.info(f"Found {len(letter_results)} letter results for query: {query}")
                elif label == "books":
                    book_results = result
                    results.extend(book_results)
                    logger.info(f"Found {len(book_results)} book results for query: {query}")

        # Sort by score descending
        results.sort(key=lambda x: x.get("score", 0), reverse=True)

        return results

    def format_results_for_llm(self, results: List[Dict[str, Any]], max_results: int = 3) -> str:
        """
        Format search results for inclusion in LLM prompt.
        
        Args:
            results: Search results from search()
            max_results: Maximum number of results to include
            
        Returns:
            Formatted string for LLM context
        """
        if not results:
            return ""

        formatted = ["### Knowledge Base Context ###\n"]

        for i, result in enumerate(results[:max_results], 1):
            source = result.get("source", "unknown")
            title = result.get("title", "Unknown")
            text = result.get("text", "")[:1500]  # Limit text length

            formatted.append(f"\n[Source {i} - {source.upper()}]")
            formatted.append(f"Title: {title}")

            if source == "letter":
                metadata = result.get("metadata", {})
                if metadata.get("creation_date"):
                    formatted.append(f"Date: {metadata['creation_date']}")
                if metadata.get("recipient"):
                    formatted.append(f"To: {metadata['recipient']}")
            elif source == "book":
                metadata = result.get("metadata", {})
                if metadata.get("book_authors"):
                    formatted.append(f"Author: {metadata['book_authors']}")
                if metadata.get("chapter_title"):
                    formatted.append(f"Chapter: {metadata['chapter_title']}")

            formatted.append(f"Content: {text}...")
            formatted.append("")

        return "\n".join(formatted)
