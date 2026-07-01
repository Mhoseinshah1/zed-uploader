"""API tests — offline (no live DB/Redis)."""
from __future__ import annotations


def test_health_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_stats_without_api_key_is_401(client):
    # Rate limiter is fail-open when Redis is offline, so the API-key check runs
    # and rejects the missing key.
    response = client.get("/api/stats")
    assert response.status_code == 401


def test_webhook_wrong_secret_is_403(client):
    response = client.post(
        "/telegram/webhook",
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong-secret"},
        json={"update_id": 1},
    )
    assert response.status_code == 403
