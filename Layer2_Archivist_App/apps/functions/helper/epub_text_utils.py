"""EPUB-specific text processing utilities.

Uses tiktoken for accurate token counting and spaCy for sentence-aware splitting.
Optimized for book content chunking and RAG retrieval.
"""

import logging
import re
from typing import List, Tuple, Dict, Any, Optional

logger = logging.getLogger(__name__)

# Lazy-loaded tokenizer and NLP model
_tokenizer = None
_nlp = None

# Constants for chunking (optimized for retrieval like book_processing)
SHORT_PARAGRAPH_TOKENS = 30      # Merge paragraphs below this limit (e.g., poems)
MAX_CHUNK_TOKENS = 462           # Target chunk size
MAX_ALLOWED_OVERFLOW = 50        # Overflow allowance to keep whole sentences
TARGET_CONSOLIDATION_TOKENS = 462  # Target for consolidated chunks
CONSOLIDATION_OVERFLOW = 25      # Overflow for consolidation


class EpubTextProcessingError(Exception):
    """Custom exception for EPUB text processing operations."""


def _get_tokenizer():
    """Get tiktoken tokenizer (lazy loaded)."""
    global _tokenizer  # pylint: disable=global-statement
    if _tokenizer is None:
        try:
            import tiktoken
            _tokenizer = tiktoken.encoding_for_model("text-embedding-3-large")
            logger.info("Loaded tiktoken tokenizer for text-embedding-3-large")
        except ImportError:
            logger.warning("tiktoken not available, falling back to word count")
            _tokenizer = "fallback"
    return _tokenizer


def _get_nlp():
    """Get spaCy NLP model (lazy loaded)."""
    global _nlp  # pylint: disable=global-statement
    if _nlp is None:
        try:
            import spacy
            try:
                _nlp = spacy.load("en_core_web_sm")
                logger.info("Loaded spaCy en_core_web_sm model")
            except OSError:
                # Model not installed, try to download it
                logger.info("Downloading spaCy en_core_web_sm model...")
                try:
                    from spacy.cli import download
                    download("en_core_web_sm")
                    _nlp = spacy.load("en_core_web_sm")
                except Exception as e:
                    logger.warning("Failed to download spaCy model: %s", e)
                    _nlp = "fallback"
        except ImportError:
            logger.warning("spaCy not available, falling back to simple sentence splitting")
            _nlp = "fallback"
    return _nlp


def count_tokens(text: str) -> int:
    """Count tokens in text using tiktoken (accurate) or word count (fallback).

    Args:
        text: The text to count tokens for

    Returns:
        int: Number of tokens
    """
    if not text:
        return 0

    tokenizer = _get_tokenizer()
    if tokenizer == "fallback":
        # Fallback to word count approximation
        return len(text.split())
    
    return len(tokenizer.encode(text))


def split_into_sentences(text: str) -> List[str]:
    """Split text into sentences using spaCy (accurate) or simple splitting (fallback).

    Args:
        text: Text to split into sentences

    Returns:
        List of sentences
    """
    if not text:
        return []

    nlp = _get_nlp()
    if nlp == "fallback":
        # Simple fallback: split on sentence-ending punctuation
        sentences = re.split(r'(?<=[.!?])\s+', text)
        return [s.strip() for s in sentences if s.strip()]
    
    doc = nlp(text)
    return [sent.text.strip() for sent in doc.sents if sent.text.strip()]


def first_sentence(text: str) -> str:
    """Get the first sentence of text."""
    sentences = split_into_sentences(text)
    return sentences[0] if sentences else ""


def last_sentence(text: str) -> str:
    """Get the last sentence of text."""
    sentences = split_into_sentences(text)
    return sentences[-1] if sentences else ""


# Illustration pattern (same as book_processing)
ILLUS_RE = re.compile(r"illustration\s+\d+\.\d+", re.IGNORECASE)


def is_illustration_para(text: str) -> bool:
    """Check if paragraph is an illustration reference."""
    return bool(ILLUS_RE.search(text))


def split_long_paragraph(
    paragraph_text: str,
    paragraph_index: int,
    chapter_id: str,
    chapter_title: str,
    headings: List[str],
    start_order_index: int,
    max_chunk_tokens: int = MAX_CHUNK_TOKENS,
    max_overflow: int = MAX_ALLOWED_OVERFLOW
) -> Tuple[List[Dict[str, Any]], int]:
    """Split a long paragraph into multiple chunks at sentence boundaries.
    
    Args:
        paragraph_text: The paragraph text to split
        paragraph_index: Index of the paragraph
        chapter_id: Chapter identifier
        chapter_title: Chapter title
        headings: List of headings for context
        start_order_index: Starting order index for chunks
        max_chunk_tokens: Maximum tokens per chunk
        max_overflow: Allowed overflow to keep sentences complete
        
    Returns:
        Tuple of (list of chunk dicts, next order index)
    """
    sentences = split_into_sentences(paragraph_text)
    chunks = []
    order_index = start_order_index
    current = []
    current_tokens = 0
    part_num = 1

    for sent in sentences:
        t = count_tokens(sent)

        if current:
            if current_tokens + t > max_chunk_tokens + max_overflow:
                chunk_text = " ".join(current)
                chunks.append({
                    "chunk_id": f"{chapter_id}_p{paragraph_index:04d}_part{part_num:02d}",
                    "chapter_id": chapter_id,
                    "chapter_title": chapter_title,
                    "paragraph_indices": [paragraph_index],
                    "headings": headings,
                    "text": chunk_text,
                    "token_count": current_tokens,
                    "order_index": order_index
                })
                order_index += 1
                part_num += 1
                current = [sent]
                current_tokens = t
                continue

        current.append(sent)
        current_tokens += t

    # Final chunk
    if current:
        chunk_text = " ".join(current)
        chunks.append({
            "chunk_id": f"{chapter_id}_p{paragraph_index:04d}_part{part_num:02d}",
            "chapter_id": chapter_id,
            "chapter_title": chapter_title,
            "paragraph_indices": [paragraph_index],
            "headings": headings,
            "text": chunk_text,
            "token_count": current_tokens,
            "order_index": order_index
        })
        order_index += 1

    return chunks, order_index


def chunk_paragraphs(
    paragraphs: List[Dict[str, Any]],
    chapter_id: str,
    chapter_title: str,
    max_chunk_tokens: int = MAX_CHUNK_TOKENS,
    short_paragraph_tokens: int = SHORT_PARAGRAPH_TOKENS
) -> List[Dict[str, Any]]:
    """Chunk paragraphs into reasonably sized pieces.
    
    Handles:
    - Splitting extremely long paragraphs
    - Merging very short ones (like poems)
    - Filtering illustration paragraphs
    
    Args:
        paragraphs: List of paragraph dicts with 'index', 'text', 'headings'
        chapter_id: Chapter identifier
        chapter_title: Chapter title
        max_chunk_tokens: Maximum tokens per chunk
        short_paragraph_tokens: Threshold for merging short paragraphs
        
    Returns:
        List of chunk dictionaries
    """
    if not paragraphs:
        return []

    # Filter illustration paragraphs
    paragraphs = [p for p in paragraphs if not is_illustration_para(p.get("text", ""))]
    
    if not paragraphs:
        return []

    chapter_chunks = []
    chapter_order_index = 0
    i = 0

    while i < len(paragraphs):
        p = paragraphs[i]
        p_idx = p.get("index", i)
        p_text = p.get("text", "")
        p_head = p.get("headings", [])
        p_tok = count_tokens(p_text)

        # Long paragraph - split it
        if p_tok > max_chunk_tokens:
            chunks, chapter_order_index = split_long_paragraph(
                p_text, p_idx, chapter_id, chapter_title,
                p_head, chapter_order_index, max_chunk_tokens
            )
            chapter_chunks.extend(chunks)
            i += 1
            continue

        # Merge short paragraphs (poem-style)
        merged = [p]
        total = p_tok
        j = i + 1

        while j < len(paragraphs):
            next_p = paragraphs[j]
            next_tokens = count_tokens(next_p.get("text", ""))

            if next_tokens < short_paragraph_tokens:
                merged.append(next_p)
                total += next_tokens
                j += 1
                continue
            break

        # Build chunk
        merged_text = "\n\n".join(x.get("text", "") for x in merged)
        indices = [x.get("index", 0) for x in merged]

        if len(indices) == 1:
            chunk_id = f"{chapter_id}_p{indices[0]:04d}"
        else:
            chunk_id = f"{chapter_id}_p{indices[0]:04d}-p{indices[-1]:04d}"

        chapter_chunks.append({
            "chunk_id": chunk_id,
            "chapter_id": chapter_id,
            "chapter_title": chapter_title,
            "paragraph_indices": indices,
            "headings": merged[0].get("headings", []),
            "text": merged_text,
            "token_count": total,
            "order_index": chapter_order_index
        })
        chapter_order_index += 1
        i = j

    return chapter_chunks


def consolidate_chunks(
    paragraph_chunks: List[Dict[str, Any]],
    target_tokens: int = TARGET_CONSOLIDATION_TOKENS,
    overflow: int = CONSOLIDATION_OVERFLOW
) -> List[Dict[str, Any]]:
    """Consolidate paragraph chunks into larger groups with sentence overlaps.
    
    This creates better semantic boundaries for retrieval by:
    - Grouping paragraphs to target ~462 tokens
    - Adding backward overlap (last sentence of previous group)
    - Adding forward overlap (first sentence of next group)
    
    Args:
        paragraph_chunks: List of paragraph chunk dicts from chunk_paragraphs()
        target_tokens: Target token count for consolidated chunks
        overflow: Allowed overflow when merging
        
    Returns:
        List of consolidated chunk dictionaries
    """
    if not paragraph_chunks:
        return []

    chapter_id = paragraph_chunks[0].get("chapter_id", "")
    chapter_title = paragraph_chunks[0].get("chapter_title", "")

    # Group paragraphs into larger chunks
    groups = []
    current_group = []
    current_tokens = 0

    for ch in paragraph_chunks:
        t = ch.get("token_count", 0)

        # If adding this paragraph exceeds target+overflow → start new group
        if current_group and (current_tokens + t > target_tokens + overflow):
            groups.append(current_group)
            current_group = []
            current_tokens = 0

        current_group.append(ch)
        current_tokens += t

    if current_group:
        groups.append(current_group)

    # Apply overlaps and build final chunks
    final_chunks = []
    chapter_order_index = 0

    for i, group in enumerate(groups):
        main_text = "\n\n".join(g.get("text", "") for g in group)

        # Backward overlap = last sentence of previous paragraph group
        backward_overlap = ""
        if i > 0:
            backward_overlap = last_sentence(groups[i - 1][-1].get("text", ""))

        # Forward overlap = first sentence of next paragraph group
        forward_overlap = ""
        if i < len(groups) - 1:
            forward_overlap = first_sentence(groups[i + 1][0].get("text", ""))

        # Assemble final merged text
        parts = []
        if backward_overlap:
            parts.append(backward_overlap)
        parts.append(main_text)
        if forward_overlap:
            parts.append(forward_overlap)

        merged = "\n\n".join(parts)

        # Collect all paragraph IDs in this group
        paragraph_ids = []
        for g in group:
            paragraph_ids.append(g.get("chunk_id", ""))

        final_chunks.append({
            "chunk_id": f"{chapter_id}_group_{i:04d}",
            "chapter_id": chapter_id,
            "chapter_title": chapter_title,
            "paragraph_ids": paragraph_ids,
            "text": merged,
            "token_count": count_tokens(merged),
            "overlap_previous": i > 0,
            "overlap_next": i < len(groups) - 1,
            "order_index": chapter_order_index
        })
        chapter_order_index += 1

    return final_chunks


def process_section_content(
    content: str,
    section_order: int,
    section_title: str,
    document_id: str,
    max_chunk_tokens: int = MAX_CHUNK_TOKENS,
    target_consolidation_tokens: int = TARGET_CONSOLIDATION_TOKENS
) -> List[Dict[str, Any]]:
    """Process section content into consolidated chunks with overlaps.
    
    This is the main entry point for EPUB section processing.
    Handles the full pipeline: paragraph extraction → chunking → consolidation.
    
    Args:
        content: Section text content
        section_order: Order index of the section
        section_title: Title of the section
        document_id: Document identifier
        max_chunk_tokens: Maximum tokens per paragraph chunk
        target_consolidation_tokens: Target tokens for consolidated chunks
        
    Returns:
        List of consolidated chunk dictionaries ready for indexing
    """
    if not content or not content.strip():
        return []

    # Split content into paragraphs (simple split on double newlines)
    raw_paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
    
    if not raw_paragraphs:
        # Try single newlines
        raw_paragraphs = [p.strip() for p in content.split("\n") if p.strip()]
    
    if not raw_paragraphs:
        return []

    # Build paragraph list with indices
    paragraphs = []
    for idx, para_text in enumerate(raw_paragraphs):
        if para_text and not is_illustration_para(para_text):
            paragraphs.append({
                "index": idx,
                "text": para_text,
                "headings": [section_title]
            })

    if not paragraphs:
        return []

    chapter_id = f"{document_id}_s{section_order}"

    # Step 1: Chunk paragraphs (split long, merge short)
    paragraph_chunks = chunk_paragraphs(
        paragraphs,
        chapter_id=chapter_id,
        chapter_title=section_title,
        max_chunk_tokens=max_chunk_tokens
    )

    if not paragraph_chunks:
        return []

    # Step 2: Consolidate chunks with sentence overlaps
    consolidated = consolidate_chunks(
        paragraph_chunks,
        target_tokens=target_consolidation_tokens
    )

    return consolidated


def create_search_document(
    chunk: Dict[str, Any],
    document_id: str,
    book_title: str,
    book_author: str,
    book_publisher: str,
    book_published_date: str,
    book_isbn: str,
    book_series: str,
    book_subjects: List[str],
    book_description: str,
    book_language: str,
    blob_url: str,
    chunk_order_within_book: int
) -> Dict[str, Any]:
    """Create a search document from a consolidated chunk.
    
    Matches the book_processing index schema for consistency.
    Reference: trpl-ai4g-lab/book_processing/06_upload_to_cosmos.py
    
    Args:
        chunk: Consolidated chunk dictionary
        document_id: Document identifier
        book_title: Book title
        book_author: Book author(s)
        book_publisher: Publisher name
        book_published_date: Publication date
        book_isbn: ISBN
        book_series: Series name
        book_subjects: List of subjects/categories
        book_description: Book description
        book_language: Book language
        blob_url: URL to the source file
        chunk_order_within_book: Global order index within the book

    Returns:
        Search document dictionary ready for indexing
    """
    chapter_id = chunk.get("chapter_id", "")
    chapter_title = chunk.get("chapter_title", "")
    
    # Clean text - no prepending (reference doesn't prepend)
    text = chunk.get("text", "")
    
    return {
        # Primary key (matches book-idx schema)
        "id": chunk.get("chunk_id", ""),
        
        # Content for full-text + semantic search
        "text": text,
        
        # Chunk identification
        "chunk_name": chunk.get("chunk_id", ""),
        
        # Book metadata fields (matches book_processing/06_upload_to_cosmos.py)
        "book_title": book_title,
        "book_authors": book_author,
        "book_publisher": book_publisher,
        "book_description": book_description,
        "book_language": book_language,
        "book_subjects": book_subjects if book_subjects else [],
        "book_published_date": book_published_date,
        "book_isbn": book_isbn,
        
        # Chapter/chunk tracking - matches book_processing
        "chapter_id": chapter_id,
        "chapter_title": chapter_title,
        "paragraph_ids": chunk.get("paragraph_ids", []),
        
        # Chunk metadata - matches book_processing (06_upload_to_cosmos.py)
        "token_count": chunk.get("token_count", 0),
        "overlap_previous": chunk.get("overlap_previous", False),
        "overlap_next": chunk.get("overlap_next", False),
        "chunk_order_within_chapter": chunk.get("order_index", 0),
        "chunk_order_within_book": chunk_order_within_book,
        
        # EPUB-specific metadata
        "record_id": document_id,
        "blob_url": blob_url
    }

