import asyncio
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

from delivery import repository
from delivery.routes import vendor_routes


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


class GetCooperationTypeTests(unittest.TestCase):
    """2026-09-18 新增：合作方式從 config.py 的固定清單改成主管可自行
    新增/停用的動態清單，且一筆可以同時套用到多個廠商（跟裝備借還管理的
    品項/放置點、車輛服務區域同一套設計，差在多了 vendors 這個陣列
    欄位）。"""

    def test_blank_id_returns_none_without_touching_firestore(self):
        with mock.patch.object(repository, "cooperation_types_ref") as mock_ref:
            self.assertIsNone(repository.get_cooperation_type(""))
        mock_ref.assert_not_called()

    def test_returns_none_when_missing(self):
        snapshot = _fake_doc_snapshot(False)
        fake_collection, _ = _fake_collection(snapshot)
        with mock.patch.object(repository, "cooperation_types_ref", return_value=fake_collection):
            self.assertIsNone(repository.get_cooperation_type("two_wheel_contract"))

    def test_returns_data_with_id_default_active_and_vendors(self):
        snapshot = _fake_doc_snapshot(
            True, {"name": "二輪承攬", "vendors": ["shopee"]}, doc_id="two_wheel_contract"
        )
        fake_collection, _ = _fake_collection(snapshot)
        with mock.patch.object(repository, "cooperation_types_ref", return_value=fake_collection):
            coop = repository.get_cooperation_type("two_wheel_contract")
        self.assertEqual(coop["id"], "two_wheel_contract")
        self.assertEqual(coop["name"], "二輪承攬")
        self.assertEqual(coop["vendors"], ["shopee"])
        self.assertTrue(coop["active"])

    def test_missing_vendors_field_defaults_to_empty_list(self):
        snapshot = _fake_doc_snapshot(True, {"name": "順豐專用"}, doc_id="x")
        fake_collection, _ = _fake_collection(snapshot)
        with mock.patch.object(repository, "cooperation_types_ref", return_value=fake_collection):
            coop = repository.get_cooperation_type("x")
        self.assertEqual(coop["vendors"], [])


class ListCooperationTypesTests(unittest.TestCase):
    def _snapshot(self, doc_id, data):
        snapshot = mock.Mock()
        snapshot.id = doc_id
        snapshot.to_dict.return_value = data
        return snapshot

    def test_filters_by_vendor_using_array_contains(self):
        fake_query = mock.Mock()
        fake_query.where.return_value = fake_query
        fake_query.stream.return_value = []
        fake_collection = mock.Mock()
        fake_collection.where.return_value = fake_query
        with mock.patch.object(repository, "cooperation_types_ref", return_value=fake_collection):
            repository.list_cooperation_types(vendor="shopee")
        fake_collection.where.assert_called_once_with("vendors", "array_contains", "shopee")

    def test_excludes_inactive_by_default(self):
        fake_collection = mock.Mock()
        fake_collection.stream.return_value = [
            self._snapshot("a", {"name": "A", "vendors": [], "active": True}),
            self._snapshot("b", {"name": "B", "vendors": [], "active": False}),
        ]
        with mock.patch.object(repository, "cooperation_types_ref", return_value=fake_collection):
            result = repository.list_cooperation_types()
        self.assertEqual([c["id"] for c in result], ["a"])

    def test_include_inactive_returns_everything_sorted_by_name(self):
        fake_collection = mock.Mock()
        fake_collection.stream.return_value = [
            self._snapshot("b", {"name": "B", "vendors": [], "active": False}),
            self._snapshot("a", {"name": "A", "vendors": [], "active": True}),
        ]
        with mock.patch.object(repository, "cooperation_types_ref", return_value=fake_collection):
            result = repository.list_cooperation_types(include_inactive=True)
        self.assertEqual([c["id"] for c in result], ["a", "b"])


class CreateCooperationTypeTests(unittest.TestCase):
    def test_explicit_id_used_for_document_id(self):
        fake_doc_ref = mock.Mock()
        fake_doc_ref.id = "two_wheel_contract"
        fake_collection = mock.Mock()
        fake_collection.document.return_value = fake_doc_ref
        with mock.patch.object(repository, "cooperation_types_ref", return_value=fake_collection):
            result = repository.create_cooperation_type(
                "二輪承攬", ["shopee", "shopee_speed_warehouse"], type_id="two_wheel_contract", created_by="gary"
            )
        fake_collection.document.assert_called_once_with("two_wheel_contract")
        self.assertEqual(result, "two_wheel_contract")
        payload = fake_doc_ref.set.call_args.args[0]
        self.assertEqual(payload["name"], "二輪承攬")
        self.assertEqual(payload["vendors"], ["shopee", "shopee_speed_warehouse"])
        self.assertTrue(payload["active"])

    def test_blank_id_uses_auto_generated_document_id(self):
        fake_doc_ref = mock.Mock()
        fake_doc_ref.id = "auto123"
        fake_collection = mock.Mock()
        fake_collection.document.return_value = fake_doc_ref
        with mock.patch.object(repository, "cooperation_types_ref", return_value=fake_collection):
            result = repository.create_cooperation_type("新方式", ["sf"], created_by="gary")
        fake_collection.document.assert_called_once_with()
        self.assertEqual(result, "auto123")


class UpdateCooperationTypeTests(unittest.TestCase):
    def test_updates_name_and_vendors(self):
        fake_collection, fake_doc_ref = _fake_collection(_fake_doc_snapshot(True))
        with mock.patch.object(repository, "cooperation_types_ref", return_value=fake_collection):
            result = repository.update_cooperation_type("x", "新名稱", ["ud", "uc"])
        self.assertTrue(result)
        payload = fake_doc_ref.update.call_args.args[0]
        self.assertEqual(payload["name"], "新名稱")
        self.assertEqual(payload["vendors"], ["ud", "uc"])

    def test_returns_false_when_missing(self):
        fake_collection, fake_doc_ref = _fake_collection(_fake_doc_snapshot(False))
        with mock.patch.object(repository, "cooperation_types_ref", return_value=fake_collection):
            result = repository.update_cooperation_type("missing", "新名稱", [])
        self.assertFalse(result)
        fake_doc_ref.update.assert_not_called()


class SetCooperationTypeActiveTests(unittest.TestCase):
    def test_updates_active_flag(self):
        fake_collection, fake_doc_ref = _fake_collection(_fake_doc_snapshot(True))
        with mock.patch.object(repository, "cooperation_types_ref", return_value=fake_collection):
            result = repository.set_cooperation_type_active("x", False)
        self.assertTrue(result)
        self.assertFalse(fake_doc_ref.update.call_args.args[0]["active"])

    def test_returns_false_when_missing(self):
        fake_collection, fake_doc_ref = _fake_collection(_fake_doc_snapshot(False))
        with mock.patch.object(repository, "cooperation_types_ref", return_value=fake_collection):
            result = repository.set_cooperation_type_active("missing", True)
        self.assertFalse(result)
        fake_doc_ref.update.assert_not_called()


class CooperationTypeHasHistoryTests(unittest.TestCase):
    def test_true_when_personnel_reference_it(self):
        fake_personnel = mock.Mock()
        fake_personnel.where.return_value.limit.return_value.stream.return_value = iter([mock.Mock()])
        with mock.patch.object(repository, "personnel_ref", return_value=fake_personnel):
            self.assertTrue(repository.cooperation_type_has_history("x"))

    def test_true_when_applicants_reference_it(self):
        fake_personnel = mock.Mock()
        fake_personnel.where.return_value.limit.return_value.stream.return_value = iter([])
        fake_applicants = mock.Mock()
        fake_applicants.where.return_value.limit.return_value.stream.return_value = iter([mock.Mock()])
        with mock.patch.object(repository, "personnel_ref", return_value=fake_personnel):
            with mock.patch.object(repository, "applicants_ref", return_value=fake_applicants):
                self.assertTrue(repository.cooperation_type_has_history("x"))

    def test_false_when_no_references(self):
        fake_personnel = mock.Mock()
        fake_personnel.where.return_value.limit.return_value.stream.return_value = iter([])
        fake_applicants = mock.Mock()
        fake_applicants.where.return_value.limit.return_value.stream.return_value = iter([])
        with mock.patch.object(repository, "personnel_ref", return_value=fake_personnel):
            with mock.patch.object(repository, "applicants_ref", return_value=fake_applicants):
                self.assertFalse(repository.cooperation_type_has_history("x"))


class DeleteCooperationTypeTests(unittest.TestCase):
    def test_deletes_when_no_history(self):
        fake_collection, fake_doc_ref = _fake_collection(_fake_doc_snapshot(True))
        with mock.patch.object(repository, "cooperation_types_ref", return_value=fake_collection):
            with mock.patch.object(repository, "cooperation_type_has_history", return_value=False):
                result = repository.delete_cooperation_type("x")
        self.assertTrue(result)
        fake_doc_ref.delete.assert_called_once()

    def test_refuses_when_history_exists(self):
        fake_collection, fake_doc_ref = _fake_collection(_fake_doc_snapshot(True))
        with mock.patch.object(repository, "cooperation_types_ref", return_value=fake_collection):
            with mock.patch.object(repository, "cooperation_type_has_history", return_value=True):
                result = repository.delete_cooperation_type("x")
        self.assertFalse(result)
        fake_doc_ref.delete.assert_not_called()


class _FakeSession(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)


class _FakeRequest:
    def __init__(self, user, form_data=None):
        self.session = _FakeSession({"user": user})
        self._form_data = form_data or {}

    async def form(self):
        return self._form_data


class _FakeFormData:
    """模擬 Starlette FormData：一般欄位用 .get()，多選欄位（多個同名的
    checkbox）用 .getlist()。"""

    def __init__(self, data: dict, lists: dict = None):
        self._data = data
        self._lists = lists or {}

    def get(self, key, default=None):
        return self._data.get(key, default)

    def getlist(self, key):
        return self._lists.get(key, [])


def _admin_account():
    return {"username": "alice", "modules": {"delivery": "staff"}, "is_platform_admin": False, "rank": "manager"}


class CooperationTypeAdminRoutesTests(unittest.TestCase):
    def test_page_marks_has_history_per_row(self):
        types = [{"id": "x", "name": "X", "vendors": [], "active": True}]
        with mock.patch.object(vendor_routes.repository, "list_cooperation_types", return_value=types):
            with mock.patch.object(vendor_routes.repository, "cooperation_type_has_history", return_value=True):
                with mock.patch.object(vendor_routes, "templates") as mock_templates:
                    vendor_routes.cooperation_types_page(_FakeRequest(_admin_account()), redirect=None)
        context = mock_templates.TemplateResponse.call_args[0][2]
        self.assertTrue(context["cooperation_types"][0]["has_history"])

    def test_create_calls_repository_with_checked_vendors(self):
        form = _FakeFormData({"name": "二輪承攬"}, {"vendors": ["shopee", "shopee_speed_warehouse"]})
        with mock.patch.object(vendor_routes.repository, "create_cooperation_type") as mock_create:
            resp = asyncio.run(
                vendor_routes.create_cooperation_type_submit(_FakeRequest(_admin_account(), form), redirect=None)
            )
        mock_create.assert_called_once_with(
            "二輪承攬", ["shopee", "shopee_speed_warehouse"], created_by="alice"
        )
        self.assertEqual(resp.status_code, 303)

    def test_create_ignores_unknown_vendor_codes(self):
        form = _FakeFormData({"name": "測試"}, {"vendors": ["shopee", "made-up"]})
        with mock.patch.object(vendor_routes.repository, "create_cooperation_type") as mock_create:
            asyncio.run(
                vendor_routes.create_cooperation_type_submit(_FakeRequest(_admin_account(), form), redirect=None)
            )
        mock_create.assert_called_once_with("測試", ["shopee"], created_by="alice")

    def test_create_blank_name_is_ignored(self):
        form = _FakeFormData({"name": "   "}, {"vendors": ["shopee"]})
        with mock.patch.object(vendor_routes.repository, "create_cooperation_type") as mock_create:
            asyncio.run(
                vendor_routes.create_cooperation_type_submit(_FakeRequest(_admin_account(), form), redirect=None)
            )
        mock_create.assert_not_called()

    def test_edit_calls_repository(self):
        form = _FakeFormData({"name": "新名稱"}, {"vendors": ["ud"]})
        with mock.patch.object(vendor_routes.repository, "update_cooperation_type") as mock_update:
            resp = asyncio.run(
                vendor_routes.edit_cooperation_type_submit(
                    "x", _FakeRequest(_admin_account(), form), redirect=None
                )
            )
        mock_update.assert_called_once_with("x", "新名稱", ["ud"])
        self.assertEqual(resp.status_code, 303)

    def test_toggle_active_calls_repository(self):
        with mock.patch.object(vendor_routes.repository, "set_cooperation_type_active") as mock_set:
            resp = vendor_routes.toggle_cooperation_type_active(
                "x", _FakeRequest(_admin_account()), active="0", redirect=None
            )
        mock_set.assert_called_once_with("x", False)
        self.assertEqual(resp.status_code, 303)

    def test_delete_calls_repository(self):
        with mock.patch.object(vendor_routes.repository, "delete_cooperation_type", return_value=True) as mock_delete:
            resp = vendor_routes.delete_cooperation_type_submit("x", _FakeRequest(_admin_account()), redirect=None)
        mock_delete.assert_called_once_with("x")
        self.assertEqual(resp.status_code, 303)


if __name__ == "__main__":
    unittest.main()
