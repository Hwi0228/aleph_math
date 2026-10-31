import os
import secrets
from pathlib import Path

from flask import Flask

from auth import register_auth_routes
from dashboard_routes import register_dashboard_routes
from hwp_routes import register_hwp_routes
from question_routes import register_question_routes


def _secret_key(app):
    if app.config["SECRET_KEY"]:
        return app.config["SECRET_KEY"]
    key_path = Path(app.instance_path) / ".secret_key"
    key_path.parent.mkdir(parents=True, exist_ok=True)
    if not key_path.exists():
        try:
            with key_path.open("x", encoding="ascii") as key_file:
                key_file.write(secrets.token_hex(32))
        except FileExistsError:
            pass
    return key_path.read_text(encoding="ascii").strip()


def create_app():
    app = Flask(__name__, template_folder="static/templates")
    app.config["DATABASE"] = os.path.join(app.instance_path, "probocr.sqlite3")
    app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET_KEY")
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = (
        os.environ.get("FLASK_SESSION_COOKIE_SECURE", "").lower() == "true"
    )
    if not app.config["SECRET_KEY"]:
        app.config["SECRET_KEY"] = _secret_key(app)

    register_auth_routes(app)
    register_dashboard_routes(app)
    register_question_routes(app)
    register_hwp_routes(app)
    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=True, use_reloader=False)
