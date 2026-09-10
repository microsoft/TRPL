# Theodore Roosevelt Presidential Library - Reading Room

An AI-powered research platform for exploring Theodore Roosevelt's life and legacy.

## Quick Start

### Docker Compose (recommended)

From the repository root:

```bash
LOCAL_UID="$(id -u)" LOCAL_GID="$(id -g)" \
  docker compose -f Layer3_Campfire/compose.configure.yaml run --rm configure
docker compose --env-file Layer1_Data_foundations/.env.local \
  --profile layer3 up --build -d
docker compose --env-file Layer1_Data_foundations/.env.local \
  --profile layer3 exec -T layer3-backend \
  python /app/scripts/verify_local_stack.py
```

This starts:

- Campfire frontend: <http://127.0.0.1:3000>
- Campfire backend/OpenAPI: <http://127.0.0.1:8001/docs>
- Redis on the internal Compose network

`configure` generates ignored `backend/.env.local` and `frontend/.env.local`
files with the same random `RAG_API_KEY`. It also creates an ignored local
placeholder file for deployment-owned safety patterns. These values are runtime
inputs, are written with owner-only permissions, and are excluded from both Git
and Docker build contexts. Local HTTP explicitly disables the cookie `Secure`
flag; cloud deployments retain the production-secure default.

The default `CAMPFIRE_RAG_MODE=degraded` does not require Azure credentials.
Redis chat history, HTTP and WebSocket authentication, the frontend proxy, and
health endpoints remain available. Chat and artifact requests explicitly
return unavailable responses; degraded mode never fabricates RAG results.

Stop the Layer 3 services with:

```bash
docker compose --env-file Layer1_Data_foundations/.env.local \
  --profile layer3 stop layer3-frontend layer3-backend layer3-redis
```

### Cloud-backed RAG mode

Edit the ignored `backend/.env.local` and set:

```dotenv
CAMPFIRE_RAG_MODE=cloud
AZURE_OPENAI_ENDPOINT=https://<ai-account>.cognitiveservices.azure.com
AZURE_OPENAI_API_KEY=<ai-api-key>
AZURE_SEARCH_ENDPOINT=https://<search-service>.search.windows.net
AZURE_SEARCH_API_KEY=<search-api-key>
AZURE_SEARCH_BOOK_INDEX=<book-index-name>
AZURE_SEARCH_LETTER_INDEX=<letter-index-name>
GPT_CHAT_MODEL=<chat-deployment-name>
GPT_SCOPE_MODEL=<scope-deployment-name>
GPT_SEARCH_QUERY_MODEL=<query-deployment-name>
GPT_FOLLOWUP_MODEL=<follow-up-deployment-name>
GPT_EVALUATION_MODEL=<evaluation-deployment-name>
AZURE_OPENAI_EMBEDDING_MODEL=<embedding-deployment-name>
```

Optional semantic configuration and Storage values are documented in
`backend/.env.sample`. Populate the ignored file referenced by
`CAMPFIRE_SAFETY_ERROR_PATTERNS_FILE` with the deployment-owned JSON list, then
recreate the backend:

```bash
docker compose --env-file Layer1_Data_foundations/.env.local \
  --profile layer3 up --build -d --force-recreate layer3-backend layer3-frontend
```

Cloud mode preserves fail-fast configuration: missing Azure OpenAI or Search
values prevent the backend from starting. After the backend is healthy, run the
authenticated cited-response smoke test:

```bash
docker compose --env-file Layer1_Data_foundations/.env.local \
  --profile layer3 exec -T layer3-backend \
  python /app/scripts/verify_cloud_rag.py
```

The smoke test fails unless the backend returns a non-empty final answer with at
least one citation. Override its default research question with the
`CAMPFIRE_SMOKE_PROMPT` environment variable when the configured indexes cover a
different corpus.

### Host-based development

### Prerequisites
- [Bun](https://bun.sh/) runtime (see installation below)
- [Python 3.13](https://www.python.org/downloads/) with uv package manager
- Node.js 20+ (for tooling compatibility)
- Git

### Running Full Stack (Frontend + Backend)

The repository-root Docker Compose workflow above is the supported local path.
The host-based commands below are available when developing the frontend or
backend independently.

### Initial Setup

1. **Clone the repository**
   ```bash
   git clone https://github.com/YOUR_ORG/YOUR_REPOSITORY.git
   cd YOUR_REPOSITORY/Layer3_Campfire
   ```

2. **Install Bun** (if not already installed)
   
   **Windows (PowerShell):**
   ```powershell
   powershell -c "irm bun.sh/install.ps1|iex"
   ```
   
   **macOS/Linux:**
   ```bash
   curl -fsSL https://bun.sh/install | bash
   ```
   
   After installation, restart your terminal/IDE to use the `bun` command.

3. **Install dependencies**
   
   **Frontend:**
   ```bash
   cd frontend
   bun install
   ```
   
   **Backend:**
   ```bash
   cd ../backend
   pip install uv  # If not already installed
   uv sync
   ```

4. **Configure environment** (required for backend integration)
   
   **Frontend** (optional - defaults to mock mode):
   ```bash
   cd frontend
   cp .env.example .env.local
   ```
   Edit `.env.local` with backend endpoint when available.
   
   **Backend** (required):
   ```bash
   cd backend
   # Copy template and fill in Azure credentials
   cp .env.sample .env
   ```
   Required environment variables in `backend/.env`:
   - `AZURE_OPENAI_ENDPOINT`
   - `AZURE_OPENAI_API_KEY`
   - `AZURE_SEARCH_ENDPOINT`
   - `AZURE_SEARCH_API_KEY`
   - `AZURE_STORAGE_KEY`
   - `CAMPFIRE_SAFETY_ERROR_PATTERNS_FILE` pointing to a deployment-only,
     non-empty JSON list under an ignored `private/guardrails/` directory.

   `CAMPFIRE_SAFETY_ERROR_PATTERNS` may instead contain the JSON list inline;
   set only one of the two variables.

5. **Run the application**
   
   **Full Stack**: use the repository-root Docker Compose workflow described at
   the beginning of this README.
   
   **Frontend only**:
   ```bash
   cd frontend
   bun dev
   ```
   Open [http://localhost:3000](http://localhost:3000)
   
   **Backend only**:
   ```bash
   cd backend
   # Start Redis if it is not already running:
   docker run -d --name redis-local -p 6379:6379 \
     redis@sha256:02419de7eddf55aa5bcf49efb74e88fa8d931b4d77c07eff8a6b2144472b6952
   uv run uvicorn main:app --reload
   ```
   Open [http://localhost:8000/docs](http://localhost:8000/docs) for the
   interactive OpenAPI explorer — full endpoint reference, WebSocket message
   types, and citation field shape.

   **Backend troubleshooting:**
   - Port 8000 in use: `lsof -i :8000`, then `kill -9 <PID>`
   - Redis connection failed: confirm Docker is running and the
     `redis-local` container is started

### Available Commands

**Frontend** (from `frontend/` directory):

```bash
bun dev              # Start dev server (http://localhost:3000)
bun dev:clean        # Clean build cache before dev
bun build            # Production build
bun start            # Start production server
bun test             # Run Jest tests
bun test:watch       # Watch mode
bun test:coverage    # Run tests with coverage report
bun lint             # Run ESLint
```

**Backend** (from `backend/` directory):

```bash
uv run uvicorn main:app --reload        # Start dev server (http://localhost:8000)
uv run pytest                            # Run tests
uv run pytest --cov                      # Tests with coverage
```

**Evaluation** (from project root):

```bash
uv run python -m evaluation.run_eval --limit 3           # Quick test (3 cases)
uv run python -m evaluation.run_eval                      # Full evaluation
uv run python -m evaluation.compare_eval                 # Compare to baseline
```

## Project Structure

```
Layer3_Campfire/
├── frontend/          # Next.js 16 user interface
├── backend/           # Python FastAPI RAG service
└── scripts/           # Utility scripts (gallery curation, API tests)
```

## Tech Stack

- **Framework**: Next.js 16 (App Router)
- **Language**: TypeScript
- **Runtime**: Bun
- **UI Components**: Base UI
- **Styling**: CSS Modules
- **Animation**: GSAP
- **State Management**: Zustand + TanStack Query
- **Validation**: Zod
- **Testing**: Jest + React Testing Library

## Documentation

- [Frontend README](./frontend/README.md) - Detailed frontend architecture and conventions
- [Backend Integration](./frontend/README.md#backend-integration) - Backend API integration guide
- Interactive OpenAPI explorer at `/docs` on the running backend - Endpoints, WebSocket message types, citation field shape

## Development Guidelines

### Current Code Patterns
- **Styling**: CSS Modules with colocated `.module.css` files
- **UI Components**: Base UI wrappers in `components/ui/`
- **Exports**: Barrel exports via `index.ts` files
- **Validation**: Zod schemas for API contracts

### Responsible AI
This project follows Microsoft's Responsible AI principles:
- All chat responses must be grounded in verified historical documents
- Azure AI Search ensures known TR documents rank highest
- Evaluation framework with baseline management
- Transparent citation of sources

### Testing
- **Unit tests**: Jest + React Testing Library for components/hooks
- **E2E tests**: Playwright for full user flows (preferred for UI testing)
- Run tests before committing: `bun test`

## Maintenance

This is a completed implementation showcase. Contributions are limited to
security maintenance and documentation corrections that preserve the published
behavior. Run the relevant tests before opening a pull request.

## Architecture

**Frontend**: Next.js 16 with TypeScript, GSAP animations, Zustand state management
**Backend**: Python FastAPI with multi-agent RAG system
- WebSocket `/ws/chat` for streaming frontend interactions
- REST `/api/chat` for evaluation frameworks and external integrations
- Multi-agent pipeline:
  1. **Scope Agent** — classifies questions as in-scope vs. out-of-scope
  2. **Search Agent** — routes to book/letter indexes and generates optimised search queries
  3. **RAG Agent** — synthesises responses with `[N]` inline citations
- **Chat history**: Redis-backed, multi-turn context, per-user organisation  
**Search**: Azure AI Search with semantic ranking, separate book/letter indexes, hybrid (vector + keyword)  
**Evaluation**: Baseline management with 24 test cases (retrieval, abstention, sensitive content)

See the `/docs` OpenAPI explorer on the running backend for endpoint and citation documentation.

## Bun Runtime Notes

### New to Bun?

Bun is a fast JavaScript runtime (like Node.js) with a built-in package manager. Key differences from npm/yarn:

- **Commands**: Use `bun install` (not `npm install`) and `bun dev` (not `npm run dev`)
- **Speed**: Bun is significantly faster for installing dependencies and running scripts
- **Compatibility**: Works with existing npm packages - no need to change package.json
- **No node_modules pollution**: Bun's cache is more efficient

### After Installing Bun

Restart your terminal/IDE for the `bun` command to work. If you can't restart immediately, use the full path:
- **Windows**: `C:\Users\<username>\.bun\bin\bun.exe`
- **macOS/Linux**: `~/.bun/bin/bun`

## Troubleshooting

### Bun command not found
Restart your terminal/IDE. If still not working, check PATH:
- **Windows**: `%USERPROFILE%\.bun\bin`
- **macOS/Linux**: `~/.bun/bin`

### Permission errors on Windows
If you get permission errors during `bun install`:
```powershell
bun install --force
```

### Port already in use
If port 3000 is occupied:
```bash
# Kill the process or use a different port
PORT=3001 bun dev
```

### Module not found errors
Clear Next.js cache and reinstall:
```bash
rm -rf .next node_modules
bun install
bun dev
```

---

**Updated**: February 5, 2026