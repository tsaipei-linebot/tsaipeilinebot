import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

from scripts.seed_vehicle_service_areas import plan_seed


class PlanSeedTests(unittest.TestCase):
    """一次性遷移腳本的規劃邏輯（不碰 Firestore 寫入），確保已經存在的
    服務區域會被跳過、不會覆蓋既有資料（例如主管已經改過的名稱）。"""

    def test_no_existing_areas_creates_all(self):
        with patch("scripts.seed_vehicle_service_areas.get_vehicle_service_area", return_value=None):
            plan = plan_seed()
        self.assertEqual(len(plan["to_create"]), 7)
        self.assertEqual(plan["already_exist"], [])

    def test_existing_area_is_skipped_not_overwritten(self):
        def fake_get(area_id):
            return {"id": "taipei", "name": "台北（已改名）"} if area_id == "taipei" else None

        with patch("scripts.seed_vehicle_service_areas.get_vehicle_service_area", side_effect=fake_get):
            plan = plan_seed()
        self.assertNotIn("taipei", [a["id"] for a in plan["to_create"]])
        self.assertIn("taipei", plan["already_exist"])
        self.assertEqual(len(plan["to_create"]), 6)

    def test_all_existing_creates_nothing(self):
        with patch("scripts.seed_vehicle_service_areas.get_vehicle_service_area", return_value={"id": "x"}):
            plan = plan_seed()
        self.assertEqual(plan["to_create"], [])
        self.assertEqual(len(plan["already_exist"]), 7)


if __name__ == "__main__":
    unittest.main()
