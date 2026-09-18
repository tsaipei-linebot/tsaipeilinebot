import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

from scripts.seed_cooperation_types import plan_seed


class PlanSeedTests(unittest.TestCase):
    """一次性遷移腳本的規劃邏輯（不碰 Firestore 寫入），確保已經存在的
    合作方式會被跳過、不會覆蓋既有資料（例如主管已經改過的名稱或適用
    廠商）。"""

    def test_no_existing_types_creates_all(self):
        with patch("scripts.seed_cooperation_types.get_cooperation_type", return_value=None):
            plan = plan_seed()
        self.assertEqual(len(plan["to_create"]), 3)
        self.assertEqual(plan["already_exist"], [])

    def test_existing_type_is_skipped_not_overwritten(self):
        def fake_get(type_id):
            return {"id": "two_wheel_contract", "name": "二輪承攬（已改名）"} if type_id == "two_wheel_contract" else None

        with patch("scripts.seed_cooperation_types.get_cooperation_type", side_effect=fake_get):
            plan = plan_seed()
        self.assertNotIn("two_wheel_contract", [c["id"] for c in plan["to_create"]])
        self.assertIn("two_wheel_contract", plan["already_exist"])
        self.assertEqual(len(plan["to_create"]), 2)

    def test_all_existing_creates_nothing(self):
        with patch("scripts.seed_cooperation_types.get_cooperation_type", return_value={"id": "x"}):
            plan = plan_seed()
        self.assertEqual(plan["to_create"], [])
        self.assertEqual(len(plan["already_exist"]), 3)

    def test_seed_entries_apply_to_shopee_and_shopee_speed_warehouse(self):
        with patch("scripts.seed_cooperation_types.get_cooperation_type", return_value=None):
            plan = plan_seed()
        for coop in plan["to_create"]:
            self.assertEqual(coop["vendors"], ["shopee", "shopee_speed_warehouse"])


if __name__ == "__main__":
    unittest.main()
