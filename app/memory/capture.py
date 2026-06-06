"""
Phase 9 — Memory Capture.

Persists conversations into long-term Chroma memory so Silex remembers
across sessions.

Flow:
  1. A session goes idle (no new turns for IDLE_MINUTES)
  2. Its turns are pulled from Redis, paired into exchanges
  3. PII is sanitized (capsule_ingest pattern)
  4. Each exchange is embedded and upserted into the right collection:
       - Alva       -> ncaidslphd  (the shared archive grows)
       - other user -> lphd_<username>  (per-user private memory)
  5. Session marked captured (dedup via content hash) so it isn't re-ingested

Identity-scoped: each user's conversational memory stays in their own collection,
matching the privacy model from get_full_system_prompt.
"""
import os
import re
import time
import hashlib
import logging

logger = logging.getLogger(__name__)

IDLE_SECONDS   = int(os.environ.get("CAPTURE_IDLE_SECONDS", "1800"))   # 30 min idle
MIN_EXCHANGE   = int(os.environ.get("CAPTURE_MIN_CHARS", "20"))         # skip trivial
CAPTURE_TEMP_LIMIT = int(os.environ.get("CAPTURE_TEMP_LIMIT", "58"))


# ── PII sanitizing (from capsule_ingest.py) ────────────────────────────────────

_PII_PATTERNS = [
    re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),         # email
    re.compile(r"(?<!\d)(\d{3}[-.\s]?\d{2}[-.\s]?\d{4})(?!\d)"),           # SSN
    re.compile(r"(?:api|secret|token|key)[=:]\s*[A-Za-z0-9_\-]{12,}", re.I),# secrets
    re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"),                            # IPv4
]


def sanitize_text(text: str) -> str:
    masked = text
    for pat in _PII_PATTERNS:
        masked = pat.sub("[REDACTED]", masked)
    return masked


# ── Redis helpers ──────────────────────────────────────────────────────────────

def _redis():
    from ..extensions import redis_client
    return redis_client


def _gpu_temp():
    import subprocess
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=temperature.gpu", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5
        )
        return int(r.stdout.strip().split("\n")[0])
    except Exception:
        return None


# ── Collection routing ─────────────────────────────────────────────────────────

def _collection_for_user(user_name: str | None) -> str:
    """Alva's memory grows the shared archive; others get their own collection."""
    if not user_name:
        return "lphd_anon"
    raw = os.environ.get("ALVA_IDENTITIES", "")
    alva = {x.strip().lower() for x in raw.split(",") if x.strip()}
    if user_name.strip().lower() in alva:
        return "ncaidslphd"
    safe = re.sub(r"[^a-z0-9_]+", "_", user_name.strip().lower())
    return f"lphd_{safe}"


# ── Exchange pairing ───────────────────────────────────────────────────────────

def _pair_exchanges(turns: list) -> list:
    """
    turns: list of {role, content}.
    Pair user+assistant into 'Alva: ... \n Silex: ...' exchange blocks.
    """
    exchanges = []
    i = 0
    while i < len(turns):
        t = turns[i]
        if t.get("role") == "user":
            user_text = t.get("content", "").strip()
            assistant_text = ""
            if i + 1 < len(turns) and turns[i + 1].get("role") == "assistant":
                assistant_text = turns[i + 1].get("content", "").strip()
                i += 2
            else:
                i += 1
            block = f"User: {user_text}"
            if assistant_text:
                block += f"\n\nSilex: {assistant_text}"
            exchanges.append(block)
        else:
            i += 1
    return exchanges


# ── Capture one session ────────────────────────────────────────────────────────

def capture_session(session_id: str, user_name: str | None = None) -> dict:
    """
    Capture a single session's turns into long-term memory.
    If the session belongs to a project, capture goes to the project's
    collection; otherwise it follows identity routing (Alva->ncaidslphd,
    others->lphd_<user>).
    Returns a result dict.
    """
    r = _redis()

    # Pull session turns — stored by session.py as a Redis list of JSON turns
    import json
    raw_turns = r.lrange(f"silex:session:{session_id}:turns", 0, -1)
    if not raw_turns:
        return {"captured": 0, "reason": "no_turns", "session": session_id}

    turns = []
    for rt in raw_turns:
        try:
            turns.append(json.loads(rt))
        except Exception:
            continue

    exchanges = _pair_exchanges(turns)
    if not exchanges:
        return {"captured": 0, "reason": "no_exchanges", "session": session_id}

    # Project routing — if the session is tied to a project, capture there.
    collection = None
    try:
        meta = r.hgetall(f"silex:session:{session_id}:meta") or {}
        project_id = meta.get("project")
        if project_id and user_name:
            uid = user_name.strip().lower()
            blob = r.hget(f"silex:user:{uid}:projects", project_id)
            if blob:
                collection = json.loads(blob).get("collection")
    except Exception:
        collection = None

    if not collection:
        collection = _collection_for_user(user_name)

    captured_set_key = f"silex:captured:{collection}"

    # Thermal gate
    t = _gpu_temp()
    if t is not None and t > CAPTURE_TEMP_LIMIT:
        return {"captured": 0, "reason": "thermal", "gpu_temp": t, "session": session_id}

    from rag.chunker import Chunk
    from rag.chroma_store import ingest_chunks

    to_ingest = []
    for ex in exchanges:
        if len(ex) < MIN_EXCHANGE:
            continue
        clean = sanitize_text(ex)
        h = hashlib.sha256(clean.encode("utf-8")).hexdigest()[:32]
        # Dedup — skip if already captured
        if r.sismember(captured_set_key, h):
            continue
        to_ingest.append((h, Chunk(
            text=clean,
            metadata={
                "collection":  collection,
                "priority_level": "LOW_HISTORICAL",
                "source":      "conversation",
                "session_id":  session_id,
                "user":        user_name or "anon",
                "ts":          time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
        )))

    if not to_ingest:
        return {"captured": 0, "reason": "all_duplicates", "session": session_id}

    chunks = [c for _, c in to_ingest]
    added = ingest_chunks(collection, chunks)

    # Mark hashes captured
    for h, _ in to_ingest:
        r.sadd(captured_set_key, h)

    logger.info("Captured %d exchanges from %s into %s", added, session_id, collection)
    return {
        "captured":   added,
        "collection": collection,
        "session":    session_id,
        "gpu_temp":   t,
    }


# ── Sweep idle sessions ────────────────────────────────────────────────────────

def sweep_idle_sessions() -> dict:
    """
    Find sessions idle > IDLE_SECONDS and capture them.
    Session metadata (last activity, user) tracked under silex:session:<id>:meta.
    """
    r = _redis()
    now = time.time()
    results = []

    # Sessions register themselves in a set with last-activity timestamps
    session_ids = r.smembers("silex:sessions:active")
    for sid in session_ids:
        meta = r.hgetall(f"silex:session:{sid}:meta")
        last = float(meta.get("last_activity", 0) or 0)
        user = meta.get("user") or None
        if now - last < IDLE_SECONDS:
            continue
        res = capture_session(sid, user_name=user)
        results.append(res)
        # Once captured, remove from active set
        if res.get("captured", 0) >= 0 and res.get("reason") != "thermal":
            r.srem("silex:sessions:active", sid)

    return {"swept": len(results), "results": results}
