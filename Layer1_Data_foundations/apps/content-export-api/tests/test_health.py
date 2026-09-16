# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

def test_health_unauthenticated(client):
    for path in ("/api/v1/health", "/health"):
        response = client.get(path)
        assert response.status_code == 200, path
        body = response.json()
        assert body["status"] == "healthy"
        assert body["service"] == "content-export-api"
        assert body["checks"]["cosmos"] == "skipped (local)"
