"""
Security Journal API — Phase 5.

Separate from the chaos journal — stores Sparta scan results.
Writable by Sparta worker via SERVICE_API_KEY.
Readable only via CSSHI tier.

Routes:
  POST /api/security-journal/internal  — write entry (SERVICE_API_KEY)
  GET  /api/security-journal           — list entries (CSSHI only)
  GET  /api/security-journal/<id>      — get single entry (CSSHI only)
  POST /api/sparta/scan                — trigger on-demand scan (CSSHI only)
"""
import logging
import time
import uuid
from flask import Blueprint, request, jsonify

from ..auth.tiers import require_service_key, resolve_tier

logger = logging.getLogger(__name__)
security_journal_bp = Blueprint("security_journal", __name__)

SJOURNAL_LIST_KEY  = "silex:security_journal:entries"
SJOURNAL_ENTRY_KEY = "silex:security_journal:entry:{id}"
SJOURNAL_MAX       = 200


def _redis():
    from ..extensions import redis_client
    return redis_client


def _require_csshi():
    tier = resolve_tier(request)
    if tier != "csshi":
        return jsonify({"error": "CSSHI tier required"}), 403
    return None


# ── Internal write ────────────────────────────────────────────────────────────

@security_journal_bp.post("/security-journal/internal")
@require_service_key
def write_entry():
    data      = request.get_json(silent=True) or {}
    content   = (data.get("content") or "").strip()
    shareable = bool(data.get("shareable", False))
    severity  = data.get("severity", "info")
    source    = data.get("source", "sparta")
    gpu_temp  = data.get("gpu_temp")
    triggered = data.get("triggered_by", "schedule")
    timestamp = data.get("timestamp") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    if not content:
        return jsonify({"error": "content is required"}), 400

    entry_id = str(uuid.uuid4())
    entry = {
        "id":           entry_id,
        "content":      content,
        "shareable":    int(shareable),
        "severity":     severity,
        "source":       source,
        "gpu_temp":     gpu_temp or "",
        "triggered_by": triggered,
        "timestamp":    timestamp,
        "sent":         0,
    }

    r = _redis()
    r.hset(f"silex:security_journal:entry:{entry_id}", mapping=entry)
    r.lpush(SJOURNAL_LIST_KEY, entry_id)
    r.ltrim(SJOURNAL_LIST_KEY, 0, SJOURNAL_MAX - 1)

    logger.info("Security journal entry: id=%s severity=%s", entry_id, severity)
    return jsonify({"ok": True, "id": entry_id})


# ── Read ──────────────────────────────────────────────────────────────────────

@security_journal_bp.get("/security-journal")
def list_entries():
    err = _require_csshi()
    if err: return err

    limit  = min(int(request.args.get("limit", 20)), 100)
    offset = int(request.args.get("offset", 0))
    severity_filter = request.args.get("severity")

    r   = _redis()
    ids = r.lrange(SJOURNAL_LIST_KEY, 0, -1)

    entries = []
    for eid in ids:
        raw = r.hgetall(f"silex:security_journal:entry:{eid}")
        if not raw:
            continue
        if severity_filter and raw.get("severity") != severity_filter:
            continue
        raw["shareable"] = bool(int(raw.get("shareable", 0)))
        raw["sent"]      = bool(int(raw.get("sent", 0)))
        entries.append(raw)

    paginated = entries[offset:offset + limit]
    return jsonify({"entries": paginated, "count": len(paginated), "total": len(entries), "offset": offset})


@security_journal_bp.get("/security-journal/<entry_id>")
def get_entry(entry_id: str):
    err = _require_csshi()
    if err: return err

    r   = _redis()
    raw = r.hgetall(f"silex:security_journal:entry:{entry_id}")
    if not raw:
        return jsonify({"error": "not found"}), 404

    raw["shareable"] = bool(int(raw.get("shareable", 0)))
    raw["sent"]      = bool(int(raw.get("sent", 0)))
    return jsonify(raw)


# ── On-demand scan trigger ─────────────────────────────────────────────────────

@security_journal_bp.post("/sparta/scan")
def trigger_scan():
    err = _require_csshi()
    if err: return err

    try:
        from workers.sparta import run_sparta_scan
        result = run_sparta_scan(triggered_by="on-demand")
        return jsonify(result)
    except Exception as exc:
        logger.error("On-demand Sparta scan failed: %s", exc)
        return jsonify({"error": str(exc)}), 500
