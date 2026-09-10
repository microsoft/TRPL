# Authentication Module

This API currently supports two auth methods in production code paths:

- HTTP Basic Auth (`BASIC_AUTH_USERNAME` + `BASIC_AUTH_PASSWORD`)
- API key auth (`CLIENT_API_KEYS` via `x-api-key` header)

Both can be enabled at once.

## Configuration

Set environment variables in `.env`:

```bash
# Basic auth
BASIC_AUTH_USERNAME=<basic-auth-username>
BASIC_AUTH_PASSWORD=<basic-auth-password>

# API key auth (comma-separated list)
CLIENT_API_KEYS=key-one,key-two
```

Notes:

- `CLIENT_API_KEYS` values are trimmed and empty entries are ignored.
- If both methods are configured, auth succeeds when either method succeeds.

## Usage in Routers

Use the dependency factory form:

```python
from fastapi import Depends
from api.auth import require_auth

@router.get("/protected")
async def protected(user: dict = Depends(require_auth())):
    return {"auth_mode": user.get("auth_mode")}
```

## Request Examples

Basic auth:

```bash
curl -u "<basic-auth-username>:<basic-auth-password>" http://localhost:8000/api/debate/health
```

API key auth:

```bash
curl -H "x-api-key: key-one" http://localhost:8000/api/debate/health
```

## Module Scope

This package intentionally contains only the active auth path:

- `base.py`: dependency factory and shared `AuthError`
- `multi.py`: orchestrates Basic/API key auth
- `basic.py` and `api_key.py`: concrete authenticators
