"""
Warden awareness — Phase 3.5 step 2.

When Silex is asked about her own system state, this maps the question to
safe warden verbs, executes them through the gate, and returns a context
block to inject into her prompt so she answers with real data.

The MODEL never selects verbs — keyword matching here picks from the
allow-listed vocabulary, and execution still goes through the warden gate.
"""
import re
import logging

logger = logging.getLogger(__name__)

# Map keyword patterns → warden verbs. Only allow-listed verbs appear here.
_KEYWORD_VERBS = [
    (re.compile(r"\b(gpu|graphics|card|temp|temperature|hot|cooling|thermal|degrees)\b", re.I),
     ["gpu_status"]),
    (re.compile(r"\b(disk|storage|space|drive|df|filesystem|full)\b", re.I),
     ["disk"]),
    (re.compile(r"\b(memory|ram|mem|free)\b", re.I),
     ["mem"]),
    (re.compile(r"\b(uptime|load|how long|running for|since.*(boot|start))\b", re.I),
     ["uptime"]),
    (re.compile(r"\b(status|health|how are you (running|doing)|system state|vitals|resources)\b", re.I),
     ["gpu_status", "mem", "uptime"]),
]


def detect_verbs(message: str) -> list:
    """Return a de-duplicated list of warden verbs relevant to the message."""
    verbs = []
    for pat, vs in _KEYWORD_VERBS:
        if pat.search(message):
            for v in vs:
                if v not in verbs:
                    verbs.append(v)
    return verbs


def _humanize(verb: str, output: str) -> str:
    """Turn terse warden output into a plain labeled statement a small model reads correctly."""
    out = output.strip()
    try:
        if verb == "gpu_status":
            # format: "util %, mem_used MiB, mem_total MiB, temp"
            parts = [p.strip() for p in out.split(",")]
            util = parts[0] if len(parts) > 0 else "?"
            mem_used = parts[1] if len(parts) > 1 else "?"
            mem_total = parts[2] if len(parts) > 2 else "?"
            temp = parts[3] if len(parts) > 3 else "?"
            return (f"GPU temperature is {temp}°C. GPU utilization is {util}. "
                    f"GPU memory: {mem_used} used of {mem_total}.")
        if verb == "gpu_temp":
            return f"GPU temperature is {out}°C."
        if verb == "disk":
            # Parse df -h to a plain sentence so the model can't transpose used/avail.
            # df output: header line, then the root/data row(s).
            best = None
            for line in out.splitlines():
                cols = line.split()
                if len(cols) >= 6 and cols[-1] in ("/", "/app/data") and "%" in cols[-2]:
                    best = cols
                    break
            if best:
                size, used, avail, pct = best[-5], best[-4], best[-3], best[-2]
                return (f"Disk storage: {size} total, {used} used, {avail} available "
                        f"({pct} used).")
            return f"Disk usage (df -h):\n{out}"
        if verb == "mem":
            return f"Memory: {out}."
        if verb == "uptime":
            return f"System {out}."
    except Exception:
        pass
    return f"{verb}: {out}"


def gather_system_state(message: str, max_verbs: int = 4) -> str:
    """
    Detect relevant verbs, execute via the warden gate, return an injectable
    context block (or empty string if nothing relevant / all failed).
    """
    verbs = detect_verbs(message)
    if not verbs:
        return ""
    verbs = verbs[:max_verbs]

    from .gate import decide_and_execute

    lines = []
    for v in verbs:
        try:
            res = decide_and_execute(v)
            if res.get("ok"):
                lines.append(_humanize(v, res.get("output", "")))
            else:
                continue  # denied/unavailable — skip silently
        except Exception as exc:
            logger.warning("awareness verb %s failed: %s", v, exc)

    if not lines:
        return ""

    return ("[LIVE SYSTEM STATE — MANDATORY]\n"
            "These are your actual real-time hardware readings. The user is asking about "
            "your system. You MUST report these specific numbers directly and plainly in "
            "your reply — lead with the actual figures (temperature, memory, uptime, etc.) "
            "before any commentary. Do NOT substitute vague reassurances like 'optimal' or "
            "'running smoothly' for the real numbers. State the values:\n" + "\n".join(lines))


# ── Security scan awareness ─────────────────────────────────────────────────────

_SECURITY_PAT = re.compile(
    r"\b(security|scan|sparta|breach|intrus|vulnerab|attack|threat|port|firewall|"
    r"safe|secure|defenses?|anything wrong|all clear)\b", re.I)


def gather_security_state(message: str) -> str:
    """
    If the message asks about security, read the most recent Sparta scan from the
    security journal and return an injectable context block. Read-only.
    """
    if not _SECURITY_PAT.search(message):
        return ""
    try:
        from ..extensions import redis_client as r
        ids = r.lrange("silex:security_journal:entries", 0, 9)
        latest = None
        for i in ids:
            h = r.hgetall(f"silex:security_journal:entry:{i}")
            if h.get("source") == "sparta":
                latest = h
                break
        if not latest:
            return ("[SECURITY STATE]\nNo Sparta security scan results are on record yet. "
                    "You can mention that a scan hasn't run recently, but do not invent findings.")
        content = (latest.get("content") or "").strip()
        severity = latest.get("severity", "info")
        return ("[LATEST SECURITY SCAN — MANDATORY]\n"
                "This is your most recent real Sparta security scan result. The user is asking "
                "about security. Report what the scan actually found — the overall severity and "
                "the specific checks — plainly and accurately. Do not invent findings or give "
                "vague reassurance; use only what is below.\n"
                f"Overall severity: {severity}\n{content}")
    except Exception as exc:
        logger.warning("security awareness failed: %s", exc)
        return ""


def gather_awareness(message: str) -> str:
    """Combined: system vitals + security scan awareness. Used by chat.py."""
    blocks = []
    sysb = gather_system_state(message)
    if sysb:
        blocks.append(sysb)
    secb = gather_security_state(message)
    if secb:
        blocks.append(secb)
    return "\n\n".join(blocks)
