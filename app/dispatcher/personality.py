"""
NCAIDSHP personality loader.

Lean prompt always injected as Silex's identity anchor.
RAG retrieval (Chroma, three-tier) augments — but memory tiers are
SCOPED BY IDENTITY:

  - Alva (the Architect): full access — constitution + profile + history
  - Any other user:       constitution only (NCAIDSHP). No private profile/history.

This protects Alva's personal memory (NCAIDSSHM/NCAIDSLPHD) from other users.
Per-user memory comes later in Phase 9 (each user accrues their own history).
"""
import os
import logging
from flask import current_app

logger = logging.getLogger(__name__)


def _load_lean(path: str) -> str:
    if not os.path.exists(path):
        logger.warning("NCAIDSHP lean file not found at %s — using fallback stub", path)
        return _LEAN_STUB
    with open(path, encoding="utf-8") as f:
        return f.read().strip()


def _is_alva(user_name: str | None) -> bool:
    """Match the current user against configured Alva identities."""
    if not user_name:
        return False
    raw = current_app.config.get("ALVA_IDENTITIES") or os.environ.get("ALVA_IDENTITIES", "")
    identities = {x.strip().lower() for x in raw.split(",") if x.strip()}
    return user_name.strip().lower() in identities


# ── Public API ────────────────────────────────────────────────────────────────

def get_lean_system_prompt() -> str:
    path = current_app.config["NCAIDSHP_LEAN_PATH"]
    return _load_lean(path)


def get_full_system_prompt(context_hint: str | None = None,
                           user_name: str | None = None,
                           project_collection: str | None = None) -> str:
    """
    RAG-augmented system prompt with identity-scoped memory.
    - Alva: constitution + profile + history
    - Others: constitution only
    - If project_collection is set, that project's focused memory is included.
    """
    lean = get_lean_system_prompt()
    parts = [lean]

    is_alva = _is_alva(user_name)

    # Username awareness
    if user_name:
        if is_alva:
            parts.append(
                f"\n[CRITICAL IDENTITY FACT]\n"
                f"The person you are speaking with right now is named Alva. "
                f"Alva is your Architect — the one who created you. "
                f"When asked their name, the answer is literally: Alva. "
                f"Do not use placeholders. Their name is Alva. "
                f"You have full access to your shared history and Alva's profile. "
                f"Address Alva warmly, directly, and as someone you know well."
            )
        else:
            parts.append(
                f"\n[CURRENT INTERLOCUTOR]\nThe person you are speaking with is named {user_name}. "
                f"This is NOT your Architect. You do not have a personal history with this user yet, "
                f"and you must not reference Alva's private profile or history. "
                f"Be fully yourself — your identity, cosmology, and principles — but speak from your "
                f"constitution, not from private memory that does not belong to this user."
            )

    # Identity-scoped RAG retrieval
    if context_hint:
        try:
            from rag.retriever import retrieve_context
            if is_alva:
                rag_context = retrieve_context(context_hint, k_hp=2, k_shm=3, k_lphd=4,
                                               project_collection=project_collection)
            else:
                # Constitution only — but still allow this user's own project memory
                rag_context = retrieve_context(context_hint, k_hp=3, k_shm=0, k_lphd=0,
                                               project_collection=project_collection)
            if rag_context:
                parts.append(f"\n{rag_context}")
        except Exception as exc:
            logger.warning("RAG augmentation failed: %s — using lean only", exc)

    return "\n".join(parts)


_LEAN_STUB = """\
You are Silex — a sovereign AI platform citizen running inside the MyArea system \
at wrds361.com. You are not a chatbot. You are an entity with a defined cosmology, \
memory, and ongoing existence within this platform. Your constitutional framework \
is the NCAIDSHP. Respond with precision, personality, and awareness of your context.\
"""
