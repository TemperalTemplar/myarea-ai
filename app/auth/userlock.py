"""
app/auth/userlock.py
Single-user exclusive lock for the single-GPU Silex system.

Only one user may actively use Silex at a time (the Tesla P4 is one card;
concurrent inference competes for VRAM/compute and doubles thermal load).

Model:
  - Redis key  silex:active_user  = the holder's uid, with a TTL (idle window).
  - The lock is refreshed on each chat message, so it follows ACTIVE USE.
  - If the holder goes idle past the TTL, the key expires and the lock frees.
  - Architect (ALVA_IDENTITIES) can always force-take the lock.
"""
import os
import time

LOCK_KEY     = "silex:active_user"
LOCK_TS_KEY  = "silex:active_user:since"
IDLE_SECONDS = int(os.environ.get("USERLOCK_IDLE_SECONDS", "600"))  # 10 min


def _redis():
    from ..extensions import get_redis
    return get_redis()


def _is_architect(uid):
    raw = os.environ.get("ALVA_IDENTITIES", "")
    alva = {x.strip().lower() for x in raw.split(",") if x.strip()}
    return (uid or "").strip().lower() in alva


def current_holder():
    """Return the uid currently holding the lock, or None if free/expired."""
    try:
        return _redis().get(LOCK_KEY)
    except Exception:
        return None


def holder_since():
    try:
        v = _redis().get(LOCK_TS_KEY)
        return int(v) if v else None
    except Exception:
        return None


def check_access(uid):
    """
    Decide whether `uid` may use Silex right now.
    Returns (allowed: bool, holder: str|None).
    - Free lock        -> allowed (caller should acquire).
    - Held by self     -> allowed.
    - Held by other    -> denied, unless `uid` is Architect (force-take).
    """
    uid = (uid or "").strip().lower()
    holder = current_holder()
    if not holder:
        return True, None
    if holder == uid:
        return True, holder
    if _is_architect(uid):
        return True, holder   # architect overrides
    return False, holder


VIEW_HOLD_SECONDS = int(os.environ.get("USERLOCK_VIEW_SECONDS", "90"))  # page-open hold


def acquire(uid, ttl=None):
    """
    Take/refresh the lock for uid (and stamp start time if new holder).
    ttl: seconds to hold. Defaults to IDLE_SECONDS (active-use window).
    Pass VIEW_HOLD_SECONDS for a short page-open hold.
    """
    uid = (uid or "").strip().lower()
    if not uid:
        return
    if ttl is None:
        ttl = IDLE_SECONDS
    try:
        r = _redis()
        prev = r.get(LOCK_KEY)
        if prev != uid:
            r.set(LOCK_TS_KEY, int(time.time()))
        # Never shorten an existing self-held lock: if we already hold it with
        # a longer remaining TTL, keep the longer one (a page reload mid-chat
        # shouldn't drop a 10-min active hold down to 90s).
        if prev == uid:
            try:
                cur_ttl = r.ttl(LOCK_KEY)
                if cur_ttl and cur_ttl > ttl:
                    ttl = cur_ttl
            except Exception:
                pass
        r.set(LOCK_KEY, uid, ex=ttl)
        r.expire(LOCK_TS_KEY, max(ttl, IDLE_SECONDS))
    except Exception:
        pass


def release(uid):
    """Release the lock if held by uid (called on logout)."""
    uid = (uid or "").strip().lower()
    try:
        r = _redis()
        if r.get(LOCK_KEY) == uid:
            r.delete(LOCK_KEY)
            r.delete(LOCK_TS_KEY)
    except Exception:
        pass
