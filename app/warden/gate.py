"""
Sparta-Warden — Phase 3.5 policy gate + safe executor.

Folds in /opt/mark1/sparta-warden's decide() pattern, adapted to our stack.

SAFETY MODEL (non-negotiable):
  - The model/caller supplies a VERB from a fixed vocabulary, never a command string.
  - Each verb maps to a HARDCODED command template here. There is no path from
    free text to a shell. No shell=True. No string interpolation into commands.
  - decide() checks the policy allow-list + per-verb constraints and issues a lease.
  - execute_verb() refuses anything without a valid lease for an allowed verb.
  - Everything is read-only. State-changing verbs are intentionally absent.
"""
import os
import time
import logging
import secrets
import subprocess

logger = logging.getLogger(__name__)

POLICY_PATH = os.environ.get("WARDEN_POLICY_PATH", "/app/data/warden/warden_policy.yaml")
MAX_RUNTIME = int(os.environ.get("WARDEN_MAX_RUNTIME_S", "10"))

# Hardcoded command templates. The ONLY commands the executor can ever run.
# Each is a fixed argv list — no shell, no interpolation of caller input
# except where a constraint-validated token is explicitly substituted.
_VERB_COMMANDS = {
    "uptime":     "PROC_UPTIME",   # special: read /proc
    "disk":       ["df", "-h"],
    "mem":        "PROC_MEM",       # special: read /proc
    "gpu_temp":   ["nvidia-smi", "--query-gpu=temperature.gpu", "--format=csv,noheader"],
    "gpu_status": ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu",
                   "--format=csv,noheader"],
    "containers": ["docker", "ps", "--format", "{{.Names}} {{.Status}}"],
    # service_status is special — needs a validated unit arg (handled in execute_verb)
    "service_status": None,
}

ALLOWED_VERBS = tuple(_VERB_COMMANDS.keys())

# In-memory lease store (short-lived). lease_id -> (verb, expires_at, args)
_LEASES = {}
_LEASE_TTL = 30  # seconds — decision must be consumed quickly


def _load_policy() -> dict:
    try:
        import yaml
        if not os.path.exists(POLICY_PATH):
            logger.warning("Warden policy not found at %s", POLICY_PATH)
            return {}
        with open(POLICY_PATH, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as exc:
        logger.error("Failed to load warden policy: %s", exc)
        return {}


def warden_health() -> dict:
    pol = _load_policy()
    subj = pol.get("csshi", {})
    return {
        "ok": True,
        "service": "sparta-warden",
        "policy_loaded": bool(subj),
        "allowed_verbs": sorted(subj.get("allowed_verbs", [])),
    }


def decide(verb: str, args: dict | None = None, subject: str = "csshi") -> dict:
    """
    Policy decision. Returns {decision: allow|deny, reason, lease_id}.
    Does NOT execute anything.
    """
    args = args or {}
    policy = _load_policy()
    p = policy.get(subject)
    if not p:
        return {"decision": "deny", "reason": f"No policy for subject '{subject}'", "lease_id": None}

    allowed = set(p.get("allowed_verbs", []))
    if verb not in allowed:
        return {"decision": "deny",
                "reason": f"Verb '{verb}' not allowed. Allowed: {sorted(allowed)}",
                "lease_id": None}

    # Verb must also have a known command template (defense in depth)
    if verb not in _VERB_COMMANDS:
        return {"decision": "deny", "reason": f"Verb '{verb}' has no executor template", "lease_id": None}

    constraints = (p.get("constraints") or {}).get(verb) or {}

    # service_status: unit must be in the allow-list
    if verb == "service_status":
        unit = str(args.get("unit", ""))
        allowed_units = constraints.get("allowed_units", [])
        if unit not in allowed_units:
            return {"decision": "deny",
                    "reason": f"Unit '{unit}' not allowed. Allowed: {allowed_units}",
                    "lease_id": None}

    lease_id = f"lease_{secrets.token_hex(8)}"
    _LEASES[lease_id] = {"verb": verb, "args": args, "expires": time.time() + _LEASE_TTL}
    return {"decision": "allow", "reason": "Approved by policy.", "lease_id": lease_id}


def execute_verb(lease_id: str) -> dict:
    """
    Execute a previously-approved verb by its lease. Refuses without a valid lease.
    Returns {ok, verb, output|error}.
    """
    lease = _LEASES.pop(lease_id, None)
    if not lease:
        return {"ok": False, "error": "invalid_or_expired_lease"}
    if time.time() > lease["expires"]:
        return {"ok": False, "error": "lease_expired"}

    verb = lease["verb"]
    args = lease.get("args", {})

    # /proc-based verbs — dependency-free, always available
    if verb == "uptime":
        return _read_uptime()
    if verb == "mem":
        return _read_mem()

    cmd = _VERB_COMMANDS.get(verb)

    # Build service_status command with the validated unit (container view: docker)
    if verb == "service_status":
        unit = str(args.get("unit", ""))
        # We re-validate here too — never trust the lease alone
        policy = _load_policy()
        allowed_units = ((policy.get("csshi", {}).get("constraints", {}) or {})
                         .get("service_status", {}) or {}).get("allowed_units", [])
        if unit not in allowed_units:
            return {"ok": False, "error": "unit_not_allowed"}
        cmd = ["docker", "inspect", "-f", "{{.State.Status}}", unit]

    if not cmd:
        return {"ok": False, "error": "no_command_for_verb"}

    try:
        result = subprocess.run(
            cmd,
            capture_output=True, text=True,
            timeout=MAX_RUNTIME,
            shell=False,   # NEVER shell=True
        )
        out = (result.stdout or "").strip()
        err = (result.stderr or "").strip()
        logger.info("warden exec verb=%s rc=%s", verb, result.returncode)
        return {
            "ok": result.returncode == 0,
            "verb": verb,
            "output": out if out else err,
            "returncode": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "verb": verb, "error": "timeout"}
    except FileNotFoundError:
        return {"ok": False, "verb": verb, "error": "command_not_available_in_container"}
    except Exception as exc:
        logger.error("warden exec failed verb=%s: %s", verb, exc)
        return {"ok": False, "verb": verb, "error": str(exc)}


def _read_uptime() -> dict:
    """Read uptime + load from /proc — no binary needed."""
    try:
        with open("/proc/uptime") as f:
            up_secs = float(f.read().split()[0])
        with open("/proc/loadavg") as f:
            load = f.read().split()[:3]
        days = int(up_secs // 86400)
        hrs = int((up_secs % 86400) // 3600)
        mins = int((up_secs % 3600) // 60)
        return {
            "ok": True, "verb": "uptime",
            "output": f"up {days}d {hrs}h {mins}m, load avg: {', '.join(load)}",
        }
    except Exception as exc:
        return {"ok": False, "verb": "uptime", "error": str(exc)}


def _read_mem() -> dict:
    """Read memory stats from /proc/meminfo — no binary needed."""
    try:
        info = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, _, rest = line.partition(":")
                info[k.strip()] = rest.strip()
        total_kb = int(info.get("MemTotal", "0").split()[0])
        avail_kb = int(info.get("MemAvailable", "0").split()[0])
        used_kb = total_kb - avail_kb
        g = lambda kb: f"{kb/1024/1024:.1f}G"
        pct = (used_kb / total_kb * 100) if total_kb else 0
        return {
            "ok": True, "verb": "mem",
            "output": f"total {g(total_kb)}, used {g(used_kb)} ({pct:.0f}%), available {g(avail_kb)}",
        }
    except Exception as exc:
        return {"ok": False, "verb": "mem", "error": str(exc)}


def decide_and_execute(verb: str, args: dict | None = None) -> dict:
    """Convenience: decide then (if allowed) execute. Used by the gated endpoint."""
    d = decide(verb, args)
    if d["decision"] != "allow":
        return {"ok": False, "denied": True, "reason": d["reason"]}
    return execute_verb(d["lease_id"])
