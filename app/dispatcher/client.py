"""
Unified Ollama client.

Supports both streaming (generator of str chunks) and blocking calls.
All LLM calls go through here — swap backends by changing this file only.
"""
import json
import time
import logging
from typing import Generator, List, Dict, Any

import requests
from flask import current_app

logger = logging.getLogger(__name__)


def _base_url() -> str:
    return current_app.config["OLLAMA_BASE_URL"].rstrip("/")


def _timeout() -> int:
    return current_app.config["LLM_TIMEOUT"]


# ── Streaming ─────────────────────────────────────────────────────────────────

def stream_chat(
    model: str,
    messages: List[Dict[str, str]],
    system: str | None = None,
    temperature: float = 0.7,
) -> Generator[str, None, None]:
    """
    Yields text chunks as they arrive from Ollama's /api/chat endpoint.
    Raises RuntimeError on connection failure.
    """
    # Inject system prompt as first message if provided
    if system:
        messages = [{"role": "system", "content": system}] + messages

    payload: Dict[str, Any] = {
        "model":    model,
        "messages": messages,
        "stream":   True,
        "options":  {"temperature": temperature},
    }

    url = f"{_base_url()}/api/chat"

    try:
        with requests.post(url, json=payload, stream=True, timeout=_timeout()) as resp:
            resp.raise_for_status()
            for raw_line in resp.iter_lines():
                if not raw_line:
                    continue
                try:
                    chunk = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue

                content = chunk.get("message", {}).get("content", "")
                if content:
                    yield content

                if chunk.get("done"):
                    return

    except requests.exceptions.ConnectionError as exc:
        logger.error("Ollama connection failed: %s", exc)
        raise RuntimeError(f"Cannot reach Ollama at {_base_url()}") from exc
    except requests.exceptions.Timeout:
        raise RuntimeError("Ollama request timed out")


# ── Blocking ──────────────────────────────────────────────────────────────────

def complete_chat(
    model: str,
    messages: List[Dict[str, str]],
    system: str | None = None,
    temperature: float = 0.7,
) -> str:
    """
    Blocking call. Collects full streaming response and returns as a string.
    Used by the dispatcher classifier and internal workers.
    """
    return "".join(stream_chat(model, messages, system=system, temperature=temperature))


# ── Health check ──────────────────────────────────────────────────────────────

def model_available(model: str) -> bool:
    """Returns True if the model is loaded in Ollama."""
    try:
        url = f"{_base_url()}/api/tags"
        resp = requests.get(url, timeout=5)
        resp.raise_for_status()
        names = [m["name"] for m in resp.json().get("models", [])]
        # Match with or without tag suffix (gemma2:9b matches gemma2:9b-instruct etc.)
        base = model.split(":")[0]
        return any(n == model or n.startswith(base + ":") for n in names)
    except Exception:
        return False


def ollama_reachable() -> bool:
    try:
        requests.get(f"{_base_url()}/api/tags", timeout=3).raise_for_status()
        return True
    except Exception:
        return False
