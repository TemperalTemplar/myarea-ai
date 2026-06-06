"""
GET /api/status
Health check endpoint — used by AppsHub, deploy scripts, and uptime monitors.
Returns model availability, Redis health, uptime, and GPU temperature.
"""
import time
import logging
import subprocess
from flask import Blueprint, jsonify, current_app
from ..llm.client import model_available, ollama_reachable
from ..extensions import redis_client

logger = logging.getLogger(__name__)
status_bp = Blueprint("status", __name__)
_START_TIME = time.monotonic()


def _gpu_temp():
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=temperature.gpu", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5
        )
        return int(r.stdout.strip().split("\n")[0])
    except Exception:
        return None


@status_bp.get("/status")
def status():
    ollama_ok = ollama_reachable()
    dispatcher_model = current_app.config["DISPATCHER_MODEL"]
    silex_model      = current_app.config["SILEX_MODEL"]
    redis_ok = _check_redis()
    overall = "ok" if (ollama_ok and redis_ok) else "degraded"
    return jsonify({
        "status":   overall,
        "service":  "myarea-ai",
        "uptime_s": round(time.monotonic() - _START_TIME),
        "ollama":   "ok" if ollama_ok else "unreachable",
        "redis":    "ok" if redis_ok  else "unreachable",
        "gpu_temp": _gpu_temp(),
        "models": {
            "dispatcher": {
                "model":     dispatcher_model,
                "available": model_available(dispatcher_model) if ollama_ok else False,
            },
            "silex": {
                "model":     silex_model,
                "available": model_available(silex_model) if ollama_ok else False,
            },
        },
    }), 200 if overall == "ok" else 503


def _check_redis() -> bool:
    try:
        from ..extensions import redis_client
        redis_client.ping()
        return True
    except Exception:
        return False
