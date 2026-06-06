"""
Chroma store — named-collection RAG backend.

Uses Ollama nomic-embed-text:v1.5 for embeddings (GPU-backed).
Three collections: ncaidshp (constitution), ncaidsshm (profile), ncaidslphd (history).

Priority hierarchy (from the documents themselves):
  ncaidshp  EXTREME_HIGH  — core identity, always wins
  ncaidsshm MEDIUM        — session/user context
  ncaidslphd LOW_HISTORICAL — deep recall, yields to all above
"""
import os
import logging
import hashlib
import httpx
import chromadb

logger = logging.getLogger(__name__)

CHROMA_PATH    = os.environ.get("CHROMA_PATH", "/app/data/chroma")
OLLAMA_BASE    = os.environ.get("OLLAMA_BASE_URL", "http://172.30.0.1:11434")
EMBED_MODEL    = os.environ.get("EMBED_MODEL", "nomic-embed-text:v1.5")
EMBED_TIMEOUT  = float(os.environ.get("EMBED_TIMEOUT", "60"))

COLLECTIONS = ("ncaidshp", "ncaidsshm", "ncaidslphd")


# ── Ollama embedding ───────────────────────────────────────────────────────────

def embed_one(text: str) -> list:
    """Embed a single text via Ollama."""
    with httpx.Client(timeout=EMBED_TIMEOUT) as c:
        r = c.post(
            f"{OLLAMA_BASE}/api/embeddings",
            json={"model": EMBED_MODEL, "prompt": text},
        )
        r.raise_for_status()
        return r.json()["embedding"]


def embed_batch(texts: list) -> list:
    """Embed a list of texts (sequential — Ollama embeddings API is one-at-a-time)."""
    return [embed_one(t) for t in texts]


# ── Client ──────────────────────────────────────────────────────────────────────

_client = None

def get_client():
    global _client
    if _client is None:
        _client = chromadb.PersistentClient(path=CHROMA_PATH)
    return _client


def get_collection(name: str):
    return get_client().get_or_create_collection(name=name)


def _hash_id(text: str, idx: int) -> str:
    return hashlib.sha256(f"{idx}:{text}".encode("utf-8")).hexdigest()[:32]


# ── Ingest ──────────────────────────────────────────────────────────────────────

def ingest_chunks(collection_name: str, chunks: list, batch_size: int = 16) -> int:
    """
    chunks: list of Chunk(text, metadata).
    Embeds via Ollama and upserts into the named Chroma collection.
    Thermally paced: pauses between batches, and waits if GPU runs hot.
    """
    import time
    import subprocess

    pause_s    = float(os.environ.get("INGEST_PAUSE_S", "0.3"))
    temp_limit = int(os.environ.get("INGEST_TEMP_LIMIT", "60"))
    cool_to    = int(os.environ.get("INGEST_COOL_TO", "50"))

    def gpu_temp():
        try:
            r = subprocess.run(
                ["nvidia-smi", "--query-gpu=temperature.gpu", "--format=csv,noheader"],
                capture_output=True, text=True, timeout=5
            )
            return int(r.stdout.strip().split("\n")[0])
        except Exception:
            return None

    def thermal_wait():
        t = gpu_temp()
        if t is None:
            return
        if t > temp_limit:
            logger.warning("GPU %d°C > %d°C — cooling to %d°C before continuing", t, temp_limit, cool_to)
            while True:
                time.sleep(10)
                t = gpu_temp()
                if t is None or t <= cool_to:
                    logger.info("GPU back to %s°C — resuming", t)
                    break

    coll = get_collection(collection_name)
    added = 0

    for i in range(0, len(chunks), batch_size):
        thermal_wait()

        batch = chunks[i:i + batch_size]
        texts = [c.text for c in batch]
        metas = [c.metadata for c in batch]
        ids   = [_hash_id(c.text, i + j) for j, c in enumerate(batch)]

        try:
            embs = embed_batch(texts)
            coll.upsert(ids=ids, documents=texts, metadatas=metas, embeddings=embs)
            added += len(batch)
            t = gpu_temp()
            logger.info("Ingested %d/%d into %s (GPU %s°C)", added, len(chunks), collection_name, t)
        except Exception as exc:
            logger.error("Batch %d failed: %s", i, exc)

        if pause_s > 0:
            time.sleep(pause_s)

    return added


# ── Query ──────────────────────────────────────────────────────────────────────

def query_collection(collection_name: str, query_text: str, top_k: int = 3) -> list:
    """Return list of {text, metadata, distance} for a single collection."""
    try:
        coll = get_collection(collection_name)
        if coll.count() == 0:
            return []
        q_emb = embed_one(query_text)
        res = coll.query(query_embeddings=[q_emb], n_results=top_k)
        out = []
        docs  = (res.get("documents") or [[]])[0]
        metas = (res.get("metadatas") or [[]])[0]
        dists = (res.get("distances") or [[]])[0]
        for i, doc in enumerate(docs):
            out.append({
                "text":     doc,
                "metadata": metas[i] if i < len(metas) else {},
                "distance": dists[i] if i < len(dists) else None,
                "collection": collection_name,
            })
        return out
    except Exception as exc:
        logger.error("Query failed on %s: %s", collection_name, exc)
        return []


def query_all(query_text: str, k_hp: int = 3, k_shm: int = 2, k_lphd: int = 2) -> dict:
    """
    Query all three collections with priority-appropriate depth.
    Returns dict keyed by collection.
    """
    return {
        "ncaidshp":   query_collection("ncaidshp",   query_text, k_hp),
        "ncaidsshm":  query_collection("ncaidsshm",  query_text, k_shm),
        "ncaidslphd": query_collection("ncaidslphd", query_text, k_lphd),
    }


def collection_stats() -> dict:
    stats = {}
    client = get_client()
    for name in COLLECTIONS:
        try:
            stats[name] = client.get_or_create_collection(name=name).count()
        except Exception:
            stats[name] = 0
    return stats
