import os


class Config:
    # ── Flask ──────────────────────────────────────────────────────────────
    SECRET_KEY          = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-me")
    PORT                = int(os.environ.get("PORT", 8930))

    # ── Platform ───────────────────────────────────────────────────────────
    SERVICE_API_KEY     = os.environ.get("SERVICE_API_KEY", "")

    # ── LLM / Ollama ───────────────────────────────────────────────────────
    OLLAMA_BASE_URL     = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    DISPATCHER_MODEL    = os.environ.get("DISPATCHER_MODEL", "gemma2:2b")
    SILEX_MODEL         = os.environ.get("SILEX_MODEL",      "gemma2:9b")
    LLM_TIMEOUT         = int(os.environ.get("LLM_TIMEOUT",  120))

    # ── Redis ──────────────────────────────────────────────────────────────
    REDIS_URL           = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

    # ── Short-term memory (NCAIDSSHM) ──────────────────────────────────────
    NCAIDSSHM_TTL       = int(os.environ.get("NCAIDSSHM_TTL_SECONDS", 3600))
    NCAIDSSHM_MAX_TURNS = int(os.environ.get("NCAIDSSHM_MAX_TURNS",   20))

    # ── NCAIDSHP personality ───────────────────────────────────────────────
    # Path to the lean dispatcher slice and full cosmology dir
    NCAIDSHP_LEAN_PATH  = os.environ.get("NCAIDSHP_LEAN_PATH",  "data/ncaidshp/lean.txt")
    NCAIDSHP_FULL_DIR   = os.environ.get("NCAIDSHP_FULL_DIR",   "data/ncaidshp/full/")

    # ── Auth ───────────────────────────────────────────────────────────────
    AUTHENTIK_URL           = os.environ.get("AUTHENTIK_URL",           "https://auth.wrds361.com")
    AI_BASE_URL             = os.environ.get("AI_BASE_URL",             "https://ai.wrds361.com")
    ALVA_IDENTITIES = os.environ.get("ALVA_IDENTITIES", "")
    AUTHENTIK_CLIENT_ID     = os.environ.get("AUTHENTIK_CLIENT_ID",     "")
    AUTHENTIK_CLIENT_SECRET = os.environ.get("AUTHENTIK_CLIENT_SECRET", "")

    # ── Permission tiers (Phase 3 — stubs) ────────────────────────────────
    # Comma-separated token lists; empty = tier disabled
    CSSHI_TOKENS        = set(filter(None, os.environ.get("CSSHI_TOKENS", "").split(",")))
    SSHI_TOKENS         = set(filter(None, os.environ.get("SSHI_TOKENS",  "").split(",")))

    # ── Journal (Phase 4) ─────────────────────────────────────────────────────
    CHAOS_TEMP_LIMIT       = int(os.environ.get("CHAOS_TEMP_LIMIT",       50))
    CHAOS_INTERVAL_SECONDS = int(os.environ.get("CHAOS_INTERVAL_SECONDS", 1800))
    CHAOS_SHARE_CHANCE     = float(os.environ.get("CHAOS_SHARE_CHANCE",   0.3))
    JOURNAL_API_URL        = os.environ.get("JOURNAL_API_URL", "http://myarea-ai:8930/api/journal/internal")
    SECURITY_JOURNAL_URL   = os.environ.get("SECURITY_JOURNAL_URL", "http://myarea-ai:8930/api/security-journal/internal")
    SPARTA_TEMP_LIMIT      = int(os.environ.get("SPARTA_TEMP_LIMIT",      55))
    SPARTA_INTERVAL_SECONDS= int(os.environ.get("SPARTA_INTERVAL_SECONDS",14400))
    CLOUDFLARE_API_TOKEN   = os.environ.get("CLOUDFLARE_API_TOKEN", "")
    CLOUDFLARE_ZONE_ID     = os.environ.get("CLOUDFLARE_ZONE_ID",   "")

    # ── Celery (Phase 4) ───────────────────────────────────────────────────
    CELERY_BROKER_URL   = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/1")
    CELERY_RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")
