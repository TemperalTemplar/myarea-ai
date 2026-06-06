"""
Test: API endpoints.
"""
import pytest
from unittest.mock import patch
from app import create_app
from app.config import Config


class TestConfig(Config):
    TESTING         = True
    REDIS_URL       = "redis://localhost:6379/15"
    SERVICE_API_KEY = "test-key"


@pytest.fixture
def app():
    return create_app(TestConfig)


@pytest.fixture
def client(app):
    return app.test_client()


class TestStatus:
    def test_status_returns_json(self, client):
        with patch("app.api.status.ollama_reachable", return_value=False), \
             patch("app.api.status._check_redis",     return_value=False):
            r = client.get("/api/status")
            assert r.content_type.startswith("application/json")
            data = r.get_json()
            assert "status" in data
            assert "models" in data


class TestChat:
    def test_chat_requires_message(self, client):
        r = client.post("/api/chat", json={}, content_type="application/json")
        assert r.status_code == 400

    def test_chat_blocking_smoke(self, client):
        with patch("app.dispatcher.router.complete_chat", return_value="casual"), \
             patch("app.llm.client.complete_chat",        return_value="hello from silex"), \
             patch("app.dispatcher.session.redis_client") as mock_redis:
            mock_redis.lrange.return_value = []
            mock_redis.rpush.return_value  = 1
            mock_redis.expire.return_value = True
            mock_redis.lrange.return_value = []

            r = client.post("/api/chat", json={
                "message": "hello",
                "stream":  False,
            })
            assert r.status_code == 200
            data = r.get_json()
            assert "reply" in data
            assert "intent" in data


class TestInternal:
    def test_inject_requires_auth(self, client):
        r = client.post("/api/internal/inject", json={
            "session_id": "abc",
            "content":    "test",
        })
        assert r.status_code == 401

    def test_inject_with_key(self, client):
        with patch("app.dispatcher.session.redis_client") as mock_redis:
            mock_redis.rpush.return_value  = 1
            mock_redis.expire.return_value = True

            r = client.post("/api/internal/inject",
                headers={"Authorization": "Bearer test-key"},
                json={
                    "session_id": "test-session",
                    "source_app": "social",
                    "content":    "user posted about weather",
                },
            )
            assert r.status_code == 200
            assert r.get_json()["ok"] is True
