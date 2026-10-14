import hmac
import json
import os
import queue
import secrets
import sqlite3
import threading
import uuid
from functools import wraps
from pathlib import Path
from urllib.parse import urlsplit

from flask import (
    Flask,
    abort,
    current_app,
    g,
    redirect,
    render_template,
    request,
    Response,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

from ocr_service import extract_text

app = Flask(__name__, template_folder="static/templates")
app.config["DATABASE"] = os.path.join(app.instance_path, "probocr.sqlite3")
app.config["MAX_CONTENT_LENGTH"] = 12 * 1024 * 1024
app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET_KEY")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = (
    os.environ.get("FLASK_SESSION_COOKIE_SECURE", "").lower() == "true"
)

NAV_ITEMS = [
    {"icon": "⇧", "label": "문제 DB 등록", "endpoint": "new_question"},
    {"icon": "▧", "label": "이미지 OCR", "endpoint": "ocr_page"},
    {"icon": "▤", "label": "문제 DB", "endpoint": "main"},
]

SUBJECTS = ["수학", "과학", "국어", "영어", "사회", "기타"]
USER_COLUMNS = {"uuid", "email", "display_name", "password_hash", "role", "created_at"}
FOLDER_COLUMNS = {"uuid", "user_uuid", "name", "parent_uuid", "created_at"}
QUESTION_COLUMNS = {
    "id",
    "user_uuid",
    "folder_uuid",
    "subject",
    "grade",
    "unit",
    "passage",
    "prompt",
    "examples",
    "choices",
    "answer",
    "source",
    "year",
    "page",
    "created_at",
}

USERS_SCHEMA = """
CREATE TABLE users (
    uuid TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    display_name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'user')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""

FOLDERS_SCHEMA = """
CREATE TABLE folders (
    uuid TEXT PRIMARY KEY,
    user_uuid TEXT REFERENCES users(uuid) ON DELETE CASCADE,
    name TEXT NOT NULL,
    parent_uuid TEXT REFERENCES folders(uuid) ON DELETE RESTRICT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""

QUESTIONS_SCHEMA = """
CREATE TABLE questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_uuid TEXT REFERENCES users(uuid) ON DELETE CASCADE,
    folder_uuid TEXT REFERENCES folders(uuid) ON DELETE SET NULL,
    subject TEXT NOT NULL,
    grade TEXT NOT NULL,
    unit TEXT NOT NULL,
    passage TEXT NOT NULL DEFAULT '',
    prompt TEXT NOT NULL,
    examples TEXT NOT NULL DEFAULT '',
    choices TEXT NOT NULL DEFAULT '[]',
    answer TEXT NOT NULL,
    source TEXT NOT NULL,
    year INTEGER,
    page INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""


def _table_columns(connection, table_name):
    return {
        row["name"]
        for row in connection.execute(f"PRAGMA table_info({table_name})")
    }


def _create_indexes(connection):
    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_folders_root_name
        ON folders(user_uuid, name) WHERE parent_uuid IS NULL
        """
    )
    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_folders_child_name
        ON folders(user_uuid, parent_uuid, name) WHERE parent_uuid IS NOT NULL
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_questions_user_subject ON questions(user_uuid, subject)"
    )


def initialize_database(connection):
    connection.execute("PRAGMA foreign_keys = OFF")
    tables = {
        row["name"]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    user_columns = _table_columns(connection, "users") if "users" in tables else set()
    folder_columns = _table_columns(connection, "folders") if "folders" in tables else set()
    question_columns = _table_columns(connection, "questions") if "questions" in tables else set()

    if "users" not in tables:
        connection.execute(USERS_SCHEMA)

    if FOLDER_COLUMNS.issubset(folder_columns) and QUESTION_COLUMNS.issubset(question_columns):
        _create_indexes(connection)
        connection.commit()
        connection.execute("PRAGMA foreign_keys = ON")
        return

    legacy_folders = (
        [dict(row) for row in connection.execute("SELECT * FROM folders")]
        if "folders" in tables
        else []
    )
    legacy_questions = (
        [dict(row) for row in connection.execute("SELECT * FROM questions")]
        if "questions" in tables
        else []
    )

    connection.execute("BEGIN IMMEDIATE")
    try:
        if "users" in tables and not USER_COLUMNS.issubset(user_columns):
            connection.execute("DROP TABLE users")
            connection.execute(USERS_SCHEMA)

        folder_uuid_map = {}
        for folder in legacy_folders:
            old_key = folder.get("uuid", folder.get("id"))
            folder_uuid_map[str(old_key)] = folder.get("uuid") or str(uuid.uuid4())

        if "questions" in tables:
            connection.execute("DROP TABLE questions")
        if "folders" in tables:
            connection.execute("DROP TABLE folders")

        connection.execute(FOLDERS_SCHEMA)
        connection.execute(QUESTIONS_SCHEMA)

        for folder in legacy_folders:
            old_parent = folder.get("parent_uuid", folder.get("parent_id"))
            parent_uuid = (
                folder_uuid_map.get(str(old_parent))
                if old_parent is not None
                else None
            )
            connection.execute(
                """
                INSERT INTO folders (uuid, user_uuid, name, parent_uuid, created_at)
                VALUES (?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP))
                """,
                (
                    folder_uuid_map[str(folder.get("uuid", folder.get("id")))],
                    folder.get("user_uuid"),
                    folder.get("name", ""),
                    parent_uuid,
                    folder.get("created_at"),
                ),
            )

        for question in legacy_questions:
            legacy_folder_ref = question.get(
                "folder_uuid", question.get("folder_id")
            )
            folder_uuid = (
                folder_uuid_map.get(str(legacy_folder_ref))
                if legacy_folder_ref is not None
                else None
            )
            connection.execute(
                """
                INSERT INTO questions (
                    id, user_uuid, folder_uuid, subject, grade, unit, passage,
                    prompt, examples, choices, answer, source, year, page, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP))
                """,
                (
                    question.get("id"),
                    question.get("user_uuid"),
                    folder_uuid,
                    question.get("subject", ""),
                    question.get("grade", ""),
                    question.get("unit", question.get("topic", "")),
                    question.get("passage", ""),
                    question.get("prompt", ""),
                    question.get("examples", ""),
                    question.get("choices", "[]"),
                    question.get("answer", ""),
                    question.get("source", ""),
                    question.get("year"),
                    question.get("page"),
                    question.get("created_at"),
                ),
            )

        _create_indexes(connection)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.execute("PRAGMA foreign_keys = ON")


def _secret_key():
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


if not app.config["SECRET_KEY"]:
    app.config["SECRET_KEY"] = _secret_key()


def get_db():
    if "db" not in g:
        database = current_app.config["DATABASE"]
        Path(database).parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(database)
        connection.row_factory = sqlite3.Row
        try:
            initialize_database(connection)
            connection.execute("PRAGMA foreign_keys = ON")
        except Exception:
            connection.close()
            raise
        g.db = connection
    return g.db


def csrf_token():
    token = session.get("csrf_token")
    if token is None:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


@app.before_request
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
        if not hmac.compare_digest(session.get("csrf_token", ""), submitted_token):
            abort(400, description="요청을 확인할 수 없습니다. 페이지를 새로고침해 주세요.")
    else:
        csrf_token()

    if request.endpoint not in {"login", "signup", "static"} and g.user is None:
        return redirect(url_for("login", next=request.full_path.rstrip("?")))


@app.context_processor
def inject_template_context():
    return {
        "current_user": g.get("user"),
        "csrf_token": csrf_token,
    }


@app.teardown_appcontext
def close_db(_error=None):
    connection = g.pop("db", None)
    if connection is not None:
        if _error is not None:
            connection.rollback()
        connection.close()


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


def get_selected_owner():
    requested_uuid = request.args.get("user_uuid")
    if requested_uuid and g.user["role"] == "admin":
        selected_user = get_db().execute(
            "SELECT uuid, email, display_name, role FROM users WHERE uuid = ?",
            (requested_uuid,),
        ).fetchone()
        if selected_user is None:
            abort(404)
        return selected_user
    if requested_uuid and requested_uuid != g.user["uuid"]:
        abort(403)
    return g.user


def get_folder_data(subject, owner_uuid):
    connection = get_db()
    folders = connection.execute(
        """
        SELECT uuid, name, parent_uuid
        FROM folders
        WHERE user_uuid = ?
        ORDER BY name COLLATE NOCASE, uuid
        """,
        (owner_uuid,),
    ).fetchall()
    question_counts = connection.execute(
        """
        SELECT folder_uuid, COUNT(*) AS count
        FROM questions
        WHERE user_uuid = ? AND subject = ?
        GROUP BY folder_uuid
        """,
        (owner_uuid, subject),
    ).fetchall()

    nodes = {
        folder["uuid"]: {
            "uuid": folder["uuid"],
            "name": folder["name"],
            "parent_uuid": folder["parent_uuid"],
            "children": [],
            "count": 0,
        }
        for folder in folders
    }
    roots = []
    for folder in folders:
        node = nodes[folder["uuid"]]
        parent = nodes.get(folder["parent_uuid"])
        if parent is None:
            roots.append(node)
        else:
            parent["children"].append(node)

    direct_counts = {
        row["folder_uuid"]: row["count"]
        for row in question_counts
        if row["folder_uuid"] is not None
    }

    def count_descendants(node):
        node["count"] = direct_counts.get(node["uuid"], 0) + sum(
            count_descendants(child) for child in node["children"]
        )
        return node["count"]

    for root in roots:
        count_descendants(root)

    options = []

    def append_options(folder_nodes, depth=0):
        for folder in folder_nodes:
            options.append(
                {
                    "uuid": folder["uuid"],
                    "name": folder["name"],
                    "depth": depth,
                }
            )
            append_options(folder["children"], depth + 1)

    append_options(roots)
    return roots, options


def get_question_page_data():
    selected_subject = request.args.get("subject", "수학")
    if selected_subject not in SUBJECTS:
        selected_subject = "수학"
    selected_user = get_selected_owner()
    selected_user_uuid = selected_user["uuid"]
    selected_folder_uuid = request.args.get("folder")
    selected_folder = None
    if selected_folder_uuid:
        selected_folder = get_db().execute(
            "SELECT uuid, name FROM folders WHERE uuid = ? AND user_uuid = ?",
            (selected_folder_uuid, selected_user_uuid),
        ).fetchone()
        if selected_folder is None:
            abort(404)

    query = """
        SELECT id, user_uuid, folder_uuid, subject, grade, unit, passage, prompt,
               examples, choices, answer, source, year, page
        FROM questions
        WHERE user_uuid = ? AND subject = ?
    """
    parameters = [selected_user_uuid, selected_subject]
    if selected_folder_uuid:
        query += """
          AND folder_uuid IN (
              WITH RECURSIVE folder_descendants(uuid) AS (
                  SELECT uuid FROM folders WHERE uuid = ? AND user_uuid = ?
                  UNION ALL
                  SELECT folders.uuid
                  FROM folders
                  JOIN folder_descendants ON folders.parent_uuid = folder_descendants.uuid
                  WHERE folders.user_uuid = ?
              )
              SELECT uuid FROM folder_descendants
          )
        """
        parameters.extend(
            [selected_folder_uuid, selected_user_uuid, selected_user_uuid]
        )
    query += " ORDER BY id DESC"
    rows = get_db().execute(query, parameters).fetchall()
    questions = []
    for row in rows:
        question = dict(row)
        question["choices"] = json.loads(question["choices"])
        questions.append(question)

    folder_tree, folder_options = get_folder_data(
        selected_subject, selected_user_uuid
    )
    folder_names = {option["uuid"]: option["name"] for option in folder_options}
    for question in questions:
        question["folder_name"] = folder_names.get(question["folder_uuid"])
    return {
        "nav_items": NAV_ITEMS,
        "subjects": SUBJECTS,
        "selected_subject": selected_subject,
        "questions": questions,
        "added": request.args.get("added") == "1",
        "folder_tree": folder_tree,
        "folder_options": folder_options,
        "selected_folder_uuid": selected_folder_uuid,
        "selected_folder_name": selected_folder["name"] if selected_folder else None,
        "folder_added": request.args.get("folder_added") == "1",
        "folder_moved": request.args.get("folder_moved") == "1",
        "folder_error": request.args.get("folder_error"),
        "selected_user": selected_user,
        "can_manage_questions": selected_user_uuid == g.user["uuid"],
        "selected_user_uuid": selected_user_uuid,
    }


def render_question_form(form_data=None, errors=None, status=200):
    _, folder_options = get_folder_data("수학", g.user["uuid"])
    return (
        render_template(
            "question_form.html",
            nav_items=NAV_ITEMS,
            subjects=SUBJECTS,
            folder_options=folder_options,
            form_data=form_data or {},
            errors=errors or [],
        ),
        status,
    )


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if g.user is not None:
        return redirect(url_for("main"))
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
    return redirect(url_for("main"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if g.user is not None:
        return redirect(url_for("main"))
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
    return redirect(url_for("main"))


@app.post("/logout")
@login_required
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/ocr", methods=["GET", "POST"])
@login_required
def ocr_page():
    allowed_extensions = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
    engine = request.form.get("engine", "trocr") if request.method == "POST" else request.args.get("engine", "trocr")
    if engine not in {"trocr", "easyocr"}:
        engine = "trocr"

    extracted_text = None
    error = None
    if request.method == "POST":
        uploaded = request.files.get("image")
        if uploaded is None or not uploaded.filename:
            error = "인식할 문제 이미지 파일을 선택해 주세요."
        elif Path(uploaded.filename).suffix.lower() not in allowed_extensions:
            error = "PNG, JPG, WEBP, BMP, TIFF 형식의 이미지 파일을 선택해 주세요."
        else:
            image_bytes = uploaded.read()
            if not image_bytes:
                error = "선택한 파일이 비어 있습니다."
            else:
                try:
                    restore_spacing = request.form.get("restore_spacing", "on") == "on"
                    binarize = request.form.get("binarize", "on") == "on"
                    extracted_text = extract_text(
                        image_bytes,
                        engine,
                        restore_spacing=restore_spacing,
                        binarize=binarize,
                    )
                    if not extracted_text.strip():
                        extracted_text = None
                        error = "문자를 찾지 못했습니다. 이미지가 선명한지 확인하거나 다른 OCR 모델을 선택해 보세요."
                except ValueError as exc:
                    error = str(exc)
                except Exception:
                    current_app.logger.exception("OCR inference failed using engine=%s", engine)
                    error = (
                        "OCR 실행에 실패했습니다. 필요한 패키지가 설치되어 있는지, "
                        "인터넷 연결이 가능한지 확인해 주세요. 모델의 첫 실행에는 가중치 다운로드가 필요합니다."
                    )

    return render_template(
        "ocr.html",
        nav_items=NAV_ITEMS,
        engine=engine,
        extracted_text=extracted_text,
        error=error,
    )


@app.post("/ocr/stream")
@login_required
def ocr_stream():
    """Stream OCR progress and the final result to the browser."""
    allowed_extensions = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
    engine = request.form.get("engine", "trocr")
    if engine not in {"trocr", "easyocr"}:
        engine = "trocr"
    restore_spacing = request.form.get("restore_spacing", "on") == "on"
    binarize = request.form.get("binarize", "on") == "on"

    def encode_event(event):
        return "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"

    def response_for_events(events):
        return Response(
            events,
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    uploaded = request.files.get("image")
    if uploaded is None or not uploaded.filename:
        return response_for_events([
            encode_event({"type": "error", "message": "인식할 문제 이미지 파일을 선택해 주세요."})
        ])
    if Path(uploaded.filename).suffix.lower() not in allowed_extensions:
        return response_for_events([
            encode_event({"type": "error", "message": "PNG, JPG, WEBP, BMP, TIFF 형식의 이미지 파일을 선택해 주세요."})
        ])

    image_bytes = uploaded.read()
    if not image_bytes:
        return response_for_events([
            encode_event({"type": "error", "message": "선택한 파일이 비어 있습니다."})
        ])

    events = queue.Queue()
    app_logger = current_app.logger

    def report_progress(message):
        events.put({"type": "log", "message": message})

    def run_ocr():
        try:
            extracted_text = extract_text(
                image_bytes,
                engine,
                progress_callback=report_progress,
                restore_spacing=restore_spacing,
                binarize=binarize,
            )
            if not extracted_text.strip():
                events.put({
                    "type": "error",
                    "message": (
                        "문자를 찾지 못했습니다. 이미지가 선명한지 확인하거나 "
                        "다른 OCR 모델을 선택해 보세요."
                    ),
                })
            else:
                events.put({
                    "type": "result",
                    "text": extracted_text,
                    "message": "OCR 완료: " + str(len(extracted_text)) + "자 추출",
                })
        except ValueError as exc:
            events.put({"type": "error", "message": str(exc)})
        except Exception as exc:
            app_logger.exception("OCR streaming inference failed using engine=%s", engine)
            details = str(exc).strip() or "자세한 오류 메시지가 없습니다."
            events.put({
                "type": "error",
                "message": (
                    "OCR 실행 실패 (" + type(exc).__name__ + "): " + details[:500]
                ),
            })

    def stream():
        yield encode_event({"type": "log", "message": "OCR 요청을 받았습니다."})
        worker = threading.Thread(target=run_ocr, daemon=True)
        worker.start()
        while True:
            try:
                event = events.get(timeout=10)
            except queue.Empty:
                # Keep slow first-time model downloads alive through reverse proxies.
                yield ": keep-alive\n\n"
                continue
            yield encode_event(event)
            if event.get("type") in {"result", "error"}:
                break

    return response_for_events(stream())


@app.route("/")
@login_required
def main():
    return render_template("main.html", **get_question_page_data())


@app.get("/admin/users")
@admin_required
def admin_users():
    users = get_db().execute(
        """
        SELECT users.uuid, users.email, users.display_name, users.role,
               users.created_at, COUNT(questions.id) AS question_count
        FROM users
        LEFT JOIN questions ON questions.user_uuid = users.uuid
        GROUP BY users.uuid
        ORDER BY users.created_at, users.email
        """
    ).fetchall()
    return render_template("admin_users.html", users=users)


@app.post("/folders/new")
@login_required
def new_folder():
    name = request.form.get("name", "").strip()
    parent_value = request.form.get("parent_uuid", "").strip()
    if not name or len(name) > 100:
        abort(400, description="폴더 이름은 1자 이상 100자 이내로 입력해 주세요.")

    parent_uuid = parent_value or None
    if parent_uuid and get_db().execute(
        "SELECT 1 FROM folders WHERE uuid = ? AND user_uuid = ?",
        (parent_uuid, g.user["uuid"]),
    ).fetchone() is None:
        abort(400, description="선택한 상위 폴더가 없습니다.")

    if parent_uuid is None:
        duplicate = get_db().execute(
            "SELECT 1 FROM folders WHERE user_uuid = ? AND parent_uuid IS NULL AND name = ?",
            (g.user["uuid"], name),
        ).fetchone()
    else:
        duplicate = get_db().execute(
            """
            SELECT 1 FROM folders
            WHERE user_uuid = ? AND parent_uuid = ? AND name = ?
            """,
            (g.user["uuid"], parent_uuid, name),
        ).fetchone()
    if duplicate:
        return redirect(
            url_for(
                "main",
                subject=request.form.get("subject", "수학"),
                folder=request.form.get("current_folder") or None,
                folder_error="duplicate",
            )
        )

    folder_uuid = str(uuid.uuid4())
    get_db().execute(
        """
        INSERT INTO folders (uuid, user_uuid, name, parent_uuid)
        VALUES (?, ?, ?, ?)
        """,
        (folder_uuid, g.user["uuid"], name, parent_uuid),
    )
    get_db().commit()
    return redirect(
        url_for(
            "main",
            subject=request.form.get("subject", "수학"),
            folder=folder_uuid,
            folder_added=1,
        )
    )


@app.route("/questions/new", methods=["GET", "POST"])
@login_required
def new_question():
    if request.method == "GET":
        return render_question_form()

    form_data = request.form
    errors = []
    subject = form_data.get("subject", "").strip()
    grade = form_data.get("grade", "").strip()
    unit = form_data.get("unit", "").strip()
    passage = form_data.get("passage", "").strip()
    prompt = form_data.get("prompt", "").strip()
    examples = form_data.get("examples", "").strip()
    choices = [choice.strip() for choice in form_data.get("choices", "").splitlines()]
    choices = [choice for choice in choices if choice]
    answer = form_data.get("answer", "").strip()
    source = form_data.get("source", "").strip()
    folder_uuid = form_data.get("folder_uuid", "").strip() or None
    if folder_uuid and get_db().execute(
        "SELECT 1 FROM folders WHERE uuid = ? AND user_uuid = ?",
        (folder_uuid, g.user["uuid"]),
    ).fetchone() is None:
        errors.append("선택한 폴더가 없습니다.")

    if subject not in SUBJECTS:
        errors.append("과목을 선택해 주세요.")
    for value, label in (
        (grade, "학년"),
        (unit, "단원"),
        (prompt, "문제 내용"),
        (answer, "답"),
        (source, "출처"),
    ):
        if not value:
            errors.append(f"{label}을(를) 입력해 주세요.")

    for value, label, maximum in (
        (grade, "학년", 50),
        (unit, "단원", 200),
        (passage, "지문", 10000),
        (prompt, "문제 내용", 10000),
        (examples, "보기", 10000),
        (answer, "답", 2000),
        (source, "출처", 300),
    ):
        if len(value) > maximum:
            errors.append(f"{label}은 {maximum}자 이내로 입력해 주세요.")
    if len(choices) > 20:
        errors.append("선택지는 20개 이하로 입력해 주세요.")
    if any(len(choice) > 2000 for choice in choices):
        errors.append("각 선택지는 2,000자 이내로 입력해 주세요.")

    optional_numbers = {}
    for field, label, maximum in (("year", "연도", 2200), ("page", "페이지", 10000)):
        value = form_data.get(field, "").strip()
        if not value:
            optional_numbers[field] = None
            continue
        try:
            number = int(value)
            if not 1 <= number <= maximum:
                raise ValueError
        except ValueError:
            errors.append(f"{label}는 1에서 {maximum} 사이의 정수로 입력해 주세요.")
            optional_numbers[field] = None
        else:
            optional_numbers[field] = number

    if errors:
        return render_question_form(form_data, errors, 400)

    get_db().execute(
        """
        INSERT INTO questions (
            user_uuid, folder_uuid, subject, grade, unit, passage, prompt, examples,
            choices, answer, source, year, page
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            g.user["uuid"],
            folder_uuid,
            subject,
            grade,
            unit,
            passage,
            prompt,
            examples,
            json.dumps(choices, ensure_ascii=False),
            answer,
            source,
            optional_numbers["year"],
            optional_numbers["page"],
        ),
    )
    get_db().commit()
    return redirect(url_for("main", added=1, subject=subject, folder=folder_uuid))


@app.post("/questions/<int:question_id>/folder")
@login_required
def move_question(question_id):
    question = get_db().execute(
        "SELECT id FROM questions WHERE id = ? AND user_uuid = ?",
        (question_id, g.user["uuid"]),
    ).fetchone()
    if question is None:
        abort(404)

    folder_uuid = request.form.get("folder_uuid", "").strip() or None
    if folder_uuid and get_db().execute(
        "SELECT 1 FROM folders WHERE uuid = ? AND user_uuid = ?",
        (folder_uuid, g.user["uuid"]),
    ).fetchone() is None:
        abort(400, description="선택한 폴더가 없습니다.")

    get_db().execute(
        "UPDATE questions SET folder_uuid = ? WHERE id = ? AND user_uuid = ?",
        (folder_uuid, question_id, g.user["uuid"]),
    )
    get_db().commit()
    return redirect(
        url_for(
            "main",
            subject=request.form.get("subject", "수학"),
            folder=request.form.get("current_folder") or None,
            folder_moved=1,
        )
    )


if __name__ == "__main__":
    app.run(debug=True, use_reloader=False)
