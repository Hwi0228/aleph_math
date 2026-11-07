import unittest

from curriculum_data import select_2022_standards


class CurriculumStandardsFilterTests(unittest.TestCase):
    def setUp(self):
        self.records = [
            {
                "key": "[10공수1-01-01]",
                "code": "[10공수1-01-01]",
                "subject_group": "수학",
                "subject": "공통수학1",
                "school_level": "고등학교",
                "grade_group": "고공통",
                "area": "다항식",
                "content": "다항식의 연산 원리를 설명할 수 있다.",
            },
            {
                "key": "[10공수1-02-01]",
                "code": "[10공수1-02-01]",
                "subject_group": "수학",
                "subject": "공통수학1",
                "school_level": "고등학교",
                "grade_group": "고공통",
                "area": "방정식과 부등식",
                "content": "방정식과 부등식을 풀 수 있다.",
            },
            {
                "key": "[12대수01-01]",
                "code": "[12대수01-01]",
                "subject_group": "수학",
                "subject": "대수",
                "school_level": "고등학교",
                "grade_group": "고선택",
                "area": "지수함수와 로그함수",
                "content": "지수와 로그의 성질을 이해한다.",
            },
            {
                "key": "[9과01-01]",
                "code": "[9과01-01]",
                "subject_group": "과학",
                "subject": "과학",
                "school_level": "중학교",
                "grade_group": "중1-3",
                "area": "과학과 인류의 지속가능한 삶",
                "content": "과학의 유용성을 인식한다.",
            },
            {
                "key": "[4과01-01]",
                "code": "[4과01-01]",
                "subject_group": "과학",
                "subject": "과학(초등 3~4학년)",
                "school_level": "초등학교",
                "grade_group": "초3-4",
                "area": "힘과 우리 생활",
                "content": "힘과 관련된 현상을 관찰할 수 있다.",
            },
        ]

    def test_filters_high_school_math_by_course_and_area(self):
        result = select_2022_standards(
            self.records,
            subject="수학",
            grade="고1",
            course="공통수학1",
            area="다항식",
        )
        self.assertEqual(result["courses"], ["공통수학1", "대수"])
        self.assertEqual(result["areas"], ["다항식", "방정식과 부등식"])
        self.assertEqual(
            result["standards"],
            ["[10공수1-01-01] 다항식의 연산 원리를 설명할 수 있다."],
        )
        self.assertTrue(result["matched"])

    def test_returns_all_standards_for_a_course_when_area_is_not_selected(self):
        result = select_2022_standards(
            self.records,
            subject="수학",
            grade="고1",
            course="공통수학1",
        )
        self.assertEqual(len(result["standards"]), 2)
        self.assertEqual(
            result["standards"][0],
            "[10공수1-01-01] 다항식의 연산 원리를 설명할 수 있다.",
        )

    def test_maps_middle_and_elementary_grade_bands(self):
        middle = select_2022_standards(
            self.records, subject="과학", grade="중2"
        )
        elementary = select_2022_standards(
            self.records, subject="과학", grade="초4"
        )
        self.assertEqual(middle["courses"], ["과학"])
        self.assertEqual(elementary["courses"], ["과학(초등 3~4학년)"])

    def test_other_subject_filter_excludes_main_subject_groups(self):
        records = self.records + [{
            "key": "[9도01-01]",
            "code": "[9도01-01]",
            "subject_group": "도덕",
            "subject": "도덕",
            "school_level": "중학교",
            "grade_group": "중1-3",
            "area": "자신과의 관계",
            "content": "자신을 존중하는 태도를 기른다.",
        }]
        result = select_2022_standards(records, subject="기타", grade="중1")
        self.assertEqual(result["courses"], ["도덕"])


if __name__ == "__main__":
    unittest.main()
