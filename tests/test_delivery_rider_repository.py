import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401  (匯入即副作用：見 _env.py 說明)
from tests import _stub_gcp
_stub_gcp.install()

from delivery import rider_repository


class HaversineDistanceTests(unittest.TestCase):
    def test_same_point_is_zero_distance(self):
        self.assertAlmostEqual(rider_repository._haversine_km(25.0, 121.5, 25.0, 121.5), 0.0, places=6)

    def test_known_distance_taipei_to_kaohsiung_roughly_correct(self):
        # 台北 101 到高雄 85 大樓，實際直線距離約 293 公里，只要求數量級正確
        # （這裡不是在測地球幾何本身，是測公式套用對不對）。
        distance = rider_repository._haversine_km(25.0339, 121.5645, 22.6163, 120.3014)
        self.assertGreater(distance, 250)
        self.assertLess(distance, 320)


class EvaluateClaimTests(unittest.TestCase):
    """_evaluate_claim() 是承接 transaction 的核心決策，刻意寫成不依賴
    Firestore 的純函式，這裡直接測邊界情況（剛好用完／超過／已關閉／
    非正整數），不需要真的連 Firestore。"""

    def _store(self, total=10, claimed=0, status="open"):
        return {"total_quantity": total, "claimed_quantity": claimed, "status": status, "store_name": "測試門市"}

    def test_accepts_when_quantity_within_remaining(self):
        ok, message, new_claimed = rider_repository._evaluate_claim(self._store(total=10, claimed=2), 5)
        self.assertTrue(ok)
        self.assertEqual(message, "")
        self.assertEqual(new_claimed, 7)

    def test_accepts_when_quantity_exactly_equals_remaining(self):
        ok, message, new_claimed = rider_repository._evaluate_claim(self._store(total=10, claimed=8), 2)
        self.assertTrue(ok)
        self.assertEqual(new_claimed, 10)

    def test_rejects_when_quantity_exceeds_remaining(self):
        ok, message, new_claimed = rider_repository._evaluate_claim(self._store(total=10, claimed=8), 3)
        self.assertFalse(ok)
        self.assertIn("剩餘可承接量只有 2 件", message)
        self.assertIsNone(new_claimed)

    def test_rejects_when_store_closed(self):
        ok, message, _ = rider_repository._evaluate_claim(self._store(status="closed"), 1)
        self.assertFalse(ok)
        self.assertIn("已經關閉", message)

    def test_rejects_zero_or_negative_quantity(self):
        ok, message, _ = rider_repository._evaluate_claim(self._store(), 0)
        self.assertFalse(ok)
        self.assertIn("大於 0", message)


class EvaluateRegistrationTests(unittest.TestCase):
    """_evaluate_registration() 同樣是報名 transaction 的核心決策，測名額
    已滿／重複報名／已關閉三種邊界情況。"""

    def _shift(self, capacity=3, status="open"):
        return {"capacity": capacity, "status": status}

    def test_accepts_when_capacity_available(self):
        ok, message = rider_repository._evaluate_registration(self._shift(capacity=3), ["r1", "r2"], "r3")
        self.assertTrue(ok)

    def test_rejects_when_capacity_full(self):
        ok, message = rider_repository._evaluate_registration(self._shift(capacity=2), ["r1", "r2"], "r3")
        self.assertFalse(ok)
        self.assertIn("名額已滿", message)

    def test_rejects_duplicate_registration(self):
        ok, message = rider_repository._evaluate_registration(self._shift(capacity=3), ["r1", "r2"], "r1")
        self.assertFalse(ok)
        self.assertIn("已經報名過", message)

    def test_rejects_when_shift_closed(self):
        ok, message = rider_repository._evaluate_registration(self._shift(status="closed"), [], "r1")
        self.assertFalse(ok)
        self.assertIn("已經關閉", message)


if __name__ == "__main__":
    unittest.main()
