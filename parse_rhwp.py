from __future__ import annotations

import re
from collections import OrderedDict
from typing import Any, Iterable

from parse_hwp import _equation_to_latex, strip_choice_label


CHOICE_LABELS = "①②③④⑤"
CHOICE_PATTERN = re.compile(rf"[{CHOICE_LABELS}]")
MAX_DOCUMENT_BYTES = 12 * 1024 * 1024


def _kind(block: Any) -> str:
    return str(getattr(block, "kind", ""))


def _provenance_key(block: Any) -> tuple[int, int]:
    provenance = getattr(block, "prov", None)
    return (
        int(getattr(provenance, "section_idx", 0)),
        int(getattr(provenance, "para_idx", 0)),
    )


def _render_formula(block: Any) -> str:
    script = str(getattr(block, "script", "") or "").strip()
    if not script:
        return ""
    try:
        latex = _equation_to_latex(script)
    except Exception:
        latex = str(getattr(block, "text_alt", "") or script).strip()
    return "$" + latex + "$" if latex else ""


def _render_picture(block: Any) -> str:
    description = str(getattr(block, "description", "") or "").strip()
    if description:
        description = re.sub(r"\s+", " ", description)
        return f"[그림: {description}]"
    return "[그림]"


def _insert_inline_events(text: str, events: list[tuple[int | None, int, str]]) -> str:
    """char_start가 있는 컨트롤은 위치에 삽입하고, 위치가 없는 것은 문단 끝에 붙인다."""
    positioned = sorted(
        (event for event in events if event[0] is not None),
        key=lambda event: (event[0], event[1]),
    )
    trailing = [event[2] for event in events if event[0] is None]
    parts: list[str] = []
    cursor = 0
    for offset, _, rendered in positioned:
        position = max(cursor, min(int(offset), len(text)))
        parts.append(text[cursor:position])
        parts.append(rendered)
        cursor = position
    parts.append(text[cursor:])
    parts.extend(trailing)
    return "".join(parts)


def _paragraphs_from_cell(cell: Any) -> list[str]:
    """rhwp IR의 셀 블록을 문단 순서로 복원한다."""
    entries: OrderedDict[tuple[int, int], dict[str, Any]] = OrderedDict()
    for block in getattr(cell, "blocks", []):
        kind = _kind(block)
        key = _provenance_key(block)
        if kind in {"paragraph", "list_item"}:
            entry = entries.setdefault(key, {"text": "", "events": []})
            entry["text"] = str(getattr(block, "text", "") or "")
        elif kind in {"formula", "picture"}:
            entry = entries.setdefault(key, {"text": "", "events": []})
            rendered = _render_formula(block) if kind == "formula" else _render_picture(block)
            if rendered:
                provenance = getattr(block, "prov", None)
                char_start = getattr(provenance, "char_start", None)
                entry["events"].append((char_start, len(entry["events"]), rendered))

    return [
        _insert_inline_events(entry["text"], entry["events"]).strip()
        for entry in entries.values()
        if entry["text"].strip() or entry["events"]
    ]


def _iter_tables(ir: Any) -> Iterable[Any]:
    iterator = getattr(ir, "iter_blocks", None)
    if not callable(iterator):
        raise ValueError("rhwp-python 문서에서 구조화 블록을 읽을 수 없습니다.")
    for block in iterator(scope="body", recurse=True):
        if _kind(block) == "table":
            yield block


def _compact(text: str) -> str:
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n+", " ", text)
    text = re.sub(r"(\[그림(?::[^\]]*)?\])(?=\S)", r"\1 ", text)
    return text.strip()


def _canonical(text: str) -> str:
    """서식 차이를 줄여 요약 문단과 별도 해설 문단의 중복 구간을 비교한다."""
    return re.sub(r"\s+", "", text).replace("$", "").replace(r"\(", "").replace(r"\)", "")


def _strip_canonical_prefix(text: str, prefix: str) -> str:
    if not prefix:
        return text
    consumed = 0
    index = 0
    while index < len(text) and consumed < len(prefix):
        character = text[index]
        if not character.isspace() and character != "$":
            consumed += 1
        index += 1
    while index < len(text) and (text[index].isspace() or text[index] == "$"):
        index += 1
    return text[index:]


def _split_choices(text: str) -> list[str]:
    starts = list(CHOICE_PATTERN.finditer(text))
    choices = []
    for index, start in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(text)
        choice = _compact(text[start.start():end])
        choice = strip_choice_label(choice)
        if choice:
            choices.append(choice)
    return choices


def _parse_question_row(paragraphs: list[str], number: int) -> dict[str, Any] | None:
    summaries = [
        (index, paragraph)
        for index, paragraph in enumerate(paragraphs)
        if "[정답]" in paragraph and "[해설]" in paragraph
    ]
    if len(summaries) != 1:
        return None

    summary_index, summary = summaries[0]
    answer_marker = summary.find("[정답]")
    explanation_marker = summary.find("[해설]", answer_marker + len("[정답]"))
    if answer_marker < 0 or explanation_marker < 0:
        return None

    answer = _compact(summary[answer_marker + len("[정답]"):explanation_marker])
    summary_after_explanation = summary[explanation_marker + len("[해설]"):]

    answer_indices = [
        index for index, paragraph in enumerate(paragraphs)
        if index != summary_index and "[정답]" in paragraph and "[해설]" not in paragraph
    ]
    if not answer:
        if not answer_indices:
            return None
        answer = _compact(paragraphs[answer_indices[0]].split("[정답]", 1)[-1])

    marker_indices = [
        index for index, paragraph in enumerate(paragraphs)
        if index != summary_index and "[해설]" in paragraph and "[정답]" not in paragraph
    ]
    explanation_indices: set[int] = set()
    explanation_parts: list[str] = []
    explanation_prefix = ""

    if marker_indices:
        marker_index = marker_indices[0]
        marker_tail = paragraphs[marker_index].split("[해설]", 1)[-1].strip()
        if marker_tail:
            marker_tail_canonical = _canonical(marker_tail)
            if _canonical(summary_after_explanation).startswith(marker_tail_canonical):
                explanation_parts.append(marker_tail)
                explanation_prefix += marker_tail_canonical
                explanation_indices.add(marker_index)

        for index in range(marker_index + 1, len(paragraphs)):
            candidate = paragraphs[index].strip()
            if not candidate or CHOICE_PATTERN.search(candidate):
                break
            if "[정답]" in candidate or "[해설]" in candidate:
                break
            candidate_canonical = _canonical(candidate)
            if not candidate_canonical:
                continue
            prospective = explanation_prefix + candidate_canonical
            if not _canonical(summary_after_explanation).startswith(prospective):
                break
            explanation_parts.append(candidate)
            explanation_prefix = prospective
            explanation_indices.add(index)

    explanation = _compact(" ".join(explanation_parts))
    question_from_summary = _strip_canonical_prefix(
        summary_after_explanation, explanation_prefix
    )
    question_from_summary = _compact(question_from_summary)

    choices: list[str] = []
    if CHOICE_PATTERN.search(question_from_summary):
        choice_start = CHOICE_PATTERN.search(question_from_summary)
        assert choice_start is not None
        choices.extend(_split_choices(question_from_summary[choice_start.start():]))
        question_from_summary = question_from_summary[:choice_start.start()].strip()

    question_parts = [question_from_summary] if question_from_summary else []
    ignored_indices = {summary_index, *answer_indices, *marker_indices, *explanation_indices}

    for index, paragraph in enumerate(paragraphs):
        if index in ignored_indices or not paragraph:
            continue
        if "[정답]" in paragraph or "[해설]" in paragraph:
            continue
        if CHOICE_PATTERN.search(paragraph):
            choices.extend(_split_choices(paragraph))
            continue
        question_parts.append(paragraph)

    question = _compact(" ".join(question_parts))
    question = re.sub(r"\s*\[\s*\d+\s*점\s*\]\s*$", "", question).strip()
    if not question or not answer:
        return None

    deduplicated_choices: list[str] = []
    for choice in choices:
        if choice not in deduplicated_choices:
            deduplicated_choices.append(choice)

    return {
        "number": number,
        "question": question,
        "choices": deduplicated_choices,
        "answer": answer,
        "explanation": explanation,
    }


def parse_questions_from_ir(ir: Any) -> list[dict[str, Any]]:
    """rhwp-python Document IR에서 시험지 표의 문항/선택지/정답/해설을 추출한다."""
    questions: list[dict[str, Any]] = []
    for table in _iter_tables(ir):
        rows: dict[int, list[Any]] = {}
        for cell in getattr(table, "cells", []):
            rows.setdefault(int(getattr(cell, "row", 0)), []).append(cell)

        for row_number in sorted(rows):
            paragraphs: list[str] = []
            for cell in sorted(rows[row_number], key=lambda value: int(getattr(value, "col", 0))):
                paragraphs.extend(_paragraphs_from_cell(cell))
            parsed = _parse_question_row(paragraphs, len(questions) + 1)
            if parsed is not None:
                questions.append(parsed)

    if not questions:
        raise ValueError(
            "문항을 찾지 못했습니다. 문항·정답·해설이 표 형태로 구성된 HWP/HWPX인지 확인해 주세요."
        )
    return questions


def parse_questions_from_rhwp_bytes(
    content: bytes, filename: str = "uploaded-document"
) -> list[dict[str, Any]]:
    """HWP/HWPX bytes를 rhwp-python으로 파싱한 뒤 ALEPH MATH 문항 구조로 변환한다."""
    if not content:
        raise ValueError("업로드한 문서가 비어 있습니다.")
    if len(content) > MAX_DOCUMENT_BYTES:
        raise ValueError("문서 크기가 허용 한도(12MB)를 초과합니다.")

    try:
        import rhwp

        document = rhwp.Document.from_bytes(content, source_uri=f"upload://{filename}")
        ir = document.to_ir()
    except ImportError as exc:
        raise RuntimeError(
            "HWP/HWPX 파싱에 필요한 rhwp-python이 설치되지 않았습니다. "
            "requirements.txt의 의존성을 설치해 주세요."
        ) from exc
    except Exception as exc:
        raise ValueError(f"rhwp-python이 문서를 해석하지 못했습니다: {exc}") from exc

    return parse_questions_from_ir(ir)
