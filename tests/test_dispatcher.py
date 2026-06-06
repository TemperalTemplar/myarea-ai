"""
Test: dispatcher intent classification.
Run: pytest tests/
"""
import pytest
from unittest.mock import patch
from app import create_app
from app.config import Config


class TestConfig(Config):
    TESTING         = True
    REDIS_URL       = "redis://localhost:6379/15"  # isolated test DB
    SERVICE_API_KEY = "test-key"
    DISPATCHER_MODEL = "gemma2:2b"
    SILEX_MODEL      = "gemma2:9b"


@pytest.fixture
def app():
    app = create_app(TestConfig)
    yield app


@pytest.fixture
def client(app):
    return app.test_client()


class TestDispatcher:
    def test_classify_intent_returns_valid(self, app):
        """classify_intent should always return a valid intent string."""
        from app.dispatcher.router import classify_intent, VALID_INTENTS

        # Mock complete_chat to return a known intent
        with app.app_context():
            with patch("app.dispatcher.router.complete_chat", return_value="task"):
                intent = classify_intent("write me a haiku")
                assert intent in VALID_INTENTS

    def test_classify_intent_fallback(self, app):
        """classify_intent should return DEFAULT_INTENT on LLM failure."""
        from app.dispatcher.router import classify_intent, DEFAULT_INTENT

        with app.app_context():
            with patch("app.dispatcher.router.complete_chat", side_effect=RuntimeError("down")):
                intent = classify_intent("hello")
                assert intent == DEFAULT_INTENT

    def test_build_plan_returns_plan(self, app):
        from app.dispatcher.router import build_plan, DispatchPlan

        with app.app_context():
            with patch("app.dispatcher.router.complete_chat", return_value="casual"):
                plan = build_plan("hey silex", tier="ssh")
                assert isinstance(plan, DispatchPlan)
                assert plan.intent == "casual"
                assert plan.tier   == "ssh"
