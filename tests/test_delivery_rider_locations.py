import os
import sys
import unittest
from unittest import mock
from urllib.parse import unquote

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401  (匯入即副作用：見 _env.py 說明)
from tests import _stub_gcp
_stub_gcp.install()

from delivery import rider_repository
from delivery.routes import rider_routes


def _fake_doc_snapshot(exists: bool, data: dict = None, doc_id: str = "loc1"):
    snapshot = mock.Mock(exists=exists)
    snapshot.id = doc_id
    snapshot.to_dict.return_value = data or {}
    return snapshot


def _fake_collection(snapshot):
    fake_doc_ref = mock.Mock()
    fake_doc_ref.get.return_value = snapshot
    fake_collection = mock.Mock()
    fake_collection.document.return_value = fake_doc_ref
    return fake_collection, fake_doc_ref


class CreateOrderLocationTests(unittest.TestCase):
    def test_sets_active_true_by_default(self):
        fake_doc_ref = mock.Mock()
        fake_doc_ref.id = "loc1"
        fake_collection = mock.Mock()
        fake_collection.document.return_value = fake_doc_ref
        with mock.patch.object(rider_repository, "rider_order_locations_ref", return_value=fake_collection):
            result = rider_repository.create_order_location("中和門市", 24.9998, 121.4996, "alice")
        self.assertEqual(result, "loc1")
        payload = fake_doc_ref.set.call_args.args[0]
        self.assertEqual(payload["name"], "中和門市")
        self.assertEqual(payload["lat"], 24.9998)
        self.assertEqual(payload["lng"], 121.4996)
        self.assertTrue(payload["active"])


class GetOrderLocationTests(unittest.TestCase):
    def test_blank_id_returns_none_without_touching_firestore(self):
        with mock.patch.object(rider_repository, "rider_order_locations_ref") as mock_ref:
            self.assertIsNone(rider_repository.get_order_location(""))
        mock_ref.assert_not_called()

    def test_returns_none_when_missing(self):
        fake_collection, _ = _fake_collection(_fake_doc_snapshot(False))
        with mock.patch.object(rider_repository, "rider_order_locations_ref", return_value=fake_collection):
            self.assertIsNone(rider_repository.get_order_location("loc1"))

    def test_returns_data_with_id(self):
        fake_collection, _ = _fake_collection(_fake_doc_snapshot(True, {"name": "中和門市", "lat": 1.0, "lng": 2.0}))
        with mock.patch.object(rider_repository, "rider_order_locations_ref", return_value=fake_collection):
            location = rider_repository.get_order_location("loc1")
        self.assertEqual(location["id"], "loc1")
        self.assertEqual(location["name"], "中和門市")


class SetOrderLocationActiveTests(unittest.TestCase):
    def test_updates_active_flag(self):
        fake_collection, fake_doc_ref = _fake_collection(_fake_doc_snapshot(True))
        with mock.patch.object(rider_repository, "rider_order_locations_ref", return_value=fake_collection):
            result = rider_repository.set_order_location_active("loc1", False)
        self.assertTrue(result)
        fake_doc_ref.update.assert_called_once_with({"active": False})

    def test_returns_false_when_missing(self):
        fake_collection, fake_doc_ref = _fake_collection(_fake_doc_snapshot(False))
        with mock.patch.object(rider_repository, "rider_order_locations_ref", return_value=fake_collection):
            result = rider_repository.set_order_location_active("missing", True)
        self.assertFalse(result)
        fake_doc_ref.update.assert_not_called()


class CreateShiftLocationTests(unittest.TestCase):
    """報班媒合用的地點清單跟即時接單完全獨立（2026-09-19 拆分），這裡
    確認寫入的是 rider_shift_locations_ref()，不是即時接單那份。"""

    def test_sets_active_true_by_default(self):
        fake_doc_ref = mock.Mock()
        fake_doc_ref.id = "sloc1"
        fake_collection = mock.Mock()
        fake_collection.document.return_value = fake_doc_ref
        with mock.patch.object(rider_repository, "rider_shift_locations_ref", return_value=fake_collection):
            result = rider_repository.create_shift_location("台北車站", 25.0478, 121.5170, "alice")
        self.assertEqual(result, "sloc1")
        payload = fake_doc_ref.set.call_args.args[0]
        self.assertEqual(payload["name"], "台北車站")
        self.assertTrue(payload["active"])


class GetShiftLocationTests(unittest.TestCase):
    def test_blank_id_returns_none_without_touching_firestore(self):
        with mock.patch.object(rider_repository, "rider_shift_locations_ref") as mock_ref:
            self.assertIsNone(rider_repository.get_shift_location(""))
        mock_ref.assert_not_called()

    def test_returns_none_when_missing(self):
        fake_collection, _ = _fake_collection(_fake_doc_snapshot(False))
        with mock.patch.object(rider_repository, "rider_shift_locations_ref", return_value=fake_collection):
            self.assertIsNone(rider_repository.get_shift_location("sloc1"))

    def test_returns_data_with_id(self):
        fake_collection, _ = _fake_collection(
            _fake_doc_snapshot(True, {"name": "台北車站", "lat": 1.0, "lng": 2.0}, doc_id="sloc1")
        )
        with mock.patch.object(rider_repository, "rider_shift_locations_ref", return_value=fake_collection):
            location = rider_repository.get_shift_location("sloc1")
        self.assertEqual(location["id"], "sloc1")
        self.assertEqual(location["name"], "台北車站")


class SetShiftLocationActiveTests(unittest.TestCase):
    def test_updates_active_flag(self):
        fake_collection, fake_doc_ref = _fake_collection(_fake_doc_snapshot(True))
        with mock.patch.object(rider_repository, "rider_shift_locations_ref", return_value=fake_collection):
            result = rider_repository.set_shift_location_active("sloc1", False)
        self.assertTrue(result)
        fake_doc_ref.update.assert_called_once_with({"active": False})

    def test_returns_false_when_missing(self):
        fake_collection, fake_doc_ref = _fake_collection(_fake_doc_snapshot(False))
        with mock.patch.object(rider_repository, "rider_shift_locations_ref", return_value=fake_collection):
            result = rider_repository.set_shift_location_active("missing", True)
        self.assertFalse(result)
        fake_doc_ref.update.assert_not_called()


class _FakeSession(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)


class _FakeRequest:
    def __init__(self, user):
        self.session = _FakeSession({"user": user})


def _staff_account():
    return {"username": "alice", "name": "Alice", "modules": {"delivery": "staff"}, "is_platform_admin": False}


class CreateRiderLocationRouteTests(unittest.TestCase):
    def test_valid_input_calls_repository(self):
        with mock.patch.object(rider_routes.rider_repository, "create_order_location") as mock_create:
            resp = rider_routes.create_rider_location(
                _FakeRequest(_staff_account()), name="中和門市", lat="24.9998", lng="121.4996", redirect=None
            )
        mock_create.assert_called_once_with("中和門市", 24.9998, 121.4996, "alice")
        self.assertEqual(resp.status_code, 303)

    def test_blank_name_is_ignored(self):
        with mock.patch.object(rider_routes.rider_repository, "create_order_location") as mock_create:
            rider_routes.create_rider_location(_FakeRequest(_staff_account()), name="  ", lat="1", lng="2", redirect=None)
        mock_create.assert_not_called()

    def test_invalid_lat_lng_is_ignored(self):
        with mock.patch.object(rider_routes.rider_repository, "create_order_location") as mock_create:
            rider_routes.create_rider_location(
                _FakeRequest(_staff_account()), name="中和門市", lat="not-a-number", lng="121.4996", redirect=None
            )
        mock_create.assert_not_called()


class UpdateRiderLocationActiveRouteTests(unittest.TestCase):
    def test_toggle_calls_repository(self):
        with mock.patch.object(rider_routes.rider_repository, "set_order_location_active") as mock_set:
            resp = rider_routes.update_rider_location_active("loc1", active="0", redirect=None)
        mock_set.assert_called_once_with("loc1", False)
        self.assertEqual(resp.status_code, 303)


class CreateRiderShiftLocationRouteTests(unittest.TestCase):
    def test_valid_input_calls_repository(self):
        with mock.patch.object(rider_routes.rider_repository, "create_shift_location") as mock_create:
            resp = rider_routes.create_rider_shift_location(
                _FakeRequest(_staff_account()), name="台北車站", lat="25.0478", lng="121.5170", redirect=None
            )
        mock_create.assert_called_once_with("台北車站", 25.0478, 121.5170, "alice")
        self.assertEqual(resp.status_code, 303)

    def test_blank_name_is_ignored(self):
        with mock.patch.object(rider_routes.rider_repository, "create_shift_location") as mock_create:
            rider_routes.create_rider_shift_location(_FakeRequest(_staff_account()), name="  ", lat="1", lng="2", redirect=None)
        mock_create.assert_not_called()


class UpdateRiderShiftLocationActiveRouteTests(unittest.TestCase):
    def test_toggle_calls_repository(self):
        with mock.patch.object(rider_routes.rider_repository, "set_shift_location_active") as mock_set:
            resp = rider_routes.update_rider_shift_location_active("sloc1", active="0", redirect=None)
        mock_set.assert_called_once_with("sloc1", False)
        self.assertEqual(resp.status_code, 303)


class CreateRiderStoreDeliveryLocationResolutionTests(unittest.TestCase):
    """建立門市當日量現在收 location_id、伺服器端查即時接單地點主檔決定
    名稱/經緯度（2026-09-19 改版；同日再拆分成獨立地點清單），不再相信
    表單直接送來的經緯度數字。"""

    def test_unknown_location_id_is_rejected(self):
        with mock.patch.object(rider_routes.rider_repository, "get_order_location", return_value=None):
            with mock.patch.object(rider_routes.rider_repository, "create_store_delivery") as mock_create:
                resp = rider_routes.create_rider_store_delivery(
                    _FakeRequest(_staff_account()),
                    location_id="missing",
                    date="2026-09-20",
                    total_quantity="10",
                    rider_capacity="2",
                    redirect=None,
                )
        mock_create.assert_not_called()
        self.assertEqual(resp.status_code, 303)
        self.assertIn("找不到您輸入的地點", unquote(resp.headers["location"]))

    def test_inactive_location_is_rejected(self):
        location = {"id": "loc1", "name": "中和門市", "lat": 1.0, "lng": 2.0, "active": False}
        with mock.patch.object(rider_routes.rider_repository, "get_order_location", return_value=location):
            with mock.patch.object(rider_routes.rider_repository, "create_store_delivery") as mock_create:
                resp = rider_routes.create_rider_store_delivery(
                    _FakeRequest(_staff_account()),
                    location_id="loc1",
                    date="2026-09-20",
                    total_quantity="10",
                    rider_capacity="2",
                    redirect=None,
                )
        mock_create.assert_not_called()
        self.assertEqual(resp.status_code, 303)

    def test_valid_location_resolves_name_and_coordinates(self):
        location = {"id": "loc1", "name": "中和門市", "lat": 24.9998, "lng": 121.4996, "active": True}
        with mock.patch.object(rider_routes.rider_repository, "get_order_location", return_value=location):
            with mock.patch.object(rider_routes.rider_repository, "create_store_delivery") as mock_create:
                resp = rider_routes.create_rider_store_delivery(
                    _FakeRequest(_staff_account()),
                    location_id="loc1",
                    date="2026-09-20",
                    total_quantity="10",
                    rider_capacity="2",
                    radius_km="",
                    redirect=None,
                )
        mock_create.assert_called_once_with(
            "中和門市",
            24.9998,
            121.4996,
            "2026-09-20",
            10,
            2,
            "alice",
            radius_km=rider_routes.RIDER_DEFAULT_SEARCH_RADIUS_KM,
        )
        self.assertEqual(resp.status_code, 303)
        self.assertNotIn("error", resp.headers["location"])

    def test_custom_radius_km_is_passed_through(self):
        location = {"id": "loc1", "name": "中和門市", "lat": 24.9998, "lng": 121.4996, "active": True}
        with mock.patch.object(rider_routes.rider_repository, "get_order_location", return_value=location):
            with mock.patch.object(rider_routes.rider_repository, "create_store_delivery") as mock_create:
                resp = rider_routes.create_rider_store_delivery(
                    _FakeRequest(_staff_account()),
                    location_id="loc1",
                    date="2026-09-20",
                    total_quantity="10",
                    rider_capacity="2",
                    radius_km="5",
                    redirect=None,
                )
        mock_create.assert_called_once_with(
            "中和門市", 24.9998, 121.4996, "2026-09-20", 10, 2, "alice", radius_km=5.0
        )
        self.assertEqual(resp.status_code, 303)

    def test_invalid_radius_km_is_rejected(self):
        location = {"id": "loc1", "name": "中和門市", "lat": 24.9998, "lng": 121.4996, "active": True}
        with mock.patch.object(rider_routes.rider_repository, "get_order_location", return_value=location):
            with mock.patch.object(rider_routes.rider_repository, "create_store_delivery") as mock_create:
                resp = rider_routes.create_rider_store_delivery(
                    _FakeRequest(_staff_account()),
                    location_id="loc1",
                    date="2026-09-20",
                    total_quantity="10",
                    rider_capacity="2",
                    radius_km="0",
                    redirect=None,
                )
        mock_create.assert_not_called()
        self.assertEqual(resp.status_code, 303)
        self.assertIn("服務半徑要大於 0", unquote(resp.headers["location"]))


class CreateRiderStoreDeliveryRiderCapacityTests(unittest.TestCase):
    """2026-09-22 新增：門市當日量除了件數，還要填「需求騎士數量」——這個
    才是實際管控騎士能不能承接的依據（見 rider_repository.py 的
    DEFAULT_RIDER_CAPACITY 說明）。"""

    _LOCATION = {"id": "loc1", "name": "中和門市", "lat": 24.9998, "lng": 121.4996, "active": True}

    def _submit(self, rider_capacity: str):
        with mock.patch.object(rider_routes.rider_repository, "get_order_location", return_value=self._LOCATION):
            with mock.patch.object(rider_routes.rider_repository, "create_store_delivery") as mock_create:
                resp = rider_routes.create_rider_store_delivery(
                    _FakeRequest(_staff_account()),
                    location_id="loc1",
                    date="2026-09-20",
                    total_quantity="30",
                    rider_capacity=rider_capacity,
                    radius_km="",
                    redirect=None,
                )
        return resp, mock_create

    def test_rider_capacity_is_passed_to_repository(self):
        resp, mock_create = self._submit("3")
        self.assertEqual(mock_create.call_args.args[5], 3)
        self.assertEqual(resp.status_code, 303)

    def test_non_numeric_rider_capacity_is_rejected(self):
        resp, mock_create = self._submit("三位")
        mock_create.assert_not_called()
        self.assertIn("需求騎士數量請輸入正確的數字", unquote(resp.headers["location"]))

    def test_zero_rider_capacity_is_rejected(self):
        resp, mock_create = self._submit("0")
        mock_create.assert_not_called()
        self.assertIn("需求騎士數量要大於 0", unquote(resp.headers["location"]))


class UpdateRiderStoreDeliveryQuantitiesTests(unittest.TestCase):
    def test_updates_both_quantity_and_rider_capacity(self):
        with mock.patch.object(rider_routes.rider_repository, "update_store_delivery_quantities") as mock_update:
            resp = rider_routes.update_rider_store_delivery_quantity(
                "store1",
                _FakeRequest(_staff_account()),
                total_quantity="40",
                rider_capacity="4",
                date="2026-09-20",
                redirect=None,
            )
        mock_update.assert_called_once_with("store1", 40, 4, "alice")
        self.assertEqual(resp.status_code, 303)

    def test_invalid_numbers_are_ignored_without_updating(self):
        with mock.patch.object(rider_routes.rider_repository, "update_store_delivery_quantities") as mock_update:
            resp = rider_routes.update_rider_store_delivery_quantity(
                "store1",
                _FakeRequest(_staff_account()),
                total_quantity="40",
                rider_capacity="零",
                date="2026-09-20",
                redirect=None,
            )
        mock_update.assert_not_called()
        self.assertEqual(resp.status_code, 303)

    def test_zero_rider_capacity_is_ignored_without_updating(self):
        with mock.patch.object(rider_routes.rider_repository, "update_store_delivery_quantities") as mock_update:
            rider_routes.update_rider_store_delivery_quantity(
                "store1",
                _FakeRequest(_staff_account()),
                total_quantity="40",
                rider_capacity="0",
                date="2026-09-20",
                redirect=None,
            )
        mock_update.assert_not_called()


class CreateRiderShiftLocationResolutionTests(unittest.TestCase):
    """建立報班時段查的是報班地點主檔（rider_repository.get_shift_location），
    不是即時接單那份（2026-09-19 拆分）。"""

    def test_unknown_location_id_is_rejected(self):
        with mock.patch.object(rider_routes.rider_repository, "get_shift_location", return_value=None):
            with mock.patch.object(rider_routes.rider_repository, "create_shift_posting") as mock_create:
                resp = rider_routes.create_rider_shift(
                    _FakeRequest(_staff_account()),
                    location_id="missing",
                    start_time="2026-09-20T09:00",
                    end_time="2026-09-20T12:00",
                    capacity="3",
                    redirect=None,
                )
        mock_create.assert_not_called()
        self.assertEqual(resp.status_code, 303)

    def test_valid_location_resolves_name(self):
        location = {"id": "sloc1", "name": "台北車站", "lat": 25.0478, "lng": 121.5170, "active": True}
        with mock.patch.object(rider_routes.rider_repository, "get_shift_location", return_value=location):
            with mock.patch.object(rider_routes.rider_repository, "create_shift_posting") as mock_create:
                resp = rider_routes.create_rider_shift(
                    _FakeRequest(_staff_account()),
                    location_id="sloc1",
                    start_time="2026-09-20T09:00",
                    end_time="2026-09-20T12:00",
                    capacity="3",
                    radius_km="",
                    redirect=None,
                )
        mock_create.assert_called_once()
        self.assertEqual(mock_create.call_args.args[1], "台北車站")
        self.assertEqual(mock_create.call_args.args[2], 25.0478)
        self.assertEqual(mock_create.call_args.args[3], 121.5170)
        self.assertEqual(mock_create.call_args.kwargs["radius_km"], rider_routes.RIDER_DEFAULT_SEARCH_RADIUS_KM)
        self.assertEqual(resp.status_code, 303)

    def test_custom_radius_km_is_passed_through(self):
        location = {"id": "sloc1", "name": "台北車站", "lat": 25.0478, "lng": 121.5170, "active": True}
        with mock.patch.object(rider_routes.rider_repository, "get_shift_location", return_value=location):
            with mock.patch.object(rider_routes.rider_repository, "create_shift_posting") as mock_create:
                rider_routes.create_rider_shift(
                    _FakeRequest(_staff_account()),
                    location_id="sloc1",
                    start_time="2026-09-20T09:00",
                    end_time="2026-09-20T12:00",
                    capacity="3",
                    radius_km="8",
                    redirect=None,
                )
        self.assertEqual(mock_create.call_args.kwargs["radius_km"], 8.0)

    def test_does_not_fall_back_to_order_location(self):
        """即使同一個 id 剛好在即時接單地點清單裡存在，建立報班時段也不能
        誤用那份資料——這裡確認完全不會呼叫 get_order_location()。"""
        with mock.patch.object(rider_routes.rider_repository, "get_shift_location", return_value=None):
            with mock.patch.object(rider_routes.rider_repository, "get_order_location") as mock_get_order:
                with mock.patch.object(rider_routes.rider_repository, "create_shift_posting") as mock_create:
                    rider_routes.create_rider_shift(
                        _FakeRequest(_staff_account()),
                        location_id="loc1",
                        start_time="2026-09-20T09:00",
                        end_time="2026-09-20T12:00",
                        capacity="3",
                        redirect=None,
                    )
        mock_get_order.assert_not_called()
        mock_create.assert_not_called()


if __name__ == "__main__":
    unittest.main()
