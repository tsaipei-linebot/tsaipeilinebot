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


def _fake_doc_snapshot(exists: bool, data: dict = None, doc_id: str = "p1"):
    snapshot = mock.Mock(exists=exists)
    snapshot.id = doc_id
    snapshot.to_dict.return_value = data or {}
    return snapshot


class CreatePersonnelEmployeeNoTests(unittest.TestCase):
    """工號（2026-09-21 新增）：外送員接單媒合用來把 LINE 綁定的騎士連到
    人員名冊正確的那一筆，藉此判斷合作方式屬於承攬還是雇傭。"""

    def test_stores_employee_no(self):
        fake_doc_ref = mock.Mock()
        fake_doc_ref.id = "p1"
        fake_collection = mock.Mock()
        fake_collection.document.return_value = fake_doc_ref
        with mock.patch.object(repository, "personnel_ref", return_value=fake_collection):
            repository.create_personnel(
                "小明", "A123456789", "0912345678", "shopee", "alice", employee_no="E001"
            )
        payload = fake_doc_ref.set.call_args.args[0]
        self.assertEqual(payload["employee_no"], "E001")

    def test_defaults_to_empty_string(self):
        fake_doc_ref = mock.Mock()
        fake_doc_ref.id = "p1"
        fake_collection = mock.Mock()
        fake_collection.document.return_value = fake_doc_ref
        with mock.patch.object(repository, "personnel_ref", return_value=fake_collection):
            repository.create_personnel("小明", "A123456789", "0912345678", "shopee", "alice")
        payload = fake_doc_ref.set.call_args.args[0]
        self.assertEqual(payload["employee_no"], "")


class UpdatePersonnelEmployeeNoTests(unittest.TestCase):
    def test_updates_field(self):
        fake_doc_ref = mock.Mock()
        fake_collection = mock.Mock()
        fake_collection.document.return_value = fake_doc_ref
        with mock.patch.object(repository, "personnel_ref", return_value=fake_collection):
            repository.update_personnel_employee_no("p1", "E002")
        fake_doc_ref.update.assert_called_once()
        self.assertEqual(fake_doc_ref.update.call_args.args[0]["employee_no"], "E002")


class FindPersonnelByEmployeeNoTests(unittest.TestCase):
    def test_blank_employee_no_returns_none_without_touching_firestore(self):
        with mock.patch.object(repository, "personnel_ref") as mock_ref:
            self.assertIsNone(repository.find_personnel_by_employee_no(""))
        mock_ref.assert_not_called()

    def test_returns_matching_personnel(self):
        snapshot = _fake_doc_snapshot(True, {"name": "小明", "employee_no": "E001"})
        fake_query = mock.Mock()
        fake_query.limit.return_value.stream.return_value = [snapshot]
        fake_collection = mock.Mock()
        fake_collection.where.return_value = fake_query
        with mock.patch.object(repository, "personnel_ref", return_value=fake_collection):
            result = repository.find_personnel_by_employee_no("E001")
        fake_collection.where.assert_called_once_with("employee_no", "==", "E001")
        self.assertEqual(result["id"], "p1")
        self.assertEqual(result["name"], "小明")

    def test_returns_none_when_no_match(self):
        fake_query = mock.Mock()
        fake_query.limit.return_value.stream.return_value = []
        fake_collection = mock.Mock()
        fake_collection.where.return_value = fake_query
        with mock.patch.object(repository, "personnel_ref", return_value=fake_collection):
            self.assertIsNone(repository.find_personnel_by_employee_no("missing"))


def _personnel_snapshot(doc_id, name, vendor, employee_no=""):
    snapshot = mock.Mock()
    snapshot.id = doc_id
    snapshot.to_dict.return_value = {"name": name, "vendor": vendor, "employee_no": employee_no}
    return snapshot


class MatchShopeePersonnelEmployeeNoTests(unittest.TestCase):
    """一次性工號搬移（2026-09-21 新增）：只在蝦皮系列廠商裡找姓名唯一
    對得上的一筆才寫入，同名同姓/查無此人/已經有工號的都不動，分開列出
    來讓管理員人工核對。"""

    def test_unique_match_in_shopee_vendor_writes_employee_no(self):
        fake_collection = mock.Mock()
        fake_collection.where.return_value.stream.return_value = [
            _personnel_snapshot("p1", "小明", "shopee"),
        ]
        with mock.patch.object(repository, "personnel_ref", return_value=fake_collection):
            with mock.patch.object(repository, "update_personnel_employee_no") as mock_update:
                result = repository.match_shopee_personnel_employee_no([{"employee_no": "E001", "name": "小明"}])
        mock_update.assert_called_once_with("p1", "E001")
        self.assertEqual(result["matched"], [{"employee_no": "E001", "name": "小明"}])
        self.assertEqual(result["ambiguous"], [])
        self.assertEqual(result["not_found"], [])
        self.assertEqual(result["already_set"], [])

    def test_non_shopee_vendor_is_excluded_and_counts_as_not_found(self):
        fake_collection = mock.Mock()
        fake_collection.where.return_value.stream.return_value = [
            _personnel_snapshot("p1", "小明", "ud"),
        ]
        with mock.patch.object(repository, "personnel_ref", return_value=fake_collection):
            with mock.patch.object(repository, "update_personnel_employee_no") as mock_update:
                result = repository.match_shopee_personnel_employee_no([{"employee_no": "E001", "name": "小明"}])
        mock_update.assert_not_called()
        self.assertEqual(result["not_found"], [{"employee_no": "E001", "name": "小明"}])

    def test_duplicate_name_in_shopee_vendors_is_ambiguous(self):
        fake_collection = mock.Mock()
        fake_collection.where.return_value.stream.return_value = [
            _personnel_snapshot("p1", "小明", "shopee"),
            _personnel_snapshot("p2", "小明", "shopee_contract"),
        ]
        with mock.patch.object(repository, "personnel_ref", return_value=fake_collection):
            with mock.patch.object(repository, "update_personnel_employee_no") as mock_update:
                result = repository.match_shopee_personnel_employee_no([{"employee_no": "E001", "name": "小明"}])
        mock_update.assert_not_called()
        self.assertEqual(result["ambiguous"], [{"employee_no": "E001", "name": "小明"}])

    def test_already_has_employee_no_is_skipped(self):
        fake_collection = mock.Mock()
        fake_collection.where.return_value.stream.return_value = [
            _personnel_snapshot("p1", "小明", "shopee", employee_no="E999"),
        ]
        with mock.patch.object(repository, "personnel_ref", return_value=fake_collection):
            with mock.patch.object(repository, "update_personnel_employee_no") as mock_update:
                result = repository.match_shopee_personnel_employee_no([{"employee_no": "E001", "name": "小明"}])
        mock_update.assert_not_called()
        self.assertEqual(result["already_set"], [{"employee_no": "E001", "name": "小明"}])

    def test_blank_employee_no_or_name_rows_are_skipped_entirely(self):
        with mock.patch.object(repository, "personnel_ref") as mock_ref:
            result = repository.match_shopee_personnel_employee_no(
                [{"employee_no": "", "name": "小明"}, {"employee_no": "E001", "name": ""}]
            )
        mock_ref.assert_not_called()
        self.assertEqual(result, {"matched": [], "ambiguous": [], "not_found": [], "already_set": []})


class _FakeRequest:
    def __init__(self, form_dict):
        self._form_dict = form_dict

    async def form(self):
        return self._form_dict


class BulkUpdatePersonnelEmployeeNoFieldTests(unittest.TestCase):
    PERSON = {"id": "p1", "vendor": "shopee", "cooperation_type": "", "client": ""}

    def test_employee_no_in_form_calls_update(self):
        with mock.patch.object(vendor_routes.repository, "get_personnel", return_value=dict(self.PERSON)):
            with mock.patch.object(vendor_routes.repository, "applicable_doc_types", return_value=[]):
                with mock.patch.object(vendor_routes.repository, "update_personnel_employee_no") as mock_update:
                    asyncio.run(
                        vendor_routes.bulk_update_personnel(
                            "p1", _FakeRequest({"employee_no": "  E003  "}), redirect=None
                        )
                    )
        mock_update.assert_called_once_with("p1", "E003")

    def test_employee_no_absent_from_form_does_not_call_update(self):
        with mock.patch.object(vendor_routes.repository, "get_personnel", return_value=dict(self.PERSON)):
            with mock.patch.object(vendor_routes.repository, "applicable_doc_types", return_value=[]):
                with mock.patch.object(vendor_routes.repository, "update_personnel_employee_no") as mock_update:
                    asyncio.run(vendor_routes.bulk_update_personnel("p1", _FakeRequest({}), redirect=None))
        mock_update.assert_not_called()


if __name__ == "__main__":
    unittest.main()
