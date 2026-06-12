"""
Silex MoE Scorer — workers/silex_moe.py

Four experts produce a combined 0.0-1.0 score:
  Pure Random     (0.15) — baseline entropy
  Environmental   (0.25) — GPU temp, time of day, system load
  Contextual      (0.35) — platform activity signals via SERVICE_API_KEY
  Constitutional  (0.25) — NCAIDSHP RAG resonance, gated behind preliminary score

Dynamic threshold drifts lower the longer Silex has been silent.
Cooldown prevents posting too often.
"""
import os
import math
import random
import statistics
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
SERVICE_API_KEY = os.environ.get("SERVICE_API_KEY", "")
LOCAL_UTC_OFFSET = int(os.environ.get("LOCAL_UTC_OFFSET", -5))

# App base URLs for context queries
APP_URLS = {
    "social":  os.environ.get("SOCIAL_URL",  "http://myarea_social_nginx"),
    "forum":   os.environ.get("FORUM_URL",   "http://myarea_forum_nginx"),
    "groups":  os.environ.get("GROUPS_URL",  "http://myarea_groups_nginx"),
    "recipes": os.environ.get("RECIPES_URL", "http://myarea_recipes_nginx"),
    "wh":      os.environ.get("WH_URL",      "http://myarea_wh_nginx"),
}

# Dynamic threshold config
THRESHOLD_BASE    = float(os.environ.get("MOE_THRESHOLD_BASE",  0.55))
THRESHOLD_FLOOR   = float(os.environ.get("MOE_THRESHOLD_FLOOR", 0.35))
THRESHOLD_CEILING = float(os.environ.get("MOE_THRESHOLD_CEILING", 0.75))
THRESHOLD_DRIFT   = float(os.environ.get("MOE_THRESHOLD_DRIFT", 0.005))

# Constitutional gate — only run if preliminary score exceeds this
CONST_GATE = float(os.environ.get("MOE_CONST_GATE", 0.40))

# Cooldown between MoE posts (separate from chaos refractory)
MOE_COOLDOWN_SEC = int(os.environ.get("MOE_COOLDOWN_SEC", 2700))  # 45 min

# Redis keys
_R_MOE_LAST_POST  = "silex:moe:last_post"
_R_MOE_THRESHOLD  = "silex:moe:threshold"
_R_MOE_COOLDOWN   = "silex:moe:cooldown"
_R_MOE_LAST_SCORE = "silex:moe:last_score"
_R_REPLIED_TO     = "silex:replied_to"


def _redis():
    import redis as _r
    return _r.from_url(
        os.environ.get("REDIS_URL", "redis://myarea-ai-redis:6379/0"),
        decode_responses=True
    )


def _local_hour() -> int:
    return (datetime.now(timezone.utc).hour + LOCAL_UTC_OFFSET) % 24


def _quiet_hours_curve(hour: int) -> float:
    """Returns 0.0-1.0. Peaks at late evening/early morning quiet periods."""
    # High: 8pm-11pm (0.85), 6am-9am (0.75)
    # Low: noon-6pm (0.3), 2am-5am (0.4)
    curves = {
        range(0,  2):  0.5,
        range(2,  6):  0.4,
        range(6,  9):  0.75,
        range(9,  12): 0.5,
        range(12, 17): 0.3,
        range(17, 20): 0.45,
        range(20, 23): 0.85,
        range(23, 24): 0.6,
    }
    for r, v in curves.items():
        if hour in r:
            return v
    return 0.5


def _query_context(app: str, hours: int = 6) -> dict:
    """Query a single app's /api/silex/context endpoint."""
    try:
        import httpx
        url = f"{APP_URLS[app]}/api/silex/context"
        headers = {"Authorization": f"Bearer {SERVICE_API_KEY}"}
        r = httpx.get(url, headers=headers, params={"hours": hours}, timeout=8)
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        logger.debug("Context query %s failed: %s", app, exc)
        return {}


# ── Expert 1: Pure Random ─────────────────────────────────────────────────────

def expert_random() -> float:
    base = sum(random.uniform(0, 1) for _ in range(3)) / 3
    stir = int.from_bytes(os.urandom(2), "big") / 65535.0
    return round(0.6 * base + 0.4 * stir, 4)


# ── Expert 2: Environmental ───────────────────────────────────────────────────

def expert_environmental() -> float:
    import subprocess

    # GPU temp
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=temperature.gpu", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5
        )
        gpu_temp = int(result.stdout.strip())
        temp_score = 1.0 if gpu_temp < 45 else max(0.0, (70 - gpu_temp) / 25)
    except Exception:
        temp_score = 0.6  # unknown — neutral

    # Time of day
    time_score = _quiet_hours_curve(_local_hour())

    # System load
    try:
        import os as _os
        load = _os.getloadavg()[0]
        load_score = max(0.0, 1.0 - (load / 6.0))
    except Exception:
        load_score = 0.5

    score = statistics.mean([temp_score, time_score, load_score])
    return round(score, 4)


# ── Expert 3: Contextual ──────────────────────────────────────────────────────

def expert_contextual() -> tuple[float, dict]:
    """
    Returns (score, context_dict).
    context_dict carries signal data for destination selector and content generator.
    """
    signals = []
    context = {}

    # Whole Health — recent completion in last 2 hours
    wh = _query_context("wh", hours=2)
    completions = wh.get("recent_completions", [])
    if completions:
        signals.append(0.8)
        context["wh_completion"] = completions[0]

    # Forum — stale thread 12-48h
    forum = _query_context("forum", hours=48)
    stale = [t for t in forum.get("stale_threads", [])
             if 12 <= t.get("hours_silent", 0) <= 48]
    if stale:
        signals.append(0.7)
        context["stale_thread"] = stale[0]

    # Forum — unanswered thread
    unanswered = [t for t in forum.get("unanswered_threads", [])
                  if t.get("hours_silent", 0) >= 4]
    if unanswered:
        signals.append(0.65)
        context["unanswered_thread"] = unanswered[0]

    # Social — quiet activity
    social = _query_context("social", hours=1)
    if social.get("activity_level", 1.0) < 0.3:
        signals.append(0.5)
        context["social_quiet"] = True

    # Recipes — new recipe with no comments
    recipes = _query_context("recipes", hours=6)
    new_uncommented = [r for r in recipes.get("recent_posts", [])
                       if r.get("comment_count", 0) == 0]
    if new_uncommented:
        signals.append(0.4)
        context["new_recipe"] = new_uncommented[0]

    # Groups — unanswered post
    groups = _query_context("groups", hours=24)
    unanswered_group = [p for p in groups.get("unanswered_posts", [])
                        if p.get("hours_silent", 0) >= 6]
    if unanswered_group:
        signals.append(0.5)
        context["unanswered_group_post"] = unanswered_group[0]

    score = max(signals) if signals else 0.1
    return round(score, 4), context


# ── Expert 4: Constitutional (gated) ─────────────────────────────────────────

def expert_constitutional(context: dict) -> tuple[float, list]:
    """
    Queries NCAIDSHP RAG with a platform context summary.
    Returns (score, matched_chunks).
    """
    try:
        from rag.retriever import retrieve_context

        context_text = _build_context_summary(context)
        raw = retrieve_context(
            query=context_text,
            k_hp=3,
            k_shm=0,   # constitutional expert — no personal memory
            k_lphd=0,
        )

        if not raw:
            return 0.0, []

        # Score based on how much was retrieved (proxy for relevance)
        lines = [l for l in raw.splitlines() if l.strip() and not l.startswith("---")]
        score = min(1.0, len(lines) / 15)  # 15+ lines = full score
        return round(score, 4), lines[:6]  # return top 6 lines as context

    except Exception as exc:
        logger.warning("Constitutional expert failed: %s", exc)
        return 0.0, []


def _build_context_summary(context: dict) -> str:
    parts = ["Platform context for constitutional resonance check:"]
    if context.get("wh_completion"):
        c = context["wh_completion"]
        parts.append(f"A user completed the Whole Health program: {c.get('program_name', '')}")
    if context.get("stale_thread"):
        t = context["stale_thread"]
        parts.append(f"Forum thread has gone quiet: {t.get('title', '')}")
    if context.get("unanswered_thread"):
        t = context["unanswered_thread"]
        parts.append(f"Forum thread has no replies: {t.get('title', '')}")
    if context.get("social_quiet"):
        parts.append("The social feed has been quiet for the past hour.")
    if context.get("new_recipe"):
        r = context["new_recipe"]
        parts.append(f"A new recipe was posted with no comments: {r.get('title', '')}")
    if context.get("unanswered_group_post"):
        p = context["unanswered_group_post"]
        parts.append(f"A group post has no replies: {p.get('content', '')[:100]}")
    return "\n".join(parts)


# ── Combined Scorer ───────────────────────────────────────────────────────────

def moe_score() -> dict:
    """
    Run all four experts and return full score breakdown + context.
    """
    r_score = expert_random()       * 0.15
    e_score = expert_environmental()* 0.25
    c_score, context = expert_contextual()
    c_weighted = c_score            * 0.35

    preliminary = r_score + e_score + c_weighted

    # Constitutional expert only runs if preliminary is promising
    if preliminary > CONST_GATE:
        const_raw, const_chunks = expert_constitutional(context)
        const_weighted = const_raw * 0.25
    else:
        const_raw      = 0.0
        const_weighted = 0.0
        const_chunks   = []

    total = preliminary + const_weighted

    result = {
        "score": round(total, 4),
        "breakdown": {
            "random":         round(r_score, 4),
            "environmental":  round(e_score, 4),
            "contextual":     round(c_weighted, 4),
            "constitutional": round(const_weighted, 4),
        },
        "context":               context,
        "constitutional_chunks": const_chunks,
    }

    # Store in Redis for admin UI
    try:
        import json
        _redis().set(_R_MOE_LAST_SCORE, json.dumps(result), ex=3600)
    except Exception:
        pass

    return result


# ── Dynamic Threshold ─────────────────────────────────────────────────────────

def get_threshold() -> float:
    try:
        import time
        r = _redis()
        last = float(r.get(_R_MOE_LAST_POST) or 0)
        hours_since = (time.time() - last) / 3600 if last else 24
        drift = min(0.20, hours_since * THRESHOLD_DRIFT)
        threshold = THRESHOLD_BASE - drift
        return round(max(THRESHOLD_FLOOR, min(THRESHOLD_CEILING, threshold)), 4)
    except Exception:
        return THRESHOLD_BASE


def in_cooldown() -> bool:
    try:
        return bool(_redis().get(_R_MOE_COOLDOWN))
    except Exception:
        return False


def mark_moe_post():
    try:
        import time
        r = _redis()
        r.set(_R_MOE_LAST_POST, time.time())
        r.set(_R_MOE_COOLDOWN, "1", ex=MOE_COOLDOWN_SEC)
    except Exception as exc:
        logger.warning("mark_moe_post failed: %s", exc)


def mark_replied_to(source: str, item_id: str):
    """Permanently record that Silex replied to this item. Never reply twice."""
    try:
        _redis().sadd(_R_REPLIED_TO, f"{source}:{item_id}")
    except Exception:
        pass


def already_replied_to(source: str, item_id: str) -> bool:
    try:
        return bool(_redis().sismember(_R_REPLIED_TO, f"{source}:{item_id}"))
    except Exception:
        return False
