import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401  (匯入即副作用：見 _env.py 說明)
from tests import _stub_gcp
_stub_gcp.install()

from delivery import rider_repository


def _fake_doc_snapshot(exists: bool, data: dict = None):
    snapshot = mock.Mock(exists=exists)
    snapshot.to_dict.return_value = data or {}
    return snapshot


def _fake_collection(snapshot):
    fake_doc_ref = mock.Mock()
    fake_doc_ref.get.return_value = snapshot
    fake_collection = mock.Mock()
    fake_collection.document.return_value = fake_doc_ref
    return fake_collection, fake_doc_ref


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


class HasPendingClaimTests(unittest.TestCase):
    """2026-09-19 新增：只檢查、不清掉暫存狀態，給 rider_events.py 判斷
    一則純數字文字看起來是不是真的在回覆承接件數用。"""

    def test_no_binding_returns_false(self):
        with mock.patch.object(rider_repository, "get_rider_binding", return_value=None):
            self.assertFalse(rider_repository.has_pending_claim("U1"))

    def test_no_pending_claim_returns_false(self):
        with mock.patch.object(rider_repository, "get_rider_binding", return_value={"user_id": "U1"}):
            self.assertFalse(rider_repository.has_pending_claim("U1"))

    def test_fresh_pending_claim_returns_true(self):
        binding = {"user_id": "U1", "pending_claim": {"store_id": "store1", "set_at": time.time()}}
        with mock.patch.object(rider_repository, "get_rider_binding", return_value=binding):
            self.assertTrue(rider_repository.has_pending_claim("U1"))

    def test_expired_pending_claim_returns_false(self):
        binding = {
            "user_id": "U1",
            "pending_claim": {"store_id": "store1", "set_at": time.time() - rider_repository.RIDER_PENDING_CLAIM_TTL_SECONDS - 1},
        }
        with mock.patch.object(rider_repository, "get_rider_binding", return_value=binding):
            self.assertFalse(rider_repository.has_pending_claim("U1"))


class AwaitingLocationTests(unittest.TestCase):
    """2026-09-19 新增：跟 pending_claim 同一種暫存機制，但給「查詢附近單
    →分享位置」這條流程用。"""

    def test_set_awaiting_location_updates_binding(self):
        fake_collection, fake_doc_ref = _fake_collection(_fake_doc_snapshot(True))
        with mock.patch.object(rider_repository, "rider_bindings_ref", return_value=fake_collection):
            rider_repository.set_awaiting_location("U1")
        payload = fake_doc_ref.update.call_args.args[0]
        self.assertIn("set_at", payload["awaiting_location"])

    def test_pop_returns_false_when_no_binding(self):
        fake_collection, fake_doc_ref = _fake_collection(_fake_doc_snapshot(False))
        with mock.patch.object(rider_repository, "rider_bindings_ref", return_value=fake_collection):
            self.assertFalse(rider_repository.pop_awaiting_location("U1"))
        fake_doc_ref.update.assert_not_called()

    def test_pop_returns_false_when_nothing_pending(self):
        fake_collection, fake_doc_ref = _fake_collection(_fake_doc_snapshot(True, {}))
        with mock.patch.object(rider_repository, "rider_bindings_ref", return_value=fake_collection):
            self.assertFalse(rider_repository.pop_awaiting_location("U1"))
        fake_doc_ref.update.assert_not_called()

    def test_pop_returns_true_and_clears_when_fresh(self):
        snapshot = _fake_doc_snapshot(True, {"awaiting_location": {"set_at": time.time()}})
        fake_collection, fake_doc_ref = _fake_collection(snapshot)
        with mock.patch.object(rider_repository, "rider_bindings_ref", return_value=fake_collection):
            result = rider_repository.pop_awaiting_location("U1")
        self.assertTrue(result)
        fake_doc_ref.update.assert_called_once_with({"awaiting_location": None})

    def test_pop_returns_false_but_still_clears_when_expired(self):
        set_at = time.time() - rider_repository.RIDER_PENDING_CLAIM_TTL_SECONDS - 1
        snapshot = _fake_doc_snapshot(True, {"awaiting_location": {"set_at": set_at}})
        fake_collection, fake_doc_ref = _fake_collection(snapshot)
        with mock.patch.object(rider_repository, "rider_bindings_ref", return_value=fake_collection):
            result = rider_repository.pop_awaiting_location("U1")
        self.assertFalse(result)
        fake_doc_ref.update.assert_called_once_with({"awaiting_location": None})


class UpsertRiderBindingPersonnelLinkTests(unittest.TestCase):
    """2026-09-21 新增：騎士綁定時拿工號去核對人員名冊，核對到才存
    personnel_id——即時接單/報班媒合的資格判斷靠這個欄位串起來。"""

    def test_matched_employee_no_stores_personnel_id(self):
        fake_collection, fake_doc_ref = _fake_collection(_fake_doc_snapshot(False))
        with mock.patch.object(rider_repository, "rider_bindings_ref", return_value=fake_collection):
            with mock.patch.object(
                rider_repository.repository, "find_personnel_by_employee_no", return_value={"id": "p1", "name": "小明"}
            ) as mock_find:
                rider_repository.upsert_rider_binding("U1", "E001", "小明")
        mock_find.assert_called_once_with("E001")
        payload = fake_doc_ref.set.call_args.args[0]
        self.assertEqual(payload["personnel_id"], "p1")

    def test_unmatched_employee_no_stores_blank_personnel_id(self):
        fake_collection, fake_doc_ref = _fake_collection(_fake_doc_snapshot(False))
        with mock.patch.object(rider_repository, "rider_bindings_ref", return_value=fake_collection):
            with mock.patch.object(rider_repository.repository, "find_personnel_by_employee_no", return_value=None):
                rider_repository.upsert_rider_binding("U1", "no-such-id", "小明")
        payload = fake_doc_ref.set.call_args.args[0]
        self.assertEqual(payload["personnel_id"], "")

    def test_blank_employee_id_does_not_query_personnel(self):
        fake_collection, fake_doc_ref = _fake_collection(_fake_doc_snapshot(False))
        with mock.patch.object(rider_repository, "rider_bindings_ref", return_value=fake_collection):
            with mock.patch.object(rider_repository.repository, "find_personnel_by_employee_no") as mock_find:
                rider_repository.upsert_rider_binding("U1", "", "小明")
        mock_find.assert_not_called()
        payload = fake_doc_ref.set.call_args.args[0]
        self.assertEqual(payload["personnel_id"], "")


class RiderFeatureCategoryTests(unittest.TestCase):
    """2026-09-21 新增：即時接單只給承攬、報班媒合只給雇傭，資格照這個人
    在人員名冊裡「目前」的合作方式即時查詢決定，不是綁定當下寫死。"""

    def test_no_personnel_id_returns_empty_string(self):
        self.assertEqual(rider_repository.rider_feature_category({}), "")

    def test_personnel_not_found_returns_empty_string(self):
        with mock.patch.object(rider_repository.repository, "get_personnel", return_value=None):
            self.assertEqual(rider_repository.rider_feature_category({"personnel_id": "p1"}), "")

    def test_no_cooperation_type_returns_empty_string(self):
        with mock.patch.object(rider_repository.repository, "get_personnel", return_value={"cooperation_type": ""}):
            self.assertEqual(rider_repository.rider_feature_category({"personnel_id": "p1"}), "")

    def test_cooperation_type_without_category_returns_empty_string(self):
        with mock.patch.object(rider_repository.repository, "get_personnel", return_value={"cooperation_type": "x"}):
            with mock.patch.object(rider_repository.repository, "get_cooperation_type", return_value={"category": ""}):
                self.assertEqual(rider_repository.rider_feature_category({"personnel_id": "p1"}), "")

    def test_returns_contract_category(self):
        with mock.patch.object(
            rider_repository.repository, "get_personnel", return_value={"cooperation_type": "two_wheel_contract"}
        ):
            with mock.patch.object(
                rider_repository.repository, "get_cooperation_type", return_value={"category": "contract"}
            ):
                self.assertEqual(rider_repository.rider_feature_category({"personnel_id": "p1"}), "contract")

    def test_returns_employed_category(self):
        with mock.patch.object(
            rider_repository.repository, "get_personnel", return_value={"cooperation_type": "two_wheel_employed"}
        ):
            with mock.patch.object(
                rider_repository.repository, "get_cooperation_type", return_value={"category": "employed"}
            ):
                self.assertEqual(rider_repository.rider_feature_category({"personnel_id": "p1"}), "employed")


if __name__ == "__main__":
    unittest.main()
