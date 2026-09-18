import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

from delivery import repository
from delivery.routes import vehicle_routes


def _fake_doc_snapshot(exists: bool, data: dict = None, doc_id: str = "id1"):
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


class GetVehicleServiceAreaTests(unittest.TestCase):
    """2026-09-18 新增：服務區域從 config.py 的固定清單改成主管可自行
    新增/停用的動態清單（跟裝備借還管理的品項/放置點同一套設計）。"""

    def test_blank_id_returns_none_without_touching_firestore(self):
        with mock.patch.object(repository, "vehicle_service_areas_ref") as mock_ref:
            self.assertIsNone(repository.get_vehicle_service_area(""))
        mock_ref.assert_not_called()

    def test_returns_none_when_missing(self):
        snapshot = _fake_doc_snapshot(False)
        fake_collection, _ = _fake_collection(snapshot)
        with mock.patch.object(repository, "vehicle_service_areas_ref", return_value=fake_collection):
            self.assertIsNone(repository.get_vehicle_service_area("taipei"))

    def test_returns_data_with_id_and_default_active(self):
        snapshot = _fake_doc_snapshot(True, {"name": "台北"}, doc_id="taipei")
        fake_collection, _ = _fake_collection(snapshot)
        with mock.patch.object(repository, "vehicle_service_areas_ref", return_value=fake_collection):
            area = repository.get_vehicle_service_area("taipei")
        self.assertEqual(area["id"], "taipei")
        self.assertEqual(area["name"], "台北")
        self.assertTrue(area["active"])


class CreateVehicleServiceAreaTests(unittest.TestCase):
    def test_explicit_id_used_for_document_id(self):
        fake_doc_ref = mock.Mock()
        fake_doc_ref.id = "taipei"
        fake_collection = mock.Mock()
        fake_collection.document.return_value = fake_doc_ref
        with mock.patch.object(repository, "vehicle_service_areas_ref", return_value=fake_collection):
            result = repository.create_vehicle_service_area("台北", area_id="taipei", created_by="gary")
        fake_collection.document.assert_called_once_with("taipei")
        self.assertEqual(result, "taipei")
        payload = fake_doc_ref.set.call_args.args[0]
        self.assertEqual(payload["name"], "台北")
        self.assertTrue(payload["active"])

    def test_blank_id_uses_auto_generated_document_id(self):
        fake_doc_ref = mock.Mock()
        fake_doc_ref.id = "auto123"
        fake_collection = mock.Mock()
        fake_collection.document.return_value = fake_doc_ref
        with mock.patch.object(repository, "vehicle_service_areas_ref", return_value=fake_collection):
            result = repository.create_vehicle_service_area("新竹", created_by="gary")
        fake_collection.document.assert_called_once_with()
        self.assertEqual(result, "auto123")


class SetVehicleServiceAreaActiveTests(unittest.TestCase):
    def test_updates_active_flag(self):
        snapshot = _fake_doc_snapshot(True)
        fake_collection, fake_doc_ref = _fake_collection(snapshot)
        with mock.patch.object(repository, "vehicle_service_areas_ref", return_value=fake_collection):
            result = repository.set_vehicle_service_area_active("taipei", False)
        self.assertTrue(result)
        fake_doc_ref.update.assert_called_once()
        self.assertFalse(fake_doc_ref.update.call_args.args[0]["active"])

    def test_returns_false_when_missing(self):
        snapshot = _fake_doc_snapshot(False)
        fake_collection, fake_doc_ref = _fake_collection(snapshot)
        with mock.patch.object(repository, "vehicle_service_areas_ref", return_value=fake_collection):
            result = repository.set_vehicle_service_area_active("missing", True)
        self.assertFalse(result)
        fake_doc_ref.update.assert_not_called()


class DeleteVehicleServiceAreaTests(unittest.TestCase):
    def test_deletes_when_no_history(self):
        fake_collection, fake_doc_ref = _fake_collection(_fake_doc_snapshot(True))
        with mock.patch.object(repository, "vehicle_service_areas_ref", return_value=fake_collection):
            with mock.patch.object(repository, "vehicle_service_area_has_history", return_value=False):
                result = repository.delete_vehicle_service_area("taipei")
        self.assertTrue(result)
        fake_doc_ref.delete.assert_called_once()

    def test_refuses_when_vehicles_reference_it(self):
        fake_collection, fake_doc_ref = _fake_collection(_fake_doc_snapshot(True))
        with mock.patch.object(repository, "vehicle_service_areas_ref", return_value=fake_collection):
            with mock.patch.object(repository, "vehicle_service_area_has_history", return_value=True):
                result = repository.delete_vehicle_service_area("taipei")
        self.assertFalse(result)
        fake_doc_ref.delete.assert_not_called()


class SetVehicleServiceAreaValidationTests(unittest.TestCase):
    """set_vehicle_service_area()（車輛詳細頁的更新入口）現在檢查的是
    「這個 ID 存在」而不是「在固定的 SERVICE_AREA_MAP 裡」。"""

    def test_blank_value_clears_it_without_lookup(self):
        vehicle_snapshot = _fake_doc_snapshot(True)
        fake_vehicles, fake_vehicle_doc = _fake_collection(vehicle_snapshot)
        with mock.patch.object(repository, "vehicles_ref", return_value=fake_vehicles):
            with mock.patch.object(repository, "get_vehicle_service_area") as mock_get:
                result = repository.set_vehicle_service_area("ERV-1", "")
        mock_get.assert_not_called()
        self.assertTrue(result)
        fake_vehicle_doc.update.assert_called_once_with({"service_area": ""})

    def test_unknown_id_is_rejected(self):
        with mock.patch.object(repository, "get_vehicle_service_area", return_value=None):
            result = repository.set_vehicle_service_area("ERV-1", "made-up")
        self.assertFalse(result)

    def test_known_id_is_accepted(self):
        vehicle_snapshot = _fake_doc_snapshot(True)
        fake_vehicles, fake_vehicle_doc = _fake_collection(vehicle_snapshot)
        with mock.patch.object(repository, "get_vehicle_service_area", return_value={"id": "taipei", "name": "台北"}):
            with mock.patch.object(repository, "vehicles_ref", return_value=fake_vehicles):
                result = repository.set_vehicle_service_area("ERV-1", "taipei")
        self.assertTrue(result)
        fake_vehicle_doc.update.assert_called_once_with({"service_area": "taipei"})


class _FakeSession(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)


class _FakeRequest:
    def __init__(self, user):
        self.session = _FakeSession({"user": user})


def _admin_account():
    return {"username": "alice", "modules": {"delivery": "staff"}, "is_platform_admin": False, "rank": "manager"}


class ServiceAreaAdminRoutesTests(unittest.TestCase):
    def test_create_service_area_calls_repository(self):
        with mock.patch.object(vehicle_routes.repository, "create_vehicle_service_area") as mock_create:
            resp = vehicle_routes.create_service_area(_FakeRequest(_admin_account()), name="新竹", redirect=None)
        mock_create.assert_called_once_with("新竹", created_by="alice")
        self.assertEqual(resp.status_code, 303)

    def test_blank_name_is_ignored(self):
        with mock.patch.object(vehicle_routes.repository, "create_vehicle_service_area") as mock_create:
            vehicle_routes.create_service_area(_FakeRequest(_admin_account()), name="   ", redirect=None)
        mock_create.assert_not_called()

    def test_toggle_active_calls_repository(self):
        with mock.patch.object(vehicle_routes.repository, "set_vehicle_service_area_active") as mock_set:
            resp = vehicle_routes.toggle_service_area_active(
                "taipei", _FakeRequest(_admin_account()), active="0", redirect=None
            )
        mock_set.assert_called_once_with("taipei", False)
        self.assertEqual(resp.status_code, 303)

    def test_delete_calls_repository(self):
        with mock.patch.object(vehicle_routes.repository, "delete_vehicle_service_area", return_value=True) as mock_delete:
            resp = vehicle_routes.delete_service_area("taipei", _FakeRequest(_admin_account()), redirect=None)
        mock_delete.assert_called_once_with("taipei")
        self.assertEqual(resp.status_code, 303)


if __name__ == "__main__":
    unittest.main()
