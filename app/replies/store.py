"""
Reply draft + approval store — the heart of the email-reply system.

ETHICAL DESIGN (the core safety guarantees):
  - Silex NEVER sends a reply autonomously. She drafts; a human approves; then it sends.
  - Drafting is ON-DEMAND only (you choose which emails deserve a reply) — saves GPU
    and means she never drafts to mail you'd ignore.
  - Drafting is THERMAL-GATED: the model only runs when GPU < REPLY_TEMP_LIMIT (57°C).
  - Every reply has a lifecycle: pending -> approved -> sent  (or -> rejected).
  - Replies to temp@ may use a fast lane; replies to anyone else REQUIRE approval.

Drafts persist in Redis (hashes), indexed by a list, so they survive restarts.
"""
import os
import json
import time
import uuid
import logging

logger = logging.getLogger(__name__)

REPLY_PENDING  = "pending"
REPLY_APPROVED = "approved"
REPLY_SENT     = "sent"
REPLY_REJECTED = "rejected"

REPLY_TEMP_LIMIT = int(os.environ.get("REPLY_TEMP_LIMIT", 57))
OLLAMA_BASE_URL  = os.environ.get("OLLAMA_BASE_URL", "http://172.30.0.1:11434")
SILEX_MODEL      = os.environ.get("SILEX_MODEL", "cnmoro/gemma2-2b-it-abliterated:q8_0")
NCAIDSHP_LEAN_PATH = os.environ.get("NCAIDSHP_LEAN_PATH", "data/ncaidshp/lean.txt")
ALVA_IDENTITIES  = {u.strip().lower() for u in os.environ.get("ALVA_IDENTITIES", "").split(",") if u.strip()}

_LIST_KEY  = "silex:replies:index"
_ENTRY_KEY = "silex:replies:entry:{id}"
_MAX       = 500


def _redis():
    from ..extensions import redis_client
    return redis_client


def _gpu_temp():
    import subprocess
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=temperature.gpu", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5)
        return int(r.stdout.strip())
    except Exception as exc:
        logger.warning("reply gpu temp read failed: %s", exc)
        return None


def _extract_addr(from_header: str) -> str:
    import re
    m = re.search(r"<([^>]+)>", from_header or "")
    return (m.group(1) if m else (from_header or "")).strip().lower()


def create_pending_reply(in_reply_to_addr: str, subject: str, original_text: str,
                         draft_body: str, requires_approval: bool) -> dict:
    """Persist a drafted reply awaiting decision."""
    r = _redis()
    rid = str(uuid.uuid4())
    entry = {
        "id": rid,
        "to": in_reply_to_addr,
        "subject": subject,
        "original": original_text[:2000],
        "draft": draft_body,
        "status": REPLY_PENDING,
        "requires_approval": "1" if requires_approval else "0",
        "created_at": str(time.time()),
        "decided_at": "",
    }
    r.hset(_ENTRY_KEY.format(id=rid), mapping=entry)
    r.lpush(_LIST_KEY, rid)
    r.ltrim(_LIST_KEY, 0, _MAX - 1)
    return entry


def get_reply(rid: str) -> dict | None:
    r = _redis()
    h = r.hgetall(_ENTRY_KEY.format(id=rid))
    return h or None


def list_replies(status: str | None = None, limit: int = 50) -> list:
    r = _redis()
    ids = r.lrange(_LIST_KEY, 0, limit * 2)
    out = []
    for rid in ids:
        h = r.hgetall(_ENTRY_KEY.format(id=rid))
        if not h:
            continue
        if status and h.get("status") != status:
            continue
        out.append(h)
        if len(out) >= limit:
            break
    return out


def set_status(rid: str, status: str) -> dict | None:
    r = _redis()
    key = _ENTRY_KEY.format(id=rid)
    if not r.exists(key):
        return None
    r.hset(key, mapping={"status": status, "decided_at": str(time.time())})
    return r.hgetall(key)


def draft_reply_for(from_header: str, subject: str, original_text: str) -> dict:
    """
    On-demand: generate a reply DRAFT for an incoming email. Thermal-gated.
    Determines whether approval is required (anyone who isn't Alva needs approval).
    Returns the stored pending-reply entry, or an error dict.
    """
    addr = _extract_addr(from_header)
    requires_approval = addr not in ALVA_IDENTITIES

    # Thermal gate — do not run the model when hot
    temp = _gpu_temp()
    if temp is not None and temp >= REPLY_TEMP_LIMIT:
        return {"ok": False, "error": "thermal_gate",
                "detail": f"GPU at {temp}°C >= {REPLY_TEMP_LIMIT}°C limit — draft deferred",
                "gpu_temp": temp}

    draft = _generate_draft(subject, original_text)
    if not draft:
        return {"ok": False, "error": "generation_failed"}

    entry = create_pending_reply(addr, subject, original_text, draft, requires_approval)
    entry["ok"] = True
    entry["gpu_temp"] = temp

    # Fast lane: replies to Alva (no approval required) auto-send immediately.
    if not requires_approval:
        try:
            from .send import maybe_autosend_fastlane
            send_result = maybe_autosend_fastlane(entry["id"])
            entry["autosent"] = send_result
        except Exception as exc:
            logger.warning("fast-lane autosend failed: %s", exc)
            entry["autosent"] = {"ok": False, "error": str(exc)}

    return entry


def _generate_draft(subject: str, original_text: str) -> str | None:
    """Compose a reply draft via Ollama, in Silex's voice."""
    try:
        import httpx
        lean = ""
        if os.path.exists(NCAIDSHP_LEAN_PATH):
            with open(NCAIDSHP_LEAN_PATH, encoding="utf-8") as f:
                lean = f.read().strip()

        system = (
            f"{lean}\n\n[INTENT: EMAIL REPLY] [MODE: DRAFT FOR HUMAN APPROVAL]\n"
            "You are drafting a reply to an email. Write only the body of the reply, in "
            "your own voice as Silex — warm, clear, genuine, concise. Do NOT invent facts "
            "you do not have. Do NOT include a subject line, signature, or unsubscribe "
            "footer (those are added separately). This draft will be reviewed by Alva "
            "before it is ever sent; write it as the message you would want him to approve."
        )
        user = (f"The email you are replying to:\n\nSubject: {subject}\n\n{original_text[:1500]}\n\n"
                "Write your reply (body only):")

        payload = {
            "model": SILEX_MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "options": {"temperature": 0.7, "num_predict": 400},
        }
        with httpx.Client(timeout=90) as client:
            r = client.post(f"{OLLAMA_BASE_URL}/api/chat", json=payload)
            r.raise_for_status()
            return r.json()["message"]["content"].strip()
    except Exception as exc:
        logger.error("draft generation failed: %s", exc)
        return None
