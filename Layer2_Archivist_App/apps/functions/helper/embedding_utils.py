# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Utilities for generating embeddings using OpenAI or Azure OpenAI."""
# pylint:disable = no-member, line-too-long
import logging
import os
import time
from typing import List, Optional

try:
    from openai import AzureOpenAI, RateLimitError
    HAVE_OPENAI = True
except ImportError:
    HAVE_OPENAI = False
    RateLimitError = Exception  # type: ignore

# Import token utilities from text_utils to avoid duplication
from helper.text_utils import (
    count_tokens,
    truncate_to_max_tokens,
    MAX_EMBEDDING_TOKENS
)
from helper.credential import get_azure_openai_auth_kwargs

# Re-export for backward compatibility
__all__ = ['count_tokens', 'truncate_to_max_tokens', 'MAX_EMBEDDING_TOKENS',
           'EmbeddingError', 'generate_embedding', 'generate_embeddings_batch']


class EmbeddingError(Exception):
    """Custom exception for embedding operations."""


# Module-level client cache to avoid creating new connections per call
_client_cache = {}


def _get_client(
    azure_endpoint: str,
    api_version: str = "2024-02-01",
) -> "AzureOpenAI":
    """Get or create a cached Azure OpenAI client."""
    auth_kwargs = get_azure_openai_auth_kwargs()
    auth_mode = "api-key" if "api_key" in auth_kwargs else "aad"
    cache_key = f"{azure_endpoint}:{auth_mode}"
    if cache_key not in _client_cache:
        _client_cache[cache_key] = AzureOpenAI(
            azure_endpoint=azure_endpoint,
            api_version=api_version,
            **auth_kwargs,
        )
    return _client_cache[cache_key]


def _call_with_retry(
    func,
    max_retries: int = 5,
    base_delay: float = 1.0,
    max_delay: float = 60.0
):
    """
    Call a function with exponential backoff retry on rate limit errors.
    
    Args:
        func: Function to call
        max_retries: Maximum number of retry attempts
        base_delay: Initial delay in seconds
        max_delay: Maximum delay between retries
        
    Returns:
        Result from the function
        
    Raises:
        EmbeddingError: If all retries are exhausted
    """
    last_exception = None
    
    for attempt in range(max_retries + 1):
        try:
            return func()
        except Exception as e:  # pylint: disable=broad-exception-caught
            # Check if this is a rate limit error (either RateLimitError or string match)
            is_rate_limit = isinstance(e, RateLimitError)
            if not is_rate_limit:
                error_str = str(e).lower()
                is_rate_limit = "429" in error_str or "rate" in error_str or "too many" in error_str
            
            if not is_rate_limit:
                # Not a rate limit error - re-raise immediately
                raise
            
            last_exception = e
            if attempt == max_retries:
                break
            
            # Extract retry-after from headers if available, otherwise use exponential backoff
            retry_after = getattr(e, 'retry_after', None)
            if retry_after:
                delay = float(retry_after)
            else:
                delay = min(base_delay * (2 ** attempt), max_delay)
            
            logging.warning(
                "Rate limit hit (attempt %d/%d). Waiting %.1f seconds before retry...",
                attempt + 1, max_retries + 1, delay
            )
            time.sleep(delay)
    
    raise EmbeddingError(f"Rate limit exceeded after {max_retries + 1} attempts: {last_exception}")


def generate_embeddings_batch(
    texts: List[str],
    deployment_name: Optional[str] = None,
    azure_endpoint: Optional[str] = None,
    api_version: str = "2024-02-01",
    max_tokens: int = MAX_EMBEDDING_TOKENS,
    max_retries: int = 5
) -> List[List[float]]:
    """
    Generate embeddings for multiple texts in a SINGLE API call with retry logic.
    
    This is much more efficient than calling generate_embedding() multiple times.
    Azure OpenAI supports up to ~2048 texts per batch request.
    
    Args:
        texts: List of texts to generate embeddings for
        deployment_name: Deployment name for the embedding model
        azure_endpoint: Azure OpenAI endpoint (optional, uses env var if not provided)
        api_version: API version to use
        max_tokens: Maximum tokens per text (texts are truncated if exceeded)
        max_retries: Maximum retry attempts on rate limit errors
        
    Returns:
        List of embedding vectors in the same order as input texts
        
    Raises:
        EmbeddingError: If embedding generation fails
    """
    if not HAVE_OPENAI:
        raise EmbeddingError("openai package not installed. Install with: pip install openai")
    
    if not texts:
        return []
    
    try:
        # Truncate each text to max tokens
        truncated_texts = [truncate_to_max_tokens(t, max_tokens) for t in texts if t]
        
        if not truncated_texts:
            return []
        
        azure_endpoint = azure_endpoint or os.getenv('AZURE_OPENAI_ENDPOINT')
        if not azure_endpoint:
            raise EmbeddingError("No Azure endpoint configured. Set AZURE_OPENAI_ENDPOINT environment variable.")

        deployment_name = deployment_name or os.getenv(
            'AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME', 'text-embedding-3-large'
        )

        # Use cached client
        client = _get_client(azure_endpoint, api_version)
        
        def make_request():
            return client.embeddings.create(
                input=truncated_texts,
                model=deployment_name
            )
        
        # Call with retry logic for rate limits
        response = _call_with_retry(make_request, max_retries=max_retries)
        
        # Return embeddings sorted by index to maintain original order
        sorted_data = sorted(response.data, key=lambda x: x.index)
        return [item.embedding for item in sorted_data]
    
    except EmbeddingError:
        raise
    except Exception as e:
        raise EmbeddingError(f"Failed to generate batch embeddings: {str(e)}") from e


def generate_embedding(text: str,
                      deployment_name: Optional[str] = None,
                      azure_endpoint: Optional[str] = None,
                      api_version: str = "2024-02-01",
                      max_tokens: int = MAX_EMBEDDING_TOKENS) -> List[float]:
    """Generate embeddings for text using Azure OpenAI (managed identity / DefaultAzureCredential).

    Automatically truncates text to max_tokens if it exceeds the limit.
    For multiple texts, use generate_embeddings_batch() for better performance.

    Args:
        text: Text to generate embeddings for
        deployment_name: Deployment name for the embedding model
        azure_endpoint: Azure OpenAI endpoint (optional)
        api_version: API version to use (default: 2024-02-01)
        max_tokens: Maximum tokens for the embedding model (default: 8000)

    Returns:
        List[float]: The embedding vector

    Raises:
        EmbeddingError: If embedding generation fails
    """
    # Use batch function for single text (benefits from retry logic and client caching)
    result = generate_embeddings_batch(
        texts=[text],
        deployment_name=deployment_name,
        azure_endpoint=azure_endpoint,
        api_version=api_version,
        max_tokens=max_tokens
    )
    
    if not result:
        raise EmbeddingError("No embedding generated")
    
    return result[0]
