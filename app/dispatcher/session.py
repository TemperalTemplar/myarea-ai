"""
Session context assembly.

Reads short-term memory (NCAIDSSHM) from Redis and builds the message
list that gets sent to the LLM.

Redis key schema:
  silex:session:<session_id>:turns   — Redis list, JSON-encoded turn objects
  silex:session:<session_id>:inject  — Redis list, JSON-encoded injected context blobs
  silex:session:<session_id>:meta    — Redis hash: title, user, project, last_activity, created
  silex:sessions:active              — Redis set of session ids with recent activity (capture sweep)
  silex:user:<uid>:sessions          — Redis sorted set: session_id -> last_activity (Phase 7 UI)
"""
import json
import time
import uuid
import logging
from typing import List, Dict

from flask import current_app
from ..extensions import redis_client

logger = logging.getLogger(__name__)

TURN_KEY_TPL   = "silex:session:{sid}:turns"
INJECT_KEY_TPL = "silex:session:{sid}:inject"
META_KEY_TPL   = "silex:session:{sid}:meta"
ACTIVE_SET     = "silex:sessions:active"


def _user_sessions_key(uid: str) -> str:
    return f"silex:user:{uid}:sessions"


# ── Read ──────────────────────────────────────────────────────────────────────

def get_session_messages(session_id: str) -> List[Dict[str, str]]:
    key = TURN_KEY_TPL.format(sid=session_id)
    max_turns = current_app.config["NCAIDSSHM_MAX_TURNS"]
    try:
        raw = redis_client.lrange(key, -max_turns * 2, -1)
    except Exception as exc:
        logger.error("Redis read failed for session %s: %s", session_id, exc)
        return []
    messages = []
    for item in raw:
        try:
            messages.append(json.loads(item))
        except json.JSONDecodeError:
            continue
    return messages


def get_injected_context(session_id: str) -> str | None:
    key = INJECT_KEY_TPL.format(sid=session_id)
    try:
        raw = redis_client.lrange(key, 0, -1)
    except Exception:
        return None
    if not raw:
        return None
    blobs = []
    for item in raw:
        try:
            blob = json.loads(item)
            blobs.append(f"[{blob.get('source_app','?')}] {blob.get('content','')}")
        except json.JSONDecodeError:
            continue
    return "\n".join(blobs) if blobs else None


def get_session_project(session_id: str) -> str | None:
    """Return the project id a session belongs to, if any."""
    try:
        return redis_client.hget(META_KEY_TPL.format(sid=session_id), "project") or None
    except Exception:
        return None


# ── Write ─────────────────────────────────────────────────────────────────────

def append_turn(session_id: str, role: str, content: str, user_name: str | None = None) -> None:
    """Append a single turn to session memory and update tracking."""
    key = TURN_KEY_TPL.format(sid=session_id)
    ttl = current_app.config["NCAIDSSHM_TTL"]
    try:
        redis_client.rpush(key, json.dumps({"role": role, "content": content}))
        redis_client.expire(key, ttl)
        _touch_session(session_id, user_name, ttl, first_user_msg=(role == "user" and content))
    except Exception as exc:
        logger.error("Redis write failed for session %s: %s", session_id, exc)


def _touch_session(session_id: str, user_name: str | None, ttl: int,
                   first_user_msg: str | bool = False) -> None:
    """Register session activity for capture sweep + Phase 7 UI listing."""
    try:
        now = time.time()
        meta_key = META_KEY_TPL.format(sid=session_id)
        existing = redis_client.hgetall(meta_key) or {}

        mapping = {"last_activity": now}
        if user_name:
            mapping["user"] = user_name
        if not existing.get("created"):
            mapping["created"] = now
        # Auto-title from the first user message if none set yet
        if not existing.get("title") and isinstance(first_user_msg, str) and first_user_msg.strip():
            mapping["title"] = first_user_msg.strip()[:60]

        redis_client.hset(meta_key, mapping=mapping)
        redis_client.expire(meta_key, ttl + 7200)
        redis_client.sadd(ACTIVE_SET, session_id)

        # Per-user sorted set for the UI sidebar
        uid = (user_name or existing.get("user") or "anon").strip().lower()
        redis_client.zadd(_user_sessions_key(uid), {session_id: now})
    except Exception as exc:
        logger.error("Session touch failed for %s: %s", session_id, exc)


def inject_context(session_id: str, source_app: str, content: str, ttl: int) -> None:
    key = INJECT_KEY_TPL.format(sid=session_id)
    blob = json.dumps({
        "source_app": source_app,
        "content":    content,
        "ts":         time.time(),
    })
    try:
        redis_client.rpush(key, blob)
        redis_client.expire(key, ttl)
    except Exception as exc:
        logger.error("Redis inject failed for session %s: %s", session_id, exc)


# ── Utilities ─────────────────────────────────────────────────────────────────

def new_session_id() -> str:
    return str(uuid.uuid4())


def clear_session(session_id: str) -> None:
    for tpl in (TURN_KEY_TPL, INJECT_KEY_TPL, META_KEY_TPL):
        try:
            redis_client.delete(tpl.format(sid=session_id))
        except Exception:
            pass
    try:
        redis_client.srem(ACTIVE_SET, session_id)
    except Exception:
        pass
