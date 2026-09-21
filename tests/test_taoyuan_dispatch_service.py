import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

import services.taoyuan_dispatch_service as taoyuan_dispatch_service


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


class HasTaoyuanAccessTests(unittest.TestCase):
    """桃園所專區的權限判斷：全平台管理員，或帳號部門是「桃園所」，才算
    有權限——跟 /contract-summary 的部門判斷同一種做法。"""

    def test_no_account_returns_false(self):
        self.assertFalse(taoyuan_dispatch_service.has_taoyuan_access(None))

    def test_platform_admin_always_has_access(self):
        self.assertTrue(taoyuan_dispatch_service.has_taoyuan_access({"is_platform_admin": True, "department": ""}))

    def test_matching_department_has_access(self):
        self.assertTrue(
            taoyuan_dispatch_service.has_taoyuan_access({"is_platform_admin": False, "department": "桃園所"})
        )

    def test_other_department_has_no_access(self):
        self.assertFalse(
            taoyuan_dispatch_service.has_taoyuan_access({"is_platform_admin": False, "department": "新北所"})
        )


class CreatePersonnelTests(unittest.TestCase):
    def test_stores_only_known_qualification_codes(self):
        fake_doc_ref = mock.Mock()
        fake_doc_ref.id = "p1"
        fake_collection = mock.Mock()
        fake_collection.document.return_value = fake_doc_ref
        with mock.patch.object(taoyuan_dispatch_service, "personnel_ref", return_value=fake_collection):
            taoyuan_dispatch_service.create_personnel(
                "王小明", "0912345678", ["restocking", "not-a-real-code"], created_by="alice"
            )
        payload = fake_doc_ref.set.call_args.args[0]
        self.assertEqual(payload["qualifications"], ["restocking"])
        self.assertTrue(payload["active"])
        self.assertEqual(payload["name"], "王小明")


class UpdatePersonnelQualificationsTests(unittest.TestCase):
    def test_returns_false_when_missing(self):
        fake_collection, fake_doc_ref = _fake_collection(_fake_doc_snapshot(False))
        with mock.patch.object(taoyuan_dispatch_service, "personnel_ref", return_value=fake_collection):
            self.assertFalse(taoyuan_dispatch_service.update_personnel_qualifications("p1", ["operator"]))
        fake_doc_ref.update.assert_not_called()

    def test_updates_with_only_known_codes(self):
        fake_collection, fake_doc_ref = _fake_collection(_fake_doc_snapshot(True))
        with mock.patch.object(taoyuan_dispatch_service, "personnel_ref", return_value=fake_collection):
            self.assertTrue(
                taoyuan_dispatch_service.update_personnel_qualifications("p1", ["operator", "bogus"])
            )
        payload = fake_doc_ref.update.call_args.args[0]
        self.assertEqual(payload["qualifications"], ["operator"])


class FindPersonnelByNameAndPhoneTests(unittest.TestCase):
    def test_blank_inputs_return_none_without_querying(self):
        with mock.patch.object(taoyuan_dispatch_service, "personnel_ref") as mock_ref:
            self.assertIsNone(taoyuan_dispatch_service.find_personnel_by_name_and_phone("", ""))
        mock_ref.assert_not_called()

    def test_found_match_returns_personnel_dict(self):
        snapshot = _fake_doc_snapshot(True, {"name": "王小明", "phone": "0912345678"}, doc_id="p1")
        fake_query = mock.Mock()
        fake_query.limit.return_value.stream.return_value = [snapshot]
        fake_collection = mock.Mock()
        fake_collection.where.return_value.where.return_value = fake_query
        with mock.patch.object(taoyuan_dispatch_service, "personnel_ref", return_value=fake_collection):
            result = taoyuan_dispatch_service.find_personnel_by_name_and_phone("王小明", "0912345678")
        self.assertEqual(result["id"], "p1")


class LocationCrudTests(unittest.TestCase):
    def test_create_location_stores_fields(self):
        fake_doc_ref = mock.Mock()
        fake_doc_ref.id = "l1"
        fake_collection = mock.Mock()
        fake_collection.document.return_value = fake_doc_ref
        with mock.patch.object(taoyuan_dispatch_service, "locations_ref", return_value=fake_collection):
            taoyuan_dispatch_service.create_location("桃園火車站", 24.98, 121.31, created_by="alice")
        payload = fake_doc_ref.set.call_args.args[0]
        self.assertEqual(payload["name"], "桃園火車站")
        self.assertEqual(payload["lat"], 24.98)
        self.assertTrue(payload["active"])

    def test_set_location_active_returns_false_when_missing(self):
        fake_collection, fake_doc_ref = _fake_collection(_fake_doc_snapshot(False))
        with mock.patch.object(taoyuan_dispatch_service, "locations_ref", return_value=fake_collection):
            self.assertFalse(taoyuan_dispatch_service.set_location_active("l1", False))
        fake_doc_ref.update.assert_not_called()


class ParsePersonnelCsvTests(unittest.TestCase):
    def test_valid_rows_with_qualification_names_and_codes(self):
        content = (
            "姓名,電話,人員資格\n"
            "王小明,0912345678,理貨、作業員\n"
            "李小華,0987654321,restocking\n"
        ).encode("utf-8")
        rows, header_error = taoyuan_dispatch_service.parse_personnel_csv(content)
        self.assertIsNone(header_error)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["qualifications"], ["restocking", "operator"])
        self.assertEqual(rows[1]["qualifications"], ["restocking"])

    def test_missing_required_header_returns_header_error(self):
        content = "姓名\n王小明\n".encode("utf-8")
        rows, header_error = taoyuan_dispatch_service.parse_personnel_csv(content)
        self.assertIsNotNone(header_error)
        self.assertEqual(rows, [])

    def test_blank_phone_is_reported_as_error(self):
        content = "姓名,電話,人員資格\n王小明,,理貨\n".encode("utf-8")
        rows, header_error = taoyuan_dispatch_service.parse_personnel_csv(content)
        self.assertIsNone(header_error)
        self.assertFalse(rows[0]["ok"])
        self.assertIn("電話", rows[0]["error"])

    def test_unknown_qualification_is_dropped_but_reported_not_a_row_failure(self):
        content = "姓名,電話,人員資格\n王小明,0912345678,不存在的資格\n".encode("utf-8")
        rows, header_error = taoyuan_dispatch_service.parse_personnel_csv(content)
        self.assertIsNone(header_error)
        self.assertTrue(rows[0]["ok"])
        self.assertEqual(rows[0]["qualifications"], [])
        self.assertEqual(rows[0]["unknown_qualifications"], ["不存在的資格"])

    def test_blank_row_is_skipped_silently(self):
        content = "姓名,電話,人員資格\n,,\n王小明,0912345678,\n".encode("utf-8")
        rows, header_error = taoyuan_dispatch_service.parse_personnel_csv(content)
        self.assertIsNone(header_error)
        self.assertEqual(len(rows), 1)


class ParseLocationCsvTests(unittest.TestCase):
    def test_valid_row(self):
        content = "地點,緯度,經度\n桃園火車站,24.9880,121.3141\n".encode("utf-8")
        rows, header_error = taoyuan_dispatch_service.parse_location_csv(content)
        self.assertIsNone(header_error)
        self.assertTrue(rows[0]["ok"])
        self.assertEqual(rows[0]["lat"], 24.9880)

    def test_missing_required_header_returns_header_error(self):
        content = "地點,緯度\n桃園火車站,24.98\n".encode("utf-8")
        rows, header_error = taoyuan_dispatch_service.parse_location_csv(content)
        self.assertIsNotNone(header_error)

    def test_non_numeric_coordinates_reported_as_error(self):
        content = "地點,緯度,經度\n桃園火車站,不是數字,121.3141\n".encode("utf-8")
        rows, header_error = taoyuan_dispatch_service.parse_location_csv(content)
        self.assertIsNone(header_error)
        self.assertFalse(rows[0]["ok"])


if __name__ == "__main__":
    unittest.main()
