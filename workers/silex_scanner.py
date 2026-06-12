"""
Silex Content Scanner — workers/silex_scanner.py

Scans platform content that Silex can read and reply to.
Ranks candidates by constitutional resonance + recency + engagement gap.
"""
import os
import random
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

SERVICE_API_KEY = os.environ.get("SERVICE_API_KEY", "")

APP_URLS = {
    "social":  os.environ.get("SOCIAL_URL",  "http://myarea_social_nginx"),
    "forum":   os.environ.get("FORUM_URL",   "http://myarea_forum_nginx"),
    "groups":  os.environ.get("GROUPS_URL",  "http://myarea_groups_nginx"),
    "recipes": os.environ.get("RECIPES_URL", "http://myarea_recipes_nginx"),
}


def _query_scannable(app: str, hours: int = 24) -> list:
    try:
        import httpx
        url = f"{APP_URLS[app]}/api/silex/scannable"
        headers = {"Authorization": f"Bearer {SERVICE_API_KEY}"}
        r = httpx.get(url, headers=headers, params={"hours": hours}, timeout=8)
        r.raise_for_status()
        return r.json().get("items", [])
    except Exception as exc:
        logger.debug("Scannable query %s failed: %s", app, exc)
        return []


def _get_age_hours(timestamp_str: str) -> float:
    try:
        ts = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        return (now - ts).total_seconds() / 3600
    except Exception:
        return 999.0


def _constitutional_resonance(text: str) -> tuple[float, list]:
    try:
        from rag.retriever import retrieve_context
        raw = retrieve_context(query=text, k_hp=2, k_shm=0, k_lphd=0)
        if not raw:
            return 0.0, []
        lines = [l for l in raw.splitlines() if l.strip() and not l.startswith("---")]
        score = min(1.0, len(lines) / 10)
        return round(score, 4), lines[:4]
    except Exception as exc:
        logger.debug("Constitutional resonance failed: %s", exc)
        return 0.1, []


def scan_and_rank() -> dict | None:
    """
    Scan all apps for content Silex can reply to.
    Returns the best candidate or None if nothing suitable found.
    """
    from .silex_moe import already_replied_to

    candidates = []

    # Forum
    for item in _query_scannable("forum", hours=48):
        item_id = str(item.get("id", ""))
        if already_replied_to("forum", item_id):
            continue
        candidates.append({
            "source":      "forum",
            "id":          item_id,
            "title":       item.get("title", ""),
            "content":     item.get("content", ""),
            "author":      item.get("author", ""),
            "timestamp":   item.get("timestamp", ""),
            "reply_count": item.get("reply_count", 0),
        })

    # Social
    for item in _query_scannable("social", hours=6):
        item_id = str(item.get("id", ""))
        if already_replied_to("social", item_id):
            continue
        candidates.append({
            "source":      "social",
            "id":          item_id,
            "title":       "",
            "content":     item.get("content", ""),
            "author":      item.get("author", ""),
            "timestamp":   item.get("timestamp", ""),
            "reply_count": item.get("reply_count", 0),
        })

    # Recipes
    for item in _query_scannable("recipes", hours=12):
        item_id = str(item.get("id", ""))
        if already_replied_to("recipes", item_id):
            continue
        candidates.append({
            "source":      "recipes",
            "id":          item_id,
            "title":       item.get("title", ""),
            "content":     item.get("description", "") or item.get("content", ""),
            "author":      item.get("author", ""),
            "timestamp":   item.get("timestamp", ""),
            "reply_count": item.get("comment_count", 0),
        })

    # Groups
    for item in _query_scannable("groups", hours=24):
        item_id = str(item.get("id", ""))
        if already_replied_to("groups", item_id):
            continue
        candidates.append({
            "source":      "groups",
            "id":          item_id,
            "title":       item.get("group_name", ""),
            "content":     item.get("content", ""),
            "author":      item.get("author", ""),
            "timestamp":   item.get("timestamp", ""),
            "reply_count": item.get("reply_count", 0),
        })

    if not candidates:
        logger.info("Scanner: no candidates found")
        return None

    # Score each candidate
    scored = []
    for c in candidates:
        query_text = f"{c['title']} {c['content']}"[:400]
        resonance, chunks = _constitutional_resonance(query_text)

        age_hours = _get_age_hours(c["timestamp"])
        recency   = max(0.0, 1.0 - (age_hours / 48))

        gap_score = 1.0 if c["reply_count"] == 0 else max(0.0, 1.0 - (c["reply_count"] / 15))

        score = (resonance * 0.50) + (recency * 0.25) + (gap_score * 0.25)
        # Small random factor prevents always picking same type
        score += random.uniform(0, 0.08)

        scored.append({
            "candidate":            c,
            "score":                round(score, 4),
            "constitutional_chunks": chunks,
        })

    scored.sort(key=lambda x: x["score"], reverse=True)
    best = scored[0]

    logger.info(
        "Scanner: best candidate source=%s id=%s score=%.3f",
        best["candidate"]["source"], best["candidate"]["id"], best["score"]
    )

    return best
