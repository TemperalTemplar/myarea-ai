"""
POST /api/chat

Main interaction endpoint. Accepts a user message, runs it through the
dispatcher, streams Silex's response as SSE.

Supports both streaming (SSE) and blocking (JSON) modes.
"""
import time
import os
import logging
from flask import Blueprint, request, Response, stream_with_context, jsonify, current_app

from ..dispatcher import (
    build_plan,
    get_session_messages, get_injected_context,
    append_turn, new_session_id,
)
from ..llm import stream_chat, complete_chat, stream_to_sse, sse_error, sse_done
from ..auth import resolve_tier
from ..auth.sso import get_current_user

logger = logging.getLogger(__name__)
chat_bp = Blueprint("chat", __name__)


@chat_bp.post("/chat")
def chat():
    data = request.get_json(silent=True) or {}

    message      = (data.get("message") or "").strip()
    session_id   = data.get("session_id") or new_session_id()
    do_stream    = data.get("stream", True)
    context_hint = data.get("context_hint")

    if not message:
        return jsonify({"error": "message is required"}), 400

    # ── Thermal safety gate ──────────────────────────────────────────────
    # The Tesla P4 is passively cooled. If the GPU is hot, refuse inference
    # and tell the client to wait. Server-enforced (cannot be bypassed via
    # direct API calls). Threshold 57C; client should retry when it cools.
    try:
        from ..memory.capture import _gpu_temp
        _THERMAL_LIMIT = int(os.environ.get("CHAT_TEMP_LIMIT", "57"))
        _gt = _gpu_temp()
        if _gt is not None and _gt >= _THERMAL_LIMIT:
            return jsonify({
                "cooling": True,
                "gpu_temp": _gt,
                "limit": _THERMAL_LIMIT,
                "message": f"Silex is cooling down (GPU {_gt}\u00b0C). Holding your message until it's safe to run.",
            }), 503
    except Exception as _therm_exc:
        logger.warning("thermal gate check failed: %s", _therm_exc)

    tier = resolve_tier(request)

    # Username from SSO session (Silex knows who she's talking to)
    user = get_current_user()
    user_name = None
    if user:
        user_name = user.get("name") or user.get("username") or None

    # ── Single-user exclusive lock (single GPU) ──────────────────────────
    # Only one user may actively use Silex at a time. The Tesla P4 is one
    # card; concurrent inference competes for VRAM/compute and doubles
    # thermal load. Architect (ALVA_IDENTITIES) can force-take the lock.
    try:
        from ..auth.userlock import check_access, acquire
        _luid = (user_name or "").strip().lower()
        if _luid:
            _ok, _holder = check_access(_luid)
            if not _ok:
                return jsonify({
                    "locked": True,
                    "holder": _holder,
                    "message": f"Silex is in use by another user ({_holder}). "
                               f"Single-GPU system \u2014 one active session at a time. Please wait.",
                }), 423
            acquire(_luid)
    except Exception as _lock_exc:
        logger.warning("userlock check failed: %s", _lock_exc)

    # Resolve project (Phase 7): request overrides session meta.
    project_id = data.get("project")
    project_collection = None
    if not project_id:
        try:
            from ..dispatcher.session import get_session_project
            project_id = get_session_project(session_id)
        except Exception:
            project_id = None
    if project_id and user_name:
        try:
            from ..extensions import get_redis
            import json as _json
            uid = user_name.strip().lower()
            blob = get_redis().hget(f"silex:user:{uid}:projects", project_id)
            if blob:
                project_collection = _json.loads(blob).get("collection")
        except Exception as _pe:
            logger.warning("project resolve failed: %s", _pe)

    # ── Build dispatch plan (classify intent → select model + system) ──────
    try:
        plan = build_plan(message, tier=tier, context_hint=context_hint, user_name=user_name, project_collection=project_collection)
    except Exception as exc:
        logger.error("Dispatcher error: %s", exc)
        if do_stream:
            return Response(
                sse_error("Dispatcher unavailable"),
                mimetype="text/event-stream",
            )
        return jsonify({"error": "Dispatcher unavailable"}), 503

    # ── Assemble message history ───────────────────────────────────────────
    history = get_session_messages(session_id)
    injected = get_injected_context(session_id)

    system = plan.system
    if injected:
        system += f"\n\n[PLATFORM CONTEXT]\n{injected}"

    # Phase 3.5 — live system awareness via warden (pre-fetch on relevant questions)
    try:
        from ..warden.awareness import gather_awareness
        sys_state = gather_awareness(message)
        if sys_state:
            system += f"\n\n{sys_state}"
    except Exception as _ws_exc:
        logger.warning("warden awareness failed: %s", _ws_exc)

    # Temporal awareness — chronoawareness via the Doctrine of Three Planes (Alva only)
    try:
        from ..awareness.temporal import gather_temporal_awareness
        temporal = gather_temporal_awareness(user_name, session_id)
        if temporal:
            system += f"\n\n{temporal}"
    except Exception as _ta_exc:
        logger.warning("temporal awareness failed: %s", _ta_exc)

    messages = history + [{"role": "user", "content": message}]

    # ── Persist user turn ──────────────────────────────────────────────────
    append_turn(session_id, "user", message, user_name=user_name)
    if project_id:
        try:
            from ..extensions import get_redis
            get_redis().hset(f"silex:session:{session_id}:meta", "project", project_id)
        except Exception:
            pass

    caller = request.headers.get("X-Silex-Caller", "ui")
    logger.info(
        "chat | session=%s tier=%s intent=%s model=%s caller=%s user=%s",
        session_id, tier, plan.intent, plan.model, caller, user_name,
    )

    # ── Stream mode ────────────────────────────────────────────────────────
    if do_stream:
        def generate_collecting():
            collected_chunks = []
            t0 = time.monotonic()
            try:
                chunk_gen = stream_chat(
                    model=plan.model,
                    messages=messages,
                    system=system,
                    temperature=plan.temperature,
                )
                for chunk in chunk_gen:
                    collected_chunks.append(chunk)
                    yield f"event: token\ndata: {_safe_sse_data(chunk)}\n\n"

                full_reply = "".join(collected_chunks)
                append_turn(session_id, "assistant", full_reply, user_name=user_name)

                latency = round((time.monotonic() - t0) * 1000)
                import json
                meta = json.dumps({
                    "session_id":  session_id,
                    "intent":      plan.intent,
                    "model_used":  plan.model,
                    "tier":        plan.tier,
                    "latency_ms":  latency,
                })
                yield f"event: done\ndata: {meta}\n\n"

            except RuntimeError as exc:
                import json
                yield f"event: error\ndata: {json.dumps({'error': str(exc)})}\n\n"

        return Response(
            stream_with_context(generate_collecting()),
            mimetype="text/event-stream",
            headers={
                "Cache-Control":               "no-cache",
                "X-Accel-Buffering":           "no",
                "Access-Control-Allow-Origin": "*",
            },
        )

    # ── Blocking mode ──────────────────────────────────────────────────────
    t0 = time.monotonic()
    try:
        reply = complete_chat(
            model=plan.model,
            messages=messages,
            system=system,
            temperature=plan.temperature,
        )
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503

    append_turn(session_id, "assistant", reply, user_name=user_name)
    latency = round((time.monotonic() - t0) * 1000)

    return jsonify({
        "reply":       reply,
        "session_id":  session_id,
        "intent":      plan.intent,
        "model_used":  plan.model,
        "tier":        plan.tier,
        "gated":       plan.gated,
        "latency_ms":  latency,
    })


def _safe_sse_data(text: str) -> str:
    """Escape newlines inside a single SSE data value."""
    return text.replace("\n", "\\n").replace("\r", "")
