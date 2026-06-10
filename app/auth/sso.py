"""
Authentik SSO for myarea-ai.
Uses Flask sessions — no database, no flask_login.
"""
import os
import secrets
import logging
from functools import wraps
from urllib.parse import urlencode

import requests
from flask import Blueprint, redirect, request, session, current_app, jsonify, g

log = logging.getLogger(__name__)
sso_bp = Blueprint("sso", __name__)


def _cfg(key, default=None):
    return current_app.config.get(key, os.environ.get(key, default))


def _callback_uri():
    base = _cfg("AI_BASE_URL", "https://ai.wrds361.com")
    return f"{base}/auth/oidc/callback"


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("user"):
            session["sso_next"] = request.url
            return redirect("/auth/login")
        g.user = session["user"]
        return f(*args, **kwargs)
    return decorated


def get_current_user():
    return session.get("user")


@sso_bp.get("/auth/login")
def login():
    if session.get("user"):
        return redirect("/")
    state = secrets.token_urlsafe(32)
    session["sso_state"] = state
    session["sso_next"]  = request.args.get("next", "/")
    params = {
        "client_id":     _cfg("AUTHENTIK_CLIENT_ID"),
        "response_type": "code",
        "scope":         "openid profile email",
        "redirect_uri":  _callback_uri(),
        "state":         state,
    }
    auth_url = _cfg("AUTHENTIK_URL", "https://auth.wrds361.com")
    return redirect(f"{auth_url}/application/o/authorize/?" + urlencode(params))


@sso_bp.get("/auth/oidc/callback")
def callback():
    if request.args.get("state") != session.pop("sso_state", None):
        log.warning("SSO state mismatch")
        return redirect("/auth/login")
    error = request.args.get("error")
    if error:
        log.warning("SSO error: %s", error)
        return redirect("/auth/login")
    code = request.args.get("code")
    if not code:
        return redirect("/auth/login")
    token_data = _exchange_code(code)
    if not token_data:
        return redirect("/auth/login")
    claims = _get_userinfo(token_data["access_token"])
    if not claims:
        return redirect("/auth/login")
    session["user"] = {
        "sub":      claims.get("sub", ""),
        "email":    claims.get("email", ""),
        "username": claims.get("preferred_username", ""),
        "name":     claims.get("name", ""),
        "groups":   claims.get("groups", []),
    }
    session.permanent = True
    log.info("SSO login: %s", session["user"]["username"])
    return redirect(session.pop("sso_next", "/"))


@sso_bp.get("/auth/logout")
def logout():
    user = session.pop("user", None)
    # Release the single-user GPU lock on logout (frees it immediately
    # instead of waiting for the idle TTL to expire).
    try:
        from .userlock import release
        if user:
            release((user.get("name") or user.get("username") or "").strip().lower())
    except Exception:
        pass
    if user:
        log.info("SSO logout: %s", user.get("username"))
    auth_url = _cfg("AUTHENTIK_URL", "https://auth.wrds361.com")
    return redirect(f"{auth_url}/application/o/myarea-ai/end-session/")


@sso_bp.get("/auth/me")
def me():
    user = session.get("user")
    if not user:
        return jsonify({"authenticated": False}), 401
    return jsonify({"authenticated": True, "user": user})


def _exchange_code(code):
    try:
        auth_url = _cfg("AUTHENTIK_URL", "https://auth.wrds361.com")
        resp = requests.post(
            f"{auth_url}/application/o/token/",
            data={
                "grant_type":    "authorization_code",
                "code":          code,
                "redirect_uri":  _callback_uri(),
                "client_id":     _cfg("AUTHENTIK_CLIENT_ID"),
                "client_secret": _cfg("AUTHENTIK_CLIENT_SECRET"),
            },
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        log.error("Token exchange error: %s", exc)
        return None


def _get_userinfo(access_token):
    try:
        auth_url = _cfg("AUTHENTIK_URL", "https://auth.wrds361.com")
        resp = requests.get(
            f"{auth_url}/application/o/userinfo/",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        log.error("Userinfo error: %s", exc)
        return None
