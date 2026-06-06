"""
Dispatcher router.

Flow:
  1. Classify intent via dispatcher_2b (fast, lean NCAIDSHP)
  2. Select target model + system prompt variant
  3. Return a DispatchPlan consumed by the chat endpoint
"""
import json
import logging
import re
from dataclasses import dataclass
from typing import Literal

from flask import current_app
from ..llm.client import complete_chat
from ..llm.models import DISPATCHER, SILEX
from .personality import get_lean_system_prompt, get_full_system_prompt

logger = logging.getLogger(__name__)

Intent = Literal["casual", "lore", "task", "platform", "security", "chaos"]

VALID_INTENTS: set[Intent] = {"casual", "lore", "task", "platform", "security", "chaos"}
DEFAULT_INTENT: Intent = "casual"

TEMPERATURE_MAP: dict[str, float] = {
    "casual":   0.75,
    "lore":     0.85,
    "task":     0.3,
    "platform": 0.3,
    "security": 0.2,
    "chaos":    0.95,
}


@dataclass
class DispatchPlan:
    intent:      Intent
    model:       str
    system:      str
    temperature: float
    tier:        str
    gated:       bool = False


def classify_intent(user_message: str) -> Intent:
    lean_system = get_lean_system_prompt()

    classification_prompt = (
        f"{lean_system}\n\n"
        "Classify the following user message into exactly one intent word from this list: "
        "casual, lore, task, platform, security, chaos.\n"
        "Respond with ONLY the single intent word. No explanation.\n\n"
        f"Message: {user_message}"
    )

    try:
        raw = complete_chat(
            model=DISPATCHER.name,
            messages=[{"role": "user", "content": classification_prompt}],
            temperature=0.0,
        ).strip().lower()

        match = re.search(r"\b(" + "|".join(VALID_INTENTS) + r")\b", raw)
        if match:
            return match.group(1)

        logger.warning("Dispatcher returned unrecognised intent %r — using default", raw)
        return DEFAULT_INTENT

    except Exception as exc:
        logger.error("Intent classification failed: %s", exc)
        return DEFAULT_INTENT


def build_plan(
    user_message: str,
    tier: str = "ssh",
    context_hint: str | None = None,
    user_name: str | None = None,
    project_collection: str | None = None,
) -> DispatchPlan:
    """
    Classify intent and build a full DispatchPlan.

    tier:
      ssh   — standard public access
      sshi  — elevated (trusted user / internal service)
      csshi — core sovereign (Alva / platform owner)
    user_name: authenticated SSO username, threaded into Silex's prompt.
    """
    intent: Intent = classify_intent(user_message)

    # ── Tier gating ────────────────────────────────────────────────────────
    gated = False
    if intent == "security" and tier == "ssh":
        intent = "casual"
        gated = True
    if intent == "chaos" and tier == "ssh":
        intent = "casual"
        gated = True
    if intent == "platform" and tier == "ssh":
        intent = "task"
        gated = True

    model = SILEX.name

    # ── System prompt: three-tier RAG + username awareness ─────────────────
    system = get_full_system_prompt(
        context_hint=context_hint or user_message,
        user_name=user_name,
        project_collection=project_collection,
    )

    system = (
        f"[INTENT: {intent.upper()}] [TIER: {tier.upper()}]\n\n"
        + system
    )

    return DispatchPlan(
        intent=intent,
        model=model,
        system=system,
        temperature=TEMPERATURE_MAP.get(intent, 0.7),
        tier=tier,
        gated=gated,
    )
