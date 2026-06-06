"""
Phase 7 — Sessions & Projects API.

Lets the UI list past conversations, resume them, rename/delete, and
organize them into projects. Sessions and projects are scoped per-user
(by SSO identity) so users only see their own.

Redis key schema (additions):
  silex:user:<uid>:sessions          — sorted set: session_id -> last_activity (score)
  silex:session:<sid>:meta           — hash: title, user, project, last_activity, created
  silex:user:<uid>:projects          — hash: project_id -> JSON {name, collection, created}
"""
import json
import time
import uuid
import logging
from flask import Blueprint, request, jsonify

from ..auth.sso import get_current_user, login_required
from ..extensions import get_redis
from ..dispatcher.session import (
    META_KEY_TPL, TURN_KEY_TPL, clear_session,
)

logger = logging.getLogger(__name__)
sessions_bp = Blueprint("sessions", __name__)


def _uid() -> str:
    user = get_current_user() or {}
    return (user.get("name") or user.get("username") or "anon").strip().lower()


def _user_sessions_key(uid: str) -> str:
    return f"silex:user:{uid}:sessions"


def _user_projects_key(uid: str) -> str:
    return f"silex:user:{uid}:projects"


# ── Sessions ────────────────────────────────────────────────────────────────────

@sessions_bp.get("/sessions")
@login_required
def list_sessions():
    """List the current user's sessions, newest first."""
    r = get_redis()
    uid = _uid()
    skey = _user_sessions_key(uid)

    try:
        # newest first (highest score)
        sid_scores = r.zrevrange(skey, 0, -1, withscores=True)
    except Exception as exc:
        logger.error("list_sessions failed: %s", exc)
        return jsonify({"sessions": []})

    out = []
    for sid, score in sid_scores:
        meta = r.hgetall(META_KEY_TPL.format(sid=sid)) or {}
        turns = 0
        try:
            turns = r.llen(TURN_KEY_TPL.format(sid=sid))
        except Exception:
            pass
        out.append({
            "session_id":    sid,
            "title":         meta.get("title") or "Untitled",
            "project":       meta.get("project") or None,
            "last_activity": float(meta.get("last_activity", score) or score),
            "created":       float(meta.get("created", 0) or 0),
            "turns":         turns,
        })
    return jsonify({"sessions": out})


@sessions_bp.get("/sessions/<sid>")
@login_required
def get_session(sid):
    """Return the full turn history for a session (to resume it)."""
    r = get_redis()
    uid = _uid()

    # ownership check
    meta = r.hgetall(META_KEY_TPL.format(sid=sid)) or {}
    if meta.get("user", "").strip().lower() != uid:
        return jsonify({"error": "not found"}), 404

    raw = r.lrange(TURN_KEY_TPL.format(sid=sid), 0, -1)
    turns = []
    for item in raw:
        try:
            turns.append(json.loads(item))
        except Exception:
            continue
    return jsonify({
        "session_id": sid,
        "title":      meta.get("title") or "Untitled",
        "project":    meta.get("project") or None,
        "turns":      turns,
    })


@sessions_bp.post("/sessions/<sid>/rename")
@login_required
def rename_session(sid):
    r = get_redis()
    uid = _uid()
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()[:120]
    if not title:
        return jsonify({"error": "title required"}), 400

    mkey = META_KEY_TPL.format(sid=sid)
    meta = r.hgetall(mkey) or {}
    if meta.get("user", "").strip().lower() != uid:
        return jsonify({"error": "not found"}), 404

    r.hset(mkey, "title", title)
    return jsonify({"ok": True, "session_id": sid, "title": title})


@sessions_bp.post("/sessions/<sid>/project")
@login_required
def assign_project(sid):
    """Assign (or clear) a session's project."""
    r = get_redis()
    uid = _uid()
    data = request.get_json(silent=True) or {}
    project_id = (data.get("project") or "").strip() or None

    mkey = META_KEY_TPL.format(sid=sid)
    meta = r.hgetall(mkey) or {}
    if meta.get("user", "").strip().lower() != uid:
        return jsonify({"error": "not found"}), 404

    if project_id:
        # validate project exists for this user
        if not r.hexists(_user_projects_key(uid), project_id):
            return jsonify({"error": "unknown project"}), 400
        r.hset(mkey, "project", project_id)
    else:
        r.hdel(mkey, "project")
    return jsonify({"ok": True, "session_id": sid, "project": project_id})


@sessions_bp.delete("/sessions/<sid>")
@login_required
def delete_session(sid):
    r = get_redis()
    uid = _uid()
    mkey = META_KEY_TPL.format(sid=sid)
    meta = r.hgetall(mkey) or {}
    if meta.get("user", "").strip().lower() != uid:
        return jsonify({"error": "not found"}), 404

    clear_session(sid)
    try:
        r.zrem(_user_sessions_key(uid), sid)
    except Exception:
        pass
    return jsonify({"ok": True, "deleted": sid})


# ── Projects ────────────────────────────────────────────────────────────────────

@sessions_bp.get("/projects")
@login_required
def list_projects():
    r = get_redis()
    uid = _uid()
    raw = r.hgetall(_user_projects_key(uid)) or {}
    projects = []
    for pid, blob in raw.items():
        try:
            p = json.loads(blob)
            p["project_id"] = pid
            projects.append(p)
        except Exception:
            continue
    projects.sort(key=lambda x: x.get("created", 0))
    return jsonify({"projects": projects})


@sessions_bp.post("/projects")
@login_required
def create_project():
    r = get_redis()
    uid = _uid()
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()[:80]
    if not name:
        return jsonify({"error": "name required"}), 400

    pid = "proj_" + uuid.uuid4().hex[:12]
    # Per-project Chroma collection, namespaced by user
    safe_uid = "".join(c for c in uid if c.isalnum() or c == "_")
    collection = f"proj_{safe_uid}_{pid}"

    project = {
        "name":       name,
        "collection": collection,
        "created":    time.time(),
    }
    r.hset(_user_projects_key(uid), pid, json.dumps(project))
    project["project_id"] = pid
    return jsonify({"ok": True, "project": project})


@sessions_bp.delete("/projects/<pid>")
@login_required
def delete_project(pid):
    r = get_redis()
    uid = _uid()
    pkey = _user_projects_key(uid)
    if not r.hexists(pkey, pid):
        return jsonify({"error": "not found"}), 404
    r.hdel(pkey, pid)
    # Note: we leave the Chroma collection intact (could be purged separately)
    return jsonify({"ok": True, "deleted": pid})
