#!/usr/bin/env python3
"""Register the Phase 9 capture sweep into the chaos worker's beat schedule."""
path = "/home/temp/myarea-ai/workers/chaos.py"
src = open(path, encoding="utf-8").read()

# Add capture-sweep to the beat_schedule dict (after chaos-cycle entry)
anchor = '''    "chaos-cycle": {
        "task": "workers.chaos.run_chaos_cycle",
        "schedule": float(os.environ.get("CHAOS_INTERVAL_SECONDS", 1800)),  # 30 min default
    }
}'''
replacement = '''    "chaos-cycle": {
        "task": "workers.chaos.run_chaos_cycle",
        "schedule": float(os.environ.get("CHAOS_INTERVAL_SECONDS", 1800)),  # 30 min default
    },
    "capture-sweep": {
        "task": "workers.capture_task.run_capture_sweep",
        "schedule": float(os.environ.get("CAPTURE_SWEEP_SECONDS", 1800)),  # 30 min default
    }
}'''

if "capture-sweep" not in src:
    src = src.replace(anchor, replacement, 1)

# Register the capture task (import + register_capture) after celery_app config block.
# We append a registration call near the end-safe spot: right after timezone line.
tz_anchor = 'celery_app.conf.timezone = "UTC"'
reg_block = '''celery_app.conf.timezone = "UTC"

# ── Phase 9 — register memory capture sweep task ───────────────────────────────
try:
    from workers.capture_task import register_capture
    register_capture(celery_app, int(os.environ.get("CAPTURE_SWEEP_SECONDS", 1800)))
except Exception as _cap_exc:
    logger.error("Could not register capture sweep: %s", _cap_exc)'''

if "register_capture" not in src:
    src = src.replace(tz_anchor, reg_block, 1)

open(path, "w", encoding="utf-8").write(src)
print("chaos.py patched: capture-sweep registered")
