"""
app/auth/access.py

Platform access gate for Silex AI.
Only Alva (ALVA_IDENTITIES) has unconditional access.
All other users require explicit approval stored in Redis with a TTL.

Redis keys:
  silex:access:approved:<sub>   — TTL key, value = granted_by + expiry info
  silex:access:denied_log       — LIST of recent denied access attempts (last 50)
  silex:access:pending:<sub>    — SET entry when user hits denied page
"""
import os
import json
import time
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_APPROVED_PREFIX = "silex:access:approved:"
_DENIED_LOG      = "silex:access:denied_log"
_PENDING_PREFIX  = "silex:access:pending:"


def _redis():
    from ..extensions import get_redis
    return get_redis()


def _is_alva(user: dict) -> bool:
    raw  = os.environ.get("ALVA_IDENTITIES", "")
    alva = {x.strip().lower() for x in raw.split(",") if x.strip()}
    for field in ("username", "name", "email", "sub"):
        if (user.get(field) or "").strip().lower() in alva:
            return True
    return False


def check_access(user: dict) -> tuple[bool, str]:
    """
    Returns (allowed, reason).
    Alva always allowed. Others need Redis approval key.
    """
    if _is_alva(user):
        return True, "architect"

    sub = user.get("sub", "")
    if not sub:
        return False, "no_sub"

    try:
        val = _redis().get(f"{_APPROVED_PREFIX}{sub}")
        if val:
            return True, "approved"
    except Exception as exc:
        logger.warning("Access check Redis error: %s", exc)
        return False, "redis_error"

    return False, "not_approved"


def approve_user(sub: str, username: str, granted_by: str,
                 duration_hours: int = 24) -> bool:
    """Grant access to a user for N hours."""
    try:
        r   = _redis()
        ttl = duration_hours * 3600
        val = json.dumps({
            "username":   username,
            "granted_by": granted_by,
            "granted_at": datetime.now(timezone.utc).isoformat(),
            "expires_in_hours": duration_hours,
        })
        r.set(f"{_APPROVED_PREFIX}{sub}", val, ex=ttl)
        # Remove from pending
        r.delete(f"{_PENDING_PREFIX}{sub}")
        logger.info("Access approved: %s by %s for %dh", username, granted_by, duration_hours)
        return True
    except Exception as exc:
        logger.error("Approve error: %s", exc)
        return False


def revoke_user(sub: str) -> bool:
    """Revoke a user's access immediately."""
    try:
        _redis().delete(f"{_APPROVED_PREFIX}{sub}")
        return True
    except Exception:
        return False


def log_denied(user: dict):
    """Log a denied access attempt for Alva to review."""
    try:
        r   = _redis()
        sub = user.get("sub", "")
        entry = json.dumps({
            "sub":      sub,
            "username": user.get("username", ""),
            "name":     user.get("name", ""),
            "email":    user.get("email", ""),
            "time":     datetime.now(timezone.utc).isoformat(),
        })
        r.lpush(_DENIED_LOG, entry)
        r.ltrim(_DENIED_LOG, 0, 49)  # keep last 50
        if sub:
            r.set(f"{_PENDING_PREFIX}{sub}", entry, ex=86400)  # 24h pending flag
    except Exception as exc:
        logger.warning("log_denied error: %s", exc)


def get_denied_log() -> list:
    try:
        raw = _redis().lrange(_DENIED_LOG, 0, 49)
        return [json.loads(r) for r in raw]
    except Exception:
        return []


def get_approved_users() -> list:
    """Return all currently approved non-Alva users."""
    try:
        r    = _redis()
        keys = r.keys(f"{_APPROVED_PREFIX}*")
        out  = []
        for key in keys:
            val = r.get(key)
            ttl = r.ttl(key)
            if val:
                entry = json.loads(val)
                entry["sub"] = key.replace(_APPROVED_PREFIX, "")
                entry["ttl_seconds"] = ttl
                out.append(entry)
        return out
    except Exception:
        return []
