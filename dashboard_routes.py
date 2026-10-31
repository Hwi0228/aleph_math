import json

from flask import g, render_template

from auth import login_required
from constants import NAV_ITEMS
from database import get_db
from question_routes import format_question_number, render_rich_html


def dashboard():
    connection = get_db()
    user_uuid = g.user["uuid"]

    summary = connection.execute(
        """
        SELECT
            COUNT(*) AS question_count,
            COUNT(DISTINCT subject) AS subject_count,
            COUNT(DISTINCT CASE
                WHEN unit <> '' THEN subject || '|' || grade || '|' || unit
            END) AS unit_count
        FROM questions
        """
    ).fetchone()
    folder_count = connection.execute(
        "SELECT COUNT(*) FROM folders WHERE user_uuid = ?",
        (user_uuid,),
    ).fetchone()[0]

    subject_stats = connection.execute(
        """
        SELECT subject, COUNT(*) AS count
        FROM questions
        GROUP BY subject
        ORDER BY count DESC, subject COLLATE NOCASE
        """
    ).fetchall()

    recent_questions = connection.execute(
        """
        SELECT id, title, subject, grade, unit, passage, prompt, examples,
               choices, source, year, page
        FROM questions
        ORDER BY id DESC
        LIMIT 6
        """
    ).fetchall()
    questions = []
    for row in recent_questions:
        question = dict(row)
        try:
            question["choices"] = json.loads(question["choices"] or "[]")
        except (TypeError, json.JSONDecodeError):
            question["choices"] = []
        question["display_id"] = format_question_number(question["id"])
        for field in ("passage", "prompt", "examples"):
            question[field] = render_rich_html(question[field])
        questions.append(question)

    return render_template(
        "dashboard.html",
        question_count=summary["question_count"],
        subject_count=summary["subject_count"],
        unit_count=summary["unit_count"],
        folder_count=folder_count,
        subject_stats=subject_stats,
        recent_questions=questions,
        nav_items=NAV_ITEMS,
        active_page="dashboard",
    )


def register_dashboard_routes(app):
    app.add_url_rule(
        "/", endpoint="dashboard", view_func=login_required(dashboard), methods=["GET"]
    )
