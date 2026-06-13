from flask import Flask
from .config import Config
from .extensions import init_extensions
from .api.chat import chat_bp
from .api.journal import journal_bp
from .api.security_journal import security_journal_bp
from .api.comms import comms_bp
from .api.status import status_bp
from .api.internal import internal_bp
from .api.sessions import sessions_bp
from .api.warden import warden_bp
from .api.subscribe import subscribe_bp
from .api.approvals import approvals_bp
from .api.notifications import bp as notifications_bp
from .subscribers.store import init_db as init_subscribers_db
from .auth.sso import sso_bp
from .ui.views import ui_bp


def create_app(config_class=Config):
    app = Flask(__name__, template_folder="ui/templates", static_folder="ui/static")
    app.config.from_object(config_class)

    init_extensions(app)

    app.register_blueprint(sso_bp)
    app.register_blueprint(chat_bp,              url_prefix="/api")
    app.register_blueprint(journal_bp,           url_prefix="/api")
    app.register_blueprint(security_journal_bp,  url_prefix="/api")
    app.register_blueprint(comms_bp,             url_prefix="/api")
    app.register_blueprint(status_bp,            url_prefix="/api")
    app.register_blueprint(sessions_bp,          url_prefix="/api")
    app.register_blueprint(warden_bp,            url_prefix="/api")
    app.register_blueprint(subscribe_bp)
    app.register_blueprint(approvals_bp)
    app.register_blueprint(internal_bp,          url_prefix="/api/internal")
    app.register_blueprint(notifications_bp)
    from .api.access_admin import access_admin_bp
    app.register_blueprint(access_admin_bp)
    app.register_blueprint(ui_bp)

    # Initialize the subscriber database (idempotent)
    try:
        init_subscribers_db()
    except Exception as _db_exc:
        app.logger.error("subscriber DB init failed: %s", _db_exc)

    return app
