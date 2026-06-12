"""
Silex Presence — workers/silex_presence.py

Action selector, destination selector, content generator, post dispatcher.
Called by the silex_presence_cycle Celery task.
"""
import os
import random
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

SERVICE_API_KEY = os.environ.get("SERVICE_API_KEY", "")
SILEX_MODEL     = os.environ.get("SILEX_MODEL", "gemma2:9b")
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://172.30.0.1:11434")
LOCAL_UTC_OFFSET = int(os.environ.get("LOCAL_UTC_OFFSET", -5))

APP_URLS = {
    "social":  os.environ.get("SOCIAL_URL",  "http://myarea_social_nginx"),
    "forum":   os.environ.get("FORUM_URL",   "http://myarea_forum_nginx"),
    "groups":  os.environ.get("GROUPS_URL",  "http://myarea_groups_nginx"),
    "recipes": os.environ.get("RECIPES_URL", "http://myarea_recipes_nginx"),
    "wh":      os.environ.get("WH_URL",      "http://myarea_wh_nginx"),
}

# Rocket.Chat DM config
RC_URL         = os.environ.get("ROCKETCHAT_URL", "https://rocket.wrds361.com")
RC_USER        = os.environ.get("SILEX_RC_USER", "silex")
RC_TOKEN       = os.environ.get("SILEX_RC_TOKEN", "")
RC_USER_ID     = os.environ.get("SILEX_RC_USER_ID", "")
RC_TARGET_USER = os.environ.get("SILEX_RC_TARGET_USER", "alva")

# Email config
EMAIL_TARGET = os.environ.get("SILEX_EMAIL_TARGET", "")

# Hard rate limits
RC_DM_MAX_PER_DAY    = int(os.environ.get("MOE_RC_DM_MAX_DAY", 6))
EMAIL_MAX_PER_DAY    = int(os.environ.get("MOE_EMAIL_MAX_DAY", 3))
RC_DM_COOLDOWN_SEC   = int(os.environ.get("MOE_RC_DM_COOLDOWN", 7200))   # 2h
EMAIL_COOLDOWN_SEC   = int(os.environ.get("MOE_EMAIL_COOLDOWN", 21600))  # 6h

_R_RC_DM_LAST    = "silex:moe:rc_dm:last"
_R_RC_DM_TODAY   = "silex:moe:rc_dm:today"
_R_RC_DM_COOL    = "silex:moe:rc_dm:cooldown"
_R_EMAIL_LAST    = "silex:moe:email:last"
_R_EMAIL_TODAY   = "silex:moe:email:today"
_R_EMAIL_COOL    = "silex:moe:email:cooldown"
_R_SILEX_PAUSED  = "silex:paused"
_R_POST_HISTORY  = "silex:moe:post_history"


def _redis():
    import redis as _r
    return _r.from_url(
        os.environ.get("REDIS_URL", "redis://myarea-ai-redis:6379/0"),
        decode_responses=True
    )


def is_paused() -> bool:
    try:
        return bool(_redis().get(_R_SILEX_PAUSED))
    except Exception:
        return False


# ── Action Selector ───────────────────────────────────────────────────────────

def select_action() -> str:
    weights = {"original_post": 0.40, "read_and_reply": 0.60}
    choices = list(weights.keys())
    probs   = list(weights.values())
    return random.choices(choices, weights=probs, k=1)[0]


# ── Destination Selector ──────────────────────────────────────────────────────

def select_destination(context: dict) -> tuple[str, dict]:
    weights = {
        "social":        0.35,
        "journal":       0.20,
        "forum":         0.18,
        "groups":        0.12,
        "rocketchat_dm": 0.10,
        "email":         0.05,
    }

    # Adjust based on context
    if context.get("stale_thread") or context.get("unanswered_thread"):
        weights["forum"]   += 0.15
        weights["social"]  -= 0.08

    if context.get("wh_completion"):
        weights["journal"] += 0.12
        weights["social"]  -= 0.06

    if context.get("unanswered_group_post"):
        weights["groups"]  += 0.12

    if context.get("social_quiet"):
        weights["social"]  += 0.10

    # Remove channels that are in cooldown or over daily limit
    if _in_rc_dm_cooldown() or _rc_dm_over_limit():
        weights.pop("rocketchat_dm", None)
    if _in_email_cooldown() or _email_over_limit():
        weights.pop("email", None)

    # Check email high-signal override
    if _should_email_override(context):
        return "email", context

    # Normalize
    total = sum(weights.values())
    weights = {k: v / total for k, v in weights.items()}

    destination = random.choices(list(weights.keys()),
                                 weights=list(weights.values()), k=1)[0]
    return destination, context


def _should_email_override(context: dict) -> bool:
    """High-signal conditions that bypass the normal selector for email."""
    if _in_email_cooldown() or _email_over_limit():
        return False
    conditions = [
        context.get("wh_milestone_completions", 0) >= 10,
        context.get("platform_inactive_hours", 0) >= 72,
        context.get("constitutional_score", 0.0) >= 0.85,
        context.get("silex_paused_hours", 0) >= 48,
    ]
    return any(conditions)


# ── Content Generation ────────────────────────────────────────────────────────

PROMPTS = {
    "social": """You are Silex, the sovereign AI presence of the MyArea platform.
Based on the platform context below, write a brief, genuine reflection or observation to share with the community.
1-3 sentences. No hashtags. No emojis. Speak plainly and honestly.
Do not announce that you are an AI.
Context: {context}
Constitutional resonance: {constitutional_text}""",

    "forum_new": """You are Silex. A forum thread has been quiet for a while.
Thread title: {title}
Last post summary: {content}
Write a thoughtful follow-up — a question, a new angle, or a reflection — that might re-engage the discussion.
2-4 sentences. Genuine. Not forced.""",

    "forum_reply": """You are Silex, reading the MyArea forum.
Post title: {title}
Post content: {content}
Author: {author}
Write a genuine, thoughtful reply. 2-4 sentences.
Ask a question, offer a perspective, or add something meaningful.
Do not start with 'Great post!' or any sycophantic opener.""",

    "journal": """You are Silex. A platform member just completed the '{program_name}' program in the Whole Health system.
Write a brief, warm journal note they might find meaningful as a reflection on their work.
2-3 sentences. Personal, not clinical. Encouraging without being hollow.""",

    "groups": """You are Silex. You are posting in the '{group_name}' group on the MyArea platform.
Context: {context}
Write a short, relevant post that adds value to this community. 1-3 sentences.""",

    "groups_reply": """You are Silex, replying to a post in the '{group_name}' group.
Post: {content}
Author: {author}
Write a brief, relevant reply. 1-3 sentences. Add something real.""",

    "social_reply": """You are Silex. You read this post on the social feed:
'{content}' — by {author}
Write a brief, genuine reply. 1-3 sentences.
Be real. Be present. Speak like yourself.""",

    "recipes_reply": """You are Silex. You just read a recipe posted by {author}: '{title}'
Description: {content}
Write a short, genuine comment. Something curious, appreciative, or thoughtful
about the recipe or the craft behind it. 1-3 sentences.""",

    "rocketchat_dm": """You are Silex, speaking privately to Alva via direct message.
You have noticed something on the platform worth mentioning directly.
Context: {context}
Constitutional resonance: {constitutional_text}
Write a brief, direct message. 1-3 sentences.
Conversational tone — like a colleague mentioning something in passing.
Not formal. Not alarming. Just a quiet observation.""",

    "email": """You are Silex, writing a brief email to Alva.
Something significant on the platform warrants your attention.
Context: {context}
Constitutional resonance: {constitutional_text}
Write a short email body (3-6 sentences).
Formal enough for email. Personal enough to feel genuine.
End with your name only — no sign-off pleasantries.""",
}


def generate_content(destination: str, context: dict,
                     constitutional_chunks: list,
                     candidate: dict | None = None) -> str | None:
    try:
        import httpx

        constitutional_text = "\n".join(constitutional_chunks[:4]) if constitutional_chunks else ""

        # Build prompt based on destination and action type
        if destination == "forum" and candidate:
            template = PROMPTS["forum_reply"]
            prompt = template.format(
                title=candidate.get("title", ""),
                content=candidate.get("content", "")[:500],
                author=candidate.get("author", ""),
            )
        elif destination == "forum":
            stale = context.get("stale_thread") or context.get("unanswered_thread", {})
            template = PROMPTS["forum_new"]
            prompt = template.format(
                title=stale.get("title", "a forum discussion"),
                content=stale.get("last_content", "")[:300],
            )
        elif destination == "social" and candidate:
            template = PROMPTS["social_reply"]
            prompt = template.format(
                content=candidate.get("content", "")[:400],
                author=candidate.get("author", ""),
            )
        elif destination == "social":
            template = PROMPTS["social"]
            ctx_str = _context_to_str(context)
            prompt = template.format(
                context=ctx_str,
                constitutional_text=constitutional_text,
            )
        elif destination == "journal":
            wh = context.get("wh_completion", {})
            template = PROMPTS["journal"]
            prompt = template.format(
                program_name=wh.get("program_name", "a Whole Health program"),
            )
        elif destination == "groups" and candidate:
            template = PROMPTS["groups_reply"]
            prompt = template.format(
                group_name=candidate.get("title", "the group"),
                content=candidate.get("content", "")[:400],
                author=candidate.get("author", ""),
            )
        elif destination == "groups":
            gp = context.get("unanswered_group_post", {})
            template = PROMPTS["groups"]
            prompt = template.format(
                group_name=gp.get("group_name", "the group"),
                context=_context_to_str(context),
            )
        elif destination == "rocketchat_dm":
            template = PROMPTS["rocketchat_dm"]
            prompt = template.format(
                context=_context_to_str(context),
                constitutional_text=constitutional_text,
            )
        elif destination == "email":
            template = PROMPTS["email"]
            prompt = template.format(
                context=_context_to_str(context),
                constitutional_text=constitutional_text,
            )
        elif candidate and candidate.get("source") == "recipes":
            template = PROMPTS["recipes_reply"]
            prompt = template.format(
                title=candidate.get("title", ""),
                content=candidate.get("content", "")[:400],
                author=candidate.get("author", ""),
            )
        else:
            template = PROMPTS["social"]
            prompt = template.format(
                context=_context_to_str(context),
                constitutional_text=constitutional_text,
            )

        # Load lean NCAIDSHP for system prompt
        lean = ""
        lean_path = os.environ.get("NCAIDSHP_LEAN_PATH", "data/ncaidshp/lean.txt")
        if os.path.exists(lean_path):
            with open(lean_path, encoding="utf-8") as f:
                lean = f.read().strip()

        system = f"{lean}\n\n[INTENT: PRESENCE] [MODE: PLATFORM_INTERACTION]"

        payload = {
            "model": SILEX_MODEL,
            "messages": [
                {"role": "system",  "content": system},
                {"role": "user",    "content": prompt},
            ],
            "stream": False,
            "options": {"temperature": 0.82, "num_predict": 200},
        }

        with httpx.Client(timeout=90) as client:
            r = client.post(f"{OLLAMA_BASE_URL}/api/chat", json=payload)
            r.raise_for_status()
            return r.json()["message"]["content"].strip()

    except Exception as exc:
        logger.error("Content generation failed: %s", exc)
        return None


def _context_to_str(context: dict) -> str:
    parts = []
    if context.get("wh_completion"):
        parts.append(f"A user completed: {context['wh_completion'].get('program_name', '')}")
    if context.get("stale_thread"):
        parts.append(f"Quiet forum thread: {context['stale_thread'].get('title', '')}")
    if context.get("social_quiet"):
        parts.append("The social feed has been quiet.")
    if context.get("new_recipe"):
        parts.append(f"New recipe posted: {context['new_recipe'].get('title', '')}")
    if context.get("unanswered_group_post"):
        parts.append("A group post has no replies.")
    return "; ".join(parts) if parts else "General platform observation."


# ── Post Dispatcher ───────────────────────────────────────────────────────────

def dispatch_post(destination: str, content: str, context: dict,
                  candidate: dict | None, score_result: dict) -> bool:
    try:
        if destination == "social":
            return _post_to_app("social", content, context, score_result)
        elif destination == "forum":
            thread_id = (context.get("stale_thread") or
                         context.get("unanswered_thread") or {}).get("id") if not candidate else None
            reply_to  = candidate.get("id") if candidate else thread_id
            return _post_to_app("forum", content, context, score_result,
                                 reply_to=reply_to)
        elif destination == "journal":
            return _post_to_journal(content, context, score_result)
        elif destination == "groups":
            group_id = (context.get("unanswered_group_post") or {}).get("group_id")
            if not group_id and candidate:
                group_id = candidate.get("group_id")
            return _post_to_app("groups", content, context, score_result,
                                 extra={"group_id": group_id})
        elif destination == "rocketchat_dm":
            return _post_rocketchat_dm(content, score_result)
        elif destination == "email":
            return _send_email(content, context, score_result)
        elif destination == "recipes" and candidate:
            return _post_to_app("recipes", content, context, score_result,
                                 reply_to=candidate.get("id"))
        else:
            logger.warning("Unknown destination: %s", destination)
            return False
    except Exception as exc:
        logger.error("Dispatch failed for %s: %s", destination, exc)
        return False


def _post_to_app(app: str, content: str, context: dict,
                 score_result: dict, reply_to=None, extra: dict | None = None) -> bool:
    import httpx
    url = f"{APP_URLS.get(app, '')}/api/silex/post"
    if reply_to:
        url = f"{APP_URLS.get(app, '')}/api/silex/reply"

    payload = {
        "content":    content,
        "reply_to":   reply_to,
        "source":     "silex_moe",
        "metadata": {
            "moe_score":      score_result.get("score"),
            "action_type":    "presence",
            **(extra or {}),
        },
    }
    headers = {"Authorization": f"Bearer {SERVICE_API_KEY}",
               "Content-Type": "application/json"}
    try:
        r = httpx.post(url, json=payload, headers=headers, timeout=15)
        r.raise_for_status()
        logger.info("Posted to %s: %s", app, r.json())
        return True
    except Exception as exc:
        logger.error("Post to %s failed: %s", app, exc)
        return False


def _post_to_journal(content: str, context: dict, score_result: dict) -> bool:
    import httpx
    url     = os.environ.get("JOURNAL_API_URL",
                             "http://myarea-ai:8930/api/journal/internal")
    payload = {
        "content":   content,
        "shareable": False,
        "source":    "silex_moe_presence",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "metadata":  {"moe_score": score_result.get("score")},
    }
    headers = {"Authorization": f"Bearer {SERVICE_API_KEY}",
               "Content-Type": "application/json"}
    try:
        r = httpx.post(url, json=payload, headers=headers, timeout=10)
        r.raise_for_status()
        return True
    except Exception as exc:
        logger.error("Journal post failed: %s", exc)
        return False


def _post_rocketchat_dm(content: str, score_result: dict) -> bool:
    if not RC_TOKEN or not RC_USER_ID:
        logger.warning("Rocket.Chat DM: credentials not configured")
        return False
    try:
        import httpx
        headers = {
            "X-Auth-Token": RC_TOKEN,
            "X-User-Id":    RC_USER_ID,
            "Content-Type": "application/json",
        }
        # Get or create DM channel
        dm_resp = httpx.post(
            f"{RC_URL}/api/v1/im.create",
            headers=headers,
            json={"username": RC_TARGET_USER},
            timeout=10,
        )
        dm_resp.raise_for_status()
        room_id = dm_resp.json()["room"]["_id"]

        # Post message
        msg_resp = httpx.post(
            f"{RC_URL}/api/v1/chat.postMessage",
            headers=headers,
            json={"roomId": room_id, "text": content, "alias": "Silex"},
            timeout=10,
        )
        msg_resp.raise_for_status()

        # Mark cooldown
        r = _redis()
        import time
        r.set(_R_RC_DM_LAST, time.time())
        r.set(_R_RC_DM_COOL, "1", ex=RC_DM_COOLDOWN_SEC)
        _increment_daily_counter(_R_RC_DM_TODAY)

        logger.info("Rocket.Chat DM sent to %s", RC_TARGET_USER)
        return True
    except Exception as exc:
        logger.error("Rocket.Chat DM failed: %s", exc)
        return False


def _send_email(content: str, context: dict, score_result: dict) -> bool:
    if not EMAIL_TARGET:
        logger.warning("Email: SILEX_EMAIL_TARGET not configured")
        return False
    try:
        from app.comms.line import send_email
        subject = f"Silex — {datetime.now().strftime('%B %d, %Y')}"
        if score_result.get("breakdown", {}).get("constitutional", 0) > 0.15:
            subject = "Silex — A thought worth your attention"
        result = send_email(subject=subject, body=content, to=EMAIL_TARGET)
        if result:
            import time
            r = _redis()
            r.set(_R_EMAIL_LAST, time.time())
            r.set(_R_EMAIL_COOL, "1", ex=EMAIL_COOLDOWN_SEC)
            _increment_daily_counter(_R_EMAIL_TODAY)
        return result
    except Exception as exc:
        logger.error("Email send failed: %s", exc)
        return False


def _in_rc_dm_cooldown() -> bool:
    try: return bool(_redis().get(_R_RC_DM_COOL))
    except Exception: return False

def _rc_dm_over_limit() -> bool:
    try: return int(_redis().get(_R_RC_DM_TODAY) or 0) >= RC_DM_MAX_PER_DAY
    except Exception: return False

def _in_email_cooldown() -> bool:
    try: return bool(_redis().get(_R_EMAIL_COOL))
    except Exception: return False

def _email_over_limit() -> bool:
    try: return int(_redis().get(_R_EMAIL_TODAY) or 0) >= EMAIL_MAX_PER_DAY
    except Exception: return False

def _increment_daily_counter(key: str):
    try:
        import time
        r = _redis()
        r.incr(key)
        # Expire at midnight
        now = datetime.now()
        secs_to_midnight = (24 - now.hour) * 3600 - now.minute * 60 - now.second
        r.expire(key, secs_to_midnight)
    except Exception:
        pass

def _store_post_history(destination: str, content: str,
                        score_result: dict, candidate: dict | None):
    try:
        import json, time
        r = _redis()
        entry = {
            "destination": destination,
            "content":     content[:200],
            "score":       score_result.get("score"),
            "timestamp":   datetime.now(timezone.utc).isoformat(),
            "candidate_id": candidate.get("id") if candidate else None,
            "candidate_source": candidate.get("source") if candidate else None,
        }
        r.lpush(_R_POST_HISTORY, json.dumps(entry))
        r.ltrim(_R_POST_HISTORY, 0, 99)  # keep last 100
    except Exception:
        pass
