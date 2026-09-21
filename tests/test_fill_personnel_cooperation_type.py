import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

from scripts.fill_personnel_cooperation_type import plan_fill


class PlanFillTests(unittest.TestCase):
    """一次性遷移腳本的規劃邏輯（不碰 Firestore 寫入）：人員名冊已經選好
    廠商、但合作方式還沒填時，只有廠商目前剛好對應唯一一種合作方式才
    自動補上，不會覆蓋既有資料、也不會亂猜。"""

    COOP_BY_VENDOR = {
        "shopee_contract": [{"id": "shopee_contract_coop", "name": "蝦皮承攬"}],
        "shopee": [
            {"id": "two_wheel_contract", "name": "二輪承攬"},
            {"id": "two_wheel_employed", "name": "二輪雇傭"},
        ],
    }

    def test_unique_option_is_filled(self):
        personnel = [{"id": "p1", "name": "小明", "vendor": "shopee_contract", "cooperation_type": ""}]
        plan = plan_fill(personnel, self.COOP_BY_VENDOR)
        self.assertEqual(len(plan["to_fill"]), 1)
        self.assertEqual(plan["to_fill"][0]["cooperation_type"], "shopee_contract_coop")
        self.assertEqual(plan["to_fill"][0]["cooperation_type_name"], "蝦皮承攬")

    def test_already_set_is_skipped(self):
        personnel = [
            {"id": "p1", "name": "小明", "vendor": "shopee_contract", "cooperation_type": "some_existing_value"}
        ]
        plan = plan_fill(personnel, self.COOP_BY_VENDOR)
        self.assertEqual(plan["to_fill"], [])
        self.assertEqual([r["id"] for r in plan["already_set"]], ["p1"])

    def test_no_vendor_is_skipped(self):
        personnel = [{"id": "p1", "name": "小明", "vendor": "", "cooperation_type": ""}]
        plan = plan_fill(personnel, self.COOP_BY_VENDOR)
        self.assertEqual(plan["to_fill"], [])
        self.assertEqual([r["id"] for r in plan["no_vendor"]], ["p1"])

    def test_multiple_options_is_ambiguous_not_guessed(self):
        personnel = [{"id": "p1", "name": "小明", "vendor": "shopee", "cooperation_type": ""}]
        plan = plan_fill(personnel, self.COOP_BY_VENDOR)
        self.assertEqual(plan["to_fill"], [])
        self.assertEqual(len(plan["ambiguous"]), 1)
        self.assertEqual(plan["ambiguous"][0]["option_count"], 2)

    def test_vendor_with_no_options_is_reported_separately(self):
        personnel = [{"id": "p1", "name": "小明", "vendor": "ud", "cooperation_type": ""}]
        plan = plan_fill(personnel, self.COOP_BY_VENDOR)
        self.assertEqual(plan["to_fill"], [])
        self.assertEqual([r["id"] for r in plan["no_options"]], ["p1"])

    def test_mixed_batch_sorts_into_correct_buckets(self):
        personnel = [
            {"id": "p1", "name": "唯一值", "vendor": "shopee_contract", "cooperation_type": ""},
            {"id": "p2", "name": "已填過", "vendor": "shopee_contract", "cooperation_type": "x"},
            {"id": "p3", "name": "沒廠商", "vendor": "", "cooperation_type": ""},
            {"id": "p4", "name": "多選項", "vendor": "shopee", "cooperation_type": ""},
            {"id": "p5", "name": "沒選項", "vendor": "ud", "cooperation_type": ""},
        ]
        plan = plan_fill(personnel, self.COOP_BY_VENDOR)
        self.assertEqual([r["id"] for r in plan["to_fill"]], ["p1"])
        self.assertEqual([r["id"] for r in plan["already_set"]], ["p2"])
        self.assertEqual([r["id"] for r in plan["no_vendor"]], ["p3"])
        self.assertEqual([r["id"] for r in plan["ambiguous"]], ["p4"])
        self.assertEqual([r["id"] for r in plan["no_options"]], ["p5"])


if __name__ == "__main__":
    unittest.main()
