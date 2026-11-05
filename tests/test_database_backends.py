import os
import unittest
from unittest.mock import patch

from app import create_app
from database import (
    POSTGRES_SCHEMA_STATEMENTS,
    HybridRow,
    PostgresConnection,
    _translate_qmark_placeholders,
)


class _FakeCursor:
    def fetchone(self):
        return {"id": 17, "label": "row"}

    def fetchall(self):
        return [{"id": 17}, {"id": 18}]


class _FakeConnection:
    def __init__(self):
        self.calls = []

    def execute(self, statement, parameters=()):
        self.calls.append((statement, parameters))
        return _FakeCursor()


class DatabaseBackendConfigurationTests(unittest.TestCase):
    def test_sqlite_remains_the_default_backend(self):
        with patch.dict(os.environ, {"FLASK_SECRET_KEY": "test"}, clear=True):
            app = create_app()
        self.assertEqual(app.config["DATABASE_TYPE"], "sqlite")
        self.assertEqual(app.config["DATABASE"], os.path.join(app.instance_path, "probocr.sqlite3"))

    def test_postgres_backend_requires_a_database_url(self):
        with patch.dict(os.environ, {"DATABASE_TYPE": "postgresql", "FLASK_SECRET_KEY": "test"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "DATABASE_URL"):
                create_app()

    def test_postgres_backend_accepts_database_url(self):
        with patch.dict(
            os.environ,
            {
                "DATABASE_TYPE": "postgresql",
                "DATABASE_URL": "postgresql://user:password@example.test/neondb",
                "FLASK_SECRET_KEY": "test",
            },
            clear=True,
        ):
            app = create_app()
        self.assertEqual(app.config["DATABASE_TYPE"], "postgresql")
        self.assertEqual(app.config["DATABASE_URL"], "postgresql://user:password@example.test/neondb")

    def test_unknown_backend_is_rejected(self):
        with patch.dict(os.environ, {"DATABASE_TYPE": "mysql", "FLASK_SECRET_KEY": "test"}, clear=True):
            with self.assertRaisesRegex(ValueError, "DATABASE_TYPE"):
                create_app()

    def test_qmark_translation_ignores_quoted_text_and_comments(self):
        sql = "SELECT ? AS value, '?' AS literal, \"?\" AS ident -- ?\nWHERE id = ?"
        self.assertEqual(
            _translate_qmark_placeholders(sql),
            "SELECT %s AS value, '?' AS literal, \"?\" AS ident -- ?\nWHERE id = %s",
        )

    def test_postgres_connection_adapter_preserves_row_access(self):
        raw = _FakeConnection()
        connection = PostgresConnection(raw)
        cursor = connection.execute("SELECT * FROM users WHERE uuid = ?", ("user-1",))
        row = cursor.fetchone()
        self.assertEqual(raw.calls, [("SELECT * FROM users WHERE uuid = %s", ("user-1",))])
        self.assertEqual(row["id"], 17)
        self.assertEqual(row[0], 17)
        self.assertEqual(cursor.fetchall()[1]["id"], 18)

    def test_postgres_schema_includes_all_application_tables(self):
        schema = "\n".join(POSTGRES_SCHEMA_STATEMENTS)
        for table in (
            "users",
            "folders",
            "questions",
            "question_revisions",
            "question_drafts",
            "question_notes",
        ):
            self.assertIn(f"CREATE TABLE IF NOT EXISTS {table}", schema)
        self.assertNotIn("PRAGMA", schema)
        self.assertNotIn("AUTOINCREMENT", schema)


if __name__ == "__main__":
    unittest.main()
