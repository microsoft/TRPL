"""
Azure OpenAI Batch API helper for creating and managing batch jobs.

This helper provides functionality to:
- Create batch jobs from JSONL input files (local or Azure Blob Storage)
- Monitor batch job status
- Retrieve batch results (to local file or Azure Blob Storage)
- Handle batch job errors
"""

import os
import json
import time
import logging
import tempfile
import shutil
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    from openai import AzureOpenAI
    from openai.types.batch import Batch
except ImportError as e:
    logger.warning("OpenAI SDK not available: %s", e)
    # Define placeholder types for type checking
    AzureOpenAI = None  # type: ignore
    Batch = None  # type: ignore

try:
    from azure.core.exceptions import AzureError

    AZURE_STORAGE_AVAILABLE = True
except ImportError:
    AZURE_STORAGE_AVAILABLE = False
    logger.warning("azure-storage-blob not available. Blob storage features disabled.")

try:
    from .config import AzureStorageConfig
    from .storage_client import (
        get_blob_service_client,
        parse_configured_blob_url,
    )
    from .credential import get_azure_openai_auth_kwargs
except ImportError:
    # Fallback for when running as script
    AzureStorageConfig = None  # type: ignore
    get_blob_service_client = None  # type: ignore
    parse_configured_blob_url = None  # type: ignore
    get_azure_openai_auth_kwargs = None  # type: ignore


class BatchJobError(Exception):
    """Raised when batch job operations fail."""


class BatchValidationError(Exception):
    """Raised when batch input validation fails."""


@dataclass
class AzureOpenAIBatchConfig:
    """Configuration for Azure OpenAI batch operations.

    Authentication is selected centrally by
    :func:`helper.credential.get_azure_openai_auth_kwargs`.
    """

    endpoint: str
    api_version: str = "2024-10-01-preview"
    completion_window: str = "24h"  # Default completion window
    deployment_name: Optional[str] = None  # Deployment name for batch operations

    @classmethod
    def from_env(cls) -> "AzureOpenAIBatchConfig":
        """Create configuration from environment variables."""
        endpoint = os.getenv("AZURE_OPENAI_ENDPOINT") or os.getenv(
            "AZURE_AI_FOUNDRY_ENDPOINT"
        )

        if not endpoint:
            raise ValueError(
                "Missing required environment variable: "
                "AZURE_OPENAI_ENDPOINT (or AZURE_AI_FOUNDRY_ENDPOINT)"
            )

        return cls(
            endpoint=endpoint.rstrip("/"),
            api_version=os.getenv(
                "AZURE_OPENAI_API_VERSION", "2024-10-01-preview"
            ),
            completion_window=os.getenv("AZURE_OPENAI_BATCH_COMPLETION_WINDOW", "24h"),
            deployment_name=os.getenv("AZURE_OPENAI_BATCH_DEPLOYMENT_NAME"),
        )


class AzureOpenAIBatchClient:
    """Client for Azure OpenAI batch operations with Azure Storage support."""

    def __init__(
        self,
        config: Optional[AzureOpenAIBatchConfig] = None,
        storage_config: Optional[AzureStorageConfig] = None, # type: ignore
    ):
        """
        Initialize the Azure OpenAI batch client.

        Args:
            config: Azure OpenAI configuration object. If None, will load from environment.
            storage_config: Azure Storage configuration object. If None, will load from environment.
        """
        self.config = config or AzureOpenAIBatchConfig.from_env()

        self.client = AzureOpenAI(
            azure_endpoint=self.config.endpoint,
            api_version=self.config.api_version,
            **get_azure_openai_auth_kwargs(),
        )

        # Initialize Azure Storage client if available
        self.storage_config = storage_config
        self.blob_service_client = None
        if AZURE_STORAGE_AVAILABLE and AzureStorageConfig:
            try:
                self.storage_config = storage_config or AzureStorageConfig.from_env()
                if self.storage_config.storage_account_name or os.getenv(
                    "AZURE_STORAGE_CONNECTION_STRING"
                ):
                    self.blob_service_client = get_blob_service_client()
                    logger.info(
                        "Initialized Azure Blob Storage client with managed identity"
                    )
            except (AzureError, AttributeError, ValueError, TypeError) as e:
                logger.exception("Failed to initialize Azure Storage client: %s", e)

        logger.info(
            "Initialized Azure OpenAI Batch Client with endpoint: %s",
            self.config.endpoint,
        )

    def _is_blob_url(self, path: str) -> bool:
        """Check if a path is an Azure Blob Storage URL."""
        try:
            parse_configured_blob_url(path)
            return True
        except (RuntimeError, TypeError, ValueError) as e:
            logger.debug("Path is not a configured Blob URL %s: %s", path, e)
            return False

    def _download_from_blob(self, blob_url: str) -> str:
        """
        Download a file from Azure Blob Storage to a temporary local file.

        Args:
            blob_url: Azure Blob Storage URL

        Returns:
            Path to temporary local file
        """
        if not self.blob_service_client:
            raise BatchJobError(
                "Azure Storage client not initialized. Cannot download from blob storage."
            )

        try:
            container_name, blob_name = parse_configured_blob_url(blob_url)

            logger.info(
                "Downloading blob from container: %s, blob: %s", container_name, blob_name
            )

            # Get blob client
            blob_client = self.blob_service_client.get_blob_client(
                container=container_name, blob=blob_name
            )

            # Download to temporary file
            with tempfile.NamedTemporaryFile(
                mode="wb", delete=False, suffix=".jsonl"
            ) as temp_file:
                download_stream = blob_client.download_blob()
                temp_file.write(download_stream.readall())
                temp_path = temp_file.name

            logger.info("Downloaded blob to temporary file: %s", temp_path)
            return temp_path

        except AzureError as e:
            raise BatchJobError(f"Error downloading from blob storage: {e}") from e
        except Exception as e:
            raise BatchJobError(f"Unexpected error downloading from blob: {e}") from e

    def _upload_to_blob(self, local_file_path: str, blob_url: str) -> str:
        """
        Upload a local file to Azure Blob Storage.

        Args:
            local_file_path: Path to local file to upload
            blob_url: Target Azure Blob Storage URL

        Returns:
            URL of uploaded blob
        """
        if not self.blob_service_client:
            raise BatchJobError(
                "Azure Storage client not initialized. Cannot upload to blob storage."
            )

        try:
            container_name, blob_name = parse_configured_blob_url(blob_url)

            logger.info(
                "Uploading file to container: %s, blob: %s", container_name, blob_name
            )

            # Get blob client
            blob_client = self.blob_service_client.get_blob_client(
                container=container_name, blob=blob_name
            )

            # Upload file
            with open(local_file_path, "rb") as f:
                blob_client.upload_blob(f, overwrite=True)

            blob_url = str(blob_client.url)

            logger.info("Uploaded file to blob: %s", blob_url)
            return blob_url

        except AzureError as e:
            raise BatchJobError(f"Error uploading to blob storage: {e}") from e
        except Exception as e:
            raise BatchJobError(f"Unexpected error uploading to blob: {e}") from e

    def validate_jsonl_file(self, file_path: str) -> bool:
        """
        Validate that a file is a valid JSONL file.
        Supports both local files and Azure Blob Storage URLs.

        Args:
            file_path: Path to the JSONL file (local path or blob URL)

        Returns:
            True if valid, raises BatchValidationError if invalid
        """
        temp_file = None
        # Handle blob storage URLs
        if self._is_blob_url(file_path):
            try:
                temp_file = self._download_from_blob(file_path)
                file_path = temp_file
            except Exception as e:
                raise BatchValidationError(f"Error downloading blob for validation: {e}") from e

        try:
            if not os.path.exists(file_path):
                raise BatchValidationError(f"File not found: {file_path}")

            if not file_path.endswith(".jsonl"):
                raise BatchValidationError(
                    f"File must be in JSONL format (.jsonl), got: {file_path}"
                )

            with open(file_path, "r", encoding="utf-8") as f:
                line_count = 0
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue  # Skip empty lines
                    try:
                        json.loads(line)
                        line_count += 1
                    except json.JSONDecodeError as e:
                        raise BatchValidationError(
                            f"Invalid JSON on line {line_num}: {e}"
                        ) from e

                if line_count == 0:
                    raise BatchValidationError("JSONL file is empty")

                logger.info("Validated JSONL file with %d requests", line_count)
                return True

        except IOError as e:
            raise BatchValidationError(f"Error reading file: {e}") from e
        finally:
            # Clean up temporary file if it was downloaded from blob
            if temp_file and os.path.exists(temp_file):
                try:
                    os.unlink(temp_file)
                except OSError as e:
                    logger.warning("Failed to delete temp file %s: %s", temp_file, e)

    @staticmethod
    def split_requests_by_size(
        requests: List[Dict[str, Any]],
        max_size_mb: int = 100
    ) -> List[List[Dict[str, Any]]]:
        """
        Split batch requests into chunks based on estimated file size.
        Delegates to batch_utils.split_requests_by_size.
        """
        from .batch_utils import split_requests_by_size as _split_requests_by_size
        return _split_requests_by_size(requests, max_size_mb)

    def create_jsonl_from_requests(
        self, requests: List[Dict[str, Any]], output_path: str
    ) -> str:
        """
        Create a JSONL file from a list of request dictionaries.
        Supports both local files and Azure Blob Storage URLs.

        Args:
            requests: List of request dictionaries (each will be a line in JSONL)
            output_path: Path where the JSONL file should be created (local path or blob URL)

        Returns:
            Path or URL to the created JSONL file
        """
        # Check if output is a blob URL
        is_blob_output = self._is_blob_url(output_path)

        # Create temporary local file first
        with tempfile.NamedTemporaryFile(
            mode="w", delete=False, suffix=".jsonl", encoding="utf-8"
        ) as temp_file:
            temp_path = temp_file.name
            try:
                for request in requests:
                    json.dump(request, temp_file, ensure_ascii=False)
                    temp_file.write("\n")

                logger.info(
                    "Created JSONL file with %d requests", len(requests)
                )
            except IOError as e:
                raise BatchJobError(f"Error creating JSONL file: {e}") from e

        # Upload to blob storage if needed
        if is_blob_output:
            try:
                blob_url = self._upload_to_blob(temp_path, output_path)
                os.unlink(temp_path)  # Clean up temp file
                return blob_url
            except (OSError, ValueError, TypeError, AzureError) as e:
                if os.path.exists(temp_path):
                    try:
                        os.unlink(temp_path)
                    except OSError as cleanup_error:
                        logger.warning("Failed to delete temp file %s: %s", temp_path, cleanup_error)
                raise BatchJobError(f"Error uploading JSONL to blob storage: {e}") from e
        else:
            # Move temp file to final location
            final_path = Path(output_path)
            if final_path.suffix != ".jsonl":
                final_path = final_path.with_suffix(".jsonl")

            try:
                shutil.move(temp_path, str(final_path))
                return str(final_path)
            except (OSError, shutil.Error) as e:
                if os.path.exists(temp_path):
                    try:
                        os.unlink(temp_path)
                    except OSError as cleanup_error:
                        logger.warning("Failed to delete temp file %s: %s", temp_path, cleanup_error)
                raise BatchJobError(f"Error moving JSONL file: {e}") from e

    def upload_input_file(self, file_path: str, expires_after_seconds: Optional[int] = None, max_file_size_mb: int = 200) -> str:
        """
        Upload a JSONL input file to Azure OpenAI.
        Supports both local files and Azure Blob Storage URLs.

        Args:
            file_path: Path to the JSONL file to upload (local path or blob URL)
            expires_after_seconds: Optional expiration time in seconds (1,209,600 to 2,592,000 = 14-30 days).
                                   Defaults to 14 days (1,209,600 seconds) to avoid quota limits.
                                   Setting expiry increases file quota from 500 to 10,000 files.
                                   See: https://learn.microsoft.com/en-us/azure/ai-foundry/openai/how-to/batch
            max_file_size_mb: Maximum file size in MB (default: 200, Azure OpenAI limit)

        Returns:
            File ID of the uploaded file

        Raises:
            BatchValidationError: If file exceeds size limit
        """
        # Default to 14 days (1,209,600 seconds) if not specified
        # This allows up to 10,000 files instead of the default 500
        if expires_after_seconds is None:
            expires_after_seconds = 1_209_600  # 14 days in seconds

        # Handle blob storage URLs
        temp_file = None
        local_file_path = file_path

        if self._is_blob_url(file_path):
            logger.info("Downloading input file from blob storage: %s", file_path)
            temp_file = self._download_from_blob(file_path)
            local_file_path = temp_file

        # Check file size before validation (Azure OpenAI has 200MB limit)
        file_size_bytes = os.path.getsize(local_file_path)
        file_size_mb = file_size_bytes / (1024 * 1024)
        if file_size_mb > max_file_size_mb:
            if temp_file and os.path.exists(temp_file):
                os.unlink(temp_file)
            raise BatchValidationError(
                f"Batch file too large: {file_size_mb:.1f}MB (max: {max_file_size_mb}MB). "
                f"Try reducing the number of records per batch, using lower image quality (DPI), "
                f"or processing fewer pages per document."
            )

        logger.info("Batch file size: %.1fMB (limit: %dMB)", file_size_mb, max_file_size_mb)

        # Validate file first
        self.validate_jsonl_file(local_file_path)

        try:
            logger.info(
                "Uploading input file to Azure OpenAI: %s (expires after %d seconds / %.1f days)",
                local_file_path, expires_after_seconds, expires_after_seconds / 86400
            )
            with open(local_file_path, "rb") as f:
                # Use the documented format for expires_after parameter
                # Format: {"seconds": N, "anchor": "created_at"}
                # See: https://learn.microsoft.com/en-us/azure/ai-foundry/openai/how-to/batch
                file_response = self.client.files.create(
                    file=f,
                    purpose="batch",
                    extra_body={
                        "expires_after": {
                            "seconds": expires_after_seconds,
                            "anchor": "created_at"
                        }
                    }
                )
                file_id = file_response.id

            logger.info("Successfully uploaded file. File ID: %s", file_id)
            return file_id

        except (OSError, AzureError, ValueError, TypeError) as e:
            raise BatchJobError(f"Error uploading file: {e}") from e

        finally:
            # Clean up temporary file if it was downloaded from blob
            if temp_file and os.path.exists(temp_file):
                try:
                    os.unlink(temp_file)
                except OSError as e:
                    logger.warning("Failed to delete temp file %s: %s", temp_file, e)

    def _get_batch_endpoint(self, endpoint: Optional[str] = None) -> str:
        """
        Get the batch endpoint path, using deployment name if configured.

        Args:
            endpoint: Optional endpoint override. If None, constructs from deployment name.

        Returns:
            Endpoint path for batch operations
        """
        if endpoint:
            return endpoint

        # If deployment name is configured, use it in the endpoint path
        if self.config.deployment_name:
            return f"/openai/deployments/{self.config.deployment_name}/chat/completions"

        # Default endpoint
        return "/v1/chat/completions"

    def create_batch_job(
        self,
        input_file_id: str,
        endpoint: Optional[str] = None,
        completion_window: Optional[str] = None,
        metadata: Optional[Dict[str, str]] = None,
        output_expires_after_seconds: Optional[int] = None,
    ) -> str:
        """
        Create a batch job from an uploaded input file.

        Args:
            input_file_id: ID of the uploaded input file
            endpoint: API endpoint to use. If None, uses deployment name from config or default.
            completion_window: Completion window (e.g., "24h"). Defaults to config value.
            metadata: Optional metadata to attach to the batch job
            output_expires_after_seconds: Optional expiration time for output files in seconds.
                                          Defaults to 14 days (1,209,600 seconds).
                                          Setting expiry increases file quota from 500 to 10,000 files.
                                          See: https://learn.microsoft.com/en-us/azure/ai-foundry/openai/how-to/batch

        Returns:
            Batch job ID
        """
        completion_window = completion_window or self.config.completion_window
        batch_endpoint = self._get_batch_endpoint(endpoint)

        # Default to 14 days (1,209,600 seconds) if not specified
        if output_expires_after_seconds is None:
            output_expires_after_seconds = 1_209_600  # 14 days in seconds

        try:
            logger.info(
                "Creating batch job with file ID: %s, endpoint: %s, deployment: %s",
                input_file_id,
                batch_endpoint,
                self.config.deployment_name or "default",
            )

            batch_params = {
                "input_file_id": input_file_id,
                "endpoint": batch_endpoint,
                "completion_window": completion_window,
            }

            if metadata:
                batch_params["metadata"] = metadata

            # Add output file expiration to increase quota from 500 to 10,000 files
            # See: https://learn.microsoft.com/en-us/azure/ai-foundry/openai/how-to/batch
            batch_params["extra_body"] = {
                "output_expires_after": {
                    "seconds": output_expires_after_seconds,
                    "anchor": "created_at"
                }
            }

            batch_response = self.client.batches.create(**batch_params)
            batch_id = batch_response.id

            logger.info("Successfully created batch job. Batch ID: %s", batch_id)
            return batch_id

        except (AzureError, ValueError, TypeError) as e:
            raise BatchJobError(f"Error creating batch job: {e}") from e

    def get_batch_status(self, batch_id: str) -> Batch: # type: ignore
        """
        Get the current status of a batch job.

        Args:
            batch_id: ID of the batch job

        Returns:
            Batch object with current status
        """
        try:
            batch = self.client.batches.retrieve(batch_id=batch_id)
            return batch

        except (AzureError, ValueError, TypeError) as e:
            raise BatchJobError(f"Error retrieving batch status: {e}") from e

    def wait_for_batch_completion(
        self,
        batch_id: str,
        poll_interval: int = 30,
        timeout: Optional[int] = None,
    ) -> Batch: # type: ignore
        """
        Wait for a batch job to complete.

        Args:
            batch_id: ID of the batch job
            poll_interval: Seconds to wait between status checks (default: 30)
            timeout: Maximum seconds to wait (None for no timeout)

        Returns:
            Batch object with final status

        Raises:
            BatchJobError: If batch fails or times out
        """
        start_time = time.time()
        logger.info("Waiting for batch job %s to complete...", batch_id)

        while True:
            batch = self.get_batch_status(batch_id)
            status = batch.status

            logger.info(
                "Batch %s status: %s (request_counts: %s)",
                batch_id,
                status,
                batch.request_counts,
            )

            # Batch status is a string, compare directly
            # Possible values: "validating", "failed", "in_progress", "finalizing",
            # "completed", "expired", "cancelling", "cancelled"
            status_str = str(status) if hasattr(status, 'value') else status

            if status_str == "completed":
                logger.info("Batch job %s completed successfully", batch_id)
                return batch

            if status_str == "failed":
                error_message = getattr(batch, "errors", {}).get(
                    "data", "Unknown error"
                )
                raise BatchJobError(
                    f"Batch job {batch_id} failed: {error_message}"
                )

            if status_str == "cancelled":
                raise BatchJobError(f"Batch job {batch_id} was cancelled")

            if status_str == "expired":
                raise BatchJobError(f"Batch job {batch_id} expired")

            # Check timeout
            if timeout and (time.time() - start_time) > timeout:
                raise BatchJobError(
                    f"Batch job {batch_id} did not complete within {timeout} seconds"
                )

            # Wait before next check
            time.sleep(poll_interval)

    def retrieve_batch_results(self, batch_id: str, output_path: str) -> str:
        """
        Retrieve and save batch results to a file.
        Supports both local files and Azure Blob Storage URLs.

        Args:
            batch_id: ID of the completed batch job
            output_path: Path where results should be saved (local path or blob URL)

        Returns:
            Path or URL to the saved results file
        """
        # Check if output is a blob URL
        is_blob_output = self._is_blob_url(output_path)

        # Create a temporary local file first
        with tempfile.NamedTemporaryFile(mode="wb", delete=False, suffix=".jsonl") as temp_file:
            temp_path = temp_file.name

        try:
            logger.info("Retrieving results for batch %s...", batch_id)

            # ✅ Step 1: Retrieve batch metadata to get the output file ID
            batch = self.client.batches.retrieve(batch_id=batch_id)
            output_file_id = getattr(batch, "output_file_id", None)

            if not output_file_id:
                raise BatchJobError(f"No output file found for batch {batch_id}")

            # ✅ Step 2: Download the output file using the file API
            try:
                output_file = self.client.files.content(output_file_id)
            except (AzureError, ValueError, TypeError) as e:
                raise BatchJobError(f"Failed to download batch output file: {e}") from e

            # ✅ Step 3: Write results to a temporary file
            try:
                # Handle file-like object
                if hasattr(output_file, "read"):
                    with open(temp_path, "wb") as f:
                        f.write(output_file.read())
                elif hasattr(output_file, "iter_bytes"):
                    with open(temp_path, "wb") as f:
                        for chunk in output_file.iter_bytes():
                            f.write(chunk)
                elif isinstance(output_file, bytes):
                    with open(temp_path, "wb") as f:
                        f.write(output_file)
                else:
                    # Fallback if the SDK response changes
                    content = getattr(output_file, "content", output_file)
                    with open(temp_path, "wb") as f:
                        if isinstance(content, bytes):
                            f.write(content)
                        else:
                            f.write(str(content).encode("utf-8"))
            except (OSError, AttributeError, TypeError, ValueError) as e:
                raise BatchJobError(f"Error writing batch output to file: {e}") from e

            # ✅ Step 4: Upload to Blob Storage (if output_path is a blob URL)
            if is_blob_output:
                try:
                    blob_url = self._upload_to_blob(temp_path, output_path)
                    os.unlink(temp_path)  # cleanup
                    logger.info("Results saved to blob: %s", blob_url)
                    return blob_url
                except (AzureError, OSError, ValueError) as e:
                    if os.path.exists(temp_path):
                        try:
                            os.unlink(temp_path)
                        except OSError as cleanup_error:
                            logger.warning("Failed to delete temp file %s: %s", temp_path, cleanup_error)
                    raise BatchJobError(f"Error uploading results to blob storage: {e}") from e

            # ✅ Step 5: Move temp file to the specified local path
            else:
                final_path = Path(output_path)
                if final_path.suffix != ".jsonl":
                    final_path = final_path.with_suffix(".jsonl")

                try:
                    shutil.move(temp_path, str(final_path))
                    logger.info("Results saved to: %s", final_path)
                    return str(final_path)
                except (OSError, shutil.Error) as e:
                    if os.path.exists(temp_path):
                        try:
                            os.unlink(temp_path)
                        except OSError as cleanup_error:
                            logger.warning(
                                "Failed to delete temp file %s: %s",
                                temp_path, cleanup_error
                            )
                    raise BatchJobError(f"Error moving results file: {e}") from e

        except (AzureError, OSError, ValueError, TypeError) as e:
            # Ensure cleanup on any failure
            if os.path.exists(temp_path):
                try:
                    os.unlink(temp_path)
                except OSError:
                    logger.warning(
                        "Failed to delete temp file %s: %s",
                        temp_path, e
                    )
            raise BatchJobError(f"Error retrieving batch results: {e}") from e


    def cancel_batch_job(self, batch_id: str) -> Batch: # type: ignore
        """
        Cancel a batch job.

        Args:
            batch_id: ID of the batch job to cancel

        Returns:
            Updated batch object
        """
        try:
            logger.info("Cancelling batch job %s...", batch_id)
            batch = self.client.batches.cancel(batch_id=batch_id)
            logger.info("Batch job %s cancelled", batch_id)
            return batch

        except Exception as e:
            raise BatchJobError(f"Error cancelling batch job: {e}") from e

    def delete_file(self, file_id: str) -> bool:
        """
        Delete a file from Azure OpenAI.

        Args:
            file_id: ID of the file to delete

        Returns:
            True if deleted successfully
        """
        try:
            logger.info("Deleting file %s...", file_id)
            response = self.client.files.delete(file_id)
            deleted = getattr(response, "deleted", True)
            if deleted:
                logger.info("File %s deleted successfully", file_id)
            else:
                logger.warning("File %s deletion returned deleted=False", file_id)
            return deleted
        except Exception as e:
            logger.exception("Failed to delete file %s: %s", file_id, e)
            return False

    def delete_batch_files(self, batch_id: str) -> Dict[str, Any]:
        """
        Delete input and output files associated with a batch job.

        Args:
            batch_id: ID of the batch job

        Returns:
            Dictionary with deletion results for input_file and output_file
        """
        result = {
            "batch_id": batch_id,
            "input_file_deleted": False,
            "output_file_deleted": False,
            "error_file_deleted": False,
            "errors": []
        }

        try:
            # Get batch status to find file IDs
            batch = self.get_batch_status(batch_id)

            # Delete input file
            input_file_id = getattr(batch, "input_file_id", None)
            if input_file_id:
                try:
                    result["input_file_deleted"] = self.delete_file(input_file_id)
                except Exception as e:
                    logger.exception("Input file deletion failed for batch %s: %s", batch_id, e)
                    result["errors"].append(f"Input file deletion failed: {e}")

            # Delete output file
            output_file_id = getattr(batch, "output_file_id", None)
            if output_file_id:
                try:
                    result["output_file_deleted"] = self.delete_file(output_file_id)
                except Exception as e:
                    logger.exception("Output file deletion failed for batch %s: %s", batch_id, e)
                    result["errors"].append(f"Output file deletion failed: {e}")

            # Delete error file if exists
            error_file_id = getattr(batch, "error_file_id", None)
            if error_file_id:
                try:
                    result["error_file_deleted"] = self.delete_file(error_file_id)
                except Exception as e:
                    logger.exception("Error file deletion failed for batch %s: %s", batch_id, e)
                    result["errors"].append(f"Error file deletion failed: {e}")

            logger.info(
                "Batch %s file cleanup: input=%s, output=%s, error=%s",
                batch_id,
                result["input_file_deleted"],
                result["output_file_deleted"],
                result["error_file_deleted"]
            )

        except Exception as e:
            logger.exception("Error cleaning up batch files for %s: %s", batch_id, e)
            result["errors"].append(f"Batch retrieval failed: {e}")

        return result

    def create_and_process_batch(
        self,
        input_file_path: str,
        output_file_path: str,
        endpoint: Optional[str] = None,
        completion_window: Optional[str] = None,
        poll_interval: int = 30,
        timeout: Optional[int] = None,
        metadata: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        Complete workflow: upload file, create batch, wait for completion, retrieve results.
        Supports both local files and Azure Blob Storage URLs for input and output.

        Args:
            input_file_path: Path to JSONL input file (local path or blob URL)
            output_file_path: Path where results should be saved (local path or blob URL)
            endpoint: API endpoint to use. If None, uses deployment name from config or default.
            completion_window: Completion window for the batch
            poll_interval: Seconds between status checks
            timeout: Maximum seconds to wait for completion
            metadata: Optional metadata for the batch job

        Returns:
            Dictionary with batch_id, status, and output_file_path
        """
        try:
            # Step 1: Upload input file (handles blob URLs automatically)
            file_id = self.upload_input_file(input_file_path)

            # Step 2: Create batch job
            batch_id = self.create_batch_job(
                input_file_id=file_id,
                endpoint=endpoint,
                completion_window=completion_window,
                metadata=metadata,
            )

            # Step 3: Wait for completion
            batch = self.wait_for_batch_completion(
                batch_id=batch_id, poll_interval=poll_interval, timeout=timeout
            )

            # Step 4: Retrieve results (handles blob URLs automatically)
            result_path = self.retrieve_batch_results(batch_id, output_file_path)

            return {
                "batch_id": batch_id,
                "file_id": file_id,
                "status": batch.status,
                "request_counts": batch.request_counts,
                "output_file_path": result_path,
            }

        except Exception as e:
            logger.exception("Error in create_and_process_batch: %s", e)
            raise
