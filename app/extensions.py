"""
Extensions — Redis client initialization.

Uses a module-level __getattr__ so that `from .extensions import redis_client`
ALWAYS resolves to the live, initialized client rather than a stale None
captured at import time. This fixes early-binding imports in session.py etc.
"""
import os
import redis as _redis

_redis_client = None


def init_extensions(app):
    global _redis_client
    _redis_client = _redis.from_url(
        app.config["REDIS_URL"],
        decode_responses=True,
    )
    app.extensions["redis"] = _redis_client
    return _redis_client


def get_redis():
    """Return the live redis client, initializing from env if needed."""
    global _redis_client
    if _redis_client is None:
        url = os.environ.get("REDIS_URL", "redis://myarea-ai-redis:6379/0")
        _redis_client = _redis.from_url(url, decode_responses=True)
    return _redis_client


def __getattr__(name):
    # Late-bind `redis_client` so importers always get the live instance.
    if name == "redis_client":
        return get_redis()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
