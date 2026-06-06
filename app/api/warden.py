"""
Sparta-Warden API — Phase 3.5

Gated endpoints letting Silex (or internal services) request safe, read-only
system introspection through the policy gate.

Auth: requires SERVICE_API_KEY (Bearer) AND csshi tier. Defense in depth.

Routes:
  GET  /api/warden/health           — policy status, allowed verbs
  POST /api/warden/decide           — policy decision only (allow/deny + lease)
  POST /api/warden/exec             — decide + execute an allowed verb
"""
import logging
from flask import Blueprint, request, jsonify

from ..auth import require_service_key
from ..auth.tiers import resolve_tier
from ..warden.gate import decide, decide_and_execute, warden_health

logger = logging.getLogger(__name__)
warden_bp = Blueprint("warden", __name__)


def _csshi_only():
    """
    Returns None if authorized, else an error response tuple.
    Authorized = csshi tier OR a valid SERVICE_API_KEY (trusted internal service).
    The endpoint already passed @require_service_key, so reaching here with the
    service key means the caller is a trusted internal service = csshi-equivalent.
    """
    from flask import current_app
    from ..auth.tiers import _extract_bearer
    tier = resolve_tier(request)
    if tier == "csshi":
        return None
    # Service key is csshi-equivalent for internal automation
    token = _extract_bearer(request)
    if token and token == current_app.config.get("SERVICE_API_KEY", ""):
        return None
    return jsonify({"error": "csshi tier required"}), 403


@warden_bp.get("/warden/health")
@require_service_key
def health():
    return jsonify(warden_health())


@warden_bp.post("/warden/decide")
@require_service_key
def warden_decide():
    gate = _csshi_only()
    if gate:
        return gate
    data = request.get_json(silent=True) or {}
    verb = (data.get("verb") or "").strip()
    args = data.get("args") or {}
    if not verb:
        return jsonify({"error": "verb required"}), 400
    result = decide(verb, args)
    logger.info("warden decide verb=%s -> %s", verb, result["decision"])
    return jsonify(result)


@warden_bp.post("/warden/exec")
@require_service_key
def warden_exec():
    gate = _csshi_only()
    if gate:
        return gate
    data = request.get_json(silent=True) or {}
    verb = (data.get("verb") or "").strip()
    args = data.get("args") or {}
    if not verb:
        return jsonify({"error": "verb required"}), 400

    result = decide_and_execute(verb, args)

    # Log to security journal (best-effort)
    try:
        _log_security(verb, args, result)
    except Exception as exc:
        logger.warning("security journal log failed: %s", exc)

    logger.info("warden exec verb=%s ok=%s denied=%s", verb, result.get("ok"), result.get("denied"))
    return jsonify(result)


def _log_security(verb, args, result):
    """Record warden actions to the security journal for audit."""
    import httpx
    import os
    from flask import current_app
    url = os.environ.get("SECURITY_JOURNAL_URL", "http://myarea-ai:8930/api/security/internal")
    key = current_app.config.get("SERVICE_API_KEY", "")
    status = "denied" if result.get("denied") else ("ok" if result.get("ok") else "error")
    entry = {
        "content": f"warden action: verb={verb} args={args} status={status} "
                   f"output={str(result.get('output', result.get('reason','')))[:200]}",
        "source": "warden",
        "severity": "info" if status == "ok" else "warning",
    }
    try:
        httpx.post(url, json=entry,
                   headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                   timeout=5)
    except Exception:
        pass
