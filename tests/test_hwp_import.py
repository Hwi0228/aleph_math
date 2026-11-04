import json
import unittest

from werkzeug.datastructures import MultiDict

from hwp_routes import _validated_import


class HwpImportValidationTests(unittest.TestCase):
    def test_document_without_answer_or_explanation_can_be_imported(self):
        form = MultiDict([
            ("questions_json", json.dumps({
                "questions": [{
                    "number": 1,
                    "question": "지역의 자연환경 특성을 파악하는 문제",
                    "choices": ["교통", "기후"],
                    "answer": "",
                    "explanation": "",
                }]
            }, ensure_ascii=False)),
            ("subject", "수학"),
            ("grade", "고1"),
            ("unit", "테스트 단원"),
            ("source", "업로드 문서"),
            ("license", "ORIGINAL"),
            ("source_type", "기타"),
            ("selected_questions", "0"),
        ])

        questions, selection, errors, error = _validated_import(form)

        self.assertIsNone(error)
        self.assertEqual(errors, [])
        self.assertEqual(questions[0]["answer"], "")
        self.assertEqual(questions[0]["explanation"], "")
        self.assertEqual(selection[1], [0])
