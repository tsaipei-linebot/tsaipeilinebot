import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

from scripts.seed_vendors import plan_seed


class PlanSeedTests(unittest.TestCase):
    """一次性建立腳本的規劃邏輯（不碰 Firestore 寫入），確保已經存在的廠商
    會被跳過、不會覆蓋既有資料（例如已經設定好的簽約公司）。"""

    def test_no_existing_vendors_creates_all(self):
        with patch("scripts.seed_vendors.vendor_exists", return_value=False):
            plan = plan_seed()
        self.assertEqual(len(plan["to_create"]), 4)
        self.assertEqual(plan["already_exist"], [])

    def test_existing_vendor_is_skipped_not_overwritten(self):
        def fake_exists(code):
            return code == "shopee"

        with patch("scripts.seed_vendors.vendor_exists", side_effect=fake_exists):
            plan = plan_seed()
        self.assertNotIn("shopee", [v["code"] for v in plan["to_create"]])
        self.assertIn("shopee", plan["already_exist"])
        self.assertEqual(len(plan["to_create"]), 3)

    def test_all_existing_creates_nothing(self):
        with patch("scripts.seed_vendors.vendor_exists", return_value=True):
            plan = plan_seed()
        self.assertEqual(plan["to_create"], [])
        self.assertEqual(len(plan["already_exist"]), 4)


if __name__ == "__main__":
    unittest.main()
