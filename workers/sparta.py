"""
Sparta — Phase 5 Security Scanner.

Randomly selects and runs security scans against MyArea platform components.
Writes findings to a separate security journal.
Flags critical findings as shareable for Phase 6 comms alerts.

Scan targets (randomly selected each cycle):
  - Platform services (container health, port responses)
  - Authentik auth health
  - Network port exposure
  - SSL/cert expiry via Cloudflare API
  - Redis/DB health across apps

Triggers:
  - Celery schedule (every SPARTA_INTERVAL_SECONDS)
  - On-demand via POST /api/sparta/scan (CSSHI only)
"""
import os
import random
import socket
import subprocess
import logging
import httpx
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
SPARTA_TEMP_LIMIT      = int(os.environ.get("SPARTA_TEMP_LIMIT", 55))
SERVICE_API_KEY        = os.environ.get("SERVICE_API_KEY", "")
SECURITY_JOURNAL_URL   = os.environ.get("SECURITY_JOURNAL_URL", "http://myarea-ai:8930/api/security-journal/internal")
CLOUDFLARE_API_TOKEN   = os.environ.get("CLOUDFLARE_API_TOKEN", "")
CLOUDFLARE_ZONE_ID     = os.environ.get("CLOUDFLARE_ZONE_ID", "")
AUTHENTIK_URL          = os.environ.get("AUTHENTIK_URL", "http://auth.wrds361.com:9001")

# Platform services to check — name: (host, port)
PLATFORM_SERVICES = {
    "social":    ("myarea_social_web",    5000),
    "forum":     ("myarea_forum",         8917),
    "fitness":   ("myarea_fitness",       8917),
    "groups":    ("myarea_groups",        8917),
    "journal":   ("myarea_journal_web",   5000),
    "apps":      ("myarea_apps_web",      5000),
    "hub":       ("myarea_hub_web",       5000),
    "redis-ai":  ("myarea-ai-redis",      6379),
}

# Ports that should NOT be exposed externally
SENSITIVE_PORTS = [5432, 6379, 11434, 9020, 8930]

# Domains to check SSL for
DOMAINS = [
    "wrds361.com", "ai.wrds361.com", "auth.wrds361.com",
    "mail.wrds361.com", "myarea.wrds361.com", "forum.wrds361.com",
    "fitness.wrds361.com", "apps.wrds361.com",
]


# ── Thermal gate ──────────────────────────────────────────────────────────────

def get_gpu_temp() -> int | None:
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=temperature.gpu", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5
        )
        return int(result.stdout.strip())
    except Exception:
        return None


def thermal_gate() -> bool:
    temp = get_gpu_temp()
    if temp is None:
        return True
    if temp > SPARTA_TEMP_LIMIT:
        logger.info("Sparta thermal gate: %d°C > %d°C — skipping", temp, SPARTA_TEMP_LIMIT)
        return False
    return True


# ── Individual scanners ───────────────────────────────────────────────────────

def scan_platform_services() -> dict:
    """Check if platform services are reachable on their internal ports."""
    findings = []
    severity = "info"

    # Pick a random subset — don't scan all every time
    targets = random.sample(list(PLATFORM_SERVICES.items()), k=min(4, len(PLATFORM_SERVICES)))

    for name, (host, port) in targets:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(3)
            result = sock.connect_ex((host, port))
            sock.close()
            if result == 0:
                findings.append(f"✓ {name} ({host}:{port}) — reachable")
            else:
                findings.append(f"✗ {name} ({host}:{port}) — UNREACHABLE")
                severity = "warning"
        except Exception as exc:
            findings.append(f"✗ {name} ({host}:{port}) — error: {exc}")
            severity = "warning"

    return {
        "scan":     "platform_services",
        "findings": findings,
        "severity": severity,
    }


def scan_authentik() -> dict:
    """Check Authentik health endpoint."""
    findings = []
    severity = "info"

    try:
        r = httpx.get(f"{AUTHENTIK_URL}/-/health/live/", timeout=5)
        if r.status_code == 204:
            findings.append(f"✓ Authentik live health OK")
        else:
            findings.append(f"✗ Authentik live health returned {r.status_code}")
            severity = "warning"
    except Exception as exc:
        findings.append(f"✗ Authentik unreachable: {exc}")
        severity = "critical"

    try:
        r = httpx.get(f"{AUTHENTIK_URL}/-/health/ready/", timeout=5)
        if r.status_code == 204:
            findings.append(f"✓ Authentik ready health OK")
        else:
            findings.append(f"✗ Authentik ready health returned {r.status_code}")
            severity = "warning"
    except Exception as exc:
        findings.append(f"✗ Authentik ready check failed: {exc}")

    return {
        "scan":     "authentik",
        "findings": findings,
        "severity": severity,
    }


def scan_sensitive_ports() -> dict:
    """Check if sensitive ports are exposed on the host's public interface."""
    findings = []
    severity = "info"

    # Get host IP from inside container
    try:
        host_ip = subprocess.run(
            ["ip", "route", "show", "default"],
            capture_output=True, text=True
        ).stdout.split()[2]
    except Exception:
        host_ip = "172.30.0.1"

    for port in random.sample(SENSITIVE_PORTS, k=min(3, len(SENSITIVE_PORTS))):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            result = sock.connect_ex((host_ip, port))
            sock.close()
            if result == 0:
                findings.append(f"⚠ Port {port} is open on {host_ip} — verify this is intentional")
                if port in (5432, 6379):
                    severity = "critical"
                elif severity != "critical":
                    severity = "warning"
            else:
                findings.append(f"✓ Port {port} not externally exposed on {host_ip}")
        except Exception as exc:
            findings.append(f"? Port {port} check error: {exc}")

    return {
        "scan":     "sensitive_ports",
        "findings": findings,
        "severity": severity,
    }


def scan_ssl_certificates() -> dict:
    """Check SSL certificate expiry via Cloudflare API."""
    findings = []
    severity = "info"

    if not CLOUDFLARE_API_TOKEN or not CLOUDFLARE_ZONE_ID:
        return {
            "scan":     "ssl_certificates",
            "findings": ["⚠ Cloudflare API token or Zone ID not configured"],
            "severity": "info",
        }

    try:
        headers = {
            "Authorization": f"Bearer {CLOUDFLARE_API_TOKEN}",
            "Content-Type":  "application/json",
        }
        r = httpx.get(
            f"https://api.cloudflare.com/client/v4/zones/{CLOUDFLARE_ZONE_ID}/ssl/certificate_packs",
            headers=headers, timeout=10
        )
        r.raise_for_status()
        data = r.json()

        if data.get("success"):
            packs = data.get("result", [])
            for pack in packs:
                status = pack.get("status", "unknown")
                cert_type = pack.get("type", "unknown")
                if status == "active":
                    findings.append(f"✓ SSL pack ({cert_type}) — status: {status}")
                else:
                    findings.append(f"✗ SSL pack ({cert_type}) — status: {status}")
                    severity = "critical"
        else:
            findings.append(f"✗ Cloudflare API error: {data.get('errors', 'unknown')}")
            severity = "warning"

    except Exception as exc:
        findings.append(f"✗ SSL check failed: {exc}")
        severity = "warning"

    return {
        "scan":     "ssl_certificates",
        "findings": findings,
        "severity": severity,
    }


def scan_redis_health() -> dict:
    """Check Redis instances across platform."""
    findings = []
    severity = "info"

    redis_instances = {
        "myarea-ai-redis":    ("myarea-ai-redis",  6379),
        "social-redis":       ("myarea_social_redis", 6379),
        "platform-redis":     ("myarea_redis",     6379),
    }

    targets = random.sample(list(redis_instances.items()), k=min(2, len(redis_instances)))

    for name, (host, port) in targets:
        try:
            import redis
            r = redis.Redis(host=host, port=port, socket_timeout=3)
            r.ping()
            info = r.info("server")
            version = info.get("redis_version", "?")
            findings.append(f"✓ {name} — Redis {version} responding")
        except Exception as exc:
            findings.append(f"✗ {name} — {exc}")
            severity = "warning"

    return {
        "scan":     "redis_health",
        "findings": findings,
        "severity": severity,
    }


# ── Scan registry ─────────────────────────────────────────────────────────────

SCAN_REGISTRY = [
    scan_platform_services,
    scan_authentik,
    scan_sensitive_ports,
    scan_ssl_certificates,
    scan_redis_health,
]


# ── Journal write ─────────────────────────────────────────────────────────────

def write_to_security_journal(
    scan_results: list[dict],
    gpu_temp: int | None,
    triggered_by: str = "schedule",
) -> bool:
    try:
        # Build summary
        critical = [r for r in scan_results if r["severity"] == "critical"]
        warnings  = [r for r in scan_results if r["severity"] == "warning"]
        scans_run = [r["scan"] for r in scan_results]

        severity = "info"
        if critical: severity = "critical"
        elif warnings: severity = "warning"

        shareable = severity in ("critical", "warning")

        lines = [f"SPARTA SCAN — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"]
        lines.append(f"Triggered by: {triggered_by} | GPU: {gpu_temp or '?'}°C")
        lines.append(f"Scans: {', '.join(scans_run)}")
        lines.append(f"Overall severity: {severity.upper()}")
        lines.append("")

        for result in scan_results:
            lines.append(f"[{result['scan'].upper()}]")
            for finding in result["findings"]:
                lines.append(f"  {finding}")
            lines.append("")

        content = "\n".join(lines).strip()

        payload = {
            "content":      content,
            "shareable":    shareable,
            "source":       "sparta",
            "severity":     severity,
            "gpu_temp":     gpu_temp,
            "triggered_by": triggered_by,
            "timestamp":    datetime.now(timezone.utc).isoformat(),
        }

        headers = {
            "Authorization": f"Bearer {SERVICE_API_KEY}",
            "Content-Type":  "application/json",
        }

        with httpx.Client(timeout=10) as client:
            r = client.post(SECURITY_JOURNAL_URL, json=payload, headers=headers)
            r.raise_for_status()
            return True

    except Exception as exc:
        logger.error("Failed to write security journal: %s", exc)
        return False


# ── Main scan cycle ───────────────────────────────────────────────────────────

def run_sparta_scan(triggered_by: str = "schedule") -> dict:
    """
    Run a random subset of scans, write results to security journal.
    Can be called directly (on-demand) or via Celery task.
    """
    gpu_temp = get_gpu_temp()

    if triggered_by == "schedule" and not thermal_gate():
        return {"skipped": True, "reason": "thermal", "gpu_temp": gpu_temp}

    # Randomly select 2-3 scanners per cycle
    selected = random.sample(SCAN_REGISTRY, k=random.randint(2, 3))
    results  = []

    for scanner in selected:
        try:
            result = scanner()
            results.append(result)
            logger.info("Sparta scan: %s — %s", result["scan"], result["severity"])
        except Exception as exc:
            logger.error("Scanner %s failed: %s", scanner.__name__, exc)

    written = write_to_security_journal(results, gpu_temp, triggered_by)

    critical_count = sum(1 for r in results if r["severity"] == "critical")
    warning_count  = sum(1 for r in results if r["severity"] == "warning")

    return {
        "skipped":        False,
        "scans_run":      [r["scan"] for r in results],
        "critical":       critical_count,
        "warnings":       warning_count,
        "written":        written,
        "gpu_temp":       gpu_temp,
        "triggered_by":   triggered_by,
    }


# ── Celery task ───────────────────────────────────────────────────────────────

try:
    from .chaos import celery_app

    @celery_app.task(name="workers.sparta.run_sparta_task")
    def run_sparta_task():
        return run_sparta_scan(triggered_by="schedule")

    # Add Sparta to beat schedule
    celery_app.conf.beat_schedule["sparta-scan"] = {
        "task":     "workers.sparta.run_sparta_task",
        "schedule": float(os.environ.get("SPARTA_INTERVAL_SECONDS", 14400)),  # 4 hours default
    }

except ImportError:
    pass
