"""Text processing utilities for chunking and token counting using tiktoken."""

import logging
from typing import List

try:
    import tiktoken
    HAVE_TIKTOKEN = True
except ImportError:
    HAVE_TIKTOKEN = False

# Model used for tokenization (text-embedding-3-large)
EMBEDDING_MODEL = "text-embedding-3-large"

# Maximum tokens for text-embedding-3-large model
MAX_EMBEDDING_TOKENS = 8000

# Cache the tokenizer for reuse
_tokenizer = None


def _get_tokenizer():
    """Get or create a cached tiktoken tokenizer."""
    global _tokenizer
    if _tokenizer is None and HAVE_TIKTOKEN:
        _tokenizer = tiktoken.encoding_for_model(EMBEDDING_MODEL)
    return _tokenizer


class TextProcessingError(Exception):
    """Custom exception for text processing operations."""


def count_tokens(text: str) -> int:
    """Count tokens in text using tiktoken for accurate counting.
    
    Falls back to word-based approximation if tiktoken is not available.

    Args:
        text: The text to count tokens for

    Returns:
        int: Number of tokens
    """
    if not text:
        return 0

    tokenizer = _get_tokenizer()
    if tokenizer:
        return len(tokenizer.encode(text))

    # Fallback: approximate using word count * 1.3 (typical ratio)
    return int(len(text.split()) * 1.3)


def truncate_to_max_tokens(text: str, max_tokens: int = MAX_EMBEDDING_TOKENS) -> str:
    """
    Truncate text to a maximum number of tokens.
    
    Args:
        text: The text to truncate
        max_tokens: Maximum number of tokens allowed
        
    Returns:
        str: Truncated text (or original if within limits)
    """
    if not text:
        return text
    
    tokenizer = _get_tokenizer()
    if not tokenizer:
        # Fallback: rough word-based truncation
        words = text.split()
        max_words = int(max_tokens / 1.3)
        if len(words) > max_words:
            logging.warning(
                "Text too long (~%d tokens). Truncating to ~%d tokens (word-based fallback).",
                int(len(words) * 1.3), max_tokens
            )
            return ' '.join(words[:max_words])
        return text
    
    tokens = tokenizer.encode(text)
    if len(tokens) > max_tokens:
        logging.warning(
            "Text too long (%d tokens). Truncating to %d tokens.",
            len(tokens), max_tokens
        )
        tokens = tokens[:max_tokens]
        return tokenizer.decode(tokens)
    
    return text


def split_text(text: str, chunk_size_tokens: int,
               overlap_tokens: int) -> List[str]:
    """Split text into overlapping chunks based on accurate token count.

    Uses tiktoken for precise token-based chunking. Falls back to word-based
    approximation if tiktoken is not available.

    Args:
        text: Text to split
        chunk_size_tokens: Target size of each chunk in tokens
        overlap_tokens: Number of overlapping tokens between chunks

    Returns:
        List[str]: List of text chunks

    Raises:
        TextProcessingError: If splitting fails
    """
    if not text:
        return []

    try:
        tokenizer = _get_tokenizer()
        
        if tokenizer:
            # Use tiktoken for accurate token-based chunking
            return _split_text_with_tiktoken(text, chunk_size_tokens, overlap_tokens, tokenizer)
        
        # Fallback to word-based chunking
        return _split_text_with_words(text, chunk_size_tokens, overlap_tokens)

    except Exception as e:
        raise TextProcessingError(f"Failed to split text: {str(e)}") from e


def _split_text_with_tiktoken(text: str, chunk_size_tokens: int,
                               overlap_tokens: int, tokenizer) -> List[str]:
    """Split text using tiktoken for accurate token-based chunking."""
    tokens = tokenizer.encode(text)
    total_tokens = len(tokens)
    
    if total_tokens <= chunk_size_tokens:
        return [text]
    
    chunks = []
    start = 0
    step = max(1, chunk_size_tokens - overlap_tokens)
    
    while start < total_tokens:
        end = min(start + chunk_size_tokens, total_tokens)
        chunk_tokens = tokens[start:end]
        chunk_text = tokenizer.decode(chunk_tokens)
        chunks.append(chunk_text)
        
        if end >= total_tokens:
            break
            
        start += step
    
    return chunks


def _split_text_with_words(text: str, chunk_size_tokens: int,
                           overlap_tokens: int) -> List[str]:
    """Fallback: Split text using word-based approximation."""
    words = text.split()
    # Approximate: 1 token ≈ 0.75 words
    chunk_size_words = max(75, int(chunk_size_tokens * 0.75))
    overlap_words = max(15, int(overlap_tokens * 0.75))
    chunks = []
    i = 0
    n = len(words)

    while i < n:
        end = min(i + chunk_size_words, n)
        chunks.append(' '.join(words[i:end]))

        step = max(1, chunk_size_words - overlap_words)
        i += step

        if end == n:
            break

    return chunks
