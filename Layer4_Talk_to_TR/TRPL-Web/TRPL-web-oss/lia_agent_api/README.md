# Debate API - Multi-Agent Debate Moderation System

A real-time debate moderation API powered by multiple AI agents that facilitate structured discussions between human participants. The system uses WebSockets for bidirectional communication and orchestrates debate flow through specialized AI agents acting as moderators, questioners, and critics.

## Overview

This system enables live, moderated debates where:

- **Human participants** express positions on a topic through different camps (e.g., "We should release" vs "We should not release")
- **AI agents** act as moderators, asking questions, facilitating discussion, providing analysis, and summarizing outcomes
- **Real-time interaction** happens via WebSocket connections with both spoken output (text-to-speech ready) and touchscreen prompts for participant responses

The debate engine orchestrates a structured flow through multiple phases: environment analysis, opening statements, questioning rounds, explanation, follow-ups, and final summary.

## Architecture

### Core Components

- **FastAPI Application** (`api/main.py`) - REST API for session management and WebSocket endpoints
- **Debate Engine** (`services/debate_engine.py`) - Orchestrates debate flow and manages AI agent interactions
- **State Machine** (`debate/graph.py`) - Defines debate flow through nodes (ENV_ANALYSIS → OPENING → EXPLAIN → CRITIC → FOLLOW_UP → SUMMARY)
- **Debate Nodes** (`debate/nodes.py`) - Individual phase implementations with AI agent interactions
- **Session Store** (`services/session_store.py`) - Manages active debate sessions and state persistence

### AI Agents

The system uses six specialized AI agents:

1. **EnvAnalyst** - Analyzes the debate environment, selects first speaker, generates opening speech
2. **QuestionerBot** - Asks targeted questions to participants based on their positions
3. **CriticAgent** - Provides Roosevelt-style reflections on explanations and determines if follow-ups are needed
4. **FollowUpAgent** - Generates follow-up questions and options for participant selection
5. **ExplainerBot** - Explains positions when participants don't respond
6. **Summarizer** - Generates final debate summary

### Message Flow

**Server → Client:**
- `debate_output` - User-facing messages with optional text and touchscreen prompts
- `debug_message` - Internal agent communications (optional, for debugging)

**Client → Server:**
- `participant_input` - Spoken input and/or touchscreen responses from participants

## Development Setup

### Provider-free local stack

The repository-root `layer4` Compose profile uses
`LIA_RUNTIME_MODE=deterministic`. It starts without LLM, Search, Speech,
LiveKit, avatar, hardware, or private guardrail values while preserving
authenticated REST sessions, single-use WebSocket tokens, ping/pong transport,
and camera event routing.

```bash
python3 Layer1_Data_foundations/scripts/local_dev.py configure
docker compose --env-file Layer1_Data_foundations/.env.local \
  --profile layer4 up --build -d
python3 Layer4_Talk_to_TR/scripts/verify_local_stack.py
```

In deterministic mode `/healthz` reports every external provider as unavailable.
No generated response is presented as AI output. Cloud mode remains the default
and fails at startup when required LLM configuration is missing.

### Prerequisites

- Python 3.11+
- LLM API access (OpenAI-compatible API, configured via environment variables)

### Installation

1. **Clone the repository** (if applicable) or navigate to the project directory

2. **Create a virtual environment:**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   npm install
   ```

4. **Set up environment variables:**
   
   Create a `.env` file in the project root with:
   ```env
   # LLM Configuration
   LLM_MODEL=gpt-4o-mini  # or your preferred model
   LLM_BASE_URL=          # Leave empty for OpenAI, or set for compatible API
   LLM_API_KEY=           # Your API key
   
   # Server Configuration (optional)
   HOST=0.0.0.0
   PORT=8000
   APP_ENV=dev
   ALLOWED_ORIGINS=http://localhost:8000,http://localhost:3000
   
   # Authentication (optional)
   BASIC_AUTH_USERNAME=   # For basic auth
   BASIC_AUTH_PASSWORD=
   CLIENT_API_KEYS=       # Comma-separated API keys for API key auth

   # WebSocket auth rollout (optional, defaults shown)
   WS_AUTH_ENABLED=false
   WS_CONTROLLER_TOKEN_TTL_SECONDS=120
   WS_OBSERVER_TOKEN_TTL_SECONDS=120
   WS_RECONNECT_TOKEN_TTL_SECONDS=3600
   WS_JOIN_CODE_TTL_SECONDS=3600
   ```

   Guardrail values are deployment-only and have no public fallback. Configure
   each value either as inline JSON/text or as a file path:

   | Inline variable | File variable | Format |
   | --- | --- | --- |
   | `LIA_PROMPT_INJECTION_CONFIG` | `LIA_PROMPT_INJECTION_CONFIG_FILE` | JSON object |
   | `LIA_OUTPUT_REVIEWER_CONFIG` | `LIA_OUTPUT_REVIEWER_CONFIG_FILE` | JSON object |
   | `LIA_ROOSEVELT_GUARDRAILS` | `LIA_ROOSEVELT_GUARDRAILS_FILE` | Text |
   | `LIA_JAILBREAK_CASES` | `LIA_JAILBREAK_CASES_FILE` | JSON array |
   | `LIA_THREAT_PATTERNS` | `LIA_THREAT_PATTERNS_FILE` | JSON array |

   Private files may be mounted from a secret store or placed under an ignored
   `private/guardrails/` directory. Set only one variable from each row.

   The prompt-injection object contains `patterns`, `categories`,
   `clean_category`, `classifier_prompt`, `redaction_marker`, `directives`,
   `default_directive`, `l1_confidence`, `l2_default_confidence`,
   `provider_filter_signals`, and `provider_filter_result`. Pattern entries
   contain `category`, `expression`, and optional `flags`. The output-reviewer
   object contains `system_prompt`, `categories`, `clean_category`, and
   `default_confidence`. Regression-case entries contain `id`, `turns`, and
   `fail_if`. No real values belong in Git.

5. **Run the server:**
   ```bash
   python run_server.py
   ```
   
   Or using uvicorn directly:
   ```bash
   uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
   ```

6. **Access the API:**
   - API Docs: http://localhost:8000/docs
   - WebSocket Test Client: http://localhost:8000/test
   - AsyncAPI Documentation: http://localhost:8000/asyncapi

### Updating AsyncAPI docs

The interactive site is generated from package-managed dependencies during
`npm install`. If changing the WebSocket interface:

1. Update static/asyncapi.yaml to reflect the change
2. Re-generate the interactive AsyncAPI docs:

```bash
npm run generate:asyncapi
```

The generated `static/asyncapi/` directory is intentionally excluded from Git.

## Usage

### Starting a Debate Session

**POST** `/api/debate/start`

```json
{
  "question_map": {
    "Q1": "We should release",
    "Q2": "We should not release"
  },
  "core_issue": "Balance public safety against market stability",
  "players": {
    "Dan": {
      "camp": "Q1",
      "reason": "Congress won't act swiftly enough."
    },
    "Jen": {
      "camp": "Q2",
      "reason": "Avoid panic; let Congress work."
    },
    "Tom": {
      "camp": "Q1",
      "reason": "Transparency builds trust and pressure."
    }
  },
  "max_rounds": 20
}
```

**Response:**
```json
{
  "session_id": "abc123-def456-ghi789",
  "ws_url": "/api/debate/ws/abc123-def456-ghi789",
  "controller_token": null,
  "reconnect_token": null,
  "observer_join_code": null
}
```

When `WS_AUTH_ENABLED=true`, `controller_token`, `reconnect_token`, and `observer_join_code` are populated.

### Connecting via WebSocket

Connect to: `ws://localhost:8000/api/debate/ws/{session_id}`

If `WS_AUTH_ENABLED=true`, provide a short-lived single-use token:

- Query param: `ws://localhost:8000/api/debate/ws/{session_id}?token=...`
- Or header: `x-ws-token: ...`

Controller connection uses `controller_token` from `/api/debate/start`.
Observer connections use `observer_token` minted via `/api/debate/join/{session_id}`.

### Observer Join Flow (`WS_AUTH_ENABLED=true`)

**POST** `/api/debate/join/{session_id}`

Request:
```json
{
  "join_code": "join_..."
}
```

Response:
```json
{
  "session_id": "abc123-def456-ghi789",
  "ws_url": "/api/debate/ws/abc123-def456-ghi789",
  "observer_token": "obs_..."
}
```

### Controller Reconnect Flow (`WS_AUTH_ENABLED=true`)

**POST** `/api/debate/reconnect/{session_id}`

Request:
```json
{
  "reconnect_token": "recon_..."
}
```

Response:
```json
{
  "session_id": "abc123-def456-ghi789",
  "ws_url": "/api/debate/ws/abc123-def456-ghi789",
  "controller_token": "ctrl_...",
  "reconnect_token": "recon_..."
}
```

Notes:
- `controller_token` is single-use and short-lived.
- `observer_token` is single-use and short-lived.
- `reconnect_token` rotates on every successful `/reconnect` call.

**Receive Messages:**

```json
{
  "type": "debate_output",
  "timestamp": "2025-10-28T16:15:53.742048",
  "text": "Welcome to the debate. Dan, you chose to release...",
  "touchscreen_prompts": [
    {
      "participant_id": "Dan",
      "question": "Do you agree with this point?",
      "options": ["Yes", "No", "Partially"]
    }
  ],
  "waiting_for_input": true,
  "done": false,
  "debug": false
}
```

**Send Messages:**

```json
{
  "type": "participant_input",
  "spoken": {
    "participant_id": "Dan",
    "text": "I think this approach has merit because..."
  },
  "touchscreen_responses": {
    "Dan": "Yes",
    "Jen": "No"
  }
}
```

## Testing

Use the interactive test client at `/test` to:
- Create new debate sessions
- Connect via WebSocket
- Send participant input
- View debate output with debug message filtering
- Test touchscreen prompts

## Project Structure

```
lia_agent_api/
├── src/                # Source code directory
│   ├── api/
│   │   ├── auth/           # Authentication modules (basic, API key, Azure AD)
│   │   ├── routers/
│   │   │   └── debate.py   # Debate API endpoints
│   │   ├── config.py       # Configuration from environment variables
│   │   └── main.py         # FastAPI application
│   ├── debate/
│   │   ├── graph.py        # State machine for debate flow
│   │   ├── models.py       # Data models (DebateState, PlayerChoice, etc.)
│   │   ├── nodes.py        # Debate phase implementations
│   │   ├── prompts.py      # AI agent system prompts
│   │   └── utils.py        # Utility functions (input handler, etc.)
│   └── services/
│       ├── debate_engine.py    # Main debate orchestration
│       └── session_store.py    # Session management
├── static/
│   ├── asyncapi/       # AsyncAPI documentation UI
│   ├── asyncapi.yaml   # AsyncAPI specification
│   └── test.html       # WebSocket test client
├── .vscode/
│   └── launch.json     # VS Code debug configuration
├── requirements.txt
├── run_server.py       # Development server script
└── startup.sh          # Production startup script (Azure App Service)
```

## API Documentation

- **REST API**: http://localhost:8000/docs (Swagger UI)
- **WebSocket Interface**: http://localhost:8000/asyncapi (Interactive AsyncAPI docs)
- **AsyncAPI Spec**: http://localhost:8000/asyncapi.yaml (YAML download)

## Key Features

- ✅ Real-time WebSocket communication
- ✅ Multi-agent AI moderation with specialized roles
- ✅ Structured debate flow with rounds and follow-ups
- ✅ Support for both spoken and touchscreen participant input
- ✅ Debug mode for viewing internal agent communications
- ✅ Session management and history tracking
- ✅ AsyncAPI documentation for WebSocket interface

## Debating Flow

1. **Environment Analysis** - System analyzes topic, selects first speaker, generates opening
2. **Opening** - First speaker explains their position
3. **Questioning** - AI asks targeted questions to participants
4. **Explanation** - If participant doesn't respond, AI explains their position
5. **Critic** - AI provides reflection and determines if follow-up is needed
6. **Follow-up** - Additional questions based on participant responses
7. **Summary** - Final debate summary when rounds complete

## Configuration

See `api/config.py` for all configuration options. Key settings:

- `LLM_MODEL` - Model to use for AI agents
- `LLM_BASE_URL` - API base URL (empty for OpenAI)
- `LLM_API_KEY` - API key for LLM access
- `DEFAULT_MAX_ROUNDS` - Default maximum debate rounds
- Authentication modes (basic auth, API key, Azure AD)

## Azure App Service Deployment

### Application Insights Integration

The application is configured to automatically send logs to Azure Application Insights when deployed to Azure App Service.

**Setup:**

1. Create an Application Insights resource in Azure Portal (or use an existing one)
2. Copy the connection string from the Application Insights resource
3. Set the `APPLICATIONINSIGHTS_CONNECTION_STRING` environment variable in your Azure App Service configuration:
   - Azure Portal → App Service → Configuration → Application settings
   - Add: `APPLICATIONINSIGHTS_CONNECTION_STRING` = `InstrumentationKey=...;IngestionEndpoint=...`

**What gets logged:**

- All Python logging output (logger.info, logger.error, etc.) appears in the `traces` table in Log Analytics
- HTTP requests are automatically tracked as dependencies/requests
- Custom attributes like `session_id` are preserved as custom properties
- Logs continue to go to stdout (captured in `AppServiceConsoleLogs`) for backward compatibility

**Viewing logs:**

- Log Analytics workspace → `traces` table (for application logs)
- `AppServiceHTTPLogs` table (for HTTP access logs, continues to work as before)
- `AppServiceConsoleLogs` table (for stdout/stderr, continues to work as before)

The Application Insights SDK (`azure-monitor-opentelemetry`) automatically instruments FastAPI and captures all Python logging without requiring changes to existing logging code.
