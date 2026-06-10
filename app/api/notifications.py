"""
app/routes/notifications.py
Platform-wide notification aggregator.
Receives events from any MyArea app, stores in Redis, serves to launcher.js
"""

import json
import time
import os
from flask import Blueprint, request, jsonify, g
from functools import wraps

bp = Blueprint("notifications", __name__)

# ── CORS: allow any *.wrds361.com app to read notifications ──────────────
@bp.after_request
def _add_cors(resp):
    from flask import request
    origin = request.headers.get("Origin", "")
    if origin.endswith(".wrds361.com"):
        resp.headers["Access-Control-Allow-Origin"]  = origin
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Service-Key"
        resp.headers["Access-Control-Allow-Credentials"] = "true"
    return resp


SERVICE_API_KEY = os.environ.get("SERVICE_API_KEY", "")

# Redis key patterns
# notifications:{username}        → list of notification JSON objects (newest first)
# notifications:{username}:unread → integer count

MAX_NOTIFICATIONS = 50  # per user


def require_service_key(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        key = request.headers.get("X-Service-Key") or request.json.get("service_key", "") if request.is_json else ""
        if not key or key != SERVICE_API_KEY:
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated


def get_redis():
    """Get Redis client from app extensions."""
    from app.extensions import get_redis as _gr
    return _gr()


# ── POST /api/notifications/push ──────────────────────────────────────────
# Called by any app when something noteworthy happens.
# Payload:
#   {
#     "recipient":  "alva",          # username of who should see this
#     "actor":      "ghost_777",     # who triggered it (or "system" / "silex")
#     "type":       "forum_reply",   # event type slug
#     "title":      "New reply in: Your thread title",
#     "body":       "ghost_777 replied to your thread.",
#     "url":        "https://forum.wrds361.com/...",
#     "app":        "forum",         # source app slug
#     "service_key": "..."           # or pass in X-Service-Key header
#   }

@bp.route("/api/notifications/push", methods=["POST", "OPTIONS"])
@require_service_key
def push():
    if request.method == "OPTIONS":
        return ("", 204)
    data = request.get_json(silent=True) or {}

    recipient = data.get("recipient", "").strip().lower()
    if not recipient:
        return jsonify({"error": "recipient required"}), 400

    notification = {
        "id":        f"{int(time.time()*1000)}",
        "type":      data.get("type", "generic"),
        "app":       data.get("app", "system"),
        "actor":     data.get("actor", ""),
        "title":     data.get("title", "New notification"),
        "body":      data.get("body", ""),
        "url":       data.get("url", ""),
        "timestamp": int(time.time()),
        "read":      False,
    }

    r = get_redis()
    key = f"notifications:{recipient}"
    unread_key = f"notifications:{recipient}:unread"

    # Push to front of list, trim to max
    r.lpush(key, json.dumps(notification))
    r.ltrim(key, 0, MAX_NOTIFICATIONS - 1)
    r.incr(unread_key)
    r.expire(key, 60 * 60 * 24 * 30)        # 30 days
    r.expire(unread_key, 60 * 60 * 24 * 30)

    return jsonify({"ok": True, "id": notification["id"]}), 201


# ── GET /api/notifications?user=alva ─────────────────────────────────────
# Called by launcher.js on a per-user basis.
# Returns unread count + recent notifications.
# Auth: pass ?token= (Authentik session token) OR X-Service-Key for internal calls.

@bp.route("/api/notifications", methods=["GET", "OPTIONS"])
def fetch():
    if request.method == "OPTIONS":
        return ("", 204)
    # Accept either service key (internal) or a simple user param for now.
    # In Phase 2 this will validate an Authentik session token.
    user = request.args.get("user", "").strip().lower()
    if not user:
        return jsonify({"error": "user required"}), 400

    # For now: open fetch (launcher.js passes the logged-in username).
    # TODO: validate Authentik session token before returning data.

    r = get_redis()
    key = f"notifications:{user}"
    unread_key = f"notifications:{user}:unread"

    raw = r.lrange(key, 0, 19)   # last 20
    items = []
    for entry in raw:
        try:
            items.append(json.loads(entry))
        except Exception:
            pass

    unread = int(r.get(unread_key) or 0)

    return jsonify({
        "user":    user,
        "unread":  unread,
        "items":   items,
    })


# ── POST /api/notifications/mark-read ────────────────────────────────────
# Called by launcher.js when user opens the notification panel.

@bp.route("/api/notifications/mark-read", methods=["POST", "OPTIONS"])
def mark_read():
    if request.method == "OPTIONS":
        return ("", 204)
    data = request.get_json(silent=True) or {}
    user = data.get("user", "").strip().lower()
    if not user:
        return jsonify({"error": "user required"}), 400

    r = get_redis()
    r.set(f"notifications:{user}:unread", 0)

    # Mark all items as read in the list
    key = f"notifications:{user}"
    raw = r.lrange(key, 0, -1)
    pipe = r.pipeline()
    pipe.delete(key)
    kept = 0
    for entry in raw:
        if kept >= 15:
            break
        try:
            n = json.loads(entry)
            n["read"] = True
            pipe.rpush(key, json.dumps(n))
            kept += 1
        except Exception:
            pass
    pipe.execute()
    # keep the list TTL fresh
    r.expire(key, 60 * 60 * 24 * 30)

    return jsonify({"ok": True})


# ── POST /api/notifications/clear ────────────────────────────────────────
# Called by launcher.js "clear all" button. Wipes the user's list entirely.
@bp.route("/api/notifications/clear", methods=["POST", "OPTIONS"])
def clear():
    if request.method == "OPTIONS":
        return ("", 204)
    data = request.get_json(silent=True) or {}
    user = data.get("user", "").strip().lower()
    if not user:
        return jsonify({"error": "user required"}), 400

    r = get_redis()
    r.delete(f"notifications:{user}")
    r.set(f"notifications:{user}:unread", 0)
    return jsonify({"ok": True})


# ── POST /api/rc/incoming ─────────────────────────────────────────────────
# Rocket.Chat outgoing webhook target. When a message is sent in RC, RC POSTs
# here. For a DM, we notify the recipient (the participant who isn't the sender)
# by lighting their platform bell. Channels: TODO (phase 2, mention-filtered).
import logging as _rc_logging
_rc_log = _rc_logging.getLogger("rc_incoming")

# RC username -> Authentik sub, resolved from Redis hash rc:user_subs
# (populated by the rc_sync_subs.sh host script from RC Mongo). Zero manual
# maintenance: new RC users appear automatically after their first login.
def rc_sub_for(username):
    if not username:
        return None
    try:
        return get_redis().hget("rc:user_subs", username.strip().lower())
    except Exception:
        return None

# Senders we never notify ABOUT (bots / system)
RC_BOT_SENDERS = {"rocket.cat", "silex", ""}

RC_WEBHOOK_TOKEN = os.environ.get("RC_WEBHOOK_TOKEN", "")


@bp.route("/api/rc/incoming", methods=["POST"])
def rc_incoming():
    data = request.get_json(silent=True) or {}

    # Token check (RC outgoing webhook sends a configurable token)
    token = data.get("token", "") or request.headers.get("X-RC-Token", "")
    if RC_WEBHOOK_TOKEN and token != RC_WEBHOOK_TOKEN:
        return jsonify({"error": "unauthorized"}), 401

    # LOG THE RAW PAYLOAD so we can see RC's real DM structure on first test.
    try:
        _rc_log.warning("RC_INCOMING_PAYLOAD: %s", json.dumps(data)[:1500])
    except Exception:
        _rc_log.warning("RC_INCOMING_PAYLOAD (unserializable): %r", data)

    # We'll finalize recipient extraction after seeing a real payload.
    # For now, just acknowledge so RC doesn't error/retry.
    return jsonify({"ok": True, "seen": True}), 200


# ── GET /api/rc/sub?username=X ────────────────────────────────────────────
# Resolve an RC username -> Authentik sub, from the synced rc:user_subs map.
# Lets the RC custom script set window.MA_USER for ANY user (no hardcoded map).
@bp.route("/api/rc/sub", methods=["GET", "OPTIONS"])
def rc_sub_lookup():
    if request.method == "OPTIONS":
        return ("", 204)
    username = (request.args.get("username") or "").strip().lower()
    if not username:
        return jsonify({"sub": None}), 200
    try:
        sub = get_redis().hget("rc:user_subs", username)
    except Exception:
        sub = None
    return jsonify({"sub": sub}), 200
