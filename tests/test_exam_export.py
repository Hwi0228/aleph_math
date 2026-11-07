import io
import json
import sqlite3
import tempfile
import unittest
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

from app import app


HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"


class ExamExportRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.original_config = {
            key: app.config.get(key)
            for key in ("DATABASE", "DATABASE_TYPE", "DATABASE_URL", "TESTING", "SECRET_KEY")
        }
        app.config.update(
            TESTING=True,
            DATABASE_TYPE="sqlite",
            DATABASE_URL="",
            DATABASE=str(Path(self.temp_directory.name) / "exam-export.sqlite3"),
            SECRET_KEY="exam-export-test-secret",
        )
        self.client = app.test_client()
        self.client.get("/signup")
        with self.client.session_transaction() as user_session:
            token = user_session["csrf_token"]
        response = self.client.post(
            "/signup",
            data={
                "_csrf_token": token,
                "email": "exam-export@example.com",
                "display_name": "시험지 테스트",
                "password": "long-test-password",
                "password_confirm": "long-test-password",
            },
        )
        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as user_session:
            self.user_uuid = user_session["user_uuid"]

    def tearDown(self):
        app.config.update(self.original_config)
        self.temp_directory.cleanup()

    def csrf_token(self):
        self.client.get("/questions")
        with self.client.session_transaction() as user_session:
            return user_session["csrf_token"]

    def add_question(self, prompt, answer, *, choices=None, passage="", examples=""):
        connection = sqlite3.connect(app.config["DATABASE"])
        try:
            cursor = connection.execute(
                """
                INSERT INTO questions (
                    user_uuid, subject, grade, unit, passage, prompt, examples,
                    choices, answer, explanation, source
                )
                VALUES (?, '수학', '고1', '다항식', ?, ?, ?, ?, ?, ?, '시험지 테스트')
                """,
                (
                    self.user_uuid,
                    passage,
                    prompt,
                    examples,
                    json.dumps(choices or [], ensure_ascii=False),
                    answer,
                    "해설 " + answer,
                ),
            )
            connection.commit()
            return cursor.lastrowid
        finally:
            connection.close()

    def export(self, question_ids, *, include_answers=False):
        data = {
            "_csrf_token": self.csrf_token(),
            "exam_title": "1학기 수학 시험",
            "question_ids": [str(question_id) for question_id in question_ids],
        }
        if include_answers:
            data["include_answers"] = "1"
        return self.client.post("/exams/export", data=data)

    def get_section_text(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertIn(".hwpx", response.headers.get("Content-Disposition", ""))
        with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
            names = archive.namelist()
            self.assertEqual(names[0], "mimetype")
            self.assertEqual(archive.read("mimetype"), b"application/hwp+zip")
            self.assertEqual(archive.getinfo("mimetype").compress_type, zipfile.ZIP_STORED)
            section_xml = archive.read("Contents/section0.xml")
        root = ET.fromstring(section_xml)
        return "".join(node.text or "" for node in root.iter(f"{{{HP}}}t"))

    def test_selected_questions_export_as_hwpx_in_submitted_order_without_answers(self):
        first_id = self.add_question(
            "<p>첫 번째 문제 $x^2$</p>",
            "①",
            choices=["하나", "둘"],
            passage="<p>공통 지문</p>",
            examples="<p>보기 내용</p>",
        )
        second_id = self.add_question("<p>두 번째 문제</p>", "②")

        response = self.export([second_id, first_id])
        text = self.get_section_text(response)

        self.assertIn("1학기 수학 시험", text)
        self.assertIn("이름:", text)
        self.assertIn("공통 지문", text)
        self.assertIn("보기 내용", text)
        self.assertIn("$x^2$", text)
        self.assertIn("① 하나", text)
        self.assertLess(text.index("두 번째 문제"), text.index("첫 번째 문제"))
        self.assertNotIn("정답: ①", text)
        self.assertNotIn("해설 ①", text)

        with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
            section_root = ET.fromstring(archive.read("Contents/section0.xml"))
        paragraphs = [
            "".join(node.text or "" for node in paragraph.iter(f"{{{HP}}}t"))
            for paragraph in section_root.findall(f"{{{HP}}}p")
        ]
        question_headers = [
            paragraph for paragraph in paragraphs
            if paragraph.startswith("QUESTION ")
        ]
        self.assertEqual(len(question_headers), 2)
        self.assertTrue(question_headers[0].startswith("QUESTION 01"))
        self.assertTrue(question_headers[1].startswith("QUESTION 02"))
        self.assertLess(text.index("QUESTION 01"), text.index("두 번째 문제"))
        self.assertLess(text.index("QUESTION 02"), text.index("첫 번째 문제"))
        self.assertIn("HEXORA", text)
        self.assertIn("MATHEMATICS  ·  ASSESSMENT", text)
        self.assertIn("이름:", text)
        self.assertIn("〈지문〉", text)
        self.assertIn("〈보기〉", text)

        styled_text = []
        for paragraph in section_root.findall(f"{{{HP}}}p"):
            paragraph_text = "".join(
                node.text or "" for node in paragraph.iter(f"{{{HP}}}t")
            )
            for run in paragraph.findall(f"{{{HP}}}run"):
                styled_text.append((run.get("charPrIDRef"), paragraph_text))
            if paragraph_text == "HEXORA":
                self.assertEqual(paragraph.get("paraPrIDRef"), "39")
        self.assertIn(("21", "HEXORA"), styled_text)
        self.assertTrue(any(
            char_style == "45" and paragraph_text.startswith("QUESTION 01")
            for char_style, paragraph_text in styled_text
        ))

    def test_teacher_export_can_include_answer_and_explanation(self):
        question_id = self.add_question("<p>교사용 문항</p>", "④")
        response = self.export([question_id], include_answers=True)
        text = self.get_section_text(response)

        self.assertIn("정답 및 해설", text)
        self.assertIn("정답: ④", text)
        self.assertIn("해설:", text)
        self.assertIn("해설 ④", text)
        with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
            section_root = ET.fromstring(archive.read("Contents/section0.xml"))
        answer_heading = next(
            paragraph
            for paragraph in section_root.findall(f"{{{HP}}}p")
            if "".join(node.text or "" for node in paragraph.iter(f"{{{HP}}}t"))
            == "ANSWER KEY  ·  TEACHER COPY"
        )
        self.assertEqual(answer_heading.get("pageBreak"), "1")
        self.assertEqual(
            answer_heading.find(f"{{{HP}}}run").get("charPrIDRef"),
            "46",
        )

    def test_exam_export_rejects_empty_or_missing_selection(self):
        empty = self.client.post(
            "/exams/export",
            data={"_csrf_token": self.csrf_token(), "exam_title": "빈 시험지"},
        )
        self.assertEqual(empty.status_code, 400)

        missing = self.export([999999])
        self.assertEqual(missing.status_code, 404)

    def test_exam_export_requires_login(self):
        anonymous = app.test_client()
        response = anonymous.get("/questions")
        self.assertEqual(response.status_code, 302)
        anonymous.get("/login")
        with anonymous.session_transaction() as user_session:
            token = user_session["csrf_token"]
        response = anonymous.post("/exams/export", data={"_csrf_token": token})
        self.assertEqual(response.status_code, 302)


class ExamExportFrontendTests(unittest.TestCase):
    def test_question_bank_selection_controls_submit_selected_ids(self):
        root = Path(__file__).resolve().parents[1]
        template = (root / "static/templates/main.html").read_text(encoding="utf-8")

        self.assertIn('id="exam-export-form"', template)
        self.assertIn('action="{{ url_for(\'export_exam_hwpx\') }}"', template)
        self.assertIn('form="exam-export-form" name="question_ids" value="{{ question.id }}"', template)
        self.assertIn('id="select-all-questions"', template)
        self.assertIn("updateQuestionSelection", template)
        self.assertIn('name="include_answers" value="1"', template)


if __name__ == "__main__":
    unittest.main()
