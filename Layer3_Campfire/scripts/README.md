# Scripts

Utility and integration test scripts for the Reading Room project. Run all scripts from the **repo root** directory.

## Directory Structure

```
scripts/
├── README.md                      # This file
└── test_api_endpoint.py           # WebSocket & REST API integration tests
```

## API Integration Test (`test_api_endpoint.py`)

Comprehensive integration test suite for the FastAPI chat endpoints. Tests WebSocket streaming, REST API, multi-turn conversations, chat history persistence, and out-of-scope handling.

**Prerequisites:**
- FastAPI server running on `localhost:8000`
- Redis available and configured
- Azure AI Search indices populated

**Running:**
```bash
uv run python scripts/test_api_endpoint.py
```

**What's tested:**
- Multi-turn WebSocket conversation (3 turns with context)
- Single-turn WebSocket conversation
- Chat history restoration across reconnections
- Out-of-scope question handling
- REST API multi-turn conversation (`POST /api/chat`)
- Chat history API (`GET /api/chat-history/{user_id}`)
- Chat messages API (`GET /api/chat-history/{user_id}/{chat_id}/messages`)
- Chat deletion API (`DELETE /api/chat-history/{user_id}/{chat_id}`)

Returns exit code 0 if all tests pass, 1 if any fail.
