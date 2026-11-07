"""Load and query the pinned, machine-readable Korean curriculum dataset."""

import json
import os
import tempfile
import threading
from pathlib import Path
from urllib.request import Request, urlopen

DATASET_VERSION = "v3.0.1"
DATASET_URL = (
    "https://raw.githubusercontent.com/greatsong/k-curriculum-2022/"
    f"{DATASET_VERSION}/data/standards.json"
)
EXPECTED_STANDARD_COUNT = 6444
CORE_SUBJECT_GROUPS = {"수학", "과학", "국어", "영어", "사회"}

_LOCK = threading.Lock()
_MEMORY_CACHE = {}


class CurriculumDataError(RuntimeError):
    """Raised when the pinned curriculum dataset cannot be read or validated."""


def _validate_records(records):
    if not isinstance(records, list) or len(records) != EXPECTED_STANDARD_COUNT:
        raise CurriculumDataError(
            f"Expected {EXPECTED_STANDARD_COUNT} curriculum standards."
        )

    required = {
        "key", "code", "subject_group", "subject", "school_level",
        "grade_group", "area", "content",
    }
    seen_keys = set()
    for record in records:
        if not isinstance(record, dict) or not required.issubset(record):
            raise CurriculumDataError("The curriculum dataset has an invalid record.")
        key = record.get("key")
        if not isinstance(key, str) or not key or key in seen_keys:
            raise CurriculumDataError("The curriculum dataset has invalid identifiers.")
        seen_keys.add(key)
    return records


def _read_records(raw):
    try:
        records = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CurriculumDataError("The curriculum dataset is not valid UTF-8 JSON.") from error
    return _validate_records(records)


def load_2022_standards(cache_path):
    """Read the local copy, downloading the pinned release only when needed."""
    path = Path(cache_path).expanduser().resolve()
    cache_key = str(path)

    with _LOCK:
        if cache_key in _MEMORY_CACHE:
            return _MEMORY_CACHE[cache_key]

        records = None
        try:
            if path.is_file():
                records = _read_records(path.read_bytes())
        except (OSError, CurriculumDataError):
            records = None

        if records is None:
            request = Request(
                DATASET_URL,
                headers={"User-Agent": "HEXORA-curriculum-integration"},
            )
            try:
                with urlopen(request, timeout=30) as response:
                    raw = response.read()
                records = _read_records(raw)
            except Exception as error:
                raise CurriculumDataError(
                    "Unable to download the pinned 2022 curriculum dataset."
                ) from error

            path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=".standards-", suffix=".tmp", dir=str(path.parent)
            )
            try:
                with os.fdopen(descriptor, "wb") as temporary_file:
                    temporary_file.write(raw)
                os.replace(temporary_name, path)
            except Exception:
                try:
                    os.unlink(temporary_name)
                except OSError:
                    pass
                raise

        _MEMORY_CACHE[cache_key] = records
        return records


def _grade_filters(grade):
    if grade in {"초1", "초2"}:
        return "초등학교", "초1-2"
    if grade in {"초3", "초4"}:
        return "초등학교", "초3-4"
    if grade in {"초5", "초6"}:
        return "초등학교", "초5-6"
    if grade in {"중1", "중2", "중3"}:
        return "중학교", "중1-3"
    if grade in {"고1", "고2", "고3"}:
        return "고등학교", None
    if grade == "기타":
        return "", "기타"
    if not grade:
        return None, None
    return None, "__unsupported_grade__"


def select_2022_standards(records, *, subject="", grade="", course="", area=""):
    """Return valid course/area choices and standards for the supplied form filters."""
    school_level, grade_group = _grade_filters(grade)

    def matches_base(record):
        group = record.get("subject_group", "")
        if subject == "기타":
            if group in CORE_SUBJECT_GROUPS:
                return False
        elif subject and group != subject:
            return False

        if school_level is not None and record.get("school_level", "") != school_level:
            return False
        if grade_group is not None and record.get("grade_group", "") != grade_group:
            return False
        return True

    base_records = [record for record in records if matches_base(record)]
    courses = sorted(
        {record["subject"] for record in base_records if record.get("subject")},
        key=str.casefold,
    )

    course_records = [
        record for record in base_records
        if course and record.get("subject") == course
    ]
    # Keep the source dataset's curriculum order rather than alphabetizing areas.
    areas = list(dict.fromkeys(
        record["area"] for record in course_records if record.get("area")
    ))

    matched_records = [
        record for record in course_records
        if not area or record.get("area") == area
    ]
    standards = [
        f'{record["code"]} {record["content"]}'.strip()
        for record in matched_records
    ]

    return {
        "courses": courses,
        "areas": areas,
        "standards": standards,
        "matched": bool(standards),
    }
