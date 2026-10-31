import unittest
from types import SimpleNamespace

from parse_rhwp import parse_questions_from_ir


def provenance(index, char_start=None):
    return SimpleNamespace(section_idx=0, para_idx=index, char_start=char_start)


def paragraph(text, index):
    return SimpleNamespace(kind="paragraph", text=text, prov=provenance(index))


class RhwpQuestionParserTests(unittest.TestCase):
    def build_ir(self, blocks):
        cell = SimpleNamespace(row=0, col=0, blocks=blocks)
        table = SimpleNamespace(kind="table", cells=[cell])
        return SimpleNamespace(
            iter_blocks=lambda scope="body", recurse=True: iter([table])
        )

    def test_extracts_question_answer_explanation_and_choices(self):
        blocks = [
            paragraph("[정답] ②[해설]이 값은 $1+1$ 문제 본문", 0),
            paragraph("[정답] ②", 1),
            paragraph("[해설]", 2),
            paragraph("이 값은 ", 3),
            SimpleNamespace(
                kind="formula",
                script="1+1",
                text_alt="1 + 1",
                prov=provenance(3, char_start=len("이 값은 ")),
            ),
            paragraph("추가 조건을 고려하시오.", 4),
            paragraph("① 하나 ② 둘", 5),
        ]
        questions = parse_questions_from_ir(self.build_ir(blocks))
        self.assertEqual(len(questions), 1)
        self.assertEqual(questions[0]["number"], 1)
        self.assertEqual(questions[0]["answer"], "②")
        self.assertEqual(questions[0]["explanation"], "이 값은 $1+1$")
        self.assertEqual(questions[0]["question"], "문제 본문 추가 조건을 고려하시오.")
        self.assertEqual(questions[0]["choices"], ["하나", "둘"])

    def test_picture_block_is_preserved_as_a_placeholder(self):
        blocks = [
            paragraph("[정답] ①[해설]풀이 문제 본문", 0),
            paragraph("[정답] ①", 1),
            paragraph("[해설]", 2),
            paragraph("풀이", 3),
            paragraph("그림을 참고하시오.", 4),
            SimpleNamespace(
                kind="picture",
                description="좌표평면",
                prov=provenance(4, char_start=None),
            ),
        ]
        questions = parse_questions_from_ir(self.build_ir(blocks))
        self.assertIn("[그림: 좌표평면]", questions[0]["question"])

    def test_ignores_tables_without_question_markers(self):
        blocks = [paragraph("일반적인 표 내용", 0)]
        with self.assertRaisesRegex(ValueError, "문항을 찾지 못했습니다"):
            parse_questions_from_ir(self.build_ir(blocks))


if __name__ == "__main__":
    unittest.main()
