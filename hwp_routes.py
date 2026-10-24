import io
import json
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

from flask import g, redirect, render_template, request, url_for

from auth import login_required
from constants import NAV_ITEMS, SUBJECTS
from database import get_db
from parse_hwp import parse_questions_from_xml, strip_choice_label


MAX_SECTION_XML_SIZE = 16 * 1024 * 1024
MAX_IMPORTED_QUESTIONS = 500


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
    folder_uuid = form.get("folder_uuid", "").strip() or None
    year_value = form.get("year", "").strip()

    if subject not in SUBJECTS:
        errors.append("과목을 선택해 주세요.")
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

    if folder_uuid and get_db().execute(
        "SELECT 1 FROM folders WHERE uuid = ? AND user_uuid = ?",
        (folder_uuid, g.user["uuid"]),
    ).fetchone() is None:
        errors.append("선택한 폴더가 없습니다.")

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
        "folder_uuid": folder_uuid,
        "year": year,
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
        questions=questions,
        result_json=result_json,
        error=error,
        form_data=form_data or {},
    )


def _get_section_xml(filename: str, content: bytes) -> bytes:
    extension = Path(filename).suffix.lower()
    if extension == ".xml":
        xml_content = content
    elif extension == ".hwpx":
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                try:
                    section = archive.getinfo("Contents/section0.xml")
                except KeyError as exc:
                    raise ValueError(
                        "HWPX 안에서 Contents/section0.xml을 찾을 수 없습니다."
                    ) from exc
                if section.file_size > MAX_SECTION_XML_SIZE:
                    raise ValueError("문서의 XML 크기가 허용 한도(16MB)를 초과합니다.")
                with archive.open(section) as section_file:
                    xml_content = section_file.read(MAX_SECTION_XML_SIZE + 1)
        except zipfile.BadZipFile as exc:
            raise ValueError("올바른 HWPX 파일이 아닙니다.") from exc
    else:
        raise ValueError("HWPX 또는 section0.xml 파일만 업로드할 수 있습니다.")

    if not xml_content:
        raise ValueError("파싱할 XML 내용이 비어 있습니다.")
    if len(xml_content) > MAX_SECTION_XML_SIZE:
        raise ValueError("XML 크기가 허용 한도(16MB)를 초과합니다.")
    return xml_content


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
                    connection.executemany(
                        """
                        INSERT INTO questions (
                            user_uuid, folder_uuid, subject, grade, unit, prompt,
                            choices, answer, explanation, source, year
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        [
                            (
                                g.user["uuid"],
                                metadata["folder_uuid"],
                                metadata["subject"],
                                metadata["grade"],
                                metadata["unit"],
                                questions[index]["question"],
                                json.dumps(questions[index]["choices"], ensure_ascii=False),
                                questions[index]["answer"],
                                questions[index]["explanation"],
                                metadata["source"],
                                metadata["year"],
                            )
                            for index in selected_indices
                        ],
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
            error = "파싱할 HWPX 또는 section0.xml 파일을 선택해 주세요."
        else:
            try:
                xml_content = _get_section_xml(uploaded.filename, uploaded.read())
                questions = parse_questions_from_xml(xml_content)
                if not questions:
                    error = "문항을 찾지 못했습니다. HWPML section0.xml인지 확인해 주세요."
                    questions = None
                else:
                    result_json = json.dumps(
                        {"questions": questions},
                        ensure_ascii=False,
                        indent=2,
                    )
            except (ValueError, ET.ParseError) as exc:
                error = str(exc)

    return _render_page(questions=questions, result_json=result_json, error=error)


def register_hwp_routes(app):
    app.add_url_rule(
        "/hwp",
        endpoint="hwp_parser_page",
        view_func=login_required(hwp_parser_page),
        methods=["GET", "POST"],
    )
