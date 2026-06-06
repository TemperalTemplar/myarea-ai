"""
RAG chunker — structure-aware chunking for NCAIDS documents.

Handles three formats:
  1. NCAIDSHP    — pre-chunked with "# END OF CHUNK — INDEX [...]" boundaries
                   and rich metadata headers. Split on boundaries, parse metadata.
  2. NCAIDSLPHD  — conversation archive with ",<------>  Speaker:---" delimiters
                   (inconsistent). Split on delimiters, fall back to windows.
  3. NCAIDSSHM   — structured user-profile document. Split on section markers,
                   fall back to windows.
"""
import re
from dataclasses import dataclass, field
from typing import List, Dict


@dataclass
class Chunk:
    text: str
    metadata: Dict = field(default_factory=dict)


# ── NCAIDSHP — boundary-delimited with metadata headers ────────────────────────

_CHUNK_BOUNDARY = re.compile(r"# END OF CHUNK\s*—\s*INDEX\s*\[[^\]]*\]")
_HEADER_LINE    = re.compile(r"^#\s*([A-Z_][A-Z0-9_\- ]*?):\s*(.+?)\s*$", re.MULTILINE)


def _parse_header_metadata(block: str) -> Dict:
    """Extract '# KEY: value' metadata lines from the top of a chunk."""
    meta = {}
    for m in _HEADER_LINE.finditer(block):
        key = m.group(1).strip().lower().replace(" ", "_").replace("-", "_")
        val = m.group(2).strip()
        # Only keep known structural keys
        if key in ("doc_id", "index", "part", "section", "sub_section",
                   "scope", "context_level", "rev", "priority_level", "characters"):
            meta[key] = val
    return meta


def chunk_ncaidshp(text: str) -> List[Chunk]:
    """Split NCAIDSHP on its explicit END OF CHUNK boundaries."""
    raw_blocks = _CHUNK_BOUNDARY.split(text)
    chunks: List[Chunk] = []

    for block in raw_blocks:
        block = block.strip()
        # strip the boundary NOTE/separator lines
        block = re.sub(r"#\s*NOTE:.*", "", block)
        block = re.sub(r"^=+$", "", block, flags=re.MULTILINE)
        block = block.strip()
        if len(block) < 20:
            continue

        meta = _parse_header_metadata(block)
        meta["collection"] = "ncaidshp"
        meta.setdefault("priority_level", "EXTREME_HIGH")

        chunks.append(Chunk(text=block, metadata=meta))

    return chunks


# ── NCAIDSLPHD — conversation delimiters with window fallback ──────────────────

_CONV_DELIM = re.compile(r",?<-+>\s*([A-Za-z][A-Za-z0-9 ]*?):-+", re.MULTILINE)


def chunk_ncaidslphd(text: str, max_chars: int = 1500, overlap: int = 150) -> List[Chunk]:
    """
    Split LPHDR conversation archive.
    Group consecutive speaker turns into exchange windows up to max_chars.
    Falls back to plain windows for regions without delimiters.
    """
    # Strip the assimilation header block
    body = text
    start_marker = "---Start of NCAIDSLPHD----"
    if start_marker in body:
        body = body.split(start_marker, 1)[1]

    chunks: List[Chunk] = []

    # Find all speaker turn positions
    matches = list(_CONV_DELIM.finditer(body))

    if not matches:
        # No delimiters at all — pure window fallback
        return _window_chunks(body, "ncaidslphd", "LOW_HISTORICAL", max_chars, overlap)

    # Build turns: each turn = (speaker, text from this delim to next)
    turns = []
    for i, m in enumerate(matches):
        speaker = m.group(1).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        turn_text = body[start:end].strip()
        if turn_text:
            turns.append((speaker, turn_text))

    # Group turns into windows up to max_chars
    buf = []
    buf_len = 0
    for speaker, turn_text in turns:
        piece = f"{speaker}: {turn_text}"

        # If a single turn is itself larger than max_chars, flush the buffer
        # then window-split the oversized turn directly.
        if len(piece) > max_chars:
            if buf:
                chunks.append(Chunk(
                    text="\n\n".join(buf),
                    metadata={"collection": "ncaidslphd", "priority_level": "LOW_HISTORICAL"}
                ))
                buf = []
                buf_len = 0
            chunks.extend(_window_chunks(piece, "ncaidslphd", "LOW_HISTORICAL", max_chars, overlap))
            continue

        if buf_len + len(piece) > max_chars and buf:
            chunks.append(Chunk(
                text="\n\n".join(buf),
                metadata={"collection": "ncaidslphd", "priority_level": "LOW_HISTORICAL"}
            ))
            buf = []
            buf_len = 0
        buf.append(piece)
        buf_len += len(piece)

    if buf:
        chunks.append(Chunk(
            text="\n\n".join(buf),
            metadata={"collection": "ncaidslphd", "priority_level": "LOW_HISTORICAL"}
        ))

    return chunks


# ── NCAIDSSHM — structured profile sections ────────────────────────────────────

_SHM_SECTION = re.compile(r"^---Start of NCAIDSSHM[^\n]*$", re.MULTILINE)


def chunk_ncaidsshm(text: str, max_chars: int = 1500, overlap: int = 150) -> List[Chunk]:
    """
    Split SSHM on its section markers, then window any oversized section.
    """
    body = text
    # Drop the assimilation header
    if "# INSTRUCTION_CHECKSUM" in body:
        body = body.split("# INSTRUCTION_CHECKSUM", 1)[1]
        body = body.split("\n", 1)[1] if "\n" in body else body

    sections = _SHM_SECTION.split(body)
    chunks: List[Chunk] = []

    for sec in sections:
        sec = sec.strip()
        if len(sec) < 20:
            continue
        if len(sec) <= max_chars:
            chunks.append(Chunk(
                text=sec,
                metadata={"collection": "ncaidsshm", "priority_level": "MEDIUM"}
            ))
        else:
            chunks.extend(_window_chunks(sec, "ncaidsshm", "MEDIUM", max_chars, overlap))

    return chunks


# ── Generic window fallback ────────────────────────────────────────────────────

def _window_chunks(text: str, collection: str, priority: str,
                   max_chars: int, overlap: int) -> List[Chunk]:
    text = text.strip()
    if not text:
        return []
    chunks = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + max_chars, n)
        piece = text[start:end].strip()
        if piece:
            chunks.append(Chunk(
                text=piece,
                metadata={"collection": collection, "priority_level": priority}
            ))
        if end == n:
            break
        start = end - overlap
    return chunks


# ── Dispatcher ──────────────────────────────────────────────────────────────────

def chunk_document(text: str, doc_type: str) -> List[Chunk]:
    """
    doc_type: 'ncaidshp' | 'ncaidslphd' | 'ncaidsshm'
    """
    if doc_type == "ncaidshp":
        return chunk_ncaidshp(text)
    elif doc_type == "ncaidslphd":
        return chunk_ncaidslphd(text)
    elif doc_type == "ncaidsshm":
        return chunk_ncaidsshm(text)
    else:
        raise ValueError(f"Unknown doc_type: {doc_type}")
