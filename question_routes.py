import json
import uuid

from flask import abort, g, redirect, render_template, request, url_for

from auth import admin_required, login_required
from constants import NAV_ITEMS, SUBJECTS
from database import get_db
from parse_hwp import normalize_katex_markup, strip_choice_label


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
               examples, choices, answer, explanation, source, year, page
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

def main():
    return render_template("main.html", **get_question_page_data())

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
