from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from functools import lru_cache
from pathlib import Path
from typing import Iterable

from hml_equation_parser import eq2latex


HP_NAMESPACE = "http://www.hancom.co.kr/hwpml/2011/paragraph"
HP = f"{{{HP_NAMESPACE}}}"
DEFAULT_INPUT = (
    Path(__file__).resolve().parent
    / "hwp"
    / "2026학년도 10월 고1 전국연합학력평가 수학(문제+정답+해설)"
    / "Contents"
    / "section0.xml"
)
CHOICE_LABELS = "①②③④⑤"
GROUPED_COMMAND = re.compile(
    r"(?<!\w)(?:vec|dyad|acute|grave|dot|ddot|bar|hat|check|arch|tilde|BOX|"
    r"matrix|pmatrix|bmatrix|dmatrix|cases)(?=\s*\{)"
)
HANGUL = re.compile(r"[\uac00-\ud7a3]")


def _group_end(expression: str, open_index: int) -> int | None:
    depth = 0
    for index in range(open_index, len(expression)):
        if expression[index] not in "{}":
            continue
        backslashes = 0
        previous = index - 1
        while previous >= 0 and expression[previous] == "\\":
            backslashes += 1
            previous -= 1
        if backslashes % 2:
            continue
        if expression[index] == "{":
            depth += 1
        else:
            depth -= 1
            if depth == 0:
                return index
    return None


def _replace_group_environment(
    expression: str, command: str, environment: str
) -> str:
    pattern = re.compile(rf"(?<!\w)(?:\\{command}|{command})\s*\{{")
    search_from = 0
    while match := pattern.search(expression, search_from):
        open_index = expression.find("{", match.start(), match.end())
        close_index = _group_end(expression, open_index)
        if close_index is None:
            search_from = match.end()
            continue
        body = expression[open_index + 1 : close_index].replace("#", r"\\")
        body = re.sub(r"\s*\\\\\s*", r" \\\\ ", body.strip())
        replacement = rf"\begin{{{environment}}} {body} \end{{{environment}}}"
        expression = expression[: match.start()] + replacement + expression[close_index + 1 :]
        search_from = match.start() + len(replacement)
    return expression


def normalize_katex_expression(expression: str) -> str:
    expression = _replace_group_environment(expression, "eqalign", "aligned")
    expression = _replace_group_environment(expression, "CASES", "cases")
    expression = re.sub(r"(?<!\w)LEFT\s+LBRACE(?!\w)", r"\\left \\{", expression)
    expression = re.sub(r"(?<!\w)RIGHT\s+RBRACE(?!\w)", r"\\right \\}", expression)
    expression = re.sub(r"(?<!\w)LBRACE(?!\w)", r"\\{", expression)
    expression = re.sub(r"(?<!\w)RBRACE(?!\w)", r"\\}", expression)
    expression = re.sub(r"(?<!\w)LEFT(?!\w)", r"\\left", expression)
    expression = re.sub(r"(?<!\w)RIGHT(?!\w)", r"\\right", expression)
    for token, command in (
        ("div", r"\div"),
        ("times", r"\times"),
        ("sqrt", r"\sqrt"),
        ("cdots", r"\cdots"),
        ("triangle", r"\triangle"),
    ):
        expression = re.sub(
            rf"(?<![\w\\]){token}(?!\w)", lambda _: command, expression
        )
    expression = expression.replace("#", " ")
    expression = re.sub(
        r"\\mathrm\{([^{}]*)\}",
        lambda match: (
            rf"\text{{{match.group(1)}}}"
            if HANGUL.search(match.group(1))
            else match.group(0)
        ),
        expression,
    )
    expression = re.sub(r"\\frac\b", r"\\dfrac", expression)
    return expression


@lru_cache(maxsize=4096)
def _equation_to_latex(script: str) -> str:
    script = script.replace("`", "").strip()
    script = re.sub(r"(?<!\w)LEFT\s+LBRACE(?!\w)", "LEFT {", script)
    script = re.sub(r"(?<!\w)RIGHT\s+RBRACE(?!\w)", "RIGHT }", script)
    script = re.sub(r"(?<!\w)LBRACE(?!\w)", "{", script)
    script = re.sub(r"(?<!\w)RBRACE(?!\w)", "}", script)
    script = re.sub(r"(?<!\w)div(?!\w)", "÷", script)
    script = re.sub(r"(?<!\w)CASES(?!\w)", "cases", script)
    script = re.sub(r"(?<!\w)cdots(?!\w)", "CDOTS", script)
    script = re.sub(r"(?<!\w)EQALIGN(?!\w)", "eqalign", script)
    script = re.sub(r"(?<!\w)angle(?!\w)", "ANGLE", script)
    script = re.sub(r"(?<!\w)triangle(?!\w)", "TRIANGLE", script)
    search_from = 0
    while True:
        match = GROUPED_COMMAND.search(script, search_from)
        if match is None:
            break
        argument_start = script.find("{", match.end())
        depth = 0
        argument_end = None
        for index in range(argument_start, len(script)):
            if script[index] == "{":
                depth += 1
            elif script[index] == "}":
                depth -= 1
                if depth == 0:
                    argument_end = index + 1
                    break
        if argument_end is None:
            raise ValueError("HWP 수식에서 그룹 연산자의 범위를 찾을 수 없습니다.")
        wrapped_command = "{" + script[match.start() : argument_end] + "}"
        script = (
            script[: match.start()]
            + wrapped_command
            + script[argument_end:]
        )
        search_from = match.start() + 4
    converted = eq2latex(script).strip()
    converted = re.sub(
        r'\brm\s+"([^"]*)"(?:\s+it\b)?',
        lambda match: (
            rf"\text{{{match.group(1)}}}"
            if HANGUL.search(match.group(1))
            else rf"\mathrm{{{match.group(1)}}}"
        ),
        converted,
    )
    return normalize_katex_expression(converted)


def normalize_katex_markup(text: str) -> str:
    delimiters = re.compile(r"\$\$.*?\$\$|\$[^$]*\$|\\\(.*?\\\)|\\\[.*?\\\]", re.DOTALL)

    def normalize(match: re.Match[str]) -> str:
        value = match.group(0)
        if value.startswith("$$"):
            left, right = "$$", "$$"
        elif value.startswith("$"):
            left, right = "$", "$"
        elif value.startswith(r"\("):
            left, right = r"\(", r"\)"
        else:
            left, right = r"\[", r"\]"
        return left + normalize_katex_expression(value[len(left) : -len(right)]) + right

    return delimiters.sub(normalize, text)


def strip_choice_label(choice: str) -> str:
    return re.sub(f"^[{CHOICE_LABELS}]\\s*", "", choice, count=1).strip()


def _events(element: ET.Element) -> list[tuple[str, str]]:
    events = []
    for node in element.iter():
        if node.tag == f"{HP}t":
            if node.text:
                events.append(("text", node.text))
        elif node.tag == f"{HP}script":
            if node.text:
                events.append(("math", _equation_to_latex(node.text)))
        elif node.tag == f"{HP}pic":
            events.append(("image", "[그림]"))
    return events


def _stream(events: Iterable[tuple[str, str]]) -> tuple[str, str, list[int]]:
    rendered_parts = []
    canonical_chars = []
    canonical_offsets = []
    display_offset = 0

    for kind, value in events:
        if kind == "math":
            rendered = f"${value}$"
            canonical_value = value
            canonical_start = 1
        elif kind == "image":
            rendered = value
            canonical_value = ""
            canonical_start = 0
        else:
            rendered = value
            canonical_value = value
            canonical_start = 0

        rendered_parts.append(rendered)
        for index, character in enumerate(canonical_value):
            if not character.isspace():
                canonical_chars.append(character)
                canonical_offsets.append(display_offset + canonical_start + index)
        display_offset += len(rendered)

    return "".join(rendered_parts), "".join(canonical_chars), canonical_offsets


def _paragraph_text(paragraph: ET.Element) -> str:
    return "".join(value for _, value in _events(paragraph))


def _compact(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return re.sub(r"(\[그림\])(?=\S)", r"\1 ", text)


def _split_choices(text: str) -> list[str]:
    starts = list(re.finditer(f"[{CHOICE_LABELS}]", text))
    return [
        strip_choice_label(
            _compact(text[start.start() : end.start() if end else len(text)])
        )
        for start, end in zip(starts, starts[1:] + [None])
    ]


def _display_offset_after(
    canonical_offsets: list[int], canonical_end: int, display: str
) -> int:
    if canonical_end == 0:
        return 0
    offset = canonical_offsets[canonical_end - 1] + 1
    if offset < len(display) and display[offset] == "$":
        offset += 1
    return offset


def _parse_row(row: ET.Element, number: int) -> dict[str, object]:
    paragraphs = row.findall(f".//{HP}p")
    summary_candidates = [
        paragraph
        for paragraph in paragraphs
        if "[정답]" in _paragraph_text(paragraph)
        and "[해설]" in _paragraph_text(paragraph)
    ]
    if len(summary_candidates) != 1:
        raise ValueError(
            f"문항 {number}: 문제/해설 통합 문단을 하나로 찾지 못했습니다."
        )

    summary_events = _events(summary_candidates[0])
    summary, summary_canonical, summary_offsets = _stream(summary_events)
    marker_start = summary_canonical.find("[해설]")
    if marker_start < 0:
        raise ValueError(f"문항 {number}: 해설 시작 표시를 찾지 못했습니다.")
    marker_end = marker_start + len("[해설]")
    explanation_start = _display_offset_after(summary_offsets, marker_end, summary)

    answer_paragraphs = [
        paragraph
        for paragraph in paragraphs
        if "[정답]" in _paragraph_text(paragraph)
        and "[해설]" not in _paragraph_text(paragraph)
    ]
    if len(answer_paragraphs) != 1:
        raise ValueError(f"문항 {number}: 정답을 하나로 찾지 못했습니다.")
    answer_display, _, _ = _stream(_events(answer_paragraphs[0]))
    answer = _compact(answer_display.split("[정답]", 1)[1])

    marker_paragraphs = [
        paragraph
        for paragraph in paragraphs
        if "[해설]" in _paragraph_text(paragraph)
        and "[정답]" not in _paragraph_text(paragraph)
    ]
    if len(marker_paragraphs) != 1:
        raise ValueError(f"문항 {number}: 해설 문단을 찾지 못했습니다.")

    marker_index = paragraphs.index(marker_paragraphs[0])
    explanation_style = marker_paragraphs[0].get("styleIDRef")
    explanation_events = []
    for paragraph in paragraphs[marker_index + 1 :]:
        if paragraph.get("styleIDRef") == explanation_style:
            explanation_events.extend(_events(paragraph))
    _, explanation_canonical, _ = _stream(explanation_events)

    explanation_index = summary_canonical.find(explanation_canonical, marker_end)
    if not explanation_canonical or explanation_index < 0:
        raise ValueError(f"문항 {number}: 해설 내용의 경계를 찾지 못했습니다.")
    explanation_end = explanation_index + len(explanation_canonical)
    explanation_end_display = _display_offset_after(
        summary_offsets, explanation_end, summary
    )

    explanation = _compact(summary[explanation_start:explanation_end_display])
    question_and_choices = _compact(summary[explanation_end_display:])
    choice_match = re.search(f"[{CHOICE_LABELS}]", question_and_choices)
    question_parts = [
        question_and_choices[: choice_match.start()]
        if choice_match
        else question_and_choices
    ]
    choice_style_ids = {
        summary_candidates[0].get("styleIDRef"),
        answer_paragraphs[0].get("styleIDRef"),
        explanation_style,
    }
    choice_paragraphs = [
        paragraph
        for paragraph in paragraphs
        if paragraph.get("styleIDRef") not in choice_style_ids
        and any(label in _paragraph_text(paragraph) for label in CHOICE_LABELS)
    ]
    choices = _split_choices(
        " ".join(_stream(_events(paragraph))[0] for paragraph in choice_paragraphs)
    )
    if not choices and choice_match:
        choices = _split_choices(question_and_choices[choice_match.start() :])

    for paragraph in paragraphs:
        if paragraph.get("styleIDRef") in choice_style_ids:
            continue
        paragraph_text = _paragraph_text(paragraph)
        if (
            not paragraph_text.strip()
            or any(label in paragraph_text for label in CHOICE_LABELS)
            or paragraph.findall(f".//{HP}pic")
            or paragraph.findall(f".//{HP}tbl")
        ):
            continue
        question_parts.append(_stream(_events(paragraph))[0])

    question = _compact(" ".join(question_parts))
    question = re.sub(r"\s*\[\d+\s*점\]\s*$", "", question).strip()
    if row.findall(f".//{HP}pic") and "[그림]" not in question:
        question = _compact(f"{question} [그림]")
    if not question:
        raise ValueError(f"문항 {number}: 문제 본문을 추출하지 못했습니다.")

    return {
        "number": number,
        "question": question,
        "choices": choices,
        "answer": answer,
        "explanation": explanation,
    }


def parse_questions(xml_path: Path) -> list[dict[str, object]]:
    root = ET.parse(xml_path).getroot()
    return _parse_root(root)


def parse_questions_from_xml(xml_content: bytes) -> list[dict[str, object]]:
    root = ET.fromstring(xml_content)
    return _parse_root(root)


def _parse_root(root: ET.Element) -> list[dict[str, object]]:
    questions = []
    for row in root.iter(f"{HP}tr"):
        paragraphs = row.findall(f".//{HP}p")
        if any(
            "[정답]" in _paragraph_text(paragraph)
            and "[해설]" in _paragraph_text(paragraph)
            for paragraph in paragraphs
        ):
            questions.append(_parse_row(row, len(questions) + 1))
    return questions


def main() -> None:
    parser = argparse.ArgumentParser(
        description="HWP section0.xml에서 문항, 선택지, 정답, 해설을 JSON으로 추출합니다."
    )
    parser.add_argument(
        "input",
        nargs="?",
        type=Path,
        default=DEFAULT_INPUT,
        help="파싱할 section0.xml 경로",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="결과 JSON 파일 경로 (생략하면 표준 출력)",
    )
    args = parser.parse_args()

    result = {"questions": parse_questions(args.input)}
    output = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(output + "\n", encoding="utf-8")
        print(
            f"{len(result['questions'])}개 문항을 {args.output}에 저장했습니다.",
            file=sys.stderr,
        )
    else:
        print(output)


if __name__ == "__main__":
    main()
