# TRPL Data Foundations

Azure Functions-based reference pipeline for provider-neutral record ingestion,
asset processing, OCR, and structured metadata extraction.

## Architecture

- `apps/functions/` contains Python Azure Functions for provider-neutral
  content-source ingestion, asset processing, OCR, and metadata extraction.
- `apps/content-export-api/` contains a read-only API for approved records.

Only the deterministic synthetic adapter and fictional pack are included.
Storage, Cosmos DB, Service Bus, and Durable Functions dependencies can run
against local Docker emulators. AI-backed OCR and metadata stages still require
an operator-provided Azure OpenAI batch deployment and explicit Azure access.

## Pipeline Stages

1. **Record sync** - Fetch records from the configured content-source adapter
2. **FetchRelatedAssets** - Get asset IDs for each record
3. **ProcessAssetDetails** - Fetch asset metadata and thumbnails
4. **ProcessOriginalFiles** - Download original files to Blob Storage
5. **ExtractResourceType** - Classify visual resource types from source files
6. **CreateOCRBatch** - Create Azure OpenAI batch OCR jobs
7. **ExtractMetadata** - Extract structured metadata from OCR text

## Project Structure

```
apps/functions/              # Azure Functions application
├── function_app.py          # Main function definitions
└── helper/
    ├── content_source/      # Adapter contracts and synthetic implementation
    └── prompts/             # AI prompts for OCR and metadata extraction
apps/content-export-api/     # Read-only approved-record API
```

## Documentation

- [Content-source adapter contract](docs/CONTENT_SOURCE_ADAPTER.md)
- [OCR Prompts](apps/functions/helper/prompts/README.md) - AI prompts for document processing

## Local Layer 1 environment

The default local mode is network-isolated and uses:

- Azurite for Blob, Queue, and Table Storage, including Durable Functions state
- Azure Cosmos DB Linux emulator vNext in NoSQL Gateway mode
- The deterministic synthetic content adapter
- Azure Service Bus emulator plus its SQL Server dependency in full-stack mode

All emulator ports bind to `127.0.0.1`. Populated settings are generated only in
gitignored files.

### Prerequisites

- Docker Desktop
- Python 3.12

Run configuration and Compose commands from the repository root:

```bash
python3 Layer1_Data_foundations/scripts/local_dev.py doctor
python3 Layer1_Data_foundations/scripts/local_dev.py configure

docker compose --env-file Layer1_Data_foundations/.env.local \
  up --build -d functions
```

Compose starts Azurite and Cosmos DB, runs the idempotent data bootstrap, and
then starts the Functions host at <http://127.0.0.1:7071>. The generated
Functions profile disables all timers by default, so startup cannot accidentally
create cloud AI work. No local Azure Functions Core Tools installation is required.
The official Functions Python image currently runs through Docker's amd64
emulation on Apple Silicon.

The containers use stable repository-wide names so later layers can reuse the
same local services: `trpl-azurite`, `trpl-cosmos`, and
`trpl-layer1-functions`. `trpl-local-bootstrap` is a one-shot initialization
container; it creates the required Blob and Cosmos resources, exits successfully,
and runs idempotently whenever the local stack is recreated. Bootstrap uses a
multi-architecture Python image and runs natively on Apple Silicon. The official
Azure Functions Python image is currently AMD64-only, so Docker Desktop reports
emulation for `trpl-layer1-functions` on Apple Silicon; this is expected and
preserves the supported Microsoft runtime.

The image build uses PyPI by default. Environments that require a package-feed
proxy can export `PIP_INDEX_URL` before running `configure`. The selected URL is
stored in an ignored, owner-readable BuildKit secret and does not enter the image
history or build context.

In another terminal, start a three-record synthetic sync:

```bash
curl -sS -X POST http://127.0.0.1:7071/api/content-source-sync-client \
  -H 'Content-Type: application/json' \
  -d '{"batch_size":3,"parallel_batches":1}'
```

Use the returned `statusQueryGetUri` to follow the Durable orchestration. Inspect
Cosmos data at <http://127.0.0.1:1234>. Azurite listens on ports 10000-10002.
Follow host logs with:

```bash
docker compose --env-file Layer1_Data_foundations/.env.local logs -f functions
```

### Full Layer 1 and Layer 2 stack

Service Bus is not needed for isolated Layer 1 processing, but it is required
for the complete application because Layer 2 consumes its ingestion and EPUB
queues. Review the Service Bus emulator and SQL Server license links in
`.env.local.example`, then explicitly set `ACCEPT_EULA=Y` in the ignored
`.env.local` file before starting full-stack mode:

```bash
docker compose --env-file Layer1_Data_foundations/.env.local \
  --profile full-stack up --build -d
curl -fsS http://127.0.0.1:5300/health
```

The emulator provisions the existing Layer 2 queues `data-ingestion-queue` and
`epub-processing-queue`. The profile also starts the Layer 2 API, Functions, and
UI. On Apple Silicon, the SQL Server and Azure Functions containers run through
Docker's AMD64 emulation.

After the stack is healthy, verify that messages published through the Archivist
API service are consumed by both Layer 2 queue triggers:

```bash
python3 Layer2_Archivist_App/scripts/verify_local_queue_flow.py
```

To carry a Layer 1 synthetic record ID through the queue test, first run the
three-record sync above, then obtain an ID from the shared API:

```bash
DOCUMENT_ID="$(curl -fsS \
  'http://127.0.0.1:8000/api/v1/documents?page_number=1&page_size=1' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["documents"][0]["id"])')"

python3 Layer2_Archivist_App/scripts/verify_local_queue_flow.py \
  --document-id "$DOCUMENT_ID"
```

This confirms the local Layer 1 record is visible to Layer 2 and reaches the
Layer 2 ingestion trigger. Publishing into Azure AI Search is a separate,
billable cloud-backed step because there is no local Azure AI Search emulator.

### Optional end-to-end Azure AI stages

The setup command also generates ignored Layer 2 API and Functions settings so
the same emulator stack can be used either through Compose or local developer
tools.

Configure an approved Azure AI scope through local shell variables, then store
its key without printing it:

```bash
export AZURE_SUBSCRIPTION_ID="<subscription-id>"
export AZURE_RESOURCE_GROUP="<resource-group>"
export AZURE_AI_ACCOUNT="<ai-services-account>"
export AZURE_AI_BATCH_DEPLOYMENT="<batch-deployment>"

az login
az account set --subscription "$AZURE_SUBSCRIPTION_ID"

python3 Layer1_Data_foundations/scripts/local_dev.py configure \
  --subscription "$AZURE_SUBSCRIPTION_ID" \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  --ai-account "$AZURE_AI_ACCOUNT" \
  --ai-deployment "$AZURE_AI_BATCH_DEPLOYMENT" \
  --enable-ai-pollers

umask 077
az cognitiveservices account keys list \
  --subscription "$AZURE_SUBSCRIPTION_ID" \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  --name "$AZURE_AI_ACCOUNT" \
  --query key1 --output tsv \
  > Layer1_Data_foundations/.env.azure-openai-key
chmod 600 Layer1_Data_foundations/.env.azure-openai-key

docker compose --env-file Layer1_Data_foundations/.env.local \
  --profile full-stack up --build -d
```

To test Layer 2 publication, the worker also needs an embeddings deployment and
an Azure AI Search index. A batch chat deployment cannot generate embeddings.
The tested Search schema uses `text-embedding-3-large` with a 3,072-dimensional
`record_ocr_text_vector` field. Use an approved embedding deployment and Search
index whose vector dimensions match that schema.

Store the Search admin key in an ignored, owner-only runtime secret and add the
non-secret endpoints to the ignored Compose settings:

```bash
export AZURE_SEARCH_SERVICE="<search-service>"
export AZURE_SEARCH_INDEX="<search-index>"
export AZURE_OPENAI_ENDPOINT="https://<ai-services-account>.openai.azure.com/"
export AZURE_OPENAI_EMBEDDING_DEPLOYMENT="<embedding-deployment>"
export AZURE_SEARCH_ENDPOINT="https://<search-service>.search.windows.net"

umask 077
az search admin-key show \
  --subscription "$AZURE_SUBSCRIPTION_ID" \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  --service-name "$AZURE_SEARCH_SERVICE" \
  --query primaryKey --output tsv \
  > Layer1_Data_foundations/.env.azure-search-key
chmod 600 Layer1_Data_foundations/.env.azure-search-key

cat >> Layer1_Data_foundations/.env.local <<EOF
LAYER2_AZURE_OPENAI_ENDPOINT=$AZURE_OPENAI_ENDPOINT
LAYER2_AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME=$AZURE_OPENAI_EMBEDDING_DEPLOYMENT
LAYER2_AZURE_SEARCH_ENDPOINT=$AZURE_SEARCH_ENDPOINT
LAYER2_AZURE_SEARCH_INDEX=$AZURE_SEARCH_INDEX
EOF

docker compose --env-file Layer1_Data_foundations/.env.local \
  --profile full-stack up --build -d layer2-functions
```

The Compose worker mounts both Azure keys as runtime secrets. API-key
authentication is rejected outside `ENVIRONMENT=local`; cloud deployments
continue to use managed identity.

Run the acceptance verifier after all containers are healthy:

```bash
python3 Layer1_Data_foundations/scripts/verify_local_ai_flow.py \
  --confirm-cloud-ai
```

The explicit confirmation is required because this creates three billable Azure
Batch jobs. The verifier:

1. Rejects non-local Cosmos, non-synthetic adapters, missing secrets, and disabled
   AI pollers.
2. Runs the three-record synthetic Layer 1 ingestion and waits on the returned
   Durable status URL.
3. Before every AI stage, refuses to continue unless its pending-record scope
   contains only `synthetic-record-001` through `synthetic-record-003`.
4. Resets AI state only for `synthetic-record-001` through
   `synthetic-record-003` in the local emulator.
5. Proves both Layer 2 Service Bus queue triggers.
6. Creates and waits for fresh resource-type, OCR, and metadata Batch jobs in
   dependency order.
7. Requires completed local record outputs and exactly one new completed
   `batchstatus` record containing only the three fixture IDs for every stage.
8. Refreshes Layer 2 repository and collection statistics so the synthetic records
   appear under `Fictional Reference Collections` in the Archivist UI.

Durable `runtimeStatus=Completed` means a stage submitted its Batch work; it does
not mean the AI output has been consumed. The verifier separately waits for the
two-minute status pollers to mark all three records complete before it starts the
dependent stage. The tested flow normally takes about 20-30 minutes. Each Azure
Batch job can take up to 24 hours; change the per-stage limit when necessary:

```bash
python3 Layer1_Data_foundations/scripts/verify_local_ai_flow.py \
  --confirm-cloud-ai \
  --stage-timeout 86400
```

Configuration discovery never reads keys or creates Azure resources. The key file
is gitignored, owner-readable, and mounted into the Functions container as a
runtime secret. API-key authentication is rejected unless `ENVIRONMENT=local`;
cloud deployments continue to use Entra authentication.

This validates the complete issue #35 ingestion and queue acceptance path.
Embedding and Azure AI Search indexing are a separate boundary because Search
has no local emulator. Do not treat Search/embedding acceptance as passing until
both an approved embedding deployment and a secure local Search credential path
exist.

### Content export API

The same setup command creates `apps/content-export-api/.env.local`. After the
synthetic flow has populated Cosmos, run:

```bash
cd Layer1_Data_foundations/apps/content-export-api
python -m pip install -r requirements.txt
python app.py
```

Swagger is available at <http://127.0.0.1:8080/docs>. Stop local infrastructure
without deleting persisted emulator data:

```bash
docker compose --env-file Layer1_Data_foundations/.env.local down
```

## Technology Stack

- **Runtime**: Azure Functions v2 (Python)
- **Orchestration**: Azure Durable Functions
- **Database**: Azure Cosmos DB
- **Storage**: Azure Blob Storage
- **AI/ML**: Azure OpenAI Batch (GPT-5 and non-reasoning models)
