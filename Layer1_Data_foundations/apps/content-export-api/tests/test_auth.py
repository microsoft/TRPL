_RANGE = "approved_from=2026-03-01T00:00:00Z&approved_to=2026-03-16T00:00:00Z"


def test_missing_api_key_returns_401(client):
    response = client.get(f"/api/v1/approved-records?{_RANGE}")
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"


def test_invalid_api_key_returns_401(client):
    response = client.get(
        f"/api/v1/approved-records?{_RANGE}",
        headers={"X-API-Key": "wrong-key"},
    )
    assert response.status_code == 401


def test_health_does_not_require_api_key(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
