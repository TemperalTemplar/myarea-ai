from flask import Blueprint, render_template
from ..auth.sso import login_required

ui_bp = Blueprint("ui", __name__)


@ui_bp.get("/")
@login_required
def index():
    # ── Single-user exclusive lock: block second user at the door ──────────
    # The Tesla P4 is one GPU; only one active session at a time. Opening the
    # page takes a short hold (VIEW_HOLD_SECONDS); chat messages refresh it to
    # the full active-use window. Architect (ALVA_IDENTITIES) overrides.
    # Fails OPEN: any lock-system error must never deny access.
    try:
        from ..auth.sso import get_current_user
        from ..auth.userlock import check_access, acquire, holder_since, VIEW_HOLD_SECONDS
        import time as _t
        u = get_current_user() or {}
        _uid = (u.get("name") or u.get("username") or "").strip().lower()
        if _uid:
            _ok, _holder = check_access(_uid)
            if not _ok:
                _since = holder_since()
                _mins = ""
                if _since:
                    _mins = str(int((_t.time() - _since) // 60)) + " min"
                return render_template("locked.html", holder=_holder, active_for=_mins), 423
            # Allowed — take a short page-open hold (true block-at-door).
            acquire(_uid, ttl=VIEW_HOLD_SECONDS)
    except Exception:
        pass  # fail open — never lock everyone out on a bug
    return render_template("index.html")


@ui_bp.get("/journal")
@login_required
def journal():
    return render_template("journal.html")


@ui_bp.get("/security")
@login_required
def security():
    return render_template("security.html")
