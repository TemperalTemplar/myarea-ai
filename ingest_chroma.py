#!/usr/bin/env python3
"""
Migration ingest — FAISS → Chroma named collections.

Reads the three NCAIDS documents, chunks each per its format,
embeds via Ollama nomic-embed-text, stores in Chroma collections.

Run inside the myarea-ai container:
  docker exec myarea-ai python3 ingest_chroma.py
"""
import sys
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("ingest")

sys.path.insert(0, "/app")

from rag.chunker import chunk_document
from rag.chroma_store import ingest_chunks, collection_stats, get_client, COLLECTIONS

# Source files (inside container)
SOURCES = {
    "ncaidshp":   "/app/data/ncaidshp/full/NCAIDSHP_CosmologyEDIT.txt",
    "ncaidsshm":  "/app/data/NCAIDSSHMv2.txt",
    "ncaidslphd": "/app/data/NCAIDSLPHDREX.txt",
}


def read_file(path: str) -> str:
    p = Path(path)
    if not p.exists():
        logger.error("Missing: %s", path)
        return ""
    return p.read_text(encoding="utf-8", errors="ignore")


def main():
    reset = "--reset" in sys.argv

    if reset:
        logger.info("Resetting collections...")
        client = get_client()
        for name in COLLECTIONS:
            try:
                client.delete_collection(name)
                logger.info("Deleted %s", name)
            except Exception:
                pass

    for doc_type, path in SOURCES.items():
        logger.info("=== %s ===", doc_type)
        text = read_file(path)
        if not text:
            logger.warning("Skipping %s — no content", doc_type)
            continue

        chunks = chunk_document(text, doc_type)
        logger.info("Chunked %s into %d chunks", doc_type, len(chunks))

        if not chunks:
            continue

        added = ingest_chunks(doc_type, chunks)
        logger.info("Ingested %d chunks into %s", added, doc_type)

    logger.info("=== FINAL STATS ===")
    for name, count in collection_stats().items():
        logger.info("  %s: %d", name, count)


if __name__ == "__main__":
    main()
