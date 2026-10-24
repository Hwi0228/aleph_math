import sqlite3
import uuid
from pathlib import Path

from flask import current_app, g


USER_COLUMNS = {"uuid", "email", "display_name", "password_hash", "role", "created_at"}

FOLDER_COLUMNS = {"uuid", "user_uuid", "name", "parent_uuid", "created_at"}

QUESTION_COLUMNS = {
    "id",
    "user_uuid",
    "folder_uuid",
    "subject",
    "grade",
    "unit",
    "passage",
    "prompt",
    "examples",
    "choices",
    "answer",
    "source",
    "year",
    "page",
    "created_at",
}

USERS_SCHEMA = """
CREATE TABLE users (
    uuid TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    display_name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'user')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""

FOLDERS_SCHEMA = """
CREATE TABLE folders (
    uuid TEXT PRIMARY KEY,
    user_uuid TEXT REFERENCES users(uuid) ON DELETE CASCADE,
    name TEXT NOT NULL,
    parent_uuid TEXT REFERENCES folders(uuid) ON DELETE RESTRICT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""

QUESTIONS_SCHEMA = """
CREATE TABLE questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_uuid TEXT REFERENCES users(uuid) ON DELETE CASCADE,
    folder_uuid TEXT REFERENCES folders(uuid) ON DELETE SET NULL,
    subject TEXT NOT NULL,
    grade TEXT NOT NULL,
    unit TEXT NOT NULL,
    passage TEXT NOT NULL DEFAULT '',
    prompt TEXT NOT NULL,
    examples TEXT NOT NULL DEFAULT '',
    choices TEXT NOT NULL DEFAULT '[]',
    answer TEXT NOT NULL,
    explanation TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL,
    year INTEGER,
    page INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""

def _table_columns(connection, table_name):
    return {
        row["name"]
        for row in connection.execute(f"PRAGMA table_info({table_name})")
    }

def _create_indexes(connection):
    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_folders_root_name
        ON folders(user_uuid, name) WHERE parent_uuid IS NULL
        """
    )
    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_folders_child_name
        ON folders(user_uuid, parent_uuid, name) WHERE parent_uuid IS NOT NULL
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_questions_user_subject ON questions(user_uuid, subject)"
    )

def _migrate_question_fractions(connection):
    fraction_fields = (
        "passage",
        "prompt",
        "examples",
        "choices",
        "answer",
        "explanation",
    )
    for field in fraction_fields:
        connection.execute(
            f"""
            UPDATE questions
            SET {field} = REPLACE({field}, ?, ?)
            WHERE INSTR({field}, ?) > 0
            """,
            (r"\frac", r"\dfrac", r"\frac"),
        )

def initialize_database(connection):
    connection.execute("PRAGMA foreign_keys = OFF")
    tables = {
        row["name"]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    user_columns = _table_columns(connection, "users") if "users" in tables else set()
    folder_columns = _table_columns(connection, "folders") if "folders" in tables else set()
    question_columns = _table_columns(connection, "questions") if "questions" in tables else set()

    if "users" not in tables:
        connection.execute(USERS_SCHEMA)

    if FOLDER_COLUMNS.issubset(folder_columns) and QUESTION_COLUMNS.issubset(question_columns):
        if "explanation" not in question_columns:
            connection.execute(
                "ALTER TABLE questions ADD COLUMN explanation TEXT NOT NULL DEFAULT ''"
            )
        _migrate_question_fractions(connection)
        _create_indexes(connection)
        connection.commit()
        connection.execute("PRAGMA foreign_keys = ON")
        return

    legacy_folders = (
        [dict(row) for row in connection.execute("SELECT * FROM folders")]
        if "folders" in tables
        else []
    )
    legacy_questions = (
        [dict(row) for row in connection.execute("SELECT * FROM questions")]
        if "questions" in tables
        else []
    )

    connection.execute("BEGIN IMMEDIATE")
    try:
        if "users" in tables and not USER_COLUMNS.issubset(user_columns):
            connection.execute("DROP TABLE users")
            connection.execute(USERS_SCHEMA)

        folder_uuid_map = {}
        for folder in legacy_folders:
            old_key = folder.get("uuid", folder.get("id"))
            folder_uuid_map[str(old_key)] = folder.get("uuid") or str(uuid.uuid4())

        if "questions" in tables:
            connection.execute("DROP TABLE questions")
        if "folders" in tables:
            connection.execute("DROP TABLE folders")

        connection.execute(FOLDERS_SCHEMA)
        connection.execute(QUESTIONS_SCHEMA)

        for folder in legacy_folders:
            old_parent = folder.get("parent_uuid", folder.get("parent_id"))
            parent_uuid = (
                folder_uuid_map.get(str(old_parent))
                if old_parent is not None
                else None
            )
            connection.execute(
                """
                INSERT INTO folders (uuid, user_uuid, name, parent_uuid, created_at)
                VALUES (?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP))
                """,
                (
                    folder_uuid_map[str(folder.get("uuid", folder.get("id")))],
                    folder.get("user_uuid"),
                    folder.get("name", ""),
                    parent_uuid,
                    folder.get("created_at"),
                ),
            )

        for question in legacy_questions:
            legacy_folder_ref = question.get(
                "folder_uuid", question.get("folder_id")
            )
            folder_uuid = (
                folder_uuid_map.get(str(legacy_folder_ref))
                if legacy_folder_ref is not None
                else None
            )
            connection.execute(
                """
                INSERT INTO questions (
                    id, user_uuid, folder_uuid, subject, grade, unit, passage,
                    prompt, examples, choices, answer, explanation, source, year,
                    page, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP))
                """,
                (
                    question.get("id"),
                    question.get("user_uuid"),
                    folder_uuid,
                    question.get("subject", ""),
                    question.get("grade", ""),
                    question.get("unit", question.get("topic", "")),
                    question.get("passage", ""),
                    question.get("prompt", ""),
                    question.get("examples", ""),
                    question.get("choices", "[]"),
                    question.get("answer", ""),
                    question.get("explanation", ""),
                    question.get("source", ""),
                    question.get("year"),
                    question.get("page"),
                    question.get("created_at"),
                ),
            )

        _migrate_question_fractions(connection)
        _create_indexes(connection)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.execute("PRAGMA foreign_keys = ON")

def get_db():
    if "db" not in g:
        database = current_app.config["DATABASE"]
        Path(database).parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(database)
        connection.row_factory = sqlite3.Row
        try:
            initialize_database(connection)
            connection.execute("PRAGMA foreign_keys = ON")
        except Exception:
            connection.close()
            raise
        g.db = connection
    return g.db

def close_db(_error=None):
    connection = g.pop("db", None)
    if connection is not None:
        if _error is not None:
            connection.rollback()
        connection.close()
