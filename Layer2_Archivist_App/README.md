# ArchivistApp

Theodore Roosevelt Presidential Library Archivist Application - A modern full-stack application for reviewing and validating AI-generated archival data.

## Architecture

This is a monorepo containing:

- **Frontend App** (`apps/archivist-app/`) - React 19 + TypeScript + Vite + TailwindCSS
- **Backend API** (`apps/archivist-api/`) - FastAPI (Python)
- **Azure Functions** (`apps/functions/`) - Data ingestion functions

Production infrastructure and deployment configuration are intentionally not
included in this repository.

## Running Locally

### Shared Docker environment

The repository-root Compose stack is the recommended path because it starts
Layer 1, shared Azurite and Cosmos emulators, the Service Bus emulator, and all
Layer 2 services together.

From the repository root:

```bash
python3 Layer1_Data_foundations/scripts/local_dev.py doctor
python3 Layer1_Data_foundations/scripts/local_dev.py configure
```

Review the Service Bus emulator and SQL Server license links in
`Layer1_Data_foundations/.env.local.example`, then set `ACCEPT_EULA=Y` in the
ignored `Layer1_Data_foundations/.env.local` file and run:

```bash
docker compose --env-file Layer1_Data_foundations/.env.local \
  --profile full-stack up --build -d
```

Local endpoints:

| Service | URL |
| --- | --- |
| Archivist UI | <http://127.0.0.1:5173> |
| Archivist API and docs | <http://127.0.0.1:8000/docs> |
| Layer 1 Functions | <http://127.0.0.1:7071> |
| Layer 2 Functions | <http://127.0.0.1:7072> |
| Cosmos Explorer | <http://127.0.0.1:1234> |
| Service Bus health | <http://127.0.0.1:5300/health> |

Confirm publication from the Archivist API service and consumption by both
Layer 2 queue triggers:

```bash
python3 Layer2_Archivist_App/scripts/verify_local_queue_flow.py
```

The verifier drives the normal Archivist API operations and confirms their
resulting document state after both queue handlers run. Service Bus, Cosmos, and
Storage account-key/connection-string credentials are rejected outside local
mode. Cloud deployments continue to use managed identity.

The local stack supports browsing Layer 1 synthetic records and EPUB
parse/extract/delete work against shared Cosmos and Azurite. EPUB embedding and
search ingestion, and document publication to AI Search, still require
operator-provided Azure OpenAI and Azure AI Search endpoints.

Follow the Layer 1 synthetic ingestion walkthrough in
[`../Layer1_Data_foundations/README.md`](../Layer1_Data_foundations/README.md)
to verify a record from Layer 1 reaches the Layer 2 ingestion trigger.

View logs or stop the stack with:

```bash
docker compose --env-file Layer1_Data_foundations/.env.local \
  --profile full-stack logs -f layer2-api layer2-functions layer2-app

docker compose --env-file Layer1_Data_foundations/.env.local \
  --profile full-stack down
```

### Prerequisites

- **Node.js 20.19+** and npm 10.8.2
- **Python 3.10+**
- Git

### Step 1: Install Root Dependencies

From the project root directory:

```powershell
npm install
```

### Step 2: Set Up and Run the Backend API

Open a terminal and run:

```powershell
# Navigate to the API directory
cd apps\archivist-api

# Create a virtual environment (first time only)
python -m venv venv

# Activate the virtual environment
.\venv\Scripts\Activate.ps1

# Install dependencies (first time only)
pip install -r requirements.txt

# Run the API server
python app.py
```

The API will start on **http://127.0.0.1:8000**

Available endpoints:

- `GET /health` - Health check
- `GET /items/{item_id}` - Sample item endpoint

### Step 3: Set Up and Run the Frontend App

Open a **new terminal** window and run:

```powershell
# From project root, navigate to frontend
cd apps\archivist-app

# Install dependencies (first time only)
npm install

# Run the development server
npm run dev
```

The frontend will start on **http://localhost:5173**

The Vite dev server is configured to proxy API requests from `/api` and `/login` to the backend at `http://127.0.0.1:8000`.

### Step 4: Access the Application

Open your browser and navigate to:

- **Frontend**: http://localhost:5173
- **Backend API Docs**: http://127.0.0.1:8000/docs (FastAPI auto-generated docs)

## Development

### Frontend Development

```powershell
cd apps\archivist-app
npm run dev      # Start dev server
npm run build    # Build for production
npm run lint     # Run linter
npm run preview  # Preview production build
```

### Backend Development

```powershell
cd apps\archivist-api
.\venv\Scripts\Activate.ps1  # Activate virtual environment
python app.py                 # Run with hot reload
```

### Running Tests

```powershell
# From project root
npm run test     # Run all tests
npm run lint     # Run all linters
```

## Project Structure

```
ArchivistApp/
├── apps/
│   ├── archivist-api/        # FastAPI backend
│   │   ├── api/              # API routes
│   │   ├── core/             # Configuration
│   │   ├── models/           # Data models
│   │   ├── services/         # Business logic
│   │   └── app.py            # Main application
│   ├── archivist-app/        # React frontend
│   │   ├── src/
│   │   │   ├── components/   # React components
│   │   │   ├── pages/        # Page components
│   │   │   ├── hooks/        # Custom hooks
│   │   │   ├── types/        # TypeScript types
│   │   │   └── data/         # Mock data
│   │   └── package.json
│   └── functions/            # Azure Functions
├── tools/                    # Utility scripts
└── package.json              # Root package.json
```

## Troubleshooting

### Backend Issues

**Error: "Could not import module 'app.app'"**

- Make sure you're running `python app.py` from the `apps/archivist-api` directory
- Ensure your virtual environment is activated

**Module not found errors**

- Activate the virtual environment: `.\venv\Scripts\Activate.ps1`
- Reinstall dependencies: `pip install -r requirements.txt`

### Frontend Issues

**Port already in use**

- Vite will automatically try the next available port
- Or stop the process using the port

**API requests failing**

- Ensure the backend is running on port 8000
- Check the proxy configuration in `vite.config.js`
