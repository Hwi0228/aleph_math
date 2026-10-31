import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import app


SAMPLE_XML = """\
<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section"
         xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">
  <hp:tbl><hp:tr><hp:tc><hp:subList>
    <hp:p styleIDRef="45"><hp:run>
      <hp:t>[정답] ②[해설]더하면 </hp:t>
      <hp:equation><hp:script>1+1 `</hp:script></hp:equation>
      <hp:t>문제 본문</hp:t>
    </hp:run></hp:p>
    <hp:p styleIDRef="57"><hp:run><hp:t>[정답] ②</hp:t></hp:run></hp:p>
    <hp:p styleIDRef="57"><hp:run><hp:t>[해설]</hp:t></hp:run></hp:p>
    <hp:p styleIDRef="57"><hp:run><hp:t>더하면 </hp:t>
      <hp:equation><hp:script>1+1 `</hp:script></hp:equation>
    </hp:run></hp:p>
    <hp:p styleIDRef="52"><hp:run><hp:t>① 하나 ② 둘</hp:t></hp:run></hp:p>
  </hp:subList></hp:tc></hp:tr></hp:tbl>
</hs:sec>
""".encode("utf-8")


class HwpParserRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        app.config.update(
            TESTING=True,
            DATABASE=str(Path(self.temp_directory.name) / "hwp-tests.sqlite3"),
            SECRET_KEY="test-session-secret",
        )
        self.client = app.test_client()
        self.client.get("/signup")
        with self.client.session_transaction() as user_session:
            token = user_session["csrf_token"]
        response = self.client.post(
            "/signup",
            data={
                "_csrf_token": token,
                "email": "hwp@example.com",
                "display_name": "HWP 테스트",
                "password": "long-test-password",
                "password_confirm": "long-test-password",
            },
        )
        self.assertEqual(response.status_code, 302)

    def tearDown(self):
        self.temp_directory.cleanup()

    def csrf_token(self):
        self.client.get("/hwp")
        with self.client.session_transaction() as user_session:
            return user_session["csrf_token"]

    def test_hwp_parser_page_is_in_navigation(self):
        response = self.client.get("/hwp")

        self.assertEqual(response.status_code, 200)
        self.assertIn("HWP 파싱".encode(), response.data)
        self.assertIn(b'name="document"', response.data)
        self.assertIn(b'accept=".hwp,.hwpx,.xml,application/xml,text/xml"', response.data)
        self.assertIn(b'href="/hwp"', self.client.get("/").data)

    def test_existing_question_data_survives_explanation_column_migration(self):
        with self.client.session_transaction() as user_session:
            user_uuid = user_session["user_uuid"]
        connection = sqlite3.connect(app.config["DATABASE"])
        try:
            connection.execute("ALTER TABLE questions DROP COLUMN explanation")
            connection.execute(
                """
                INSERT INTO questions (
                    user_uuid, subject, grade, unit, prompt, choices, answer,
                    source
                )
                VALUES (?, '수학', '고1', '대수', ?, ?, '①', '기존 출처')
                """,
                (
                    user_uuid,
                    r"기존 문항 $\frac{1}{2}$",
                    json.dumps([r"① $\frac{1}{3}$"], ensure_ascii=False),
                ),
            )
            connection.commit()
        finally:
            connection.close()

        response = self.client.get("/hwp")

        self.assertEqual(response.status_code, 200)
        connection = sqlite3.connect(app.config["DATABASE"])
        try:
            row = connection.execute(
                "SELECT prompt, choices, explanation FROM questions"
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(
            row,
            (
                r"기존 문항 $\dfrac{1}{2}$",
                json.dumps([r"① $\dfrac{1}{3}$"], ensure_ascii=False),
                "",
            ),
        )
        question_response = self.client.get("/questions")
        self.assertIn(r"\dfrac{1}{2}".encode(), question_response.data)
        self.assertIn(r"\dfrac{1}{3}".encode(), question_response.data)

    def test_existing_hwp_equations_are_normalized_for_katex_display(self):
        with self.client.session_transaction() as user_session:
            user_uuid = user_session["user_uuid"]
        connection = sqlite3.connect(app.config["DATABASE"])
        try:
            connection.execute(
                """
                INSERT INTO questions (
                    user_uuid, subject, grade, unit, prompt, choices, answer,
                    explanation, source
                )
                VALUES (?, '수학', '고1', '대수', ?, ?, '①', ?, '기출')
                """,
                (
                    user_uuid,
                    r"$\eqalign{ & \mathrm{가입비 15000원} \\ & \mathrm{- 추가 요금 없이 가능} }$",
                    json.dumps(
                        [r"$\left LBRACE \dfrac{4}{3} \right RBRACE div 2$"],
                        ensure_ascii=False,
                    ),
                    r"$\eqalign{ & x = \dfrac{1}{2} \\ & = \dfrac{2}{4} }$",
                ),
            )
            connection.commit()
        finally:
            connection.close()

        response = self.client.get("/questions")

        self.assertEqual(response.status_code, 200)
        self.assertIn(r"\begin{aligned}".encode(), response.data)
        self.assertIn(r"\text{가입비 15000원}".encode(), response.data)
        self.assertIn(
            r"$\left \{ \dfrac{4}{3} \right \} \div 2$".encode(),
            response.data,
        )
        self.assertNotIn(r"\eqalign".encode(), response.data)

    def test_uploading_section_xml_renders_parsed_questions(self):
        response = self.client.post(
            "/hwp",
            data={
                "_csrf_token": self.csrf_token(),
                "document": (io.BytesIO(SAMPLE_XML), "section0.xml"),
            },
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("추출 결과 · 1문항".encode(), response.data)
        self.assertIn("문제 본문".encode(), response.data)
        self.assertIn("더하면 $1+1$".encode(), response.data)
        self.assertIn("JSON 다운로드".encode(), response.data)

    def test_uploading_hwpx_uses_rhwp_parser(self):
        parsed = [{
            "number": 1,
            "question": "문제 본문",
            "choices": ["하나", "둘"],
            "answer": "②",
            "explanation": "풀이 $1+1$",
        }]
        with patch("hwp_routes.parse_questions_from_rhwp_bytes", return_value=parsed) as parser:
            response = self.client.post(
                "/hwp",
                data={
                    "_csrf_token": self.csrf_token(),
                    "document": (io.BytesIO(b"hwpx bytes"), "exam.hwpx"),
                },
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("추출 결과 · 1문항".encode(), response.data)
        self.assertIn("문제 본문".encode(), response.data)
        parser.assert_called_once()

    def test_uploading_hwp_uses_rhwp_parser(self):
        parsed = [{
            "number": 1,
            "question": "HWP 문제 본문",
            "choices": [],
            "answer": "①",
            "explanation": "",
        }]
        with patch("hwp_routes.parse_questions_from_rhwp_bytes", return_value=parsed) as parser:
            response = self.client.post(
                "/hwp",
                data={
                    "_csrf_token": self.csrf_token(),
                    "document": (io.BytesIO(b"hwp bytes"), "exam.hwp"),
                },
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("HWP 문제 본문".encode(), response.data)
        parser.assert_called_once()

    def test_selected_parsed_questions_are_saved_with_shared_metadata(self):
        questions_json = json.dumps(
            {
                "questions": [
                    {
                        "number": 1,
                        "question": "문제 본문 $x^2$",
                        "choices": ["① 하나", "② 둘"],
                        "answer": "②",
                        "explanation": "풀이 $x^2$",
                    },
                    {
                        "number": 2,
                        "question": "선택하지 않을 문항",
                        "choices": [],
                        "answer": "①",
                        "explanation": "",
                    },
                ]
            },
            ensure_ascii=False,
        )
        response = self.client.post(
            "/hwp",
            data={
                "_csrf_token": self.csrf_token(),
                "action": "save",
                "questions_json": questions_json,
                "selected_questions": ["0"],
                "subject": "수학",
                "grade": "고1",
                "unit": "다항식",
                "source": "2026학년도 10월 학력평가",
                "year": "2026",
                "license": "ORIGINAL",
                "source_type": "수능 및 모의평가",
            },
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("문제를 등록했습니다.".encode(), response.data)
        self.assertIn("풀이 $x^2$".encode(), response.data)
        self.assertIn(">①</span>하나".encode(), response.data)
        self.assertNotIn(">①</span>① 하나".encode(), response.data)
        connection = sqlite3.connect(app.config["DATABASE"])
        try:
            rows = connection.execute(
                """
                SELECT subject, grade, unit, prompt, choices, answer, explanation,
                       source, year, license
                FROM questions
                """
            ).fetchall()
        finally:
            connection.close()
        self.assertEqual(
            rows,
            [
                (
                    "수학",
                    "고1",
                    "다항식",
                    "문제 본문 $x^2$",
                    '["하나", "둘"]',
                    "②",
                    "풀이 $x^2$",
                    "2026학년도 10월 학력평가",
                    2026,
                    "ORIGINAL",
                )
            ],
        )
        connection = sqlite3.connect(app.config["DATABASE"])
        try:
            revision_count = connection.execute(
                "SELECT COUNT(*) FROM question_revisions"
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(revision_count, 1)
        history_response = self.client.get("/questions/1/history")
        self.assertIn("HWP 문서에서 가져옴".encode(), history_response.data)

    def test_invalid_import_metadata_keeps_preview_without_saving(self):
        response = self.client.post(
            "/hwp",
            data={
                "_csrf_token": self.csrf_token(),
                "action": "save",
                "questions_json": json.dumps(
                    {
                        "questions": [
                            {
                                "number": 1,
                                "question": "미리보기 문제",
                                "choices": [],
                                "answer": "①",
                                "explanation": "",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                "selected_questions": ["0"],
                "subject": "수학",
                "grade": "",
                "unit": "다항식",
                "source": "출처",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("학년을(를) 입력해 주세요.".encode(), response.data)
        self.assertIn("미리보기 문제".encode(), response.data)
        connection = sqlite3.connect(app.config["DATABASE"])
        try:
            count = connection.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(count, 0)

    def test_invalid_uploads_show_an_error(self):
        for filename, content, expected in (
            ("exam.hwp", b"not supported", "rhwp-python이 문서를 해석하지 못했습니다"),
            ("exam.hwpx", b"not a zip", "rhwp-python이 문서를 해석하지 못했습니다"),
            ("exam.xml", b"<not-closed>", "no element found"),
        ):
            with self.subTest(filename=filename):
                response = self.client.post(
                    "/hwp",
                    data={
                        "_csrf_token": self.csrf_token(),
                        "document": (io.BytesIO(content), filename),
                    },
                    content_type="multipart/form-data",
                )

                self.assertEqual(response.status_code, 200)
                self.assertIn(expected.encode(), response.data)

    def test_upload_requires_login_and_csrf(self):
        anonymous = app.test_client()
        response = anonymous.get("/hwp")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

        response = self.client.post(
            "/hwp",
            data={"document": (io.BytesIO(SAMPLE_XML), "section0.xml")},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
