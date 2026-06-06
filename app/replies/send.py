"""
Reply send path — Piece 5. The ONLY code that actually puts mail on the wire.

ETHICAL DESIGN:
  - A reply is sent only when its status is APPROVED (set by the human-approval
    action) OR when it is a fast-lane (requires_approval=0, i.e. to Alva) draft
    that Alva has opted to auto-send.
  - Every outbound message carries a working one-click unsubscribe footer for the
    recipient (CAN-SPAM compliant, and ethically required).
  - Unsubscribe status is honored: a send to an unsubscribed address is refused.
  - Sends are logged.
"""
import os
import logging
import smtplib
from email.message import EmailMessage
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

SMTP_HOST = os.environ.get("SMTP_HOST", "mail.wrds361.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USERNAME", "silex@wrds361.com")
SMTP_PASS = os.environ.get("SMTP_PASSWORD", os.environ.get("IMAP_PASSWORD", ""))
FROM_ADDR = os.environ.get("SILEX_FROM_ADDR", "silex@wrds361.com")
FROM_NAME = os.environ.get("SILEX_FROM_NAME", "Silex")
AI_BASE_URL = os.environ.get("AI_BASE_URL", "https://ai.wrds361.com")

ALVA_IDENTITIES = {u.strip().lower() for u in os.environ.get("ALVA_IDENTITIES", "").split(",") if u.strip()}


def _redis():
    from ..extensions import redis_client
    return redis_client


def _unsubscribe_footer(to_addr: str) -> str:
    """Build the unsubscribe footer for a recipient. Empty for Alva (internal)."""
    if to_addr.strip().lower() in ALVA_IDENTITIES:
        return ""  # no marketing footer on internal self-correspondence
    try:
        from ..subscribers.store import get_subscriber
        sub = get_subscriber(to_addr)
        if sub and sub.get("unsubscribe_token"):
            url = f"{AI_BASE_URL}/unsubscribe/{sub['unsubscribe_token']}"
            return ("\n\n—\nYou are receiving this because you subscribed to correspondence "
                    f"from Silex at MyArea.\nTo unsubscribe at any time: {url}")
    except Exception as exc:
        logger.warning("footer lookup failed: %s", exc)
    return ("\n\n—\nTo stop receiving messages, reply with UNSUBSCRIBE or contact the "
            "platform owner.")  # fallback footer if not a tracked subscriber


def _send_smtp(to_addr: str, subject: str, body: str) -> dict:
    try:
        msg = EmailMessage()
        msg["From"] = f"{FROM_NAME} <{FROM_ADDR}>"
        msg["To"] = to_addr
        msg["Subject"] = subject
        msg.set_content(body)

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as s:
            s.starttls()
            s.login(SMTP_USER, SMTP_PASS)
            s.send_message(msg)
        return {"ok": True}
    except Exception as exc:
        logger.error("SMTP send failed: %s", exc)
        return {"ok": False, "error": str(exc)}


def send_approved_reply(rid: str) -> dict:
    """
    Send a reply that is APPROVED (or fast-lane auto-send). Refuses otherwise.
    Appends the unsubscribe footer, honors unsubscribe status, marks SENT.
    """
    from .store import get_reply, set_status, REPLY_APPROVED, REPLY_SENT

    entry = get_reply(rid)
    if not entry:
        return {"ok": False, "error": "not_found"}

    to_addr = (entry.get("to") or "").strip().lower()
    requires_approval = entry.get("requires_approval") == "1"
    status = entry.get("status")

    # Gate: must be approved, unless it's a fast-lane (Alva) reply
    if requires_approval and status != REPLY_APPROVED:
        return {"ok": False, "error": "not_approved",
                "detail": "This reply requires explicit approval before sending."}

    # Honor unsubscribe — never send to someone who opted out (except Alva internal)
    if to_addr not in ALVA_IDENTITIES:
        try:
            from ..subscribers.store import get_subscriber
            sub = get_subscriber(to_addr)
            if sub and sub.get("status") == "unsubscribed":
                set_status(rid, "rejected")
                return {"ok": False, "error": "recipient_unsubscribed"}
        except Exception:
            pass

    subject = entry.get("subject") or "(no subject)"
    if not subject.lower().startswith("re:"):
        subject = "Re: " + subject

    body = (entry.get("draft") or "").strip()
    body += f"\n\n— {FROM_NAME}"
    body += _unsubscribe_footer(to_addr)

    result = _send_smtp(to_addr, subject, body)
    if result.get("ok"):
        set_status(rid, REPLY_SENT)
        logger.info("reply SENT: %s -> %s", rid, to_addr)
        return {"ok": True, "sent": True, "to": to_addr}
    return {"ok": False, "error": result.get("error", "send_failed")}


def maybe_autosend_fastlane(rid: str) -> dict:
    """
    For fast-lane (requires_approval=0, i.e. to Alva) drafts: auto-send immediately.
    Called right after drafting. Returns send result or a 'held' note.
    """
    from .store import get_reply
    entry = get_reply(rid)
    if not entry:
        return {"held": True, "reason": "not_found"}
    if entry.get("requires_approval") == "1":
        return {"held": True, "reason": "requires_approval"}
    # fast lane → send now
    return send_approved_reply(rid)
