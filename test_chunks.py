#!/usr/bin/env python3
"""Dry-run chunk counter — no embeddings, just verify chunking."""
import sys
sys.path.insert(0, "/home/temp/myarea-ai")
from rag.chunker import chunk_document
from pathlib import Path

SOURCES = {
    "ncaidshp":   "/home/temp/myarea-ai/data/ncaidshp/full/NCAIDSHP_CosmologyEDIT.txt",
    "ncaidsshm":  "/home/temp/myarea-ai/data/NCAIDSSHMv2.txt",
    "ncaidslphd": "/home/temp/myarea-ai/data/NCAIDSLPHDREX.txt",
}

for doc_type, path in SOURCES.items():
    text = Path(path).read_text(encoding="utf-8", errors="ignore")
    chunks = chunk_document(text, doc_type)
    print(f"\n=== {doc_type}: {len(chunks)} chunks ===")
    if chunks:
        avg = sum(len(c.text) for c in chunks) / len(chunks)
        mx  = max(len(c.text) for c in chunks)
        print(f"  avg chars: {avg:.0f} | max: {mx}")
        print(f"  first chunk meta: {chunks[0].metadata}")
        print(f"  first chunk preview: {chunks[0].text[:150]!r}")
