import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

from scripts.seed_departments import _DEPARTMENTS, plan_seed


class PlanSeedTests(unittest.TestCase):
    """一次性建立腳本的規劃邏輯（不碰 Firestore 寫入），確保已經存在的部門
    會被跳過、不會重複建立。"""

    def test_no_existing_departments_creates_all(self):
        with patch("scripts.seed_departments.department_name_exists", return_value=False):
            plan = plan_seed()
        self.assertEqual(plan["to_create"], _DEPARTMENTS)
        self.assertEqual(plan["already_exist"], [])

    def test_existing_department_is_skipped_not_duplicated(self):
        def fake_exists(name):
            return name == "管理部"

        with patch("scripts.seed_departments.department_name_exists", side_effect=fake_exists):
            plan = plan_seed()
        self.assertNotIn("管理部", plan["to_create"])
        self.assertIn("管理部", plan["already_exist"])
        self.assertEqual(len(plan["to_create"]), len(_DEPARTMENTS) - 1)

    def test_all_existing_creates_nothing(self):
        with patch("scripts.seed_departments.department_name_exists", return_value=True):
            plan = plan_seed()
        self.assertEqual(plan["to_create"], [])
        self.assertEqual(len(plan["already_exist"]), len(_DEPARTMENTS))

    def test_preserves_display_order(self):
        with patch("scripts.seed_departments.department_name_exists", return_value=False):
            plan = plan_seed()
        self.assertEqual(plan["to_create"][0], "台北所(派遣組)")
        self.assertEqual(plan["to_create"][-1], "財務部")


if __name__ == "__main__":
    unittest.main()
