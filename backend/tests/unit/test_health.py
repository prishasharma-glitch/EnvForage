"""Tests for the /health endpoint."""
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app


def test_health_returns_200():
    client = TestClient(create_app())
    response = client.get("/health")
    assert response.status_code == 200


def test_health_returns_correct_payload():
    settings = get_settings()
    client = TestClient(create_app())
    response = client.get("/health")
    assert response.json() == {
        "status": "healthy",
        "service": "EnvForage",
        "version": settings.app_version,
    }
