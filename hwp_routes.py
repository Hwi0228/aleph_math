import json
import xml.etree.ElementTree as ET
from pathlib import Path

from flask import g, redirect, render_template, request, url_for

from auth import login_required
from constants import NAV_ITEMS, SUBJECTS
from database import get_db
from parse_hwp import parse_questions_from_xml, strip_choice_label
from parse_rhwp import MAX_DOCUMENT_BYTES, parse_questions_from_rhwp_bytes


MAX_SECTION_XML_SIZE = 16 * 1024 * 1024
MAX_IMPORTED_QUESTIONS = 500

LICENSE_CHOICES = {
    "ORIGINAL": "직접 제작 (권리 보유)",
    "CC-BY-4.0": "CC BY 4.0",
    "CC-BY-SA-4.0": "CC BY-SA 4.0",
    "CC0-1.0": "CC0 1.0 / 퍼블릭 도메인",
    "PERMISSION": "권리자로부터 공개 허가 받음",
}
SOURCE_TYPES = (
    "직접 제작", "교재 및 참고서", "학교 시험", "수능 및 모의평가",
    "공개 데이터셋", "기타",
)


def _validated_import(form):
    errors = []
    try:
        payload = json.loads(form.get("questions_json", ""))
    except (TypeError, ValueError):
        return None, None, errors, "파싱 결과가 올바르지 않습니다. 파일을 다시 업로드해 주세요."
    questions = payload.get("questions") if isinstance(payload, dict) else payload

    if not isinstance(questions, list) or not 1 <= len(questions) <= MAX_IMPORTED_QUESTIONS:
        return None, None, errors, "등록할 문항이 없거나 허용 개수(500개)를 초과했습니다."

    subject = form.get("subject", "").strip()
    grade = form.get("grade", "").strip()
    unit = form.get("unit", "").strip()
    source = form.get("source", "").strip()
    license_value = form.get("license", "").strip()
    source_type = form.get("source_type", "기타").strip() or "기타"
    year_value = form.get("year", "").strip()

    if subject not in SUBJECTS:
        errors.append("과목을 선택해 주세요.")
    if license_value not in LICENSE_CHOICES:
        errors.append("문항 공개에 사용할 라이선스를 선택해 주세요.")
    if source_type not in SOURCE_TYPES:
        errors.append("출처 유형이 올바르지 않습니다.")
    for value, label in ((grade, "학년"), (unit, "단원"), (source, "출처")):
        if not value:
            errors.append(f"{label}을(를) 입력해 주세요.")
    for value, label, maximum in (
        (grade, "학년", 50),
        (unit, "단원", 200),
        (source, "출처", 300),
    ):
        if len(value) > maximum:
            errors.append(f"{label}은 {maximum}자 이내로 입력해 주세요.")

    year = None
    if year_value:
        try:
            year = int(year_value)
            if not 1 <= year <= 2200:
                raise ValueError
        except ValueError:
            errors.append("연도는 1에서 2200 사이의 정수로 입력해 주세요.")

    selected_values = form.getlist("selected_questions")
    selected_indices = []
    for value in selected_values:
        try:
            index = int(value)
        except (TypeError, ValueError):
            errors.append("선택한 문항이 올바르지 않습니다.")
            break
        if not 0 <= index < len(questions):
            errors.append("선택한 문항이 올바르지 않습니다.")
            break
        if index not in selected_indices:
            selected_indices.append(index)
    if not selected_indices:
        errors.append("DB에 등록할 문항을 하나 이상 선택해 주세요.")

    normalized_questions = []
    for question in questions:
        if not isinstance(question, dict):
            errors.append("파싱 결과의 문항 형식이 올바르지 않습니다.")
            break
        prompt = question.get("question")
        answer = question.get("answer")
        explanation = question.get("explanation", "")
        choices = question.get("choices", [])
        if (
            not isinstance(prompt, str)
            or not prompt.strip()
            or len(prompt) > 10000
            or not isinstance(answer, str)
            or not answer.strip()
            or len(answer) > 2000
            or not isinstance(explanation, str)
            or len(explanation) > 10000
            or not isinstance(choices, list)
            or len(choices) > 20
            or any(
                not isinstance(choice, str) or len(choice) > 2000
                for choice in choices
            )
        ):
            errors.append("문항 내용, 정답, 해설 또는 선택지의 형식이 올바르지 않습니다.")
            break
        normalized_questions.append(
            {
                "number": len(normalized_questions) + 1,
                "question": prompt.strip(),
                "answer": answer.strip(),
                "explanation": explanation.strip(),
                "choices": [strip_choice_label(choice) for choice in choices],
            }
        )

    if errors:
        return normalized_questions or None, None, errors, None
    metadata = {
        "subject": subject,
        "grade": grade,
        "unit": unit,
        "source": source,
        "folder_uuid": None,
        "year": year,
        "license": license_value,
        "source_type": source_type,
    }
    return normalized_questions, (metadata, selected_indices), errors, None


def _render_page(questions=None, result_json=None, error=None, form_data=None):
    folders = get_db().execute(
        "SELECT uuid, name FROM folders WHERE user_uuid = ? ORDER BY name COLLATE NOCASE, uuid",
        (g.user["uuid"],),
    ).fetchall()
    return render_template(
        "hwp_parser.html",
        nav_items=NAV_ITEMS,
        subjects=SUBJECTS,
        folders=folders,
        license_choices=LICENSE_CHOICES,
        source_types=SOURCE_TYPES,
        questions=questions,
        result_json=result_json,
        error=error,
        form_data=form_data or {},
    )


def _get_section_xml(filename: str, content: bytes) -> bytes:
    if Path(filename).suffix.lower() != ".xml":
        raise ValueError("단독 HWPML XML 파일만 이 경로에서 처리할 수 있습니다.")
    if not content:
        raise ValueError("파싱할 XML 내용이 비어 있습니다.")
    if len(content) > MAX_SECTION_XML_SIZE:
        raise ValueError("XML 크기가 허용 한도(16MB)를 초과합니다.")
    return content


def hwp_parser_page():
    questions = None
    result_json = None
    error = None

    if request.method == "POST":
        if request.form.get("action") == "save":
            result_json = request.form.get("questions_json", "")
            questions, selection, errors, error = _validated_import(request.form)
            if not error and errors:
                error = " ".join(errors)
            elif not error:
                metadata, selected_indices = selection
                connection = get_db()
                with connection:
                    for index in selected_indices:
                        imported = questions[index]
                        cursor = connection.execute(
                            """
                            INSERT INTO questions (
                                user_uuid, folder_uuid, subject, grade, unit, title,
                                prompt, choices, answer, explanation, source, year,
                                source_type, license
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                g.user["uuid"], None, metadata["subject"],
                                metadata["grade"], metadata["unit"], "",
                                imported["question"],
                                json.dumps(imported["choices"], ensure_ascii=False),
                                imported["answer"], imported["explanation"],
                                metadata["source"], metadata["year"],
                                metadata["source_type"], metadata["license"],
                            ),
                        )
                        question_id = cursor.lastrowid
                        snapshot = {
                            "title": "",
                            "subject": metadata["subject"],
                            "grade": metadata["grade"],
                            "course": "",
                            "curriculum": "2015 개정 교육과정",
                            "unit": metadata["unit"],
                            "achievement_standard": "",
                            "difficulty": "중",
                            "question_type": "객관식" if imported["choices"] else "단답형",
                            "points": 4,
                            "passage": "",
                            "prompt": imported["question"],
                            "examples": "",
                            "choices": imported["choices"],
                            "answer": imported["answer"],
                            "explanation": imported["explanation"],
                            "source_type": metadata["source_type"],
                            "source": metadata["source"],
                            "year": metadata["year"],
                            "exam": "",
                            "original_question_number": str(imported["number"]),
                            "page": None,
                            "source_url": "",
                            "license": metadata["license"],
                        }
                        connection.execute(
                            """
                            INSERT INTO question_revisions (
                                question_id, revision_number, editor_uuid,
                                summary, snapshot_json
                            )
                            VALUES (?, 1, ?, ?, ?)
                            """,
                            (
                                question_id, g.user["uuid"], "HWP 문서에서 가져옴",
                                json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")),
                            ),
                        )
                return redirect(
                    url_for(
                        "main",
                        added=1,
                        subject=metadata["subject"],
                        folder=metadata["folder_uuid"],
                    )
                )
            form_data = request.form
            return _render_page(
                questions=questions,
                result_json=result_json,
                error=error,
                form_data=form_data,
            )

        uploaded = request.files.get("document")
        if uploaded is None or not uploaded.filename:
            error = "파싱할 HWP, HWPX 또는 section0.xml 파일을 선택해 주세요."
        else:
            try:
                content = uploaded.read(MAX_DOCUMENT_BYTES + 1)
                if len(content) > MAX_DOCUMENT_BYTES:
                    raise ValueError("문서 크기가 허용 한도(12MB)를 초과합니다.")

                extension = Path(uploaded.filename).suffix.lower()
                if extension in {".hwp", ".hwpx"}:
                    questions = parse_questions_from_rhwp_bytes(
                        content,
                        filename=Path(uploaded.filename).name,
                    )
                elif extension == ".xml":
                    xml_content = _get_section_xml(uploaded.filename, content)
                    questions = parse_questions_from_xml(xml_content)
                else:
                    raise ValueError("HWP, HWPX 또는 HWPML section0.xml 파일만 업로드할 수 있습니다.")

                result_json = json.dumps(
                    {"questions": questions},
                    ensure_ascii=False,
                    indent=2,
                )
            except (ValueError, RuntimeError, ET.ParseError) as exc:
                error = str(exc)

    return _render_page(questions=questions, result_json=result_json, error=error)


def register_hwp_routes(app):
    app.add_url_rule(
        "/hwp",
        endpoint="hwp_parser_page",
        view_func=login_required(hwp_parser_page),
        methods=["GET", "POST"],
    )
