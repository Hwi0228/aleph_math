import json
import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path

from app import app


class QuestionRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.database = Path(self.temp_directory.name) / "questions.sqlite3"
        app.config.update(
            TESTING=True,
            DATABASE=str(self.database),
            SECRET_KEY="test-session-secret",
        )
        self.client = app.test_client()
        self.register(self.client, "admin@example.com", "관리자")
        self.admin_uuid = self.get_user_uuid("admin@example.com")

    def tearDown(self):
        self.temp_directory.cleanup()

    def csrf_post(self, client, path, data=None, **kwargs):
        client.get("/")
        with client.session_transaction() as user_session:
            token = user_session["csrf_token"]
        form = dict(data or {})
        form["_csrf_token"] = token
        return client.post(path, data=form, **kwargs)

    def register(self, client, email, display_name, password="long-test-password"):
        client.get("/signup")
        with client.session_transaction() as user_session:
            token = user_session["csrf_token"]
        return client.post(
            "/signup",
            data={
                "_csrf_token": token,
                "email": email,
                "display_name": display_name,
                "password": password,
                "password_confirm": password,
            },
        )

    def get_user_uuid(self, email):
        connection = sqlite3.connect(self.database)
        try:
            return connection.execute(
                "SELECT uuid FROM users WHERE email = ?", (email,)
            ).fetchone()[0]
        finally:
            connection.close()

    def query_one(self, query, parameters=()):
        connection = sqlite3.connect(self.database)
        try:
            return connection.execute(query, parameters).fetchone()[0]
        finally:
            connection.close()

    def valid_question(self, **overrides):
        form = {
            "subject": "수학",
            "grade": "고2",
            "unit": "미분",
            "passage": "지문 $x^2$",
            "prompt": "문제 내용 \\(x^2\\)",
            "examples": "보기 $$x + 1$$",
            "choices": "첫 번째 $x$\n두 번째\n세 번째",
            "answer": "$x = 2$",
            "source": "직접 등록한 출처",
            "year": "2026",
            "page": "12",
        }
        form.update(overrides)
        return form

    def create_folder(self, client, name, parent_uuid=""):
        response = self.csrf_post(
            client,
            "/folders/new",
            data={
                "name": name,
                "parent_uuid": parent_uuid,
                "subject": "수학",
            },
        )
        self.assertEqual(response.status_code, 302)
        connection = sqlite3.connect(self.database)
        try:
            return connection.execute(
                """
                SELECT uuid FROM folders
                WHERE user_uuid = ? AND name = ?
                ORDER BY rowid DESC LIMIT 1
                """,
                (self.get_user_uuid_for_client(client), name),
            ).fetchone()[0]
        finally:
            connection.close()

    def get_user_uuid_for_client(self, client):
        with client.session_transaction() as user_session:
            session_uuid = user_session["user_uuid"]
        return session_uuid

    def test_first_account_is_admin_and_login_required(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("관리자".encode(), response.data)
        self.assertEqual(
            self.query_one(
                "SELECT role FROM users WHERE uuid = ?", (self.admin_uuid,)
            ),
            "admin",
        )

        anonymous = app.test_client()
        response = anonymous.get("/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_dashboard_shows_recent_questions_and_links_to_question_database(self):
        self.csrf_post(
            self.client,
            "/questions/new",
            data=self.valid_question(prompt="대시보드 최근 문항"),
        )

        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("등록한 문제".encode(), response.data)
        self.assertIn("대시보드 최근 문항".encode(), response.data)
        self.assertIn(b'href="/questions"', response.data)

    def test_signup_validates_password_and_csrf(self):
        client = app.test_client()
        response = self.register(
            client,
            "user@example.com",
            "사용자",
            password="short",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("비밀번호는 8자 이상".encode(), response.data)

        csrf_client = app.test_client()
        csrf_client.get("/signup")
        response = csrf_client.post(
            "/signup",
            data={
                "email": "no-csrf@example.com",
                "display_name": "No CSRF",
                "password": "long-test-password",
                "password_confirm": "long-test-password",
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_user_registration_and_login(self):
        client = app.test_client()
        signup_response = self.register(client, "user@example.com", "사용자")
        self.assertEqual(signup_response.status_code, 302)
        self.assertEqual(
            self.query_one(
                "SELECT role FROM users WHERE email = ?", ("user@example.com",)
            ),
            "user",
        )

        self.csrf_post(client, "/logout")
        client.get("/login")
        with client.session_transaction() as user_session:
            token = user_session["csrf_token"]
        login_response = client.post(
            "/login?next=%2F%2Fevil.example",
            data={
                "_csrf_token": token,
                "email": "USER@example.com",
                "password": "long-test-password",
            },
        )
        self.assertEqual(login_response.status_code, 302)
        self.assertEqual(login_response.headers["Location"], "/")
        self.assertEqual(client.get("/").status_code, 200)

    def test_admin_can_browse_user_data_but_user_cannot_browse_another_account(self):
        user_client = app.test_client()
        self.register(user_client, "user@example.com", "사용자")
        user_uuid = self.get_user_uuid("user@example.com")
        self.csrf_post(
            user_client,
            "/questions/new",
            data=self.valid_question(prompt="사용자 소유 문제"),
        )

        admin_response = self.client.get("/admin/users")
        self.assertEqual(admin_response.status_code, 200)
        self.assertIn(f"/questions?user_uuid={user_uuid}".encode(), admin_response.data)
        user_question_response = self.client.get(f"/questions?user_uuid={user_uuid}")
        self.assertIn("사용자 소유 문제".encode(), user_question_response.data)

        forbidden_response = user_client.get(f"/questions?user_uuid={self.admin_uuid}")
        self.assertEqual(forbidden_response.status_code, 403)
        self.assertEqual(user_client.get("/admin/users").status_code, 403)
        self.assertNotIn("관리자 소유 문제".encode(), user_client.get("/questions").data)

    def test_empty_database_shows_no_fake_questions_or_count(self):
        response = self.client.get("/questions")

        self.assertEqual(response.status_code, 200)
        self.assertIn("표시할 문제가 없습니다.".encode(), response.data)
        self.assertNotIn(b"15,551", response.data)
        self.assertNotIn(b'class="result-count"', response.data)
        self.assertNotIn("광희중학교".encode(), response.data)

    def test_only_working_database_items_appear_in_sidebar(self):
        response = self.client.get("/questions")
        sidebar = response.data.split(b'<aside class="sidebar"', 1)[1].split(b"</aside>", 1)[0]

        self.assertIn("문제 등록".encode(), sidebar)
        self.assertIn("문제 DB".encode(), sidebar)
        for unused_item in ("홈", "문제 상점", "문제지", "AI 변형문제", "출처 분석", "학생 관리", "설정"):
            self.assertNotIn(unused_item.encode(), sidebar)

    def test_registration_form_is_reachable_from_the_sidebar(self):
        database_response = self.client.get("/questions")
        form_response = self.client.get("/questions/new")

        self.assertIn(b'href="/questions/new"', database_response.data)
        self.assertEqual(form_response.status_code, 200)
        for field in (
            "subject",
            "grade",
            "unit",
            "folder_uuid",
            "passage",
            "prompt",
            "examples",
            "choices",
            "answer",
            "source",
        ):
            self.assertIn(f'name="{field}"'.encode(), form_response.data)
        self.assertIn(b"renderMathInElement", form_response.data)

    def test_only_user_created_uuid_folders_and_nested_folders_appear(self):
        empty_response = self.client.get("/questions")
        self.assertIn("아직 만든 폴더가 없습니다.".encode(), empty_response.data)
        self.assertNotIn("광희중학교".encode(), empty_response.data)

        parent_uuid = self.create_folder(self.client, "내신 대비")
        child_uuid = self.create_folder(self.client, "미분", parent_uuid)
        response = self.client.get("/questions")

        self.assertIn("내신 대비".encode(), response.data)
        self.assertIn("미분".encode(), response.data)
        uuid.UUID(parent_uuid)
        uuid.UUID(child_uuid)
        connection = sqlite3.connect(self.database)
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT parent_uuid FROM folders WHERE uuid = ?", (child_uuid,)
                ).fetchone()[0],
                parent_uuid,
            )
        finally:
            connection.close()

    def test_users_cannot_read_or_assign_other_users_folders(self):
        other_client = app.test_client()
        self.register(other_client, "other@example.com", "다른 사용자")
        other_uuid = self.create_folder(other_client, "비공개 폴더")

        response = self.csrf_post(
            self.client,
            "/questions/new",
            data=self.valid_question(folder_uuid=other_uuid),
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("선택한 폴더가 없습니다.".encode(), response.data)
        self.assertEqual(self.client.get(f"/questions?folder={other_uuid}").status_code, 404)

    def test_question_can_be_registered_in_uuid_folder_and_filter_includes_children(self):
        parent_uuid = self.create_folder(self.client, "내신 대비")
        child_uuid = self.create_folder(self.client, "미분", parent_uuid)
        response = self.csrf_post(
            self.client,
            "/questions/new",
            data=self.valid_question(folder_uuid=child_uuid),
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("문제를 등록했습니다.".encode(), response.data)
        self.assertIn("문제 내용".encode(), response.data)
        self.assertIn('class="folder-count">1</span>'.encode(), response.data)
        connection = sqlite3.connect(self.database)
        try:
            self.assertEqual(
                connection.execute("SELECT folder_uuid FROM questions").fetchone()[0],
                child_uuid,
            )
        finally:
            connection.close()

        parent_response = self.client.get(f"/questions?folder={parent_uuid}")
        self.assertIn("문제 내용".encode(), parent_response.data)
        self.assertIn("내신 대비 폴더의 문제".encode(), parent_response.data)
        child_response = self.client.get(f"/questions?folder={child_uuid}")
        self.assertIn("문제 내용".encode(), child_response.data)

    def test_existing_question_can_be_moved_to_uuid_folder(self):
        self.csrf_post(
            self.client,
            "/questions/new",
            data=self.valid_question(),
        )
        folder_uuid = self.create_folder(self.client, "오답")
        response = self.csrf_post(
            self.client,
            "/questions/1/folder",
            data={"folder_uuid": folder_uuid, "subject": "수학"},
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("문제를 이동했습니다.".encode(), response.data)
        self.assertIn("오답".encode(), response.data)
        connection = sqlite3.connect(self.database)
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT folder_uuid FROM questions WHERE id = 1"
                ).fetchone()[0],
                folder_uuid,
            )
        finally:
            connection.close()

    def test_question_is_stored_and_shown_with_actual_count(self):
        response = self.csrf_post(
            self.client,
            "/questions/new",
            data=self.valid_question(),
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("문제를 등록했습니다.".encode(), response.data)
        self.assertIn('class="result-count">1</span>'.encode(), response.data)
        for value in ("고2", "미분", "지문", "보기", "문제 내용", "답", "직접 등록한 출처"):
            self.assertIn(value.encode(), response.data)
        self.assertIn("$$x + 1$$".encode(), response.data)
        self.assertIn("katex@0.16.22".encode(), response.data)

        connection = sqlite3.connect(self.database)
        try:
            row = connection.execute(
                """
                SELECT user_uuid, subject, grade, unit, passage, prompt, examples,
                       choices, answer, source, year, page
                FROM questions
                """
            ).fetchone()
        finally:
            connection.close()

        self.assertEqual(row[0], self.admin_uuid)
        self.assertEqual(row[1:7], (
            "수학",
            "고2",
            "미분",
            "지문 $x^2$",
            "문제 내용 \\(x^2\\)",
            "보기 $$x + 1$$",
        ))
        self.assertEqual(json.loads(row[7]), ["첫 번째 $x$", "두 번째", "세 번째"])
        self.assertEqual(row[8:], ("$x = 2$", "직접 등록한 출처", 2026, 12))

    def test_question_details_button_expands_and_collapses_card_content(self):
        self.csrf_post(
            self.client,
            "/questions/new",
            data=self.valid_question(),
        )

        response = self.client.get("/questions")

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            b'<button class="detail-button" type="button" aria-expanded="false">'
            + "자세히".encode(),
            response.data,
        )
        self.assertIn(
            b'document.querySelectorAll(".question-card .card-top .detail-button")',
            response.data,
        )
        self.assertIn(b'card.classList.toggle("expanded")', response.data)
        self.assertIn(
            'button.textContent = expanded ? "접기" : "자세히"'.encode(),
            response.data,
        )

    def test_subject_tab_filters_real_questions(self):
        self.csrf_post(self.client, "/questions/new", data=self.valid_question())
        science_form = self.valid_question(
            subject="과학",
            grade="고1",
            unit="물리",
            prompt="과학 문제",
        )
        self.csrf_post(self.client, "/questions/new", data=science_form)

        math_response = self.client.get("/questions?subject=수학")
        science_response = self.client.get("/questions?subject=과학")

        self.assertIn("문제 내용".encode(), math_response.data)
        self.assertNotIn("과학 문제".encode(), math_response.data)
        self.assertIn("과학 문제".encode(), science_response.data)
        self.assertIn('class="result-count">1</span>', science_response.get_data(as_text=True))

    def test_invalid_question_is_not_saved(self):
        response = self.csrf_post(
            self.client,
            "/questions/new",
            data=self.valid_question(answer="", grade=""),
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("학년을(를) 입력해 주세요.".encode(), response.data)
        self.assertIn("답을(를) 입력해 주세요.".encode(), response.data)
        connection = sqlite3.connect(self.database)
        try:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM questions").fetchone()[0],
                0,
            )
        finally:
            connection.close()

    def test_math_delimiters_are_configured_on_question_list(self):
        response = self.client.get("/questions")
        html = response.get_data(as_text=True)

        self.assertIn("katex.min.css", html)
        self.assertIn("auto-render.min.js", html)
        self.assertIn('left: "\\\\("', html)
        self.assertIn('left: "$"', html)

    def test_question_text_is_html_escaped(self):
        self.csrf_post(
            self.client,
            "/questions/new",
            data=self.valid_question(prompt="<script>alert(1)</script>"),
        )

        response = self.client.get("/questions")
        self.assertIn(b"&lt;script&gt;alert(1)&lt;/script&gt;", response.data)
        self.assertNotIn(b"<script>alert(1)</script>", response.data)

    def test_legacy_database_migrates_folder_ids_to_uuids_and_first_admin_owns_data(self):
        legacy_database = Path(self.temp_directory.name) / "legacy.sqlite3"
        connection = sqlite3.connect(legacy_database)
        connection.executescript(
            """
            CREATE TABLE folders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                parent_id INTEGER REFERENCES folders(id),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE questions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                folder_id INTEGER REFERENCES folders(id),
                subject TEXT NOT NULL,
                question_type TEXT NOT NULL,
                points INTEGER NOT NULL CHECK (points > 0),
                prompt TEXT NOT NULL,
                choices TEXT NOT NULL,
                difficulty TEXT,
                topic TEXT NOT NULL DEFAULT '',
                keyword TEXT NOT NULL DEFAULT '',
                year INTEGER,
                page INTEGER,
                source TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO folders (id, name) VALUES (1, '기출');
            INSERT INTO folders (id, name, parent_id) VALUES (2, '미분', 1);
            INSERT INTO questions (id, folder_id, subject, question_type, points, prompt, choices, topic, source)
            VALUES (1, 2, '수학', '객관식', 3, '이전 문제', '["가","나"]', '기존 단원', '기존 출처');
            """
        )
        connection.close()

        original_database = app.config["DATABASE"]
        app.config["DATABASE"] = str(legacy_database)
        try:
            client = app.test_client()
            signup_response = self.register(
                client, "legacy-admin@example.com", "기존 관리자"
            )
            self.assertEqual(signup_response.status_code, 302)
            migrated_uuid = self.get_user_uuid_from_database(
                legacy_database, "legacy-admin@example.com"
            )

            connection = sqlite3.connect(legacy_database)
            try:
                folders = connection.execute(
                    "SELECT uuid, user_uuid, name, parent_uuid FROM folders ORDER BY name"
                ).fetchall()
                question = connection.execute(
                    """
                    SELECT user_uuid, folder_uuid, unit, prompt, choices, source,
                           explanation
                    FROM questions
                    """
                ).fetchone()
                columns = {
                    row[1]
                    for row in connection.execute("PRAGMA table_info(folders)")
                }
                question_columns = {
                    row[1]
                    for row in connection.execute("PRAGMA table_info(questions)")
                }
            finally:
                connection.close()

            folder_by_name = {folder[2]: folder for folder in folders}
            self.assertEqual(len(folders), 2)
            self.assertTrue(all(uuid.UUID(folder[0]) for folder in folders))
            self.assertTrue(all(folder[1] == migrated_uuid for folder in folders))
            self.assertEqual(folder_by_name["미분"][3], folder_by_name["기출"][0])
            self.assertEqual(question, (
                migrated_uuid,
                folder_by_name["미분"][0],
                "기존 단원",
                "이전 문제",
                '["가","나"]',
                "기존 출처",
                "",
            ))
            self.assertIn("uuid", columns)
            self.assertNotIn("id", columns)
            self.assertIn("explanation", question_columns)
        finally:
            app.config["DATABASE"] = original_database

    def get_user_uuid_from_database(self, database, email):
        connection = sqlite3.connect(database)
        try:
            return connection.execute(
                "SELECT uuid FROM users WHERE email = ?", (email,)
            ).fetchone()[0]
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
