"""
Admin access management — Alva only.
GET  /admin/access          — view approved users + denied log
POST /admin/access/approve  — approve a user
POST /admin/access/revoke   — revoke a user
"""
from flask import Blueprint, render_template, request, redirect, jsonify, session
from functools import wraps
import os

access_admin_bp = Blueprint("access_admin", __name__, url_prefix="/admin/access")


def alva_only(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        user = session.get("user", {})
        raw  = os.environ.get("ALVA_IDENTITIES", "")
        alva = {x.strip().lower() for x in raw.split(",") if x.strip()}
        uid  = (user.get("username") or user.get("name") or "").strip().lower()
        if uid not in alva:
            return jsonify({"error": "Forbidden"}), 403
        return f(*args, **kwargs)
    return decorated


@access_admin_bp.get("/")
@alva_only
def index():
    from app.auth.access import get_approved_users, get_denied_log
    approved = get_approved_users()
    denied   = get_denied_log()
    return render_template("access_admin.html",
                           approved=approved, denied=denied)


@access_admin_bp.post("/approve")
@alva_only
def approve():
    from app.auth.access import approve_user
    sub      = request.form.get("sub", "").strip()
    username = request.form.get("username", "").strip()
    hours    = int(request.form.get("hours", 24))
    granter  = (session.get("user") or {}).get("username", "alva")
    if sub:
        approve_user(sub, username, granter, hours)
    return redirect("/admin/access/")


@access_admin_bp.post("/revoke")
@alva_only
def revoke():
    from app.auth.access import revoke_user
    sub = request.form.get("sub", "").strip()
    if sub:
        revoke_user(sub)
    return redirect("/admin/access/")
