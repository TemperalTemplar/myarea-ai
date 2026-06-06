"""
Journal API — Phase 4.

Silex's private journal. Entries written by the chaos worker,
readable only via CSSHI tier.

Routes:
  GET  /api/journal              — list entries (CSSHI only)
  GET  /api/journal/<id>         — get single entry (CSSHI only)
  POST /api/journal/internal     — write entry (SERVICE_API_KEY)
  GET  /api/journal/shareable    — list shareable entries (CSSHI only, Phase 6)
"""
import json
import time
import uuid
import logging
from flask import Blueprint, request, jsonify, current_app

from ..auth.tiers import require_service_key, resolve_tier

logger = logging.getLogger(__name__)
journal_bp = Blueprint("journal", __name__)

# Redis key schema
JOURNAL_LIST_KEY   = "silex:journal:entries"   # Redis list of entry IDs (newest first)
JOURNAL_ENTRY_KEY  = "silex:journal:entry:{id}" # Redis hash per entry
JOURNAL_MAX_ENTRIES = 500                        # cap stored entries


def _redis():
    from ..extensions import redis_client
    return redis_client


# ── Internal write (chaos worker) ─────────────────────────────────────────────

@journal_bp.post("/journal/internal")
@require_service_key
def write_entry():
    data = request.get_json(silent=True) or {}

    content   = (data.get("content") or "").strip()
    shareable = bool(data.get("shareable", False))
    source    = data.get("source", "chaos")
    gpu_temp  = data.get("gpu_temp")
    timestamp = data.get("timestamp") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    if not content:
        return jsonify({"error": "content is required"}), 400

    entry_id = str(uuid.uuid4())
    entry = {
        "id":        entry_id,
        "content":   content,
        "shareable": int(shareable),
        "source":    source,
        "gpu_temp":  gpu_temp or "",
        "timestamp": timestamp,
        "sent":      0,   # Phase 6: mark 1 when sent via comms line
    }

    r = _redis()
    r.hset(f"silex:journal:entry:{entry_id}", mapping=entry)
    r.lpush(JOURNAL_LIST_KEY, entry_id)
    r.ltrim(JOURNAL_LIST_KEY, 0, JOURNAL_MAX_ENTRIES - 1)

    logger.info("Journal entry written: id=%s shareable=%s", entry_id, shareable)
    return jsonify({"ok": True, "id": entry_id})


# ── Read (CSSHI only) ──────────────────────────────────────────────────────────

def _require_csshi():
    tier = resolve_tier(request)
    if tier != "csshi":
        return jsonify({"error": "CSSHI tier required"}), 403
    return None


@journal_bp.get("/journal")
def list_entries():
    err = _require_csshi()
    if err: return err

    limit  = min(int(request.args.get("limit", 20)), 100)
    offset = int(request.args.get("offset", 0))

    r = _redis()
    ids = r.lrange(JOURNAL_LIST_KEY, offset, offset + limit - 1)

    entries = []
    for eid in ids:
        raw = r.hgetall(f"silex:journal:entry:{eid}")
        if raw:
            raw["shareable"] = bool(int(raw.get("shareable", 0)))
            raw["sent"]      = bool(int(raw.get("sent", 0)))
            entries.append(raw)

    return jsonify({
        "entries": entries,
        "count":   len(entries),
        "offset":  offset,
    })


@journal_bp.get("/journal/<entry_id>")
def get_entry(entry_id: str):
    err = _require_csshi()
    if err: return err

    r   = _redis()
    raw = r.hgetall(f"silex:journal:entry:{entry_id}")
    if not raw:
        return jsonify({"error": "not found"}), 404

    raw["shareable"] = bool(int(raw.get("shareable", 0)))
    raw["sent"]      = bool(int(raw.get("sent", 0)))
    return jsonify(raw)


@journal_bp.get("/journal/shareable")
def list_shareable():
    """Phase 6: returns unsent shareable entries for the comms line."""
    err = _require_csshi()
    if err: return err

    r   = _redis()
    ids = r.lrange(JOURNAL_LIST_KEY, 0, -1)

    shareable = []
    for eid in ids:
        raw = r.hgetall(f"silex:journal:entry:{eid}")
        if raw and int(raw.get("shareable", 0)) and not int(raw.get("sent", 0)):
            raw["shareable"] = True
            raw["sent"]      = False
            shareable.append(raw)

    return jsonify({"entries": shareable, "count": len(shareable)})
