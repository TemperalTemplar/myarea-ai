"""
SSH / SSHI / CSSHI permission tier resolution.

Phase 1: reads tier from request header, validates token against config.
Phase 3: full implementation with per-tier capability gates.

Tier hierarchy:
  ssh    — standard access (any authenticated session)
  sshi   — elevated (trusted internal services, power users)
  csshi  — core sovereign (Alva / platform owner only)
"""
from functools import wraps
from flask import request, current_app
import logging

logger = logging.getLogger(__name__)

TIERS = ("ssh", "sshi", "csshi")


def resolve_tier(req=None) -> str:
    """
    Determine the permission tier for the current request.

    Reads X-Silex-Tier header and validates the bearer token against
    the corresponding token set from config.

    Returns the validated tier string, defaults to 'ssh'.
    """
    if req is None:
        req = request

    requested_tier = req.headers.get("X-Silex-Tier", "ssh").lower().strip()
    if requested_tier not in TIERS:
        requested_tier = "ssh"

    # csshi and sshi require a matching token
    if requested_tier == "csshi":
        token = _extract_bearer(req)
        if token and token in current_app.config.get("CSSHI_TOKENS", set()):
            return "csshi"
        logger.warning("CSSHI access attempted with invalid token — downgrading to ssh")
        return "ssh"

    if requested_tier == "sshi":
        token = _extract_bearer(req)
        if token and (
            token in current_app.config.get("SSHI_TOKENS",  set()) or
            token in current_app.config.get("CSSHI_TOKENS", set())
        ):
            return "sshi"
        logger.warning("SSHI access attempted with invalid token — downgrading to ssh")
        return "ssh"

    return "ssh"


def _extract_bearer(req) -> str | None:
    auth = req.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    return None


def require_service_key(f):
    """
    Decorator: enforces SERVICE_API_KEY for internal endpoints.
    Checks Authorization: Bearer <key> header.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        token = _extract_bearer(request)
        expected = current_app.config.get("SERVICE_API_KEY", "")
        if not expected or token != expected:
            return {"error": "Unauthorized"}, 401
        return f(*args, **kwargs)
    return decorated
