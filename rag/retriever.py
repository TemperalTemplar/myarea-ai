"""
RAG retriever — Chroma-backed, multi-collection with priority ordering.

Respects k=0 to skip a collection entirely (used for identity scoping:
non-Alva users get k_shm=0, k_lphd=0 so only the constitution is queried).
"""
import logging

logger = logging.getLogger(__name__)


def retrieve_context(query: str, k_hp: int = 3, k_shm: int = 2, k_lphd: int = 2,
                     project_collection: str | None = None, k_proj: int = 3) -> str:
    """
    Build a priority-ordered context block. A collection with k=0 is skipped.
    If project_collection is given, its memory is retrieved as a dedicated
    high-relevance tier (a project's own focused history).
    Returns formatted string for prompt injection, or empty string on failure.
    """
    try:
        from .chroma_store import query_collection
    except Exception as exc:
        logger.error("Chroma store import failed: %s", exc)
        return ""

    parts = []

    # NCAIDSHP — constitution (highest priority)
    if k_hp > 0:
        try:
            hp = query_collection("ncaidshp", query, k_hp)
            if hp:
                parts.append("--- CONSTITUTIONAL LAW (NCAIDSHP — HIGHEST PRIORITY) ---")
                for r in hp:
                    sec = r.get("metadata", {}).get("section", "")
                    tag = f"[{sec}] " if sec else ""
                    parts.append(f"{tag}{r['text']}")
                parts.append("")
        except Exception as exc:
            logger.error("ncaidshp query failed: %s", exc)

    # PROJECT memory — focused history for the active project
    if project_collection and k_proj > 0:
        try:
            proj = query_collection(project_collection, query, k_proj)
            if proj:
                parts.append("--- PROJECT MEMORY (this project's own history — HIGH RELEVANCE) ---")
                for r in proj:
                    parts.append(r["text"])
                parts.append("")
        except Exception as exc:
            logger.error("project query failed: %s", exc)

    # NCAIDSSHM — user profile (medium)
    if k_shm > 0:
        try:
            shm = query_collection("ncaidsshm", query, k_shm)
            if shm:
                parts.append("--- USER & SESSION CONTEXT (NCAIDSSHM — MEDIUM PRIORITY) ---")
                for r in shm:
                    parts.append(r["text"])
                parts.append("")
        except Exception as exc:
            logger.error("ncaidsshm query failed: %s", exc)

    # NCAIDSLPHD — historical recall (low)
    if k_lphd > 0:
        try:
            lphd = query_collection("ncaidslphd", query, k_lphd)
            if lphd:
                parts.append("--- HISTORICAL RECALL (NCAIDSLPHD — LOW PRIORITY, yields to above) ---")
                for r in lphd:
                    parts.append(r["text"])
                parts.append("")
        except Exception as exc:
            logger.error("ncaidslphd query failed: %s", exc)

    return "\n".join(parts).strip()


def retriever_health() -> dict:
    try:
        from .chroma_store import collection_stats
        return collection_stats()
    except Exception as exc:
        logger.error("Retriever health failed: %s", exc)
        return {}
