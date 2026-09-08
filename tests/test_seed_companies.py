import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

from scripts.seed_companies import plan_seed


class PlanSeedTests(unittest.TestCase):
    """一次性建立腳本的規劃邏輯（不碰 Firestore 寫入），確保已經存在的公司
    會被跳過、不會覆蓋既有資料。"""

    def test_no_existing_companies_creates_all(self):
        with patch("scripts.seed_companies.company_exists", return_value=False):
            plan = plan_seed()
        self.assertEqual(len(plan["to_create"]), 10)
        self.assertEqual(plan["already_exist"], [])

    def test_existing_company_is_skipped_not_overwritten(self):
        def fake_exists(short_name):
            return short_name == "材霈"

        with patch("scripts.seed_companies.company_exists", side_effect=fake_exists):
            plan = plan_seed()
        self.assertNotIn("材霈", [c["short_name"] for c in plan["to_create"]])
        self.assertIn("材霈", plan["already_exist"])
        self.assertEqual(len(plan["to_create"]), 9)

    def test_all_existing_creates_nothing(self):
        with patch("scripts.seed_companies.company_exists", return_value=True):
            plan = plan_seed()
        self.assertEqual(plan["to_create"], [])
        self.assertEqual(len(plan["already_exist"]), 10)


if __name__ == "__main__":
    unittest.main()
