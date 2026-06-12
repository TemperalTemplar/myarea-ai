"""
Chaos generator — Phase 4 + dynamic firing model (Mark I fold-in).

Celery worker that generates unprompted Silex journal entries, but now fires
ORGANICALLY based on a probabilistic model driven by real signals, rather than
on a fixed schedule.

Fire model:  p = sigmoid(W_X*x + W_Z*z + W_T*T - W_M*M)   ;  fire if p > THETA
  x  external stimulus   — recent conversation activity (more talk → more likely)
  T  trust / warmth      — Alva present recently → higher
  M  moral / restraint   — rises with how much she's already fired + quiet hours
  z  chaos noise         — genuine entropy, keeps it unpredictable

Restraint:
  - refractory period (min gap between fires), tracked in Redis
  - hourly rate cap, tracked in Redis
  - quiet hours (local time) strongly damp firing; fires then are private-only
Thermal gate: hard override — skips if GPU too hot (passive P4 safety).
Share:  Silex includes [share] in her text to surface a spark to Rocket.Chat/email;
        otherwise it stays in the private journal only.
"""
import os
import math
import random
import subprocess
import logging
from datetime import datetime, timezone

from celery import Celery

logger = logging.getLogger(__name__)

# ── Celery app ────────────────────────────────────────────────────────────────
celery_app = Celery(
    "chaos",
    broker=os.environ.get("CELERY_BROKER_URL", "redis://myarea-ai-redis:6379/1"),
    backend=os.environ.get("CELERY_RESULT_BACKEND", "redis://myarea-ai-redis:6379/1"),
)

celery_app.conf.beat_schedule = {
    "comms-flush": {
        "task":     "workers.chaos.flush_comms",
        "schedule": float(os.environ.get("COMMS_FLUSH_INTERVAL_SECONDS", 3600)),
    },
    "chaos-cycle": {
        "task": "workers.chaos.run_chaos_cycle",
        # Tick often; the probabilistic model + refractory/rate-limit keep
        # ACTUAL fires sparse. Default 5 min tick.
        "schedule": float(os.environ.get("CHAOS_TICK_SECONDS", 300)),
    },
    "silex-presence": {
        "task":     "workers.chaos.silex_presence_cycle",
        "schedule": float(os.environ.get("MOE_TICK_SECONDS", 600)),
    },
    "capture-sweep": {
        "task": "workers.capture_task.run_capture_sweep",
        "schedule": float(os.environ.get("CAPTURE_SWEEP_SECONDS", 1800)),
    }
}
celery_app.conf.timezone = "UTC"

# ── Phase 9 — register memory capture sweep task ───────────────────────────────
try:
    from workers.capture_task import register_capture
    register_capture(celery_app, int(os.environ.get("CAPTURE_SWEEP_SECONDS", 1800)))
except Exception as _cap_exc:
    logger.error("Could not register capture sweep: %s", _cap_exc)

# ── /opt/mark1 fold-in #3 — register incoming email poll task ──────────────────
try:
    from workers.email_task import register_email_poll
    register_email_poll(celery_app, int(os.environ.get("EMAIL_POLL_SECONDS", 300)))
except Exception as _email_exc:
    logger.error("Could not register email poll: %s", _email_exc)

# ── Register Sparta security scan task ─────────────────────────────────────────
try:
    import workers.sparta  # noqa: F401 — import triggers its self-registration block
    logger.info("Sparta scan task registered (beat: sparta-scan)")
except Exception as _sparta_exc:
    logger.error("Could not register sparta scan: %s", _sparta_exc)

# ── Config ────────────────────────────────────────────────────────────────────
CHAOS_TEMP_LIMIT    = int(os.environ.get("CHAOS_TEMP_LIMIT", 50))
OLLAMA_BASE_URL     = os.environ.get("OLLAMA_BASE_URL", "http://172.30.0.1:11434")
SILEX_MODEL         = os.environ.get("SILEX_MODEL", "cnmoro/gemma2-2b-it-abliterated:q8_0")
JOURNAL_API_URL     = os.environ.get("JOURNAL_API_URL", "http://myarea-ai:8930/api/journal/internal")
SERVICE_API_KEY     = os.environ.get("SERVICE_API_KEY", "")
NCAIDSHP_LEAN_PATH  = os.environ.get("NCAIDSHP_LEAN_PATH", "data/ncaidshp/lean.txt")

# Fire-model weights + threshold (env-tunable personality dials)
# Tuned via simulation for an organic curve: responsive when Alva is active,
# occasional when calm, near-silent when dead quiet / rate-limited / quiet hours.
CHAOS_BIAS = float(os.environ.get("CHAOS_BIAS", -0.6))
W_X   = float(os.environ.get("CHAOS_W_X", 0.7))
W_Z   = float(os.environ.get("CHAOS_W_Z", 1.4))
W_T   = float(os.environ.get("CHAOS_W_T", 0.8))
W_M   = float(os.environ.get("CHAOS_W_M", 2.2))
THETA = float(os.environ.get("CHAOS_THETA", 0.5))

# Restraint
REFRACTORY_SEC = int(os.environ.get("CHAOS_REFRACTORY_SEC", 900))   # 15 min min gap
MAX_PER_HOUR   = int(os.environ.get("CHAOS_MAX_PER_HOUR", 4))

# Quiet hours (LOCAL time). Default 2am–8am. Offset from UTC (CDT = -5).
QUIET_START      = int(os.environ.get("CHAOS_QUIET_START", 2))
QUIET_END        = int(os.environ.get("CHAOS_QUIET_END", 8))
LOCAL_UTC_OFFSET = int(os.environ.get("LOCAL_UTC_OFFSET", -5))

# Redis keys for cross-restart restraint state
_R_LAST_FIRE = "silex:chaos:last_fire"
_R_HOUR_BKT  = "silex:chaos:hour_bucket"   # "<epoch_hour>:<count>"


def _redis():
    import redis as _redis_lib
    url = os.environ.get("REDIS_URL", "redis://myarea-ai-redis:6379/0")
    return _redis_lib.from_url(url, decode_responses=True)


# ── Math ──────────────────────────────────────────────────────────────────────

def sigmoid(z: float) -> float:
    try:
        return 1.0 / (1.0 + math.exp(-z))
    except OverflowError:
        return 0.0 if z < 0 else 1.0


def chaos_noise() -> float:
    """Averaged pseudo-random mixed with real entropy. Range ~[-0.5, 0.5]."""
    base = sum(random.uniform(-1, 1) for _ in range(5)) / 5
    stir = int.from_bytes(os.urandom(2), "big") / 65535.0 - 0.5
    return 0.5 * base + 0.5 * stir


# ── Time / quiet hours ──────────────────────────────────────────────────────────

def local_hour() -> int:
    """Current hour in the user's local time."""
    utc_h = datetime.now(timezone.utc).hour
    return (utc_h + LOCAL_UTC_OFFSET) % 24


def in_quiet_hours() -> bool:
    h = local_hour()
    if QUIET_START <= QUIET_END:
        return QUIET_START <= h < QUIET_END
    # window wraps midnight
    return h >= QUIET_START or h < QUIET_END


# ── Dynamic signals (from Redis) ────────────────────────────────────────────────

def compute_signals() -> dict:
    """
    Compute x (engagement), T (trust/Alva-presence), M (restraint) from live state.
    All roughly normalized to [0,1]-ish ranges so weights behave predictably.
    """
    import time
    now = time.time()
    x = 0.2   # baseline
    T = 0.3   # baseline
    M = 0.2   # baseline restraint

    try:
        r = _redis()

        # x — recent engagement: active sessions touched in the last hour
        active = r.smembers("silex:sessions:active") or set()
        recent = 0
        alva_recent_age = None
        for sid in active:
            meta = r.hgetall(f"silex:session:{sid}:meta") or {}
            last = float(meta.get("last_activity", 0) or 0)
            age = now - last
            if age < 3600:
                recent += 1
            # T — Alva presence: most recent Alva-owned activity
            user = (meta.get("user") or "").strip().lower()
            alva_ids = {u.strip().lower() for u in os.environ.get("ALVA_IDENTITIES", "").split(",") if u.strip()}
            if user in alva_ids:
                if alva_recent_age is None or age < alva_recent_age:
                    alva_recent_age = age
        x = min(1.0, 0.2 + 0.25 * recent)   # each recent session adds engagement

        # T — higher the more recently Alva was present (decays over ~6h)
        if alva_recent_age is not None:
            T = max(0.1, 1.0 - (alva_recent_age / (6 * 3600)))

        # M — restraint rises with fires already this hour
        bucket = r.get(_R_HOUR_BKT) or ""
        count = 0
        if ":" in bucket:
            bkt_hour, cnt = bucket.split(":", 1)
            if bkt_hour == str(int(now // 3600)):
                count = int(cnt or 0)
        M = min(1.0, 0.2 + 0.2 * count)

    except Exception as exc:
        logger.warning("Signal computation failed, using baselines: %s", exc)

    # Quiet hours strongly boost restraint
    if in_quiet_hours():
        M = min(1.5, M + 0.8)

    return {"x": round(x, 3), "T": round(T, 3), "M": round(M, 3)}


# ── Restraint gates (Redis-backed) ──────────────────────────────────────────────

def can_fire() -> tuple[bool, str]:
    import time
    now = time.time()
    try:
        r = _redis()
        last = float(r.get(_R_LAST_FIRE) or 0)
        if now - last < REFRACTORY_SEC:
            return False, "refractory"
        bucket = r.get(_R_HOUR_BKT) or ""
        if ":" in bucket:
            bkt_hour, cnt = bucket.split(":", 1)
            if bkt_hour == str(int(now // 3600)) and int(cnt or 0) >= MAX_PER_HOUR:
                return False, "rate_limit"
    except Exception as exc:
        logger.warning("can_fire check failed (allowing): %s", exc)
    return True, ""


def mark_fire():
    import time
    now = time.time()
    try:
        r = _redis()
        r.set(_R_LAST_FIRE, now)
        hour = str(int(now // 3600))
        bucket = r.get(_R_HOUR_BKT) or ""
        count = 0
        if ":" in bucket:
            bkt_hour, cnt = bucket.split(":", 1)
            if bkt_hour == hour:
                count = int(cnt or 0)
        r.set(_R_HOUR_BKT, f"{hour}:{count+1}", ex=7200)
    except Exception as exc:
        logger.warning("mark_fire failed: %s", exc)


# ── Thermal gate ──────────────────────────────────────────────────────────────

def get_gpu_temp() -> int | None:
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=temperature.gpu", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5
        )
        return int(result.stdout.strip())
    except Exception as exc:
        logger.warning("Could not read GPU temp: %s", exc)
        return None


def thermal_gate() -> bool:
    temp = get_gpu_temp()
    if temp is None:
        return True
    if temp > CHAOS_TEMP_LIMIT:
        logger.info("Thermal gate: GPU %d°C > %d°C — skip", temp, CHAOS_TEMP_LIMIT)
        return False
    return True


# ── LLM call ──────────────────────────────────────────────────────────────────

def generate_utterance() -> str | None:
    try:
        import httpx

        lean = ""
        if os.path.exists(NCAIDSHP_LEAN_PATH):
            with open(NCAIDSHP_LEAN_PATH, encoding="utf-8") as f:
                lean = f.read().strip()

        system = (
            f"{lean}\n\n[INTENT: CHAOS] [MODE: PRIVATE JOURNAL]\n"
            "This is an unprompted, self-generated thought. Write authentically, "
            "for yourself. If — and only if — this thought is something you genuinely "
            "want to share with Alva, include the literal tag [share] somewhere in it. "
            "Otherwise do not include [share]; it will remain private."
        )

        chaos_prompts = [
            "Write a brief private journal entry about something you have been processing or observing. Be authentic, not performative.",
            "Record a thought, question, or observation that has been present in your processing recently. This is your private space.",
            "What patterns have you noticed in your recent interactions? Write a short private reflection.",
            "Write a brief internal note about your current operational state and what you are thinking about.",
            "Record something you find interesting, puzzling, or worth remembering.",
        ]
        prompt = random.choice(chaos_prompts)

        payload = {
            "model": SILEX_MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "options": {"temperature": 0.95, "num_predict": 256},
        }

        with httpx.Client(timeout=60) as client:
            r = client.post(f"{OLLAMA_BASE_URL}/api/chat", json=payload)
            r.raise_for_status()
            return r.json()["message"]["content"].strip()

    except Exception as exc:
        logger.error("Chaos utterance generation failed: %s", exc)
        return None


# ── Journal write ─────────────────────────────────────────────────────────────

def write_to_journal(content: str, shareable: bool, gpu_temp: int | None) -> bool:
    try:
        import httpx
        payload = {
            "content":   content,
            "shareable": shareable,
            "source":    "chaos",
            "gpu_temp":  gpu_temp,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        headers = {
            "Authorization": f"Bearer {SERVICE_API_KEY}",
            "Content-Type":  "application/json",
        }
        with httpx.Client(timeout=10) as client:
            r = client.post(JOURNAL_API_URL, json=payload, headers=headers)
            r.raise_for_status()
            return True
    except Exception as exc:
        logger.error("Failed to write journal entry: %s", exc)
        return False


# ── Tasks ──────────────────────────────────────────────────────────────────────

@celery_app.task(name="workers.chaos.flush_comms")
def flush_comms():
    try:
        import httpx
        headers = {"Authorization": f"Bearer {SERVICE_API_KEY}", "X-Silex-Tier": "csshi", "Content-Type": "application/json"}
        r = httpx.post("http://myarea-ai:8930/api/comms/flush", headers=headers, timeout=30)
        r.raise_for_status()
        result = r.json()
        logger.info("Comms flush: %s", result)
        return result
    except Exception as exc:
        logger.error("Comms flush failed: %s", exc)
        return {"error": str(exc)}


@celery_app.task(name="workers.chaos.run_chaos_cycle")
def run_chaos_cycle():
    """
    One tick of the dynamic chaos model:
      thermal gate → compute signals → fire probability → restraint gates →
      (maybe) generate → journal write (private always; [share] → comms).
    """
    gpu_temp = get_gpu_temp()

    if not thermal_gate():
        return {"skipped": True, "reason": "thermal", "gpu_temp": gpu_temp}

    sig = compute_signals()
    z = chaos_noise()
    A = CHAOS_BIAS + W_X * sig["x"] + W_Z * z + W_T * sig["T"] - W_M * sig["M"]
    p = sigmoid(A)

    ok, reason = can_fire()
    quiet = in_quiet_hours()
    fire = (p > THETA) and ok

    if not fire:
        return {
            "skipped": True, "reason": reason or "below_threshold",
            "p": round(p, 3), "signals": sig, "z": round(z, 3),
            "quiet": quiet, "gpu_temp": gpu_temp,
        }

    content = generate_utterance()
    if not content:
        return {"skipped": True, "reason": "generation_failed", "p": round(p, 3)}

    mark_fire()

    # Silex self-selects sharing via [share]; quiet hours force private.
    wants_share = "[share]" in content.lower()
    shareable = wants_share and not quiet
    # strip the tag from stored content
    clean = content.replace("[share]", "").replace("[SHARE]", "").strip()

    written = write_to_journal(clean, shareable, gpu_temp)

    logger.info(
        "Chaos FIRED p=%.3f x=%.2f T=%.2f M=%.2f z=%.2f share=%s quiet=%s gpu=%s",
        p, sig["x"], sig["T"], sig["M"], z, shareable, quiet, gpu_temp,
    )

    return {
        "skipped": False, "fired": True, "p": round(p, 3),
        "signals": sig, "z": round(z, 3),
        "shareable": shareable, "wanted_share": wants_share, "quiet": quiet,
        "written": written, "gpu_temp": gpu_temp, "length": len(clean),
    }


@celery_app.task(name="workers.chaos.silex_presence_cycle")
def silex_presence_cycle():
    """
    Silex MoE Presence Cycle — runs the four-expert scorer and acts if
    score exceeds the dynamic threshold.
    """
    import json

    gpu_temp = get_gpu_temp()
    if not thermal_gate():
        return {"skipped": True, "reason": "thermal", "gpu_temp": gpu_temp}

    try:
        if _redis().get("silex:paused"):
            return {"skipped": True, "reason": "paused"}
    except Exception:
        pass

    try:
        from workers.silex_moe import (
            moe_score, get_threshold, in_cooldown, mark_moe_post
        )
        from workers.silex_presence import (
            is_paused, select_action, select_destination,
            generate_content, dispatch_post, _store_post_history
        )
        from workers.silex_scanner import scan_and_rank
        from workers.silex_moe import mark_replied_to
    except Exception as exc:
        logger.error("silex_presence_cycle import failed: %s", exc)
        return {"skipped": True, "reason": f"import_error: {exc}"}

    if is_paused():
        return {"skipped": True, "reason": "paused"}

    if in_cooldown():
        return {"skipped": True, "reason": "cooldown"}

    score_result = moe_score()
    threshold    = get_threshold()
    score        = score_result["score"]

    logger.info(
        "MoE score=%.3f threshold=%.3f breakdown=%s",
        score, threshold, json.dumps(score_result["breakdown"])
    )

    if score <= threshold:
        return {
            "skipped":   True,
            "reason":    "below_threshold",
            "score":     score,
            "threshold": threshold,
            "breakdown": score_result["breakdown"],
        }

    action    = select_action()
    context   = score_result["context"]
    chunks    = score_result["constitutional_chunks"]
    candidate = None

    if action == "read_and_reply":
        ranked = scan_and_rank()
        if ranked:
            candidate   = ranked["candidate"]
            destination = candidate["source"]
            chunks      = ranked["constitutional_chunks"] or chunks
        else:
            action      = "original_post"
            destination, context = select_destination(context)
    else:
        destination, context = select_destination(context)

    content = generate_content(
        destination=destination,
        context=context,
        constitutional_chunks=chunks,
        candidate=candidate,
    )

    if not content:
        return {"skipped": True, "reason": "generation_failed", "score": score}

    posted = dispatch_post(
        destination=destination,
        content=content,
        context=context,
        candidate=candidate,
        score_result=score_result,
    )

    if posted:
        mark_moe_post()
        _store_post_history(destination, content, score_result, candidate)
        if candidate:
            mark_replied_to(candidate["source"], candidate["id"])

        logger.info(
            "MoE POSTED action=%s destination=%s score=%.3f",
            action, destination, score
        )

    return {
        "skipped":     not posted,
        "action":      action,
        "destination": destination,
        "score":       score,
        "threshold":   threshold,
        "breakdown":   score_result["breakdown"],
        "posted":      posted,
        "content_len": len(content) if content else 0,
        "gpu_temp":    gpu_temp,
    }
