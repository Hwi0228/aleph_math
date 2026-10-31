import hmac
import secrets
import sqlite3
import uuid
from functools import wraps
from urllib.parse import urlsplit

from flask import abort, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from database import close_db, get_db


def csrf_token():
    token = session.get("csrf_token")
    if token is None:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token

def load_user_and_protect_forms():
    g.user = None
    get_db()
    user_uuid = session.get("user_uuid")
    if user_uuid:
        g.user = get_db().execute(
            "SELECT uuid, email, display_name, role FROM users WHERE uuid = ?",
            (user_uuid,),
        ).fetchone()
        if g.user is None:
            session.clear()

    if request.method == "POST":
        submitted_token = request.form.get("_csrf_token") or request.headers.get(
            "X-CSRF-Token", ""
        )
        expected_token = session.get("csrf_token")
        if not expected_token or not hmac.compare_digest(
            expected_token, submitted_token
        ):
            abort(400, description="요청을 확인할 수 없습니다. 페이지를 새로고침해 주세요.")
    else:
        csrf_token()

    if request.endpoint not in {"login", "signup", "static"} and g.user is None:
        return redirect(url_for("login", next=request.full_path.rstrip("?")))

def inject_template_context():
    return {
        "current_user": g.get("user"),
        "csrf_token": csrf_token,
    }

def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("login", next=request.full_path.rstrip("?")))
        return view(*args, **kwargs)

    return wrapped_view

def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped_view(*args, **kwargs):
        if g.user["role"] != "admin":
            abort(403)
        return view(*args, **kwargs)

    return wrapped_view

def render_account_form(mode, form_data=None, errors=None, status=200):
    return (
        render_template(
            "account_form.html",
            mode=mode,
            first_account=(
                mode == "signup"
                and get_db().execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
            ),
            form_data=form_data or {},
            errors=errors or [],
        ),
        status,
    )

def signup():
    if g.user is not None:
        return redirect(url_for("dashboard"))
    if request.method == "GET":
        return render_account_form("signup")

    email = request.form.get("email", "").strip().lower()
    display_name = request.form.get("display_name", "").strip()
    password = request.form.get("password", "")
    password_confirm = request.form.get("password_confirm", "")
    errors = []
    if len(email) > 254 or "@" not in email or "." not in email.rsplit("@", 1)[-1]:
        errors.append("올바른 이메일 주소를 입력해 주세요.")
    if not display_name or len(display_name) > 80:
        errors.append("이름은 1자 이상 80자 이내로 입력해 주세요.")
    if len(password) < 12 or len(password) > 256:
        errors.append("비밀번호는 12자 이상 256자 이내로 입력해 주세요.")
    if password != password_confirm:
        errors.append("비밀번호가 일치하지 않습니다.")
    if errors:
        return render_account_form(
            "signup",
            {"email": email, "display_name": display_name},
            errors,
            400,
        )

    connection = get_db()
    user_uuid = str(uuid.uuid4())
    try:
        connection.execute("BEGIN IMMEDIATE")
        is_first_user = (
            connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
        )
        connection.execute(
            """
            INSERT INTO users (uuid, email, display_name, password_hash, role)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                user_uuid,
                email,
                display_name,
                generate_password_hash(password),
                "admin" if is_first_user else "user",
            ),
        )
        if is_first_user:
            connection.execute(
                "UPDATE folders SET user_uuid = ? WHERE user_uuid IS NULL",
                (user_uuid,),
            )
            connection.execute(
                "UPDATE questions SET user_uuid = ? WHERE user_uuid IS NULL",
                (user_uuid,),
            )
        connection.commit()
    except sqlite3.IntegrityError:
        connection.rollback()
        if connection.execute(
            "SELECT 1 FROM users WHERE email = ?", (email,)
        ).fetchone() is None:
            raise
        return render_account_form(
            "signup",
            {"email": email, "display_name": display_name},
            ["이미 가입된 이메일입니다."],
            409,
        )
    except Exception:
        connection.rollback()
        raise

    session.clear()
    session["user_uuid"] = user_uuid
    return redirect(url_for("dashboard"))

def login():
    if g.user is not None:
        return redirect(url_for("dashboard"))
    if request.method == "GET":
        return render_account_form("login")

    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    user = get_db().execute(
        "SELECT uuid, password_hash FROM users WHERE email = ?",
        (email,),
    ).fetchone()
    if user is None or not check_password_hash(user["password_hash"], password):
        return render_account_form(
            "login",
            {"email": email},
            ["이메일 또는 비밀번호를 확인해 주세요."],
            400,
        )

    session.clear()
    session["user_uuid"] = user["uuid"]
    destination = request.args.get("next", "")
    try:
        parsed_destination = urlsplit(destination)
    except ValueError:
        parsed_destination = None
    if parsed_destination and (
        parsed_destination.scheme == ""
        and parsed_destination.netloc == ""
        and parsed_destination.path.startswith("/")
        and not parsed_destination.path.startswith("//")
        and "\\" not in destination
    ):
        return redirect(destination)
    return redirect(url_for("dashboard"))

def logout():
    session.clear()
    return redirect(url_for("login"))

def register_auth_routes(app):
    app.before_request(load_user_and_protect_forms)
    app.context_processor(inject_template_context)
    app.teardown_appcontext(close_db)
    app.add_url_rule(
        "/signup", endpoint="signup", view_func=signup, methods=["GET", "POST"]
    )
    app.add_url_rule(
        "/login", endpoint="login", view_func=login, methods=["GET", "POST"]
    )
    app.add_url_rule(
        "/logout",
        endpoint="logout",
        view_func=login_required(logout),
        methods=["POST"],
    )
