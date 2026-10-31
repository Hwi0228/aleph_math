import json
import uuid

from flask import abort, g, redirect, render_template, request, url_for

from auth import admin_required, login_required
from constants import NAV_ITEMS, SUBJECTS
from database import get_db
from parse_hwp import normalize_katex_markup, strip_choice_label

QUESTION_CONTENT_FIELDS = (
    "subject", "grade", "unit", "passage", "prompt", "examples",
    "choices", "answer", "explanation", "source", "year", "page",
)
QUESTION_FIELD_LABELS = {
    "subject": "과목", "grade": "학년", "unit": "단원", "passage": "지문",
    "prompt": "문제 내용", "examples": "보기", "choices": "선택지",
    "answer": "정답", "explanation": "해설", "source": "출처",
    "year": "연도", "page": "페이지",
}


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
        SELECT questions.id, questions.user_uuid, questions.folder_uuid,
               questions.subject, questions.grade, questions.unit,
               questions.passage, questions.prompt, questions.examples,
               questions.choices, questions.answer, questions.explanation,
               questions.source, questions.year, questions.page, questions.created_at,
               users.display_name AS author_name,
               COALESCE(revisions.latest_revision, 1) AS revision_number
        FROM questions
        LEFT JOIN users ON users.uuid = questions.user_uuid
        LEFT JOIN (
            SELECT question_id, MAX(revision_number) AS latest_revision
            FROM question_revisions
            GROUP BY question_id
        ) AS revisions ON revisions.question_id = questions.id
        WHERE questions.subject = ?
    """
    parameters = [selected_subject]
    if request.args.get("user_uuid"):
        query += " AND questions.user_uuid = ?"
        parameters.append(selected_user_uuid)
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
        question["choices"] = [
            normalize_katex_markup(strip_choice_label(choice))
            for choice in json.loads(question["choices"])
        ]
        for field in ("passage", "prompt", "examples", "answer", "explanation"):
            question[field] = normalize_katex_markup(question[field])
        questions.append(question)

    folder_tree, folder_options = get_folder_data(
        selected_subject, selected_user_uuid
    )
    folder_names = {option["uuid"]: option["name"] for option in folder_options}
    for question in questions:
        question["folder_name"] = folder_names.get(question["folder_uuid"])
    for question in questions:
        question["can_manage_folder"] = question["user_uuid"] == g.user["uuid"]
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
        "can_manage_questions": True,
        "can_manage_folders": selected_user_uuid == g.user["uuid"],
        "selected_user_uuid": selected_user_uuid,
    }

def render_question_form(
    form_data=None, errors=None, status=200, *, mode="create",
    question=None, base_revision=None,
):
    _, folder_options = get_folder_data("수학", g.user["uuid"])
    return (
        render_template(
            "question_form.html",
            nav_items=NAV_ITEMS,
            subjects=SUBJECTS,
            folder_options=folder_options,
            form_data=form_data or {},
            errors=errors or [],
            mode=mode,
            question=question,
            base_revision=base_revision,
        ),
        status,
    )

def main():
    return render_template(
        "main.html", **get_question_page_data(), active_page="main"
    )

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
    return render_template(
        "admin_users.html",
        users=users,
        nav_items=NAV_ITEMS,
        active_page="admin_users",
    )

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

def _question_snapshot(question):
    snapshot = {field: question[field] for field in QUESTION_CONTENT_FIELDS}
    raw_choices = snapshot.get("choices") or "[]"
    if isinstance(raw_choices, str):
        try:
            snapshot["choices"] = json.loads(raw_choices)
        except json.JSONDecodeError:
            snapshot["choices"] = []
    return snapshot


def _insert_question_revision(connection, question_id, revision_number, editor_uuid, summary, snapshot):
    connection.execute(
        """
        INSERT INTO question_revisions (
            question_id, revision_number, editor_uuid, summary, snapshot_json
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            question_id, revision_number, editor_uuid, summary,
            json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")),
        ),
    )


def _ensure_initial_question_revision(connection, question):
    snapshot = _question_snapshot(question)
    connection.execute(
        """
        INSERT OR IGNORE INTO question_revisions (
            question_id, revision_number, editor_uuid, summary,
            snapshot_json, created_at
        )
        VALUES (?, 1, ?, ?, ?, ?)
        """,
        (
            question["id"],
            question["user_uuid"],
            "최초 등록 버전 · 이력 추적 시작",
            json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")),
            question["created_at"],
        ),
    )


def _question_row(question_id):
    row = get_db().execute(
        """
        SELECT questions.*, users.display_name AS author_name
        FROM questions
        LEFT JOIN users ON users.uuid = questions.user_uuid
        WHERE questions.id = ?
        """,
        (question_id,),
    ).fetchone()
    if row is None:
        abort(404)
    return row


def _question_form_values(question):
    values = dict(question)
    raw_choices = values.get("choices") or "[]"
    if isinstance(raw_choices, str):
        try:
            raw_choices = json.loads(raw_choices)
        except json.JSONDecodeError:
            raw_choices = []
    values["choices"] = "\n".join(raw_choices)
    return values


def _validate_question_edit(form):
    errors = []
    data = {
        "subject": form.get("subject", "").strip(),
        "grade": form.get("grade", "").strip(),
        "unit": form.get("unit", "").strip(),
        "passage": form.get("passage", "").strip(),
        "prompt": form.get("prompt", "").strip(),
        "examples": form.get("examples", "").strip(),
        "choices": [value.strip() for value in form.get("choices", "").splitlines() if value.strip()],
        "answer": form.get("answer", "").strip(),
        "explanation": form.get("explanation", "").strip(),
        "source": form.get("source", "").strip(),
        "year": None,
        "page": None,
    }
    if data["subject"] not in SUBJECTS:
        errors.append("과목을 선택해 주세요.")
    for field, label in (
        ("grade", "학년"), ("unit", "단원"), ("prompt", "문제 내용"),
        ("answer", "답"), ("source", "출처"),
    ):
        if not data[field]:
            errors.append(f"{label}을(를) 입력해 주세요.")

    limits = {
        "grade": ("학년", 50), "unit": ("단원", 200),
        "passage": ("지문", 10000), "prompt": ("문제 내용", 10000),
        "examples": ("보기", 10000), "answer": ("답", 2000),
        "explanation": ("해설", 10000), "source": ("출처", 300),
    }
    for field, (label, maximum) in limits.items():
        if len(data[field]) > maximum:
            errors.append(f"{label}은 {maximum}자 이내로 입력해 주세요.")
    if len(data["choices"]) > 20:
        errors.append("선택지는 20개 이하로 입력해 주세요.")
    if any(len(value) > 2000 for value in data["choices"]):
        errors.append("각 선택지는 2,000자 이내로 입력해 주세요.")

    for field, label, maximum in (("year", "연도", 2200), ("page", "페이지", 10000)):
        value = form.get(field, "").strip()
        if not value:
            continue
        try:
            parsed = int(value)
            if not 1 <= parsed <= maximum:
                raise ValueError
            data[field] = parsed
        except ValueError:
            errors.append(f"{label}는 1에서 {maximum} 사이의 정수로 입력해 주세요.")

    data["summary"] = form.get("summary", "").strip() or "문제 내용 수정"
    if len(data["summary"]) > 200:
        errors.append("수정 요약은 200자 이내로 입력해 주세요.")
    return data, errors


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
    explanation = form_data.get("explanation", "").strip()
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
        (explanation, "해설", 10000),
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

    cursor = get_db().execute(
        """
        INSERT INTO questions (
            user_uuid, folder_uuid, subject, grade, unit, passage, prompt, examples,
            choices, answer, explanation, source, year, page
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            g.user["uuid"], folder_uuid, subject, grade, unit, passage, prompt,
            examples, json.dumps(choices, ensure_ascii=False), answer, explanation,
            source, optional_numbers["year"], optional_numbers["page"],
        ),
    )
    question_id = cursor.lastrowid
    question = get_db().execute(
        "SELECT * FROM questions WHERE id = ?", (question_id,)
    ).fetchone()
    _insert_question_revision(
        get_db(), question_id, 1, g.user["uuid"], "최초 등록",
        _question_snapshot(question),
    )
    get_db().commit()
    return redirect(url_for("main", added=1, subject=subject, folder=folder_uuid))

def edit_question(question_id):
    question = _question_row(question_id)
    connection = get_db()
    if request.method == "GET":
        connection.execute("BEGIN IMMEDIATE")
        question = _question_row(question_id)
        _ensure_initial_question_revision(connection, question)
        connection.commit()
        latest = connection.execute(
            "SELECT MAX(revision_number) FROM question_revisions WHERE question_id = ?",
            (question_id,),
        ).fetchone()[0]
        return render_question_form(
            _question_form_values(question), mode="edit",
            question=question, base_revision=latest,
        )

    data, errors = _validate_question_edit(request.form)
    try:
        base_revision = int(request.form.get("base_revision", ""))
    except ValueError:
        abort(400, description="수정 기준 버전이 올바르지 않습니다.")
    if errors:
        values = dict(data)
        values["choices"] = "\n".join(data["choices"])
        return render_question_form(
            values, errors, 400, mode="edit", question=question,
            base_revision=base_revision,
        )

    connection = get_db()
    try:
        connection.execute("BEGIN IMMEDIATE")
        current = _question_row(question_id)
        _ensure_initial_question_revision(connection, current)
        latest = connection.execute(
            "SELECT MAX(revision_number) FROM question_revisions WHERE question_id = ?",
            (question_id,),
        ).fetchone()[0]
        if latest != base_revision:
            connection.rollback()
            errors.append(
                f"다른 사용자가 먼저 수정했습니다. 최신 버전은 v{latest}입니다. "
                "수정 이력을 확인한 뒤 최신 문서를 다시 열어 주세요."
            )
            values = dict(data)
            values["choices"] = "\n".join(data["choices"])
            return render_question_form(
                values, errors, 409, mode="edit", question=current,
                base_revision=base_revision,
            )

        next_revision = latest + 1
        connection.execute(
            """
            UPDATE questions
            SET subject = ?, grade = ?, unit = ?, passage = ?, prompt = ?,
                examples = ?, choices = ?, answer = ?, explanation = ?,
                source = ?, year = ?, page = ?
            WHERE id = ?
            """,
            (
                data["subject"], data["grade"], data["unit"], data["passage"],
                data["prompt"], data["examples"],
                json.dumps(data["choices"], ensure_ascii=False),
                data["answer"], data["explanation"], data["source"],
                data["year"], data["page"], question_id,
            ),
        )
        snapshot = {
            field: data[field]
            for field in QUESTION_CONTENT_FIELDS
        }
        _insert_question_revision(
            connection, question_id, next_revision, g.user["uuid"],
            data["summary"], snapshot,
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return redirect(url_for("question_history", question_id=question_id, saved=1))


def question_history(question_id):
    question = _question_row(question_id)
    connection = get_db()
    _ensure_initial_question_revision(connection, question)
    connection.commit()
    revisions = connection.execute(
        """
        SELECT question_revisions.revision_number, question_revisions.summary,
               question_revisions.created_at, users.display_name AS editor_name
        FROM question_revisions
        LEFT JOIN users ON users.uuid = question_revisions.editor_uuid
        WHERE question_revisions.question_id = ?
        ORDER BY question_revisions.revision_number DESC
        """,
        (question_id,),
    ).fetchall()

    compare_from = request.args.get("from", type=int)
    compare_to = request.args.get("to", type=int)
    if compare_from is None or compare_to is None:
        if len(revisions) >= 2:
            compare_from = revisions[1]["revision_number"]
            compare_to = revisions[0]["revision_number"]
        else:
            compare_from = compare_to = revisions[0]["revision_number"]

    comparison = []
    if compare_from != compare_to:
        rows = connection.execute(
            """
            SELECT revision_number, snapshot_json
            FROM question_revisions
            WHERE question_id = ? AND revision_number IN (?, ?)
            """,
            (question_id, compare_from, compare_to),
        ).fetchall()
        snapshots = {
            row["revision_number"]: json.loads(row["snapshot_json"])
            for row in rows
        }
        if compare_from not in snapshots or compare_to not in snapshots:
            abort(404)
        for field in QUESTION_CONTENT_FIELDS:
            before = snapshots[compare_from].get(field)
            after = snapshots[compare_to].get(field)
            if field == "choices":
                before = "\n".join(before or [])
                after = "\n".join(after or [])
            if before != after:
                comparison.append({
                    "field": QUESTION_FIELD_LABELS[field],
                    "before": "" if before is None else str(before),
                    "after": "" if after is None else str(after),
                })

    return render_template(
        "question_history.html", nav_items=NAV_ITEMS, question=question,
        revisions=revisions, compare_from=compare_from, compare_to=compare_to,
        comparison=comparison, latest_revision=revisions[0]["revision_number"],
        saved=request.args.get("saved") == "1",
        restored=request.args.get("restored") == "1",
    )


def restore_question(question_id, revision_number):
    question = _question_row(question_id)
    connection = get_db()
    try:
        connection.execute("BEGIN IMMEDIATE")
        question = _question_row(question_id)
        _ensure_initial_question_revision(connection, question)
        target = connection.execute(
            """
            SELECT snapshot_json FROM question_revisions
            WHERE question_id = ? AND revision_number = ?
            """,
            (question_id, revision_number),
        ).fetchone()
        latest = connection.execute(
            "SELECT MAX(revision_number) FROM question_revisions WHERE question_id = ?",
            (question_id,),
        ).fetchone()[0]
        if target is None:
            connection.rollback()
            abort(404)
        snapshot = json.loads(target["snapshot_json"])
        next_revision = latest + 1
        connection.execute(
            """
            UPDATE questions
            SET subject = ?, grade = ?, unit = ?, passage = ?, prompt = ?,
                examples = ?, choices = ?, answer = ?, explanation = ?,
                source = ?, year = ?, page = ?
            WHERE id = ?
            """,
            (
                snapshot["subject"], snapshot["grade"], snapshot["unit"],
                snapshot["passage"], snapshot["prompt"], snapshot["examples"],
                json.dumps(snapshot["choices"], ensure_ascii=False),
                snapshot["answer"], snapshot["explanation"], snapshot["source"],
                snapshot["year"], snapshot["page"], question_id,
            ),
        )
        _insert_question_revision(
            connection, question_id, next_revision, g.user["uuid"],
            f"이전 버전 v{revision_number} 복원", snapshot,
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return redirect(url_for("question_history", question_id=question_id, restored=1))


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

def register_question_routes(app):
    app.add_url_rule(
        "/questions", endpoint="main", view_func=login_required(main), methods=["GET"]
    )
    app.add_url_rule(
        "/admin/users",
        endpoint="admin_users",
        view_func=admin_required(admin_users),
        methods=["GET"],
    )
    app.add_url_rule(
        "/folders/new",
        endpoint="new_folder",
        view_func=login_required(new_folder),
        methods=["POST"],
    )
    app.add_url_rule(
        "/questions/new",
        endpoint="new_question",
        view_func=login_required(new_question),
        methods=["GET", "POST"],
    )
    app.add_url_rule(
        "/questions/<int:question_id>/folder",
        endpoint="move_question",
        view_func=login_required(move_question),
        methods=["POST"],
    )
    app.add_url_rule(
        "/questions/<int:question_id>/edit",
        endpoint="edit_question",
        view_func=login_required(edit_question),
        methods=["GET", "POST"],
    )
    app.add_url_rule(
        "/questions/<int:question_id>/history",
        endpoint="question_history",
        view_func=login_required(question_history),
        methods=["GET"],
    )
    app.add_url_rule(
        "/questions/<int:question_id>/restore/<int:revision_number>",
        endpoint="restore_question",
        view_func=login_required(restore_question),
        methods=["POST"],
    )
