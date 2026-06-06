"""
Comms Line — Phase 6.

Routes shareable journal entries and Sparta critical alerts
to Rocket.Chat webhook and email via Mailcow SMTP.
"""
import os
import smtplib
import logging
import httpx
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

logger = logging.getLogger(__name__)


def _env(key, default=None):
    return os.environ.get(key, default)


def post_to_rocketchat(text: str, emoji: str = ":robot:", username: str = "Silex") -> bool:
    webhook = _env("ROCKETCHAT_WEBHOOK")
    if not webhook:
        logger.warning("ROCKETCHAT_WEBHOOK not set")
        return False
    try:
        with httpx.Client(timeout=10) as client:
            r = client.post(webhook, json={"text": text, "username": username, "emoji": emoji})
            r.raise_for_status()
            return True
    except Exception as exc:
        logger.error("Rocket.Chat post failed: %s", exc)
        return False


def send_email(subject: str, body: str, to: str | None = None) -> bool:
    smtp_host = _env("SMTP_HOST", "mail.wrds361.com")
    smtp_port = int(_env("SMTP_PORT", 587))
    smtp_user = _env("SMTP_USER", "silex@wrds361.com")
    smtp_pass = _env("SMTP_PASSWORD", "")
    smtp_from = _env("SMTP_FROM", "silex@wrds361.com")
    to_addr   = to or _env("COMMS_ALERT_EMAIL", "temp@wrds361.com")
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = f"Silex <{smtp_from}>"
        msg["To"]      = to_addr
        msg.attach(MIMEText(body, "plain"))
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_from, to_addr, msg.as_string())
        logger.info("Email sent to %s", to_addr)
        return True
    except Exception as exc:
        logger.error("Email failed: %s", exc)
        return False


def send_journal_entry(entry: dict) -> dict:
    """Route shareable chaos journal entry to Rocket.Chat."""
    content  = entry.get("content", "")
    ts       = _fmt_ts(entry.get("timestamp", ""))
    gpu_temp = entry.get("gpu_temp", "?")
    text = f"*Silex — Private Thought* | {ts} | GPU: {gpu_temp}°C\n\n{content}"
    return {"rocketchat": post_to_rocketchat(text, emoji=":thought_balloon:")}


def send_alert(entry: dict) -> dict:
    """Route critical Sparta finding to Rocket.Chat and email."""
    content  = entry.get("content", "")
    severity = entry.get("severity", "warning").upper()
    ts       = _fmt_ts(entry.get("timestamp", ""))
    gpu_temp = entry.get("gpu_temp", "?")
    emoji    = ":rotating_light:" if severity == "CRITICAL" else ":warning:"
    rc_text  = f"{emoji} *SPARTA {severity}* | {ts} | GPU: {gpu_temp}°C\n\n```\n{content}\n```"
    rc_ok    = post_to_rocketchat(rc_text, emoji=emoji)
    mail_ok  = send_email(f"[Silex/Sparta] {severity} Alert — {ts}", content) if severity == "CRITICAL" else False
    return {"rocketchat": rc_ok, "email": mail_ok}


def _fmt_ts(ts: str) -> str:
    try:
        return datetime.fromisoformat(ts).strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return ts
