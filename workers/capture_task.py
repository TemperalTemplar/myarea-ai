"""
Phase 9 — Celery task wrapper for memory capture sweep.

Registered into the chaos worker's beat schedule. Runs the idle-session
sweep periodically, capturing completed conversations into long-term memory.
"""
import logging

logger = logging.getLogger(__name__)


def register_capture(celery_app, interval_seconds: int = 1800):
    """Register the capture sweep task + beat schedule on an existing celery app."""

    @celery_app.task(name="workers.capture_task.run_capture_sweep")
    def run_capture_sweep():
        # Build a Flask app context so config + redis + chroma work
        try:
            from app import create_app
            flask_app = create_app()
            with flask_app.app_context():
                from app.memory.capture import sweep_idle_sessions
                result = sweep_idle_sessions()
                logger.info("Capture sweep: %s", result)
                return result
        except Exception as exc:
            logger.error("Capture sweep failed: %s", exc)
            return {"error": str(exc)}

    celery_app.conf.beat_schedule["capture-sweep"] = {
        "task":     "workers.capture_task.run_capture_sweep",
        "schedule": float(interval_seconds),
    }

    return run_capture_sweep
