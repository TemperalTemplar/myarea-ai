"""
Incoming email bridge — /opt/mark1 fold-in #3.

Polls Silex's IMAP inbox, processes mail ONLY from whitelisted senders,
sanitizes content, and makes Silex aware via journal + comms notify.

SAFETY: email is an untrusted input channel. Mail from non-whitelisted senders
is ignored entirely (never enters Silex's context — prompt-injection guard).
No auto-reply. Read-only awareness.

Adapted from email_bridge.py: header decode, multipart extraction, html->text,
sender whitelist, subject filter, mark-seen. State (processed UIDs) in Redis.
"""
import os
import re
import time
import email
import imaplib
import logging
from email.header import decode_header, make_header
from email import policy
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

IMAP_HOST     = os.environ.get("IMAP_HOST", "mail.wrds361.com")
IMAP_PORT     = int(os.environ.get("IMAP_PORT", "993"))
IMAP_USERNAME = os.environ.get("IMAP_USERNAME", "silex@wrds361.com")
IMAP_PASSWORD = os.environ.get("IMAP_PASSWORD", "")
IMAP_MAILBOX  = os.environ.get("IMAP_MAILBOX", "INBOX")

# Whitelist — ONLY these senders are processed. Default: Alva only.
WHITELIST_SENDERS = [s.strip().lower() for s in
                     os.environ.get("EMAIL_WHITELIST", "temp@wrds361.com,alvaroberts@ar-ics.com").split(",")
                     if s.strip()]

MAX_PREVIEW  = int(os.environ.get("EMAIL_PREVIEW_CHARS", "400"))
MARK_SEEN    = os.environ.get("EMAIL_MARK_SEEN", "true").lower() == "true"

# Auto-reply: ONLY to Alva's own mail. Everyone else goes through /approvals.
EMAIL_AUTO_REPLY_ALVA = os.environ.get("EMAIL_AUTO_REPLY_ALVA", "true").lower() == "true"
ALVA_IDENTITIES = {u.strip().lower() for u in os.environ.get("ALVA_IDENTITIES", "").split(",") if u.strip()}


def _is_alva_sender(from_header: str) -> bool:
    m = re.search(r"<([^>]+)>", from_header or "")
    addr = (m.group(1) if m else (from_header or "")).strip().lower()
    return addr in ALVA_IDENTITIES

JOURNAL_API_URL = os.environ.get("JOURNAL_API_URL", "http://myarea-ai:8930/api/journal/internal")
SERVICE_API_KEY = os.environ.get("SERVICE_API_KEY", "")

# PII sanitize (same patterns as capsule_ingest / capture)
_PII = [
    re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    re.compile(r"(?<!\d)(\d{3}[-.\s]?\d{2}[-.\s]?\d{4})(?!\d)"),
    re.compile(r"(?:api|secret|token|key)[=:]\s*[A-Za-z0-9_\-]{12,}", re.I),
    re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"),
]


def _sanitize(text: str) -> str:
    for p in _PII:
        text = p.sub("[REDACTED]", text)
    return text


def _redis():
    import redis as _r
    return _r.from_url(os.environ.get("REDIS_URL", "redis://myarea-ai-redis:6379/0"), decode_responses=True)


def _decode_hdr(v) -> str:
    if v is None:
        return ""
    try:
        return str(make_header(decode_header(v)))
    except Exception:
        return str(v)


def _sender_ok(sender: str) -> bool:
    sender = (sender or "").lower()
    m = re.search(r"<([^>]+)>", sender)
    addr = m.group(1).lower() if m else sender.strip()
    return any(addr == x or addr.endswith(x) for x in WHITELIST_SENDERS)


def _html_to_text(html: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", "", html)
    text = re.sub(r"(?is)<br\s*/?>", "\n", text)
    text = re.sub(r"(?is)</p>", "\n\n", text)
    text = re.sub(r"(?is)<.*?>", "", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _extract_text(msg) -> str:
    plain, html = [], []
    if msg.is_multipart():
        for part in msg.walk():
            disp = str(part.get("Content-Disposition") or "")
            if "attachment" in disp.lower():
                continue
            ctype = part.get_content_type()
            try:
                payload = part.get_payload(decode=True) or b""
            except Exception:
                payload = b""
            charset = part.get_content_charset() or "utf-8"
            try:
                decoded = payload.decode(charset, errors="replace")
            except Exception:
                decoded = payload.decode("utf-8", errors="replace")
            if ctype == "text/plain":
                plain.append(decoded)
            elif ctype == "text/html":
                html.append(decoded)
    else:
        payload = msg.get_payload(decode=True) or b""
        charset = msg.get_content_charset() or "utf-8"
        try:
            decoded = payload.decode(charset, errors="replace")
        except Exception:
            decoded = payload.decode("utf-8", errors="replace")
        if msg.get_content_type() == "text/html":
            html.append(decoded)
        else:
            plain.append(decoded)

    ptxt = "\n".join(p.strip() for p in plain if p.strip())
    htxt = "\n".join(h.strip() for h in html if h.strip())
    if not ptxt and htxt:
        ptxt = _html_to_text(htxt)
    return ptxt.strip()


def _notify(from_, subject, preview):
    """
    Make Silex aware of incoming mail by writing a journal entry marked shareable.
    The existing hourly comms flush picks up shareable entries and delivers them
    to Rocket.Chat + email — so no separate notify endpoint is needed.
    """
    import httpx
    headers = {"Authorization": f"Bearer {SERVICE_API_KEY}", "Content-Type": "application/json"}

    summary = f"📧 Email received from {from_}\nSubject: {subject}\n\n{preview}"

    try:
        httpx.post(JOURNAL_API_URL, json={
            "content": summary,
            "shareable": True,   # flush will surface this to Rocket.Chat + email
            "source": "email-in",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }, headers=headers, timeout=10)
    except Exception as exc:
        logger.warning("email journal write failed: %s", exc)


def poll_inbox() -> dict:
    """One poll cycle. Returns a summary dict."""
    if not IMAP_PASSWORD:
        return {"error": "IMAP_PASSWORD not set"}

    r = _redis()
    processed_key = "silex:email:processed_uids"
    seen_count = 0
    processed_count = 0
    skipped = 0

    try:
        M = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        M.login(IMAP_USERNAME, IMAP_PASSWORD)
        M.select(IMAP_MAILBOX)

        typ, data = M.uid("search", None, "(UNSEEN)")
        if typ != "OK":
            M.logout()
            return {"error": "search_failed"}

        uids = data[0].split()
        seen_count = len(uids)

        for uid in uids:
            uid_s = uid.decode()
            if r.sismember(processed_key, uid_s):
                continue

            typ, mdata = M.uid("fetch", uid, "(RFC822)")
            if typ != "OK" or not mdata or not mdata[0]:
                continue
            raw = mdata[0][1] if isinstance(mdata[0], tuple) else None
            if not raw:
                continue

            msg = email.message_from_bytes(raw, policy=policy.default)
            from_ = _decode_hdr(msg.get("From"))
            subj = _decode_hdr(msg.get("Subject"))

            # SAFETY: only whitelisted senders
            if not _sender_ok(from_):
                logger.info("email skip (sender not whitelisted): %s", from_)
                r.sadd(processed_key, uid_s)  # mark so we don't re-evaluate
                if MARK_SEEN:
                    try:
                        M.uid("store", uid, "+FLAGS", r"(\Seen)")
                    except Exception:
                        pass
                skipped += 1
                continue

            text = _extract_text(msg)
            preview = _sanitize(text)[:MAX_PREVIEW]
            if len(text) > MAX_PREVIEW:
                preview += "…"

            _notify(from_, subj, preview)
            r.sadd(processed_key, uid_s)
            processed_count += 1

            # Auto-reply for Alva's own mail only. Everyone else waits for approval
            # in the /approvals UI. Thermal gate lives inside draft_reply_for.
            if EMAIL_AUTO_REPLY_ALVA and _is_alva_sender(from_):
                try:
                    from ..replies.store import draft_reply_for
                    res = draft_reply_for(from_, subj, text)
                    logger.info("auto-reply (Alva) for '%s': %s",
                                subj, res.get("autosent") or res.get("error"))
                except Exception as exc:
                    logger.warning("auto-reply failed: %s", exc)

            if MARK_SEEN:
                try:
                    M.uid("store", uid, "+FLAGS", r"(\Seen)")
                except Exception:
                    pass

            logger.info("email processed from=%s subject=%s", from_, subj)

        M.logout()

    except Exception as exc:
        logger.error("email poll failed: %s", exc)
        return {"error": str(exc)}

    return {"unseen": seen_count, "processed": processed_count, "skipped": skipped}
