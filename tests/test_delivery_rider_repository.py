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


class AwaitingShiftLocationTests(unittest.TestCase):
    """2026-09-21 新增：報班媒合加入服務半徑篩選後，也要先請騎士分享
    位置——跟 AwaitingLocationTests 是同一種機制，存在不同欄位
    （awaiting_shift_location），兩條線互不干擾。"""

    def test_set_awaiting_shift_location_updates_binding(self):
        fake_collection, fake_doc_ref = _fake_collection(_fake_doc_snapshot(True))
        with mock.patch.object(rider_repository, "rider_bindings_ref", return_value=fake_collection):
            rider_repository.set_awaiting_shift_location("U1")
        payload = fake_doc_ref.update.call_args.args[0]
        self.assertIn("set_at", payload["awaiting_shift_location"])

    def test_pop_returns_false_when_nothing_pending(self):
        fake_collection, fake_doc_ref = _fake_collection(_fake_doc_snapshot(True, {}))
        with mock.patch.object(rider_repository, "rider_bindings_ref", return_value=fake_collection):
            self.assertFalse(rider_repository.pop_awaiting_shift_location("U1"))
        fake_doc_ref.update.assert_not_called()

    def test_pop_returns_true_and_clears_when_fresh(self):
        snapshot = _fake_doc_snapshot(True, {"awaiting_shift_location": {"set_at": time.time()}})
        fake_collection, fake_doc_ref = _fake_collection(snapshot)
        with mock.patch.object(rider_repository, "rider_bindings_ref", return_value=fake_collection):
            result = rider_repository.pop_awaiting_shift_location("U1")
        self.assertTrue(result)
        fake_doc_ref.update.assert_called_once_with({"awaiting_shift_location": None})

    def test_pop_returns_false_but_still_clears_when_expired(self):
        set_at = time.time() - rider_repository.RIDER_PENDING_CLAIM_TTL_SECONDS - 1
        snapshot = _fake_doc_snapshot(True, {"awaiting_shift_location": {"set_at": set_at}})
        fake_collection, fake_doc_ref = _fake_collection(snapshot)
        with mock.patch.object(rider_repository, "rider_bindings_ref", return_value=fake_collection):
            result = rider_repository.pop_awaiting_shift_location("U1")
        self.assertFalse(result)
        fake_doc_ref.update.assert_called_once_with({"awaiting_shift_location": None})

    def test_setting_shift_location_does_not_touch_order_location(self):
        """兩個暫存欄位各自獨立：設定報班媒合的暫存狀態，寫入的 payload
        不會動到即時接單那個欄位。"""
        fake_collection, fake_doc_ref = _fake_collection(_fake_doc_snapshot(True))
        with mock.patch.object(rider_repository, "rider_bindings_ref", return_value=fake_collection):
            rider_repository.set_awaiting_shift_location("U1")
        payload = fake_doc_ref.update.call_args.args[0]
        self.assertNotIn("awaiting_location", payload)


class CreateStoreDeliveryRadiusTests(unittest.TestCase):
    """2026-09-21 新增：即時接單開需求時可以調整服務半徑（幾公里內才看得
    到），預設 RIDER_DEFAULT_SEARCH_RADIUS_KM。"""

    def test_defaults_to_config_radius_when_not_given(self):
        fake_doc_ref = mock.Mock()
        fake_doc_ref.id = "s1"
        fake_collection = mock.Mock()
        fake_collection.document.return_value = fake_doc_ref
        with mock.patch.object(rider_repository, "rider_store_deliveries_ref", return_value=fake_collection):
            rider_repository.create_store_delivery("中和門市", 1.0, 2.0, "2026-09-20", 10, "alice")
        payload = fake_doc_ref.set.call_args.args[0]
        self.assertEqual(payload["radius_km"], rider_repository.RIDER_DEFAULT_SEARCH_RADIUS_KM)

    def test_stores_custom_radius(self):
        fake_doc_ref = mock.Mock()
        fake_doc_ref.id = "s1"
        fake_collection = mock.Mock()
        fake_collection.document.return_value = fake_doc_ref
        with mock.patch.object(rider_repository, "rider_store_deliveries_ref", return_value=fake_collection):
            rider_repository.create_store_delivery("中和門市", 1.0, 2.0, "2026-09-20", 10, "alice", radius_km=5)
        payload = fake_doc_ref.set.call_args.args[0]
        self.assertEqual(payload["radius_km"], 5)


class ListNearbyOpenStoresRadiusTests(unittest.TestCase):
    """list_nearby_open_stores() 只回傳在這筆門市自己的 radius_km 範圍內
    的結果——每一筆可以各自設定不同的半徑，不是全域一個值。"""

    def _snapshot(self, doc_id, data):
        snapshot = mock.Mock()
        snapshot.id = doc_id
        snapshot.to_dict.return_value = data
        return snapshot

    def _fake_query(self, snapshots):
        fake_query = mock.Mock()
        fake_query.where.return_value = fake_query
        fake_query.stream.return_value = snapshots
        fake_collection = mock.Mock()
        fake_collection.where.return_value = fake_query
        return fake_collection

    def test_excludes_store_beyond_its_own_radius(self):
        # 中和 (24.9998, 121.4996) 到左營高鐵站 (22.6873, 120.3086) 約 280 公里，遠超過 5 公里半徑
        far_store = self._snapshot(
            "far",
            {
                "store_name": "遠門市", "lat": 22.6873, "lng": 120.3086,
                "total_quantity": 10, "claimed_quantity": 0, "radius_km": 5,
            },
        )
        collection = self._fake_query([far_store])
        with mock.patch.object(rider_repository, "rider_store_deliveries_ref", return_value=collection):
            results = rider_repository.list_nearby_open_stores(24.9998, 121.4996, "2026-09-20")
        self.assertEqual(results, [])

    def test_includes_store_within_its_own_radius(self):
        near_store = self._snapshot(
            "near",
            {
                "store_name": "近門市", "lat": 24.9999, "lng": 121.4997,
                "total_quantity": 10, "claimed_quantity": 0, "radius_km": 5,
            },
        )
        collection = self._fake_query([near_store])
        with mock.patch.object(rider_repository, "rider_store_deliveries_ref", return_value=collection):
            results = rider_repository.list_nearby_open_stores(24.9998, 121.4996, "2026-09-20")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["store_name"], "近門市")

    def test_missing_radius_field_falls_back_to_default(self):
        near_store = self._snapshot(
            "near",
            {"store_name": "近門市", "lat": 24.9999, "lng": 121.4997, "total_quantity": 10, "claimed_quantity": 0},
        )
        collection = self._fake_query([near_store])
        with mock.patch.object(rider_repository, "rider_store_deliveries_ref", return_value=collection):
            results = rider_repository.list_nearby_open_stores(24.9998, 121.4996, "2026-09-20")
        self.assertEqual(len(results), 1)


class CreateShiftPostingRadiusTests(unittest.TestCase):
    """2026-09-21 新增：報班媒合開時段時可以調整服務半徑，時段本身也要
    存經緯度（之前只存地點名稱文字）才有東西可以算距離。"""

    def test_stores_lat_lng_and_default_radius(self):
        fake_doc_ref = mock.Mock()
        fake_doc_ref.id = "sh1"
        fake_collection = mock.Mock()
        fake_collection.document.return_value = fake_doc_ref
        with mock.patch.object(rider_repository, "rider_shift_postings_ref", return_value=fake_collection):
            rider_repository.create_shift_posting("alice", "台北車站", 25.0478, 121.5170, 100.0, 200.0, 3)
        payload = fake_doc_ref.set.call_args.args[0]
        self.assertEqual(payload["lat"], 25.0478)
        self.assertEqual(payload["lng"], 121.5170)
        self.assertEqual(payload["radius_km"], rider_repository.RIDER_DEFAULT_SEARCH_RADIUS_KM)

    def test_stores_custom_radius(self):
        fake_doc_ref = mock.Mock()
        fake_doc_ref.id = "sh1"
        fake_collection = mock.Mock()
        fake_collection.document.return_value = fake_doc_ref
        with mock.patch.object(rider_repository, "rider_shift_postings_ref", return_value=fake_collection):
            rider_repository.create_shift_posting("alice", "台北車站", 25.0478, 121.5170, 100.0, 200.0, 3, radius_km=8)
        payload = fake_doc_ref.set.call_args.args[0]
        self.assertEqual(payload["radius_km"], 8)


class ListOpenShiftPostingsRadiusTests(unittest.TestCase):
    """list_open_shift_postings() 沒給位置時維持原本「全部、依開始時間
    排序」的行為；有給位置時改成只列出各自服務半徑內的時段，依距離排序。"""

    def _snapshot(self, doc_id, data):
        snapshot = mock.Mock()
        snapshot.id = doc_id
        snapshot.to_dict.return_value = data
        return snapshot

    def _fake_query(self, snapshots):
        fake_query = mock.Mock()
        fake_query.where.return_value = fake_query
        fake_query.stream.return_value = snapshots
        fake_collection = mock.Mock()
        fake_collection.where.return_value = fake_query
        return fake_collection

    def test_without_location_returns_all_sorted_by_start_time(self):
        shifts = [
            self._snapshot("later", {"location": "B", "start_time": 200}),
            self._snapshot("earlier", {"location": "A", "start_time": 100}),
        ]
        collection = self._fake_query(shifts)
        with mock.patch.object(rider_repository, "rider_shift_postings_ref", return_value=collection):
            with mock.patch.object(rider_repository, "count_registrations", return_value=0):
                results = rider_repository.list_open_shift_postings()
        self.assertEqual([r["id"] for r in results], ["earlier", "later"])
        self.assertNotIn("distance_km", results[0])

    def test_with_location_excludes_shift_beyond_its_own_radius(self):
        far_shift = self._snapshot(
            "far", {"location": "遠地點", "lat": 22.6873, "lng": 120.3086, "start_time": 100, "radius_km": 5}
        )
        collection = self._fake_query([far_shift])
        with mock.patch.object(rider_repository, "rider_shift_postings_ref", return_value=collection):
            with mock.patch.object(rider_repository, "count_registrations", return_value=0):
                results = rider_repository.list_open_shift_postings(24.9998, 121.4996)
        self.assertEqual(results, [])

    def test_with_location_includes_shift_within_its_own_radius(self):
        near_shift = self._snapshot(
            "near", {"location": "近地點", "lat": 24.9999, "lng": 121.4997, "start_time": 100, "radius_km": 5}
        )
        collection = self._fake_query([near_shift])
        with mock.patch.object(rider_repository, "rider_shift_postings_ref", return_value=collection):
            with mock.patch.object(rider_repository, "count_registrations", return_value=0):
                results = rider_repository.list_open_shift_postings(24.9998, 121.4996)
        self.assertEqual(len(results), 1)
        self.assertIsNotNone(results[0]["distance_km"])

    def test_with_location_legacy_shift_without_coordinates_is_always_included(self):
        legacy_shift = self._snapshot("legacy", {"location": "舊資料沒有經緯度", "start_time": 100})
        collection = self._fake_query([legacy_shift])
        with mock.patch.object(rider_repository, "rider_shift_postings_ref", return_value=collection):
            with mock.patch.object(rider_repository, "count_registrations", return_value=0):
                results = rider_repository.list_open_shift_postings(24.9998, 121.4996)
        self.assertEqual(len(results), 1)
        self.assertIsNone(results[0]["distance_km"])


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
