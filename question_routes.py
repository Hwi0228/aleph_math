import html
import json
import re
import uuid
from html.parser import HTMLParser

from flask import abort, g, jsonify, redirect, render_template, request, url_for

from auth import admin_required, login_required
from constants import NAV_ITEMS, SUBJECTS
from database import get_db
from parse_hwp import normalize_katex_markup, strip_choice_label

QUESTION_CONTENT_FIELDS = (
    "title", "subject", "grade", "course", "curriculum", "unit",
    "achievement_standard", "difficulty", "question_type", "points",
    "passage", "prompt", "examples", "choices", "answer", "explanation",
    "source_type", "source", "year", "exam", "original_question_number",
    "page", "source_url", "license",
)
QUESTION_FIELD_LABELS = {
    "title": "문항 제목", "subject": "과목", "grade": "학년", "course": "교과목",
    "curriculum": "교육과정", "unit": "단원",
    "achievement_standard": "성취기준", "difficulty": "난이도",
    "question_type": "문항 유형", "points": "배점", "passage": "지문",
    "prompt": "문제 내용", "examples": "보기", "choices": "선택지",
    "answer": "정답", "explanation": "해설", "source_type": "출처 유형",
    "source": "출처명", "year": "연도", "exam": "시험 / 회차",
    "original_question_number": "원본 문항 번호", "page": "페이지",
    "source_url": "출처 URL", "license": "라이선스",
}

QUESTION_TYPES = ("객관식", "복수 선택", "단답형", "서술형")
DIFFICULTIES = ("하", "중", "상")
SOURCE_TYPES = (
    "직접 제작", "교재 및 참고서", "학교 시험", "수능 및 모의평가",
    "공개 데이터셋", "기타",
)
LICENSE_CHOICES = {
    "ORIGINAL": "직접 제작 (권리 보유)",
    "CC-BY-4.0": "CC BY 4.0",
    "CC-BY-SA-4.0": "CC BY-SA 4.0",
    "CC0-1.0": "CC0 1.0 / 퍼블릭 도메인",
    "PERMISSION": "권리자로부터 공개 허가 받음",
}
COURSE_OPTIONS = {
    "2015 개정 교육과정": ["수학", "수학Ⅰ", "수학Ⅱ", "미적분", "확률과 통계", "기하"],
    "2022 개정 교육과정": ["공통수학1", "공통수학2", "대수", "미적분Ⅰ", "미적분Ⅱ", "확률과 통계", "기하"],
}
SUBJECT_COURSE_OPTIONS = {
    "수학": ["수학", "수학Ⅰ", "수학Ⅱ", "미적분", "확률과 통계", "기하", "공통수학1", "공통수학2", "대수", "미적분Ⅰ", "미적분Ⅱ"],
    "과학": ["과학", "통합과학", "물리학", "화학", "생명과학", "지구과학"],
    "국어": ["국어", "화법과 작문", "언어와 매체", "독서", "문학"],
    "영어": ["영어", "영어Ⅰ", "영어Ⅱ", "영어 독해와 작문"],
    "사회": ["통합사회", "한국사", "생활과 윤리", "윤리와 사상", "한국지리", "세계지리", "동아시아사", "세계사", "정치와 법", "경제", "사회문화"],
    "기타": ["기타"],
}
CURRICULUM_STANDARDS = {
    "2015 개정 교육과정": {
        "수학Ⅱ": {
            "함수의 극한": [
                "[12수학Ⅱ01-01] 함수의 극한의 뜻을 안다.",
                "[12수학Ⅱ01-02] 함수의 극한에 대한 성질을 이해하고, 함수의 극한값을 구할 수 있다.",
            ],
            "함수의 연속": [
                "[12수학Ⅱ01-03] 함수의 연속의 뜻을 안다.",
                "[12수학Ⅱ01-04] 연속함수의 성질을 이해하고, 이를 활용할 수 있다.",
            ],
            "미분계수와 도함수": [
                "[12수학Ⅱ02-01] 미분계수의 뜻을 알고, 그 값을 구할 수 있다.",
                "[12수학Ⅱ02-02] 미분계수의 기하적 의미를 이해한다.",
                "[12수학Ⅱ02-03] 미분가능성과 연속성의 관계를 이해한다.",
                "[12수학Ⅱ02-04] 함수 y=xⁿ (n은 양의 정수)의 도함수를 구할 수 있다.",
                "[12수학Ⅱ02-05] 함수의 실수배, 합, 차, 곱의 미분법을 알고, 다항함수의 도함수를 구할 수 있다.",
            ],
            "도함수의 활용": [
                "[12수학Ⅱ02-06] 접선의 방정식을 구할 수 있다.",
                "[12수학Ⅱ02-07] 함수에 대한 평균값 정리를 이해한다.",
                "[12수학Ⅱ02-08] 함수의 증가와 감소, 극대와 극소를 판정하고 설명할 수 있다.",
                "[12수학Ⅱ02-09] 함수의 그래프의 개형을 그릴 수 있다.",
                "[12수학Ⅱ02-10] 방정식과 부등식에 대한 문제를 해결할 수 있다.",
                "[12수학Ⅱ02-11] 속도와 가속도에 대한 문제를 해결할 수 있다.",
            ],
            "부정적분": [
                "[12수학Ⅱ03-01] 부정적분의 뜻을 안다.",
                "[12수학Ⅱ03-02] 함수의 실수배, 합, 차의 부정적분을 알고, 다항함수의 부정적분을 구할 수 있다.",
            ],
            "정적분": [
                "[12수학Ⅱ03-03] 정적분의 뜻을 안다.",
                "[12수학Ⅱ03-04] 다항함수의 정적분을 구할 수 있다.",
                "[12수학Ⅱ03-05] 곡선으로 둘러싸인 도형의 넓이를 구할 수 있다.",
                "[12수학Ⅱ03-06] 속도와 거리에 대한 문제를 해결할 수 있다.",
            ],
        }
    }
}

BASE36_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
BASE36_WIDTH = 6


def format_question_number(question_id):
    value = int(question_id)
    if value < 0 or value >= 36 ** BASE36_WIDTH:
        raise ValueError("문항 번호가 6자리 Base36 범위를 초과했습니다.")
    encoded = "0" if value == 0 else ""
    while value:
        value, remainder = divmod(value, 36)
        encoded = BASE36_ALPHABET[remainder] + encoded
    return encoded.rjust(BASE36_WIDTH, "0")


class _RichTextSanitizer(HTMLParser):
    ALLOWED_TAGS = {
        "p", "br", "strong", "b", "em", "i", "u", "s", "ul", "ol",
        "li", "blockquote", "pre", "code", "h2", "h3", "hr", "span", "div",
    }
    VOID_TAGS = {"br", "hr"}
    LITERAL_TAGS = {"script", "style", "iframe", "object", "embed", "svg"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.stack = []

    def handle_starttag(self, tag, attrs):
        if self.stack and self.stack[-1].startswith("__literal__:"):
            self.parts.append(html.escape(self.get_starttag_text() or f"<{tag}>"))
            return
        if tag in self.LITERAL_TAGS:
            self.parts.append(html.escape(self.get_starttag_text() or f"<{tag}>"))
            self.stack.append(f"__literal__:{tag}")
            return
        if tag not in self.ALLOWED_TAGS:
            return
        safe_attrs = []
        attr_map = dict(attrs)
        math_type = attr_map.get("data-type")
        if tag in {"span", "div"} and math_type in {"inline-math", "block-math"}:
            safe_attrs = [
                ("data-type", math_type),
                ("data-latex", attr_map.get("data-latex", "") or ""),
            ]
        encoded = "".join(
            f' {name}="{html.escape(value, quote=True)}"'
            for name, value in safe_attrs
        )
        self.parts.append(f"<{tag}{encoded}>")
        if tag not in self.VOID_TAGS:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if self.stack and self.stack[-1].startswith("__literal__:"):
            literal_tag = self.stack[-1].split(":", 1)[1]
            self.parts.append(html.escape(f"</{tag}>"))
            if tag == literal_tag:
                self.stack.pop()
            return
        if tag not in self.stack:
            return
        while self.stack:
            current = self.stack.pop()
            self.parts.append(f"</{current}>")
            if current == tag:
                break

    def handle_data(self, data):
        self.parts.append(html.escape(data))

    def finish(self):
        while self.stack:
            tag = self.stack.pop()
            if tag.startswith("__literal__:"):
                self.parts.append(html.escape(f"</{tag.split(':', 1)[1]}>"))
            else:
                self.parts.append(f"</{tag}>")
        return "".join(self.parts)


def sanitize_rich_html(value):
    parser = _RichTextSanitizer()
    parser.feed(value or "")
    parser.close()
    return parser.finish()


def _rich_text_has_content(value):
    if not value:
        return False
    stripped = re.sub(r"<[^>]*>", "", value)
    return bool(html.unescape(stripped).strip() or 'data-latex="' in value)


def render_rich_html(value):
    # Keep Tiptap math nodes intact for the client-side KaTeX renderer.
    # The sanitizer preserves only the editor's safe data-type/data-latex attributes.
    return sanitize_rich_html(normalize_katex_markup(value or ""))


def _draft_key(question_id=None):
    return "new" if question_id is None else f"question:{int(question_id)}"


def _load_question_draft(question_id=None):
    row = get_db().execute(
        "SELECT payload_json, base_revision, updated_at FROM question_drafts "
        "WHERE user_uuid = ? AND draft_key = ?",
        (g.user["uuid"], _draft_key(question_id)),
    ).fetchone()
    if row is None:
        return None
    try:
        payload = json.loads(row["payload_json"])
    except (TypeError, json.JSONDecodeError):
        return None
    payload["choices"] = "\n".join(payload.get("choices") or [])
    payload["draft_updated_at"] = row["updated_at"]
    payload["draft_base_revision"] = row["base_revision"]
    return payload


def _save_question_draft(data, question_id=None, base_revision=None):
    payload = {field: data.get(field) for field in QUESTION_CONTENT_FIELDS}
    payload["choices"] = data.get("choices") or []
    payload["folder_uuid"] = data.get("folder_uuid")
    payload["summary"] = data.get("summary", "")
    get_db().execute(
        """
        INSERT INTO question_drafts (
            user_uuid, draft_key, question_id, base_revision, payload_json,
            created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ON CONFLICT(user_uuid, draft_key) DO UPDATE SET
            question_id = excluded.question_id,
            base_revision = excluded.base_revision,
            payload_json = excluded.payload_json,
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            g.user["uuid"], _draft_key(question_id), question_id, base_revision,
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        ),
    )
    get_db().commit()


def _delete_question_draft(question_id=None):
    get_db().execute(
        "DELETE FROM question_drafts WHERE user_uuid = ? AND draft_key = ?",
        (g.user["uuid"], _draft_key(question_id)),
    )


def _collect_question_data(form):
    data = {
        "title": form.get("title", "").strip(),
        "subject": form.get("subject", "").strip(),
        "grade": form.get("grade", "").strip(),
        "course": form.get("course", "").strip(),
        "curriculum": form.get("curriculum", "2015 개정 교육과정").strip(),
        "unit": form.get("unit", "").strip(),
        "achievement_standard": form.get("achievement_standard", "").strip(),
        "difficulty": form.get("difficulty", "중").strip(),
        "question_type": form.get("question_type", "단답형").strip(),
        "points": None,
        "passage": sanitize_rich_html(form.get("passage", "")),
        "prompt": sanitize_rich_html(form.get("prompt", "")),
        "examples": sanitize_rich_html(form.get("examples", "")),
        "choices": [
            sanitize_rich_html(value.strip())
            for value in form.get("choices", "").splitlines()
            if value.strip()
        ],
        "answer": sanitize_rich_html(form.get("answer", "")),
        "explanation": sanitize_rich_html(form.get("explanation", "")),
        "source_type": form.get("source_type", "기타").strip(),
        "source": form.get("source", "").strip(),
        "year": None,
        "exam": form.get("exam", "").strip(),
        "original_question_number": form.get("original_question_number", "").strip(),
        "page": None,
        "source_url": form.get("source_url", "").strip(),
        "license": form.get("license", "").strip(),
        "summary": form.get("summary", "").strip() or "문제 내용 수정",
        "folder_uuid": form.get("folder_uuid", "").strip() or None,
    }
    errors = []
    if data["subject"] not in SUBJECTS:
        errors.append("과목을 선택해 주세요.")
    if data["curriculum"] not in COURSE_OPTIONS:
        errors.append("교육과정을 선택해 주세요.")
    if data["difficulty"] not in DIFFICULTIES:
        errors.append("난이도를 선택해 주세요.")
    if data["question_type"] not in QUESTION_TYPES:
        errors.append("문항 유형을 선택해 주세요.")
    if data["source_type"] not in SOURCE_TYPES:
        errors.append("출처 유형을 선택해 주세요.")
    if data["license"] not in LICENSE_CHOICES:
        errors.append("라이선스를 선택해 주세요.")
    for field, label in (("grade", "학년"), ("unit", "단원")):
        if not data[field]:
            errors.append(f"{label}을(를) 입력해 주세요.")
    if not _rich_text_has_content(data["prompt"]):
        errors.append("문제 내용을 입력해 주세요.")
    if not _rich_text_has_content(data["answer"]):
        errors.append("정답을 입력해 주세요.")

    for field, label, maximum in (
        ("title", "문항 제목", 200), ("grade", "학년", 50), ("course", "교과목", 100),
        ("unit", "단원", 200), ("achievement_standard", "성취기준", 4000),
        ("passage", "지문", 20000), ("prompt", "문제 내용", 20000),
        ("examples", "보기", 10000), ("answer", "정답", 10000),
        ("explanation", "해설", 20000), ("source", "출처명", 300),
        ("exam", "시험 / 회차", 200), ("original_question_number", "원본 문항 번호", 50),
        ("source_url", "출처 URL", 1000), ("summary", "수정 요약", 200),
    ):
        if len(data[field]) > maximum:
            errors.append(f"{label}은 {maximum}자 이내로 입력해 주세요.")
    if len(data["choices"]) > 20:
        errors.append("선택지는 20개 이하로 입력해 주세요.")
    if any(len(choice) > 2000 for choice in data["choices"]):
        errors.append("각 선택지는 2,000자 이내로 입력해 주세요.")

    for field, label, maximum in (
        ("year", "연도", 2200), ("page", "페이지", 10000), ("points", "배점", 100)
    ):
        value = form.get(field, "").strip()
        if field == "points" and not value:
            value = "4"
        if not value:
            continue
        try:
            parsed = int(value)
            if not 1 <= parsed <= maximum:
                raise ValueError
            data[field] = parsed
        except ValueError:
            errors.append(f"{label}는 1에서 {maximum} 사이의 정수로 입력해 주세요.")

    if data["folder_uuid"] and get_db().execute(
        "SELECT 1 FROM folders WHERE uuid = ? AND user_uuid = ?",
        (data["folder_uuid"], g.user["uuid"]),
    ).fetchone() is None:
        errors.append("선택한 폴더가 없습니다.")
    return data, errors


def _form_values_from_data(data):
    values = dict(data or {})
    choices = values.get("choices") or []
    if not isinstance(choices, str):
        values["choices"] = "\n".join(choices)
    return values


def curriculum_standards():
    curriculum = request.args.get("curriculum", "2015 개정 교육과정")
    course = request.args.get("course", "").strip()
    unit = request.args.get("unit", "").strip()
    course_data = CURRICULUM_STANDARDS.get(curriculum, {}).get(course, {})
    return jsonify({
        "standards": course_data.get(unit, []),
        "units": list(course_data.keys()),
        "matched": bool(course_data.get(unit)),
    })


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
               questions.subject, questions.grade, questions.unit, questions.title,
               questions.course, questions.curriculum, questions.achievement_standard,
               questions.difficulty, questions.question_type, questions.points,
               questions.source_type, questions.exam, questions.original_question_number,
               questions.source_url, questions.license,
               questions.passage, questions.prompt, questions.examples,
               questions.choices, questions.answer, questions.explanation,
               questions.source, questions.year, questions.page, questions.created_at,
               COALESCE(question_notes.note, '') AS personal_note,
               users.display_name AS author_name,
               COALESCE(revisions.latest_revision, 1) AS revision_number
        FROM questions
        LEFT JOIN question_notes
          ON question_notes.question_id = questions.id
         AND question_notes.user_uuid = ?
        LEFT JOIN users ON users.uuid = questions.user_uuid
        LEFT JOIN (
            SELECT question_id, MAX(revision_number) AS latest_revision
            FROM question_revisions
            GROUP BY question_id
        ) AS revisions ON revisions.question_id = questions.id
        WHERE questions.subject = ?
    """
    parameters = [g.user["uuid"], selected_subject]
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
    query += " ORDER BY questions.id DESC"
    rows = get_db().execute(query, parameters).fetchall()
    questions = []
    for row in rows:
        question = dict(row)
        question["choices"] = [
            render_rich_html(strip_choice_label(choice))
            for choice in json.loads(question["choices"] or "[]")
        ]
        for field in ("passage", "prompt", "examples", "answer", "explanation"):
            question[field] = render_rich_html(question[field])
        question["display_id"] = format_question_number(question["id"])
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
    question=None, base_revision=None, draft_saved=False,
    draft_updated_at=None, draft_conflict=False,
):
    _, folder_options = get_folder_data("수학", g.user["uuid"])
    form_data = dict(form_data or {})
    return (
        render_template(
            "question_form.html",
            nav_items=NAV_ITEMS,
            subjects=SUBJECTS,
            folder_options=folder_options,
            form_data=form_data,
            errors=errors or [],
            mode=mode,
            question=question,
            question_number=(
                format_question_number(question["id"]) if question is not None else None
            ),
            base_revision=base_revision,
            question_types=QUESTION_TYPES,
            difficulties=DIFFICULTIES,
            source_types=SOURCE_TYPES,
            license_choices=LICENSE_CHOICES,
            curriculum_options=list(COURSE_OPTIONS.keys()),
            course_options=COURSE_OPTIONS,
            subject_course_options=SUBJECT_COURSE_OPTIONS,
            draft_saved=draft_saved,
            draft_updated_at=draft_updated_at or form_data.get("draft_updated_at"),
            draft_conflict=draft_conflict,
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
    if values.get("difficulty") not in DIFFICULTIES:
        values["difficulty"] = "중"
    if values.get("question_type") not in QUESTION_TYPES:
        values["question_type"] = "단답형"
    if values.get("curriculum") not in COURSE_OPTIONS:
        values["curriculum"] = "2015 개정 교육과정"
    if values.get("source_type") not in SOURCE_TYPES:
        values["source_type"] = "기타"
    return values


def new_question():
    if request.method == "GET":
        if request.args.get("discard_draft") == "1":
            _delete_question_draft()
            get_db().commit()
        draft = _load_question_draft()
        return render_question_form(
            draft or {}, draft_saved=request.args.get("draft_saved") == "1",
            draft_updated_at=(draft or {}).get("draft_updated_at"),
        )

    data, errors = _collect_question_data(request.form)
    # Personal folders are managed after publication from the question's
    # "문제 더보기" menu, not from the authoring form.
    data["folder_uuid"] = None
    if request.form.get("form_action") == "draft":
        _save_question_draft(data)
        return redirect(url_for("new_question", draft_saved=1))

    if errors:
        return render_question_form(
            _form_values_from_data(data), errors, 400
        )

    connection = get_db()
    try:
        connection.execute("BEGIN IMMEDIATE")
        cursor = connection.execute(
            """
            INSERT INTO questions (
                user_uuid, folder_uuid, subject, grade, unit, passage, prompt,
                examples, choices, answer, explanation, source, year, page,
                difficulty, question_type, points, course, curriculum,
                achievement_standard, source_type, exam, original_question_number,
                source_url, license, title
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                g.user["uuid"], data["folder_uuid"], data["subject"], data["grade"],
                data["unit"], data["passage"], data["prompt"], data["examples"],
                json.dumps(data["choices"], ensure_ascii=False), data["answer"],
                data["explanation"], data["source"], data["year"], data["page"],
                data["difficulty"], data["question_type"], data["points"],
                data["course"], data["curriculum"], data["achievement_standard"],
                data["source_type"], data["exam"], data["original_question_number"],
                data["source_url"], data["license"], data["title"],
            ),
        )
        question_id = cursor.lastrowid
        question = connection.execute(
            "SELECT * FROM questions WHERE id = ?", (question_id,)
        ).fetchone()
        _insert_question_revision(
            connection, question_id, 1, g.user["uuid"], "최초 등록",
            _question_snapshot(question),
        )
        _delete_question_draft()
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return redirect(url_for("main", added=1, subject=data["subject"]))


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
        if request.args.get("discard_draft") == "1":
            _delete_question_draft(question_id)
            connection.commit()
        draft = _load_question_draft(question_id)
        conflict = bool(
            draft is not None
            and draft.get("draft_base_revision") is not None
            and draft["draft_base_revision"] != latest
        )
        values = draft if draft is not None else _question_form_values(question)
        form_base_revision = (
            draft.get("draft_base_revision") or latest
            if draft is not None else latest
        )
        return render_question_form(
            values, mode="edit", question=question, base_revision=form_base_revision,
            draft_saved=request.args.get("draft_saved") == "1",
            draft_updated_at=(draft or {}).get("draft_updated_at"),
            draft_conflict=conflict,
        )

    data, errors = _collect_question_data(request.form)
    try:
        base_revision = int(request.form.get("base_revision", ""))
    except (TypeError, ValueError):
        abort(400, description="수정 기준 버전이 올바르지 않습니다.")

    if request.form.get("form_action") == "draft":
        _save_question_draft(data, question_id, base_revision)
        return redirect(url_for("edit_question", question_id=question_id, draft_saved=1))

    if errors:
        return render_question_form(
            _form_values_from_data(data), errors, 400, mode="edit",
            question=question, base_revision=base_revision,
        )

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
            return render_question_form(
                _form_values_from_data(data), errors, 409, mode="edit",
                question=current, base_revision=base_revision,
            )

        next_revision = latest + 1
        connection.execute(
            """
            UPDATE questions
            SET subject = ?, grade = ?, unit = ?, passage = ?, prompt = ?,
                examples = ?, choices = ?, answer = ?, explanation = ?,
                source = ?, year = ?, page = ?, difficulty = ?, question_type = ?,
                points = ?, course = ?, curriculum = ?, achievement_standard = ?,
                source_type = ?, exam = ?, original_question_number = ?,
                source_url = ?, license = ?, title = ?
            WHERE id = ?
            """,
            (
                data["subject"], data["grade"], data["unit"], data["passage"],
                data["prompt"], data["examples"],
                json.dumps(data["choices"], ensure_ascii=False),
                data["answer"], data["explanation"], data["source"],
                data["year"], data["page"], data["difficulty"],
                data["question_type"], data["points"], data["course"],
                data["curriculum"], data["achievement_standard"],
                data["source_type"], data["exam"], data["original_question_number"],
                data["source_url"], data["license"], data["title"], question_id,
            ),
        )
        snapshot = {field: data[field] for field in QUESTION_CONTENT_FIELDS}
        _insert_question_revision(
            connection, question_id, next_revision, g.user["uuid"],
            data["summary"], snapshot,
        )
        _delete_question_draft(question_id)
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
        question_number=format_question_number(question["id"]),
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
        defaults = {
            "title": "",
            "course": "",
            "curriculum": "2015 개정 교육과정",
            "achievement_standard": "",
            "difficulty": "중",
            "question_type": "단답형",
            "points": 4,
            "source_type": "기타",
            "exam": "",
            "original_question_number": "",
            "source_url": "",
            "license": "",
        }
        for field, default in defaults.items():
            snapshot.setdefault(field, default)
        next_revision = latest + 1
        connection.execute(
            """
            UPDATE questions
            SET subject = ?, grade = ?, unit = ?, passage = ?, prompt = ?,
                examples = ?, choices = ?, answer = ?, explanation = ?,
                source = ?, year = ?, page = ?, difficulty = ?, question_type = ?,
                points = ?, course = ?, curriculum = ?, achievement_standard = ?,
                source_type = ?, exam = ?, original_question_number = ?,
                source_url = ?, license = ?, title = ?
            WHERE id = ?
            """,
            (
                snapshot["subject"], snapshot["grade"], snapshot["unit"],
                snapshot["passage"], snapshot["prompt"], snapshot["examples"],
                json.dumps(snapshot["choices"], ensure_ascii=False),
                snapshot["answer"], snapshot["explanation"], snapshot["source"],
                snapshot["year"], snapshot["page"], snapshot.get("difficulty", "중"),
                snapshot.get("question_type", "단답형"), snapshot.get("points", 4), snapshot.get("course", ""),
                snapshot.get("curriculum", "2015 개정 교육과정"), snapshot.get("achievement_standard", ""),
                snapshot.get("source_type", "기타"), snapshot.get("exam", ""),
                snapshot.get("original_question_number", ""), snapshot.get("source_url", ""),
                snapshot.get("license", ""), snapshot.get("title", ""), question_id,
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


def save_question_note(question_id):
    _question_row(question_id)
    note = request.form.get("personal_note", "").strip()
    if len(note) > 5000:
        abort(400, description="개인 메모는 5,000자 이내로 입력해 주세요.")
    get_db().execute(
        """
        INSERT INTO question_notes (user_uuid, question_id, note, updated_at)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(user_uuid, question_id) DO UPDATE SET
            note = excluded.note,
            updated_at = CURRENT_TIMESTAMP
        """,
        (g.user["uuid"], question_id, note),
    )
    get_db().commit()
    return redirect(url_for("main", subject=request.form.get("subject", "수학")))


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
        "/curriculum/standards",
        endpoint="curriculum_standards",
        view_func=login_required(curriculum_standards),
        methods=["GET"],
    )
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
        "/questions/<int:question_id>/notes",
        endpoint="save_question_note",
        view_func=login_required(save_question_note),
        methods=["POST"],
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
