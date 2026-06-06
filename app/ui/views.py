from flask import Blueprint, render_template
from ..auth.sso import login_required

ui_bp = Blueprint("ui", __name__)


@ui_bp.get("/")
@login_required
def index():
    return render_template("index.html")


@ui_bp.get("/journal")
@login_required
def journal():
    return render_template("journal.html")


@ui_bp.get("/security")
@login_required
def security():
    return render_template("security.html")
