import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

from hr.repository import (
    care_log_matches_filters,
    health_check_matches_filters,
    license_matches_filters,
    training_matches_filters,
)


class HealthCheckMatchesFiltersTests(unittest.TestCase):
    def test_no_filters_matches(self):
        self.assertTrue(health_check_matches_filters({"personnel_name": "小明", "department": "業務部"}))

    def test_name_filter_matches_substring(self):
        self.assertTrue(
            health_check_matches_filters({"personnel_name": "王小明", "department": ""}, name_filter="小明")
        )

    def test_name_filter_excludes_non_matching(self):
        self.assertFalse(
            health_check_matches_filters({"personnel_name": "王小明", "department": ""}, name_filter="小華")
        )

    def test_department_filter_matches_substring(self):
        self.assertTrue(
            health_check_matches_filters({"personnel_name": "", "department": "業務部"}, department_filter="業務")
        )


class CareLogMatchesFiltersTests(unittest.TestCase):
    def test_empty_keyword_matches_everything(self):
        self.assertTrue(care_log_matches_filters({"subject": "關懷面談", "personnel_name": "", "content": ""}))

    def test_keyword_matches_subject(self):
        self.assertTrue(care_log_matches_filters({"subject": "關懷面談", "personnel_name": "", "content": ""}, "面談"))

    def test_keyword_matches_personnel_name(self):
        self.assertTrue(
            care_log_matches_filters({"subject": "", "personnel_name": "王小明", "content": ""}, "小明")
        )

    def test_keyword_matches_content(self):
        self.assertTrue(
            care_log_matches_filters({"subject": "", "personnel_name": "", "content": "討論工作適應狀況"}, "適應")
        )

    def test_keyword_not_found_excludes(self):
        self.assertFalse(
            care_log_matches_filters({"subject": "關懷面談", "personnel_name": "王小明", "content": "內容"}, "不法侵害")
        )


class LicenseMatchesFiltersTests(unittest.TestCase):
    def test_no_filter_matches(self):
        self.assertTrue(license_matches_filters({"name": "消防安全設備師證照"}))

    def test_name_filter_matches_substring(self):
        self.assertTrue(license_matches_filters({"name": "消防安全設備師證照"}, name_filter="消防"))

    def test_name_filter_excludes_non_matching(self):
        self.assertFalse(license_matches_filters({"name": "消防安全設備師證照"}, name_filter="工廠登記"))


class TrainingMatchesFiltersTests(unittest.TestCase):
    def test_no_filters_matches(self):
        self.assertTrue(training_matches_filters({"personnel_name": "小明", "course_name": "職業安全衛生教育訓練"}))

    def test_name_filter_matches_substring(self):
        self.assertTrue(
            training_matches_filters(
                {"personnel_name": "王小明", "course_name": "職業安全衛生教育訓練"}, name_filter="小明"
            )
        )

    def test_course_filter_matches_substring(self):
        self.assertTrue(
            training_matches_filters(
                {"personnel_name": "王小明", "course_name": "職業安全衛生教育訓練"}, course_filter="安全衛生"
            )
        )

    def test_course_filter_excludes_non_matching(self):
        self.assertFalse(
            training_matches_filters(
                {"personnel_name": "王小明", "course_name": "職業安全衛生教育訓練"}, course_filter="消防"
            )
        )


if __name__ == "__main__":
    unittest.main()
