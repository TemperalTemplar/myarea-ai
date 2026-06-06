"""
Internal platform endpoints.
All routes require SERVICE_API_KEY via Authorization: Bearer header.

Routes:
  POST /api/internal/inject              — push context into a session
  GET  /api/internal/memory/<session_id> — read session memory
  POST /api/internal/chaos-trigger       — Phase 4 stub
  DELETE /api/internal/memory/<session_id> — clear session
"""
import logging
from flask import Blueprint, request, jsonify, current_app

from ..auth import require_service_key
from ..dispatcher.session import (
    inject_context, get_session_messages,
    get_injected_context, clear_session,
)

logger = logging.getLogger(__name__)
internal_bp = Blueprint("internal", __name__)


@internal_bp.post("/inject")
@require_service_key
def inject():
    data = request.get_json(silent=True) or {}
    session_id = data.get("session_id", "").strip()
    source_app = data.get("source_app", "unknown")
    content    = data.get("content", "").strip()
    ttl        = int(data.get("ttl_seconds", current_app.config["NCAIDSSHM_TTL"]))

    if not session_id or not content:
        return jsonify({"error": "session_id and content are required"}), 400

    inject_context(session_id, source_app, content, ttl)
    logger.info("inject | session=%s source=%s ttl=%d", session_id, source_app, ttl)
    return jsonify({"ok": True, "session_id": session_id})


@internal_bp.get("/memory/<session_id>")
@require_service_key
def read_memory(session_id: str):
    turns    = get_session_messages(session_id)
    injected = get_injected_context(session_id)
    return jsonify({
        "session_id": session_id,
        "turns":      turns,
        "injected":   injected,
    })


@internal_bp.delete("/memory/<session_id>")
@require_service_key
def delete_memory(session_id: str):
    clear_session(session_id)
    return jsonify({"ok": True, "session_id": session_id})


@internal_bp.post("/chaos-trigger")
@require_service_key
def chaos_trigger():
    """
    Phase 4 stub.
    Will accept a generated utterance from the chaos Celery worker
    and route it to the comms line (Discord / push notification).
    """
    data = request.get_json(silent=True) or {}
    logger.info("chaos-trigger received (stub): %s", data)
    return jsonify({"ok": True, "status": "stub — Phase 4 not yet implemented"})
