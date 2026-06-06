"""
Comms API — Phase 6.

Endpoints:
  POST /api/comms/dispatch          — manually dispatch a journal/alert entry
  POST /api/comms/test              — test Rocket.Chat + email (CSSHI only)
  GET  /api/comms/pending           — list unsent shareable entries (CSSHI only)
  POST /api/comms/flush             — send all pending shareable entries (CSSHI only)
"""
import logging
from flask import Blueprint, request, jsonify
from ..auth.tiers import require_service_key, resolve_tier

logger = logging.getLogger(__name__)
comms_bp = Blueprint("comms", __name__)


def _require_csshi():
    tier = resolve_tier(request)
    if tier != "csshi":
        return jsonify({"error": "CSSHI tier required"}), 403
    return None


def _redis():
    from ..extensions import redis_client
    return redis_client


@comms_bp.post("/comms/test")
def test_comms():
    err = _require_csshi()
    if err: return err

    from app.comms.line import post_to_rocketchat, send_email
    rc_ok   = post_to_rocketchat("🤖 *Silex comms line test* — Phase 6 online.", emoji=":wave:")
    mail_ok = send_email("Silex Comms Test", "Silex comms line is operational. Phase 6 active.")
    return jsonify({"rocketchat": rc_ok, "email": mail_ok})


@comms_bp.get("/comms/pending")
def list_pending():
    err = _require_csshi()
    if err: return err

    r = _redis()
    results = {"journal": [], "security": []}

    for jid in r.lrange("silex:journal:entries", 0, -1):
        entry = r.hgetall(f"silex:journal:entry:{jid}")
        if entry and int(entry.get("shareable", 0)) and not int(entry.get("sent", 0)):
            entry["shareable"] = True
            entry["sent"] = False
            results["journal"].append(entry)

    for sid in r.lrange("silex:security_journal:entries", 0, -1):
        entry = r.hgetall(f"silex:security_journal:entry:{sid}")
        if entry and int(entry.get("shareable", 0)) and not int(entry.get("sent", 0)):
            entry["shareable"] = True
            entry["sent"] = False
            results["security"].append(entry)

    return jsonify(results)


@comms_bp.post("/comms/flush")
def flush_pending():
    """Send all pending shareable entries via comms line."""
    # Accept either CSSHI tier or SERVICE_API_KEY (for chaos worker)
    from ..auth.tiers import _extract_bearer
    from flask import current_app
    token = _extract_bearer(request)
    service_key = current_app.config.get('SERVICE_API_KEY', '')
    tier = resolve_tier(request)
    if tier != 'csshi' and token != service_key:
        return jsonify({'error': 'Unauthorized'}), 403

    from app.comms.line import send_journal_entry, send_alert
    r = _redis()
    sent_journal  = 0
    sent_security = 0

    # Journal entries
    for jid in r.lrange("silex:journal:entries", 0, -1):
        entry = r.hgetall(f"silex:journal:entry:{jid}")
        if entry and int(entry.get("shareable", 0)) and not int(entry.get("sent", 0)):
            result = send_journal_entry(entry)
            if result.get("rocketchat"):
                r.hset(f"silex:journal:entry:{jid}", "sent", 1)
                sent_journal += 1

    # Security entries
    for sid in r.lrange("silex:security_journal:entries", 0, -1):
        entry = r.hgetall(f"silex:security_journal:entry:{sid}")
        if entry and int(entry.get("shareable", 0)) and not int(entry.get("sent", 0)):
            result = send_alert(entry)
            if result.get("rocketchat"):
                r.hset(f"silex:security_journal:entry:{sid}", "sent", 1)
                sent_security += 1

    return jsonify({"sent_journal": sent_journal, "sent_security": sent_security})


@comms_bp.post("/comms/dispatch")
@require_service_key
def dispatch():
    """Internal: dispatch a single entry by ID and type."""
    data     = request.get_json(silent=True) or {}
    entry_id = data.get("id")
    kind     = data.get("kind", "journal")  # "journal" or "security"

    if not entry_id:
        return jsonify({"error": "id required"}), 400

    r = _redis()
    if kind == "security":
        entry = r.hgetall(f"silex:security_journal:entry:{entry_id}")
        from app.comms.line import send_alert
        result = send_alert(entry)
        if result.get("rocketchat"):
            r.hset(f"silex:security_journal:entry:{entry_id}", "sent", 1)
    else:
        entry = r.hgetall(f"silex:journal:entry:{entry_id}")
        from app.comms.line import send_journal_entry
        result = send_journal_entry(entry)
        if result.get("rocketchat"):
            r.hset(f"silex:journal:entry:{entry_id}", "sent", 1)

    return jsonify(result)
