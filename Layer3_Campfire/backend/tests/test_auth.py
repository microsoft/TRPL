"""Unit tests for auth.py covering header parsing and key verification."""

import os

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from auth import verify_api_key, verify_websocket_auth


@pytest.mark.asyncio
async def test_verify_api_key_accepts_valid_bearer():
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=os.environ["RAG_API_KEY"])
    result = await verify_api_key(creds)
    assert result == os.environ["RAG_API_KEY"]


@pytest.mark.asyncio
async def test_verify_api_key_accepts_lowercase_bearer():
    creds = HTTPAuthorizationCredentials(scheme="bearer", credentials=os.environ["RAG_API_KEY"])
    result = await verify_api_key(creds)
    assert result == os.environ["RAG_API_KEY"]


@pytest.mark.asyncio
async def test_verify_api_key_rejects_missing_credentials():
    with pytest.raises(HTTPException) as exc:
        await verify_api_key(None)
    assert exc.value.status_code == 401
    assert "Missing Authorization" in exc.value.detail


@pytest.mark.asyncio
async def test_verify_api_key_rejects_non_bearer_scheme():
    creds = HTTPAuthorizationCredentials(scheme="Basic", credentials=os.environ["RAG_API_KEY"])
    with pytest.raises(HTTPException) as exc:
        await verify_api_key(creds)
    assert exc.value.status_code == 401
    assert "scheme" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_verify_api_key_rejects_wrong_key():
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="not-the-key")
    with pytest.raises(HTTPException) as exc:
        await verify_api_key(creds)
    assert exc.value.status_code == 401
    assert "Invalid API key" in exc.value.detail


@pytest.mark.asyncio
async def test_verify_websocket_auth_accepts_valid_token():
    assert await verify_websocket_auth(os.environ["RAG_API_KEY"]) is True


@pytest.mark.asyncio
async def test_verify_websocket_auth_rejects_empty_token():
    assert await verify_websocket_auth("") is False


@pytest.mark.asyncio
async def test_verify_websocket_auth_rejects_wrong_token():
    assert await verify_websocket_auth("not-the-key") is False
