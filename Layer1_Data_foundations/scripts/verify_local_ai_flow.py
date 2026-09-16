#!/usr/bin/env python3
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Verify the complete local Layer 1 and Layer 2 flow with Azure AI Batch."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import sys
import time
from typing import Any
import urllib.error
import urllib.request


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_FILE = REPOSITORY_ROOT / "Layer1_Data_foundations" / ".env.local"
QUEUE_VERIFIER = (
    REPOSITORY_ROOT
    / "Layer2_Archivist_App"
    / "scripts"
    / "verify_local_queue_flow.py"
)
FUNCTIONS_URL = "http://127.0.0.1:7071"
LAYER2_API_URL = "http://127.0.0.1:8000/api/v1"
SYNTHETIC_RECORD_IDS = tuple(f"synthetic-record-{index:03d}" for index in range(1, 4))
TERMINAL_DURABLE_STATES = {"Completed", "Failed", "Terminated", "Canceled"}
FAILED_PROCESSING_STATES = {"error", "failed"}


@dataclass(frozen=True)
class Stage:
    name: str
    route: str
    batch_type: str
    batch_field: str
    processing_field: str
    pending_status: str


STAGES = (
    Stage(
        name="resource type",
        route="create-resource-type-batch",
        batch_type="resource_type",
        batch_field="resource_type_batch_status",
        processing_field="resource_type_processing_status",
        pending_status="resource_type",
    ),
    Stage(
        name="OCR",
        route="create-ocr-batch",
        batch_type="ocr",
        batch_field="ocr_batch_status",
        processing_field="ocr_processing_status",
        pending_status="ocr",
    ),
    Stage(
        name="metadata",
        route="create-metadata-batch",
        batch_type="metadata_extraction",
        batch_field="metadata_batch_status",
        processing_field="metadata_extraction_status",
        pending_status="metadata",
    ),
)


def _compose(env_file: Path, *arguments: str) -> list[str]:
    return [
        "docker",
        "compose",
        "--env-file",
        str(env_file),
        "--profile",
        "full-stack",
        *arguments,
    ]


def _run(
    command: list[str],
    *,
    input_text: str | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=REPOSITORY_ROOT,
        input=input_text,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"Command failed ({' '.join(command[:3])}): {detail}")
    return result


def _functions_json(
    env_file: Path,
    source: str,
    arguments: tuple[str, ...] = (),
    *,
    timeout: float = 60,
) -> Any:
    result = _run(
        _compose(
            env_file,
            "exec",
            "-T",
            "functions",
            "python",
            "-",
            *arguments,
        ),
        input_text=source,
        timeout=timeout,
    )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Functions container returned invalid JSON: {result.stdout!r}"
        ) from exc


def _get_json(url: str, timeout: float = 30) -> Any:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1000]
        raise RuntimeError(f"HTTP {exc.code} from {url}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach {url}: {exc.reason}") from exc


def _post_json(url: str, payload: dict[str, Any]) -> Any:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1000]
        raise RuntimeError(f"HTTP {exc.code} from {url}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach {url}: {exc.reason}") from exc


def _wait_for_url(url: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                if response.status < 400:
                    return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
        time.sleep(2)
    raise RuntimeError(f"Timed out waiting for {url}: {last_error}")


def _runtime_configuration(env_file: Path) -> dict[str, Any]:
    source = """
import json
import os
from pathlib import Path

pollers = (
    "ResourceTypeBatchStatusPoller",
    "OcrBatchStatusPoller",
    "MetadataBatchStatusPoller",
)
key_file = Path(os.environ.get("AZURE_OPENAI_API_KEY_FILE", ""))
print(json.dumps({
    "environment": os.environ.get("ENVIRONMENT"),
    "adapter": os.environ.get("CONTENT_SOURCE_ADAPTER"),
    "cosmos_endpoint": os.environ.get("COSMOS_ENDPOINT"),
    "endpoint_configured": bool(os.environ.get("AZURE_OPENAI_ENDPOINT")),
    "deployment": os.environ.get("AZURE_OPENAI_BATCH_DEPLOYMENT_NAME"),
    "model": os.environ.get("AZURE_OPENAI_MODEL_NAME"),
    "api_version": os.environ.get("AZURE_OPENAI_API_VERSION"),
    "secret_ready": key_file.is_file() and key_file.stat().st_size > 0,
    "disabled_pollers": [
        name for name in pollers
        if os.environ.get(f"AzureWebJobs.{name}.Disabled", "").lower() == "true"
    ],
}))
"""
    return _functions_json(env_file, source)


def _validate_runtime(configuration: dict[str, Any]) -> None:
    if configuration.get("environment") != "local":
        raise RuntimeError("The Functions container must use ENVIRONMENT=local")
    if configuration.get("adapter") != "synthetic":
        raise RuntimeError("The Functions container must use the synthetic adapter")
    endpoint = str(configuration.get("cosmos_endpoint") or "")
    if endpoint not in {"http://cosmos:8081/", "http://127.0.0.1:8081/"}:
        raise RuntimeError(
            "Refusing to reset records outside the local Cosmos emulator"
        )
    if not configuration.get("endpoint_configured"):
        raise RuntimeError("AZURE_OPENAI_ENDPOINT is not configured")
    if not configuration.get("deployment"):
        raise RuntimeError("AZURE_OPENAI_BATCH_DEPLOYMENT_NAME is not configured")
    if not configuration.get("model"):
        raise RuntimeError("AZURE_OPENAI_MODEL_NAME is not configured")
    if not configuration.get("secret_ready"):
        raise RuntimeError("The mounted Azure OpenAI API key is missing or empty")
    disabled = configuration.get("disabled_pollers") or []
    if disabled:
        raise RuntimeError(
            "AI status pollers are disabled: "
            + ", ".join(str(item) for item in disabled)
        )


def _wait_for_durable(
    status_url: str,
    name: str,
    timeout: float,
    poll_interval: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    previous_state = ""
    while time.monotonic() < deadline:
        status = _get_json(status_url)
        state = str(status.get("runtimeStatus") or "")
        if state != previous_state:
            print(f"{name} orchestration: {state or 'unknown'}", flush=True)
            previous_state = state
        if state in TERMINAL_DURABLE_STATES:
            if state != "Completed":
                raise RuntimeError(
                    f"{name} orchestration ended in {state}: {status.get('output')}"
                )
            output = status.get("output")
            if isinstance(output, dict):
                if output.get("status") not in {None, "success"}:
                    raise RuntimeError(f"{name} orchestration failed: {output}")
                if output.get("total_failed_so_far", 0):
                    raise RuntimeError(
                        f"{name} orchestration reported failures: {output}"
                    )
            return status
        time.sleep(poll_interval)
    raise RuntimeError(f"Timed out waiting for the {name} orchestration")


def _start_orchestration(
    route: str,
    name: str,
    payload: dict[str, Any],
    timeout: float,
    poll_interval: float,
) -> dict[str, Any]:
    started = _post_json(f"{FUNCTIONS_URL}/api/{route}", payload)
    status_url = started.get("statusQueryGetUri")
    if not status_url:
        raise RuntimeError(f"{name} did not return statusQueryGetUri")
    print(f"Started {name} orchestration {started.get('id', 'unknown')}.")
    return _wait_for_durable(status_url, name, timeout, poll_interval)


def _reset_synthetic_records(env_file: Path) -> list[str]:
    source = """
import json
import os
import sys
from azure.cosmos import CosmosClient

record_ids = sys.argv[1:]
client = CosmosClient.from_connection_string(os.environ["COSMOS_CONNECTION_STRING"])
container = client.get_database_client(
    os.environ.get("COSMOS_DATABASE_NAME", "contentdb")
).get_container_client(
    os.environ.get("COSMOS_CONTAINER_NAME", "recordsmetadata")
)
reset = []
for record_id in record_ids:
    item = container.read_item(record_id, partition_key=record_id)
    if item.get("id") != record_id or not record_id.startswith("synthetic-record-"):
        raise RuntimeError(f"Refusing to reset unexpected record {record_id}")
    item.pop("resource_type", None)
    item.pop("extracted_metadata", None)
    item.pop("metadata_extraction_confidence", None)
    for key in list(item):
        if (
            key.startswith(("resource_type_", "ocr_", "metadata_"))
            and key != "metadata_key"
        ):
            item.pop(key, None)
    item["resource_type_batch_status"] = "pending"
    item["resource_type_processing_status"] = "pending"
    item["ocr_batch_status"] = "pending"
    item["ocr_processing_status"] = "pending"
    item["metadata_batch_status"] = "pending"
    item["metadata_extraction_status"] = "pending"
    for asset in item.get("asset_details", []):
        asset.pop("ocr_result", None)
    container.replace_item(record_id, item)
    reset.append(record_id)
print(json.dumps(reset))
"""
    return _functions_json(env_file, source, SYNTHETIC_RECORD_IDS)


def _validate_stage_scope(env_file: Path, stage: Stage) -> None:
    source = """
import json
import os
import sys
from azure.cosmos import CosmosClient

client = CosmosClient.from_connection_string(os.environ["COSMOS_CONNECTION_STRING"])
container = client.get_database_client(
    os.environ.get("COSMOS_DATABASE_NAME", "contentdb")
).get_container_client(
    os.environ.get("COSMOS_CONTAINER_NAME", "recordsmetadata")
)
where_by_stage = {
    "resource_type": (
        "(NOT IS_DEFINED(c.resource_type_batch_status) "
        "OR c.resource_type_batch_status = 'pending') "
        "AND c.original_file_status = 'completed'"
    ),
    "ocr": (
        "(NOT IS_DEFINED(c.ocr_batch_status) OR c.ocr_batch_status = 'pending') "
        "AND c.resource_type_processing_status = 'completed'"
    ),
    "metadata": (
        "(NOT IS_DEFINED(c.metadata_batch_status) "
        "OR c.metadata_batch_status = 'pending') "
        "AND c.ocr_processing_status = 'completed'"
    ),
}
rows = container.query_items(
    query=f"SELECT c.id FROM c WHERE {where_by_stage[sys.argv[1]]}",
    enable_cross_partition_query=True,
)
print(json.dumps(sorted(row["id"] for row in rows)))
"""
    actual = tuple(_functions_json(env_file, source, (stage.pending_status,)))
    if actual != SYNTHETIC_RECORD_IDS:
        unexpected = sorted(set(actual) - set(SYNTHETIC_RECORD_IDS))
        missing = sorted(set(SYNTHETIC_RECORD_IDS) - set(actual))
        raise RuntimeError(
            f"{stage.name} pending scope must contain only the acceptance fixtures; "
            f"unexpected={unexpected}, missing={missing}"
        )


def _stage_records(env_file: Path, stage: Stage) -> list[dict[str, Any]]:
    source = """
import json
import os
import sys
from azure.cosmos import CosmosClient

batch_field, processing_field, stage_name, *record_ids = sys.argv[1:]
client = CosmosClient.from_connection_string(os.environ["COSMOS_CONNECTION_STRING"])
container = client.get_database_client(
    os.environ.get("COSMOS_DATABASE_NAME", "contentdb")
).get_container_client(
    os.environ.get("COSMOS_CONTAINER_NAME", "recordsmetadata")
)
rows = []
for record_id in record_ids:
    item = container.read_item(record_id, partition_key=record_id)
    if stage_name == "resource type":
        output_ready = bool(item.get("resource_type"))
    elif stage_name == "OCR":
        assets = item.get("asset_details", [])
        output_ready = bool(assets) and all("ocr_result" in asset for asset in assets)
    else:
        output_ready = bool(item.get("extracted_metadata"))
    rows.append({
        "id": record_id,
        "batch": item.get(batch_field),
        "processing": item.get(processing_field),
        "output_ready": output_ready,
        "error": (
            item.get(f"{processing_field}_last_error")
            or item.get(f"{batch_field}_last_error")
        ),
    })
print(json.dumps(rows))
"""
    arguments = (
        stage.batch_field,
        stage.processing_field,
        stage.name,
        *SYNTHETIC_RECORD_IDS,
    )
    return _functions_json(env_file, source, arguments)


def _batches(env_file: Path, batch_type: str) -> list[dict[str, Any]]:
    source = """
import json
import os
import sys
from azure.cosmos import CosmosClient

client = CosmosClient.from_connection_string(os.environ["COSMOS_CONNECTION_STRING"])
container = client.get_database_client(
    os.environ.get("COSMOS_DATABASE_NAME", "contentdb")
).get_container_client(
    os.environ.get("COSMOS_BATCH_STATUS_CONTAINER_NAME", "batchstatus")
)
rows = container.query_items(
    query=(
        "SELECT c.id, c.batch_id, c.batch_type, c.status, c.record_ids "
        "FROM c WHERE c.batch_type = @batch_type"
    ),
    parameters=[{"name": "@batch_type", "value": sys.argv[1]}],
    enable_cross_partition_query=True,
)
print(json.dumps(list(rows)))
"""
    return _functions_json(env_file, source, (batch_type,))


def _wait_for_stage(
    env_file: Path,
    stage: Stage,
    previous_batch_ids: set[str],
    timeout: float,
    poll_interval: float,
) -> str:
    deadline = time.monotonic() + timeout
    previous_summary = ""
    while time.monotonic() < deadline:
        rows = _stage_records(env_file, stage)
        summary = ", ".join(
            f"{row['id']}={row.get('processing') or 'unknown'}" for row in rows
        )
        if summary != previous_summary:
            print(f"{stage.name} records: {summary}", flush=True)
            previous_summary = summary

        failed = [
            row
            for row in rows
            if str(row.get("processing") or "").lower() in FAILED_PROCESSING_STATES
            or str(row.get("batch") or "").lower() in FAILED_PROCESSING_STATES
        ]
        if failed:
            raise RuntimeError(f"{stage.name} processing failed: {failed}")

        completed = all(
            row.get("batch") == "completed"
            and row.get("processing") == "completed"
            and row.get("output_ready")
            for row in rows
        )
        if completed:
            new_batches = [
                batch
                for batch in _batches(env_file, stage.batch_type)
                if str(batch.get("batch_id")) not in previous_batch_ids
            ]
            if len(new_batches) != 1:
                raise RuntimeError(
                    f"{stage.name} expected one new Azure batch job, "
                    f"found {len(new_batches)}"
                )
            batch = new_batches[0]
            record_ids = tuple(sorted(batch.get("record_ids") or []))
            if record_ids != SYNTHETIC_RECORD_IDS:
                raise RuntimeError(
                    f"{stage.name} batch contains unexpected record IDs: "
                    f"{batch.get('record_ids')}"
                )
            if batch.get("status") != "completed":
                raise RuntimeError(
                    f"{stage.name} records completed but batch status is {batch}"
                )
            return str(batch["batch_id"])
        time.sleep(poll_interval)
    raise RuntimeError(f"Timed out waiting for {stage.name} record processing")


def _run_queue_verifier(env_file: Path, timeout: float) -> None:
    _run(
        [
            sys.executable,
            str(QUEUE_VERIFIER),
            "--env-file",
            str(env_file),
            "--document-id",
            SYNTHETIC_RECORD_IDS[0],
            "--timeout",
            str(timeout),
        ],
        timeout=timeout + 30,
    )
    print("Layer 2 Service Bus queue flow completed.")


def _refresh_layer2_navigation_statistics() -> None:
    for scope in ("repositories", "collections"):
        result = _post_json(
            f"{LAYER2_API_URL}/statistics/rebuild/{scope}",
            {},
        )
        if result.get("status") != "success":
            raise RuntimeError(
                f"Layer 2 {scope} statistics refresh failed: {result}"
            )
    print("Layer 2 repository and collection statistics refreshed.")


def _verify_ai_stages(
    env_file: Path,
    orchestration_timeout: float,
    stage_timeout: float,
    poll_interval: float,
) -> None:
    for stage in STAGES:
        _validate_stage_scope(env_file, stage)
        previous_batch_ids = {
            str(batch["batch_id"])
            for batch in _batches(env_file, stage.batch_type)
        }
        _start_orchestration(
            stage.route,
            stage.name,
            {"batch_size": len(SYNTHETIC_RECORD_IDS)},
            orchestration_timeout,
            min(poll_interval, 2),
        )
        batch_id = _wait_for_stage(
            env_file,
            stage,
            previous_batch_ids,
            stage_timeout,
            poll_interval,
        )
        print(f"{stage.name} Azure batch completed ({batch_id}).")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument(
        "--confirm-cloud-ai",
        action="store_true",
        help="Acknowledge that the verifier creates three billable Azure Batch jobs.",
    )
    parser.add_argument("--startup-timeout", type=float, default=180)
    parser.add_argument("--orchestration-timeout", type=float, default=300)
    parser.add_argument(
        "--stage-timeout",
        type=float,
        default=3600,
        help="Maximum seconds to wait for each Azure Batch stage.",
    )
    parser.add_argument("--poll-interval", type=float, default=30)
    args = parser.parse_args()

    if not args.confirm_cloud_ai:
        raise RuntimeError(
            "Refusing to create Azure jobs without --confirm-cloud-ai"
        )
    env_file = args.env_file.resolve()
    if not env_file.is_file():
        raise RuntimeError(
            f"Missing {env_file}; run local_dev.py configure first"
        )
    if args.stage_timeout <= 0 or args.poll_interval <= 0:
        raise RuntimeError("Timeouts and poll intervals must be positive")

    _wait_for_url(FUNCTIONS_URL, args.startup_timeout)
    configuration = _runtime_configuration(env_file)
    _validate_runtime(configuration)
    print(
        "Verified local AI runtime: "
        f"deployment={configuration['deployment']}, "
        f"model={configuration['model']}, "
        f"api_version={configuration['api_version']}."
    )

    _start_orchestration(
        "content-source-sync-client",
        "synthetic ingestion",
        {"batch_size": len(SYNTHETIC_RECORD_IDS), "parallel_batches": 1},
        args.orchestration_timeout,
        min(args.poll_interval, 2),
    )
    reset = _reset_synthetic_records(env_file)
    if tuple(reset) != SYNTHETIC_RECORD_IDS:
        raise RuntimeError(f"Unexpected reset result: {reset}")
    print("Reset AI state for the three local synthetic records.")

    _run_queue_verifier(env_file, args.startup_timeout)
    _verify_ai_stages(
        env_file,
        args.orchestration_timeout,
        args.stage_timeout,
        args.poll_interval,
    )
    _refresh_layer2_navigation_statistics()
    print(
        "Complete Layer 1 and Layer 2 acceptance flow passed for "
        "synthetic-record-001 through synthetic-record-003."
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
