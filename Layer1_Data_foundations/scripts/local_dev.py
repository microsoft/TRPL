#!/usr/bin/env python3
"""Configure and bootstrap the Layer 1 local emulator environment."""

from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
FUNCTIONS_DIR = ROOT / "apps" / "functions"
EXPORT_API_DIR = ROOT / "apps" / "content-export-api"
REPOSITORY_ROOT = ROOT.parent
LAYER2_ROOT = REPOSITORY_ROOT / "Layer2_Archivist_App"
LAYER3_ROOT = REPOSITORY_ROOT / "Layer3_Campfire"
LAYER4_WEB_ROOT = (
    REPOSITORY_ROOT
    / "Layer4_Talk_to_TR"
    / "TRPL-Web"
    / "TRPL-web-oss"
)
LAYER2_API_DIR = LAYER2_ROOT / "apps" / "archivist-api"
LAYER2_FUNCTIONS_DIR = LAYER2_ROOT / "apps" / "functions"
FUNCTION_SETTINGS = FUNCTIONS_DIR / "local.settings.json"
FUNCTION_ENV_SETTINGS = FUNCTIONS_DIR / ".env.local"
EXPORT_API_SETTINGS = EXPORT_API_DIR / ".env.local"
LAYER2_API_SETTINGS = LAYER2_API_DIR / ".env.local"
LAYER2_FUNCTION_SETTINGS = LAYER2_FUNCTIONS_DIR / "local.settings.json"
LAYER2_FUNCTION_ENV_SETTINGS = LAYER2_FUNCTIONS_DIR / ".env.local"
LAYER3_BACKEND_SETTINGS = LAYER3_ROOT / "backend" / ".env.local"
LAYER3_FRONTEND_SETTINGS = LAYER3_ROOT / "frontend" / ".env.local"
LAYER3_GUARDRAIL_DIR = LAYER3_ROOT / "private" / "guardrails"
LAYER3_SAFETY_PATTERNS = (
    LAYER3_GUARDRAIL_DIR / "campfire-safety-error-patterns.json"
)
LAYER4_BRAIN_SETTINGS = LAYER4_WEB_ROOT / "lia_agent_api" / ".env.local"
LAYER4_TOKEN_SETTINGS = LAYER4_WEB_ROOT / "webapp-tokenserver" / ".env.local"
LAYER4_GUARDRAIL_DIR = LAYER4_WEB_ROOT / "lia_agent_api" / "private" / "guardrails"
LAYER4_GUARDRAIL_FILES = (
    LAYER4_GUARDRAIL_DIR / "prompt-injection.json",
    LAYER4_GUARDRAIL_DIR / "output-reviewer.json",
    LAYER4_GUARDRAIL_DIR / "roosevelt-guardrails.txt",
    LAYER4_GUARDRAIL_DIR / "jailbreak-cases.json",
    LAYER4_GUARDRAIL_DIR / "threat-patterns.json",
)
COMPOSE_SETTINGS = ROOT / ".env.local"
PIP_INDEX_SECRET = ROOT / ".env.pip-index"
NPM_REGISTRY_SECRET = ROOT / ".env.npm-registry"
AZURE_OPENAI_KEY_SECRET = ROOT / ".env.azure-openai-key"
AZURE_SEARCH_KEY_SECRET = ROOT / ".env.azure-search-key"

AZURITE_BLOB_ENDPOINT = "http://127.0.0.1:10000/devstoreaccount1"
AZURITE_CONNECTION_STRING = (
    "DefaultEndpointsProtocol=http;"
    "AccountName=devstoreaccount1;"
    "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/"
    "K1SZFPTOtr/KBHBeksoGMGw==;"
    f"BlobEndpoint={AZURITE_BLOB_ENDPOINT};"
    "QueueEndpoint=http://127.0.0.1:10001/devstoreaccount1;"
    "TableEndpoint=http://127.0.0.1:10002/devstoreaccount1;"
)
COSMOS_KEY = (
    "C2y6yDjf5/R+ob0N8A7Cgv30VRDJIweEQMu+"
    "Ta4kZL9Dac5fSnGWyH7bQiFuGM5GJdjM/lCg=="
)
COSMOS_ENDPOINT = "http://127.0.0.1:8081/"
COSMOS_CONNECTION_STRING = (
    f"AccountEndpoint={COSMOS_ENDPOINT};AccountKey={COSMOS_KEY};"
)
SERVICEBUS_CONNECTION_STRING = (
    "Endpoint=sb://localhost;"
    "SharedAccessKeyName=RootManageSharedAccessKey;"
    "SharedAccessKey=SAS_KEY_VALUE;"
    "UseDevelopmentEmulator=true;"
)

CONTAINERS = {
    "recordsmetadata": "/id",
    "ingestion-errors": "/id",
    "batchstatus": "/id",
    "statistics": "/id",
    "periodic-run-history": "/id",
    "digital-items": "/source",
    "ocrmetadataaudit": "/id",
    "correction-requests": "/id",
    "bulk-publish-batches": "/id",
    "epub": "/id",
    "epub-chunks": "/id",
    "record-chunks": "/id",
}

DISABLED_TIMERS = (
    "ContentSourcePeriodicSyncSchedulesPoller",
    "ContentSourcePeriodicSyncTrigger",
    "DigitalItemsOcrPoller",
)
AI_STATUS_POLLERS = (
    "OcrBatchStatusPoller",
    "MetadataBatchStatusPoller",
    "ResourceTypeBatchStatusPoller",
)


def _run_az(arguments: list[str]) -> object:
    if not shutil.which("az"):
        raise RuntimeError("Azure CLI is required for AI discovery")
    result = subprocess.run(
        ["az", *arguments, "--output", "json"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"Azure CLI command failed: {detail}")
    return json.loads(result.stdout or "null")


def _pip_index_url() -> str:
    configured = os.getenv("PIP_INDEX_URL", "").strip()
    for scope in ("--site", "--user", "--global"):
        if configured:
            break
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "config",
                scope,
                "get",
                "global.index-url",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            configured = result.stdout.strip()
    return configured or "https://pypi.org/simple"


def _npm_registry_url() -> str:
    configured = os.getenv("NPM_CONFIG_REGISTRY", "").strip()
    if not configured and shutil.which("npm"):
        result = subprocess.run(
            ["npm", "config", "get", "registry"],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            configured = result.stdout.strip()
    return configured or "https://registry.npmjs.org/"


def _discover_ai(
    subscription: str,
    resource_group: str,
    account_name: str | None,
    deployment_name: str | None,
) -> dict[str, str]:
    _run_az(["account", "show", "--subscription", subscription])
    accounts = _run_az(
        [
            "cognitiveservices",
            "account",
            "list",
            "--subscription",
            subscription,
            "--resource-group",
            resource_group,
        ]
    )
    candidates = [
        account
        for account in accounts
        if account.get("kind", "").lower() in {"openai", "aiservices"}
        and (not account_name or account.get("name") == account_name)
    ]
    if not candidates:
        raise RuntimeError("No OpenAI or AI Services account found in the requested scope")
    if len(candidates) > 1:
        names = ", ".join(sorted(account["name"] for account in candidates))
        raise RuntimeError(f"Multiple AI accounts found; pass --ai-account: {names}")

    account = candidates[0]
    deployments = _run_az(
        [
            "cognitiveservices",
            "account",
            "deployment",
            "list",
            "--subscription",
            subscription,
            "--resource-group",
            resource_group,
            "--name",
            account["name"],
        ]
    )
    batch_deployments = [
        deployment
        for deployment in deployments
        if deployment.get("properties", {}).get("model", {}).get("format", "").lower()
        == "openai"
        and "batch" in deployment.get("sku", {}).get("name", "").lower()
        and deployment.get("properties", {}).get("provisioningState", "Succeeded")
        == "Succeeded"
        and (not deployment_name or deployment.get("name") == deployment_name)
    ]
    if not batch_deployments:
        raise RuntimeError(
            "No compatible GlobalBatch or DataZoneBatch deployment exists in the "
            "requested AI account"
        )
    if len(batch_deployments) > 1:
        names = ", ".join(sorted(item["name"] for item in batch_deployments))
        raise RuntimeError(
            f"Multiple batch deployments found; pass --ai-deployment: {names}"
        )

    account_details = _run_az(
        [
            "cognitiveservices",
            "account",
            "show",
            "--subscription",
            subscription,
            "--resource-group",
            resource_group,
            "--name",
            account["name"],
        ]
    )
    endpoints = account_details.get("properties", {}).get("endpoints", {})
    openai_endpoint = endpoints.get("OpenAI Language Model Instance API")
    foundry_endpoint = endpoints.get("AI Foundry API")
    if not openai_endpoint:
        raise RuntimeError("The selected AI account does not expose an OpenAI endpoint")

    selected_deployment = batch_deployments[0]
    selected = selected_deployment["name"]
    model_name = (
        selected_deployment.get("properties", {})
        .get("model", {})
        .get("name")
    )
    if not model_name:
        raise RuntimeError(
            f"Deployment '{selected}' does not report a model name"
        )
    settings = {
        "AZURE_OPENAI_ENDPOINT": openai_endpoint,
        "AZURE_OPENAI_API_VERSION": "2025-03-01-preview",
        "AZURE_OPENAI_BATCH_DEPLOYMENT_NAME": selected,
        "AZURE_OPENAI_MODEL_NAME": model_name,
        "AZURE_AI_FOUNDRY_MODEL_NAME": selected,
    }
    if foundry_endpoint:
        settings["AZURE_AI_FOUNDRY_ENDPOINT"] = foundry_endpoint
    return settings


def _disabled_timer_settings(enable_ai_pollers: bool) -> dict[str, str]:
    disabled = list(DISABLED_TIMERS)
    if not enable_ai_pollers:
        disabled.extend(AI_STATUS_POLLERS)
    return {f"AzureWebJobs.{name}.Disabled": "true" for name in disabled}


def _function_values(enable_ai_pollers: bool = False) -> dict[str, str]:
    values = {
        "AzureWebJobsStorage": AZURITE_CONNECTION_STRING,
        "FUNCTIONS_WORKER_RUNTIME": "python",
        "AzureWebJobsFeatureFlags": "EnableWorkerIndexing",
        "ENVIRONMENT": "local",
        "CONTENT_SOURCE_ADAPTER": "synthetic",
        "CONTENT_SOURCE_COLLECTION_IDS": "",
        "CONTENT_SOURCE_BATCH_SIZE": "3",
        "CONTENT_SOURCE_PARALLEL_BATCHES": "1",
        "AZURE_STORAGE_ACCOUNT_NAME": "devstoreaccount1",
        "AZURE_STORAGE_CONNECTION_STRING": AZURITE_CONNECTION_STRING,
        "AZURE_STORAGE_BLOB_ENDPOINT": AZURITE_BLOB_ENDPOINT,
        "AZURE_STORAGE_CONTAINER_NAME": "content-assets",
        "DIGITAL_ITEMS_STORAGE_CONTAINER": "digital-resources",
        "COSMOS_ENDPOINT": COSMOS_ENDPOINT,
        "COSMOS_CONNECTION_STRING": COSMOS_CONNECTION_STRING,
        "COSMOS_DATABASE_NAME": "contentdb",
        "COSMOS_CONTAINER_NAME": "recordsmetadata",
        "COSMOS_CONNECTION_MODE": "Gateway",
        "COSMOS_ENABLE_DIAGNOSTICS": "false",
        "COSMOS_LOG_CONTAINER_NAME": "ingestion-errors",
        "COSMOS_BATCH_STATUS_CONTAINER_NAME": "batchstatus",
        "COSMOS_DB_STATS_CONTAINER_NAME": "statistics",
        "COSMOS_DIGITAL_ITEMS_CONTAINER_NAME": "digital-items",
        "COSMOS_DB_PERIODIC_RUN_HISTORY_CONTAINER_NAME": "periodic-run-history",
        "CONTENT_SOURCE_PERIODIC_TIMER_PROCESS_SCHEDULES": "false",
        "CONTENT_SOURCE_PERIODIC_TIMER_SKIP_LEGACY_LOOKBACK": "true",
        "AZURE_SERVICEBUS_CONNECTION_STRING": SERVICEBUS_CONNECTION_STRING,
        "SERVICEBUS_QUEUE_NAME": "data-ingestion-queue",
        "RETRY_MAX_RETRIES": "3",
        "MODEL_CONTEXT_LIMIT": "128000",
    }
    values.update(_disabled_timer_settings(enable_ai_pollers))
    return values


def _layer2_api_values() -> dict[str, str]:
    return {
        "ENVIRONMENT": "local",
        "FRONTEND_URL": "http://localhost:5173",
        "COSMOS_DB_ENDPOINT": COSMOS_ENDPOINT,
        "COSMOS_DB_KEY": COSMOS_KEY,
        "COSMOS_DB_DATABASE_NAME": "contentdb",
        "COSMOS_DB_CONTAINER_NAME": "recordsmetadata",
        "COSMOS_DB_AUDIT_CONTAINER_NAME": "ocrmetadataaudit",
        "COSMOS_DB_STATS_CONTAINER_NAME": "statistics",
        "COSMOS_DB_BULK_PUBLISH_BATCHES_CONTAINER_NAME": "bulk-publish-batches",
        "COSMOS_DB_PERIODIC_RUN_HISTORY_CONTAINER_NAME": "periodic-run-history",
        "COSMOS_DB_CORRECTION_REQUESTS_CONTAINER_NAME": "correction-requests",
        "COSMOS_DB_CONNECTION_MODE": "Gateway",
        "EPUB_COSMOS_CONTAINER": "epub",
        "EPUB_CHUNKS_CONTAINER": "epub-chunks",
        "EPUB_STORAGE_CONTAINER_NAME": "epub-files",
        "AZURE_STORAGE_ACCOUNT_NAME": "devstoreaccount1",
        "ARCHIVIST_STORAGE_ACCOUNT_NAME": "devstoreaccount1",
        "AZURE_STORAGE_CONNECTION_STRING": AZURITE_CONNECTION_STRING,
        "AZURE_SERVICEBUS_CONNECTION_STRING": SERVICEBUS_CONNECTION_STRING,
        "DATA_INGESTION_QUEUE_NAME": "data-ingestion-queue",
        "EPUB_QUEUE_NAME": "epub-processing-queue",
        "AZURE_FUNCTIONS_BASE_URL": "http://127.0.0.1:7071",
        "DATA_INGEST_FUNCTION_URL": "http://127.0.0.1:7072",
    }


def _layer2_function_values() -> dict[str, str]:
    values = _layer2_api_values()
    values.update(
        {
            "AzureWebJobsStorage": AZURITE_CONNECTION_STRING,
            "FUNCTIONS_WORKER_RUNTIME": "python",
            "AzureWebJobsFeatureFlags": "EnableWorkerIndexing",
            "ServiceBusConnection": SERVICEBUS_CONNECTION_STRING,
            "AzureFunctionsJobHost__extensions__serviceBus__transportType": "amqpTcp",
            "ARCHIVIST_API_BASE_URL": "http://127.0.0.1:8000",
            "WEBSITE_HOSTNAME": "localhost:7072",
            "AzureWebJobs.ReconcileStuckPublishingTimer.Disabled": "true",
        }
    )
    return values


def _write_secret_file(path: Path, value: str) -> None:
    secret = value.strip()
    if not secret:
        raise ValueError("Secret value cannot be empty")

    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as file:
        file.write(secret + "\n")
    path.chmod(0o600)


def _write_private_env(path: Path, values: dict[str, str]) -> None:
    content = "\n".join(f"{key}={value}" for key, value in values.items()) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as file:
        file.write(content)
    path.chmod(0o600)


def _ensure_secret_file(path: Path) -> None:
    if path.exists():
        path.chmod(0o600)
        return
    fd = os.open(path, os.O_WRONLY | os.O_CREAT, 0o600)
    os.close(fd)


def configure(args: argparse.Namespace) -> None:
    values = _function_values(args.enable_ai_pollers)
    if bool(args.subscription) != bool(args.resource_group):
        raise RuntimeError("--subscription and --resource-group must be supplied together")
    if args.subscription:
        try:
            values.update(
                _discover_ai(
                    args.subscription,
                    args.resource_group,
                    args.ai_account,
                    args.ai_deployment,
                )
            )
            print("Discovered a compatible Azure OpenAI batch deployment.")
        except RuntimeError as exc:
            if args.enable_ai_pollers:
                raise RuntimeError(
                    "AI pollers cannot be enabled without a verified batch "
                    "deployment"
                ) from exc
            print(f"AI discovery skipped: {exc}", file=sys.stderr)

    FUNCTION_SETTINGS.write_text(
        json.dumps({"IsEncrypted": False, "Values": values}, indent=2) + "\n",
        encoding="utf-8",
    )
    FUNCTION_ENV_SETTINGS.write_text(
        "\n".join(f"{key}={value}" for key, value in values.items()) + "\n",
        encoding="utf-8",
    )
    export_values = {
        "ENVIRONMENT": "local",
        "OUTBOUND_API_KEY": secrets.token_urlsafe(32),
        "COSMOS_DB_ENDPOINT": COSMOS_ENDPOINT,
        "COSMOS_DB_CONNECTION_STRING": COSMOS_CONNECTION_STRING,
        "COSMOS_DB_DATABASE_NAME": "contentdb",
        "COSMOS_DB_CONTAINER_NAME": "recordsmetadata",
        "AZURE_STORAGE_ACCOUNT_NAME": "devstoreaccount1",
        "AZURE_STORAGE_CONNECTION_STRING": AZURITE_CONNECTION_STRING,
        "AZURE_STORAGE_BLOB_ENDPOINT": AZURITE_BLOB_ENDPOINT,
        "AZURE_STORAGE_CONTAINER_NAME": "content-assets",
    }
    EXPORT_API_SETTINGS.write_text(
        "\n".join(f"{key}={value}" for key, value in export_values.items()) + "\n",
        encoding="utf-8",
    )
    layer2_api_values = _layer2_api_values()
    LAYER2_API_SETTINGS.write_text(
        "\n".join(f"{key}={value}" for key, value in layer2_api_values.items()) + "\n",
        encoding="utf-8",
    )
    layer2_function_values = _layer2_function_values()
    LAYER2_FUNCTION_SETTINGS.write_text(
        json.dumps(
            {"IsEncrypted": False, "Values": layer2_function_values},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    LAYER2_FUNCTION_ENV_SETTINGS.write_text(
        "\n".join(
            f"{key}={value}" for key, value in layer2_function_values.items()
        )
        + "\n",
        encoding="utf-8",
    )
    rag_api_key = secrets.token_urlsafe(32)
    LAYER3_GUARDRAIL_DIR.mkdir(parents=True, exist_ok=True)
    _ensure_secret_file(LAYER3_SAFETY_PATTERNS)
    layer3_backend_values = {
        "CAMPFIRE_RAG_MODE": "degraded",
        "RAG_API_KEY": rag_api_key,
        "REDIS_HOST": "layer3-redis",
        "REDIS_PORT": "6379",
        "REDIS_SSL": "false",
        "CORS_ALLOW_ORIGINS": "http://localhost:3000",
        "CAMPFIRE_SAFETY_ERROR_PATTERNS_FILE": (
            "/run/secrets/campfire-safety-error-patterns.json"
        ),
    }
    layer3_frontend_values = {
        "PYTHON_RAG_API_URL": "http://layer3-backend:8000",
        "RAG_API_KEY": rag_api_key,
        "NEXT_PUBLIC_APP_URL": "http://localhost:3000",
        "COOKIE_SECURE": "false",
    }
    _write_private_env(LAYER3_BACKEND_SETTINGS, layer3_backend_values)
    _write_private_env(LAYER3_FRONTEND_SETTINGS, layer3_frontend_values)
    layer4_api_key = secrets.token_urlsafe(32)
    layer4_admin_token = secrets.token_urlsafe(32)
    layer4_brain_values = {
        "LIA_RUNTIME_MODE": "deterministic",
        "APP_ENV": "local",
        "HOST": "0.0.0.0",
        "PORT": "8010",
        "CLIENT_API_KEYS": layer4_api_key,
        "WS_AUTH_ENABLED": "true",
        "TTS_ENABLED": "false",
        "PROMPT_INJECTION_ENABLED": "false",
        "OUTPUT_REVIEWER_ENABLED": "false",
    }
    layer4_token_values = {
        "TALK_TO_TR_RUNTIME_MODE": "deterministic",
        "TOKEN_SERVER_PORT": "8002",
        "BRAIN_URL": "http://layer4-brain:8010",
        "BRAIN_API_KEY": layer4_api_key,
        "ADMIN_TOKEN": layer4_admin_token,
        "JAILBREAK_VIEW_CODE": layer4_admin_token,
    }
    _write_private_env(LAYER4_BRAIN_SETTINGS, layer4_brain_values)
    _write_private_env(LAYER4_TOKEN_SETTINGS, layer4_token_values)
    LAYER4_GUARDRAIL_DIR.mkdir(parents=True, exist_ok=True)
    for guardrail_file in LAYER4_GUARDRAIL_FILES:
        _ensure_secret_file(guardrail_file)
    if not COMPOSE_SETTINGS.exists():
        sql_password = f"Trpl!{secrets.token_urlsafe(18)}9a"
        COMPOSE_SETTINGS.write_text(
            "ACCEPT_EULA=N\n"
            f"MSSQL_SA_PASSWORD={sql_password}\n",
            encoding="utf-8",
        )
    _write_secret_file(PIP_INDEX_SECRET, _pip_index_url())
    _write_secret_file(NPM_REGISTRY_SECRET, _npm_registry_url())
    _ensure_secret_file(AZURE_OPENAI_KEY_SECRET)
    _ensure_secret_file(AZURE_SEARCH_KEY_SECRET)

    print(f"Wrote ignored Functions settings: {FUNCTION_SETTINGS}")
    print(f"Wrote ignored Functions container settings: {FUNCTION_ENV_SETTINGS}")
    print(f"Wrote ignored export API settings: {EXPORT_API_SETTINGS}")
    print(f"Wrote ignored Layer 2 API settings: {LAYER2_API_SETTINGS}")
    print(f"Wrote ignored Layer 2 Functions settings: {LAYER2_FUNCTION_SETTINGS}")
    print(
        "Wrote ignored Layer 2 Functions container settings: "
        f"{LAYER2_FUNCTION_ENV_SETTINGS}"
    )
    print(f"Wrote ignored Layer 3 backend settings: {LAYER3_BACKEND_SETTINGS}")
    print(f"Wrote ignored Layer 3 frontend settings: {LAYER3_FRONTEND_SETTINGS}")
    print(f"Prepared ignored Layer 3 safety-pattern file: {LAYER3_SAFETY_PATTERNS}")
    print(f"Wrote ignored Layer 4 brain settings: {LAYER4_BRAIN_SETTINGS}")
    print(f"Wrote ignored Layer 4 token-server settings: {LAYER4_TOKEN_SETTINGS}")
    print(f"Prepared ignored Layer 4 guardrail directory: {LAYER4_GUARDRAIL_DIR}")
    print(f"Wrote or preserved ignored Compose settings: {COMPOSE_SETTINGS}")
    print(f"Wrote ignored package index build secret: {PIP_INDEX_SECRET}")
    print(f"Wrote ignored npm registry build secret: {NPM_REGISTRY_SECRET}")
    print(f"Prepared ignored Azure OpenAI runtime secret: {AZURE_OPENAI_KEY_SECRET}")
    print(f"Prepared ignored Azure AI Search runtime secret: {AZURE_SEARCH_KEY_SECRET}")
    if args.enable_ai_pollers:
        print("Enabled OCR, metadata, and resource-type batch status pollers.")


def set_ai_key(_: argparse.Namespace) -> None:
    api_key = getpass.getpass("Azure OpenAI API key: ")
    confirmation = getpass.getpass("Confirm Azure OpenAI API key: ")
    if api_key != confirmation:
        raise ValueError("API key entries did not match")
    _write_secret_file(AZURE_OPENAI_KEY_SECRET, api_key)
    print(f"Stored the local API key in {AZURE_OPENAI_KEY_SECRET}")


def _wait_for(url: str, service_name: str) -> None:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            if response.status >= 400:
                raise RuntimeError(f"HTTP {response.status}")
    except Exception as exc:
        raise RuntimeError(
            f"{service_name} is not ready at {url}; start Docker Compose first"
        ) from exc


def bootstrap(_: argparse.Namespace) -> None:
    cosmos_health_endpoint = os.getenv(
        "COSMOS_HEALTH_ENDPOINT", "http://127.0.0.1:18080/ready"
    )
    _wait_for(cosmos_health_endpoint, "Cosmos DB emulator")
    try:
        from azure.cosmos import CosmosClient, PartitionKey
        from azure.core.exceptions import ResourceExistsError
        from azure.storage.blob import BlobServiceClient
    except ImportError as exc:
        raise RuntimeError(
            "Install apps/functions/requirements.txt before bootstrapping"
        ) from exc

    blob_connection_string = os.getenv(
        "AZURE_STORAGE_CONNECTION_STRING", AZURITE_CONNECTION_STRING
    )
    blob_service = BlobServiceClient.from_connection_string(blob_connection_string)
    for container_name in ("content-assets", "digital-resources", "epub-files"):
        try:
            blob_service.create_container(container_name)
        except ResourceExistsError:
            pass

    cosmos_connection_string = os.getenv(
        "COSMOS_CONNECTION_STRING", COSMOS_CONNECTION_STRING
    )
    cosmos = CosmosClient.from_connection_string(cosmos_connection_string)
    database = cosmos.create_database_if_not_exists("contentdb")
    for container_name, partition_key in CONTAINERS.items():
        database.create_container_if_not_exists(
            id=container_name,
            partition_key=PartitionKey(path=partition_key),
        )
    print("Created or verified local Blob and Cosmos containers.")


def doctor(_: argparse.Namespace) -> None:
    missing = []
    for executable in ("docker", "python3"):
        path = shutil.which(executable)
        print(f"{executable}: {path or 'missing'}")
        if not path:
            missing.append(executable)
    if missing:
        raise RuntimeError(f"Missing required tools: {', '.join(missing)}")
    subprocess.run(["docker", "compose", "version"], check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    configure_parser = subparsers.add_parser(
        "configure", help="Generate ignored local application settings"
    )
    configure_parser.add_argument("--subscription")
    configure_parser.add_argument("--resource-group")
    configure_parser.add_argument("--ai-account")
    configure_parser.add_argument("--ai-deployment")
    configure_parser.add_argument(
        "--enable-ai-pollers",
        action="store_true",
        help=(
            "Enable the OCR, metadata, and resource-type batch status pollers; "
            "requires successful Azure AI discovery"
        ),
    )
    configure_parser.set_defaults(handler=configure)

    set_ai_key_parser = subparsers.add_parser(
        "set-ai-key",
        help="Securely store a local-only Azure OpenAI API key",
    )
    set_ai_key_parser.set_defaults(handler=set_ai_key)

    bootstrap_parser = subparsers.add_parser(
        "bootstrap", help="Create local Blob containers and Cosmos data structures"
    )
    bootstrap_parser.set_defaults(handler=bootstrap)

    doctor_parser = subparsers.add_parser(
        "doctor", help="Check required local development tools"
    )
    doctor_parser.set_defaults(handler=doctor)

    args = parser.parse_args()
    try:
        args.handler(args)
    except (RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
