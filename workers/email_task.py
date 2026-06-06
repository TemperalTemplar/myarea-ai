"""
/opt/mark1 fold-in #3 — Celery task wrapper for incoming email poll.
Registered into the chaos worker's beat schedule. Polls Silex's IMAP inbox
for mail from whitelisted senders and surfaces it via journal + comms flush.
"""
import logging
logger = logging.getLogger(__name__)


def register_email_poll(celery_app, interval_seconds: int = 300):
    """Register the email poll task + beat schedule on an existing celery app."""
    @celery_app.task(name="workers.email_task.run_email_poll")
    def run_email_poll():
        # Build a Flask app context so config + redis work
        try:
            from app import create_app
            flask_app = create_app()
            with flask_app.app_context():
                from app.email_in.bridge import poll_inbox
                result = poll_inbox()
                logger.info("Email poll: %s", result)
                return result
        except Exception as exc:
            logger.error("Email poll failed: %s", exc)
            return {"error": str(exc)}

    celery_app.conf.beat_schedule["email-poll"] = {
        "task":     "workers.email_task.run_email_poll",
        "schedule": float(interval_seconds),
    }
    return run_email_poll
