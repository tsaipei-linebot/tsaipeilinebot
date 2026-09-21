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


class BindLineUserTests(unittest.TestCase):
    """LINE 綁定（Phase 2）：文件 ID 直接用 LINE user_id，重複綁定會直接
    覆蓋成最新對應的人員。"""

    def test_bind_stores_personnel_and_bound_at(self):
        fake_doc_ref = mock.Mock()
        fake_collection = mock.Mock()
        fake_collection.document.return_value = fake_doc_ref
        with mock.patch.object(taoyuan_dispatch_service, "bindings_ref", return_value=fake_collection):
            taoyuan_dispatch_service.bind_line_user("U123", "p1", "王小明")
        fake_collection.document.assert_called_once_with("U123")
        payload = fake_doc_ref.set.call_args.args[0]
        self.assertEqual(payload["personnel_id"], "p1")
        self.assertEqual(payload["personnel_name"], "王小明")
        self.assertIn("bound_at", payload)

    def test_get_binding_blank_user_id_returns_none_without_querying(self):
        with mock.patch.object(taoyuan_dispatch_service, "bindings_ref") as mock_ref:
            self.assertIsNone(taoyuan_dispatch_service.get_binding(""))
        mock_ref.assert_not_called()

    def test_get_binding_found(self):
        fake_collection, _ = _fake_collection(_fake_doc_snapshot(True, {"personnel_id": "p1"}, doc_id="U123"))
        with mock.patch.object(taoyuan_dispatch_service, "bindings_ref", return_value=fake_collection):
            result = taoyuan_dispatch_service.get_binding("U123")
        self.assertEqual(result["line_user_id"], "U123")
        self.assertEqual(result["personnel_id"], "p1")

    def test_get_bound_personnel_returns_none_when_not_bound(self):
        with mock.patch.object(taoyuan_dispatch_service, "get_binding", return_value=None):
            self.assertIsNone(taoyuan_dispatch_service.get_bound_personnel("U123"))

    def test_get_bound_personnel_queries_live_and_skips_inactive(self):
        with mock.patch.object(taoyuan_dispatch_service, "get_binding", return_value={"personnel_id": "p1"}):
            with mock.patch.object(
                taoyuan_dispatch_service, "get_personnel", return_value={"id": "p1", "active": False}
            ):
                self.assertIsNone(taoyuan_dispatch_service.get_bound_personnel("U123"))

    def test_get_bound_personnel_returns_active_personnel(self):
        with mock.patch.object(taoyuan_dispatch_service, "get_binding", return_value={"personnel_id": "p1"}):
            with mock.patch.object(
                taoyuan_dispatch_service, "get_personnel", return_value={"id": "p1", "active": True, "name": "王小明"}
            ):
                result = taoyuan_dispatch_service.get_bound_personnel("U123")
        self.assertEqual(result["name"], "王小明")


class CreatePostingTests(unittest.TestCase):
    def test_stores_fields_and_short_code(self):
        fake_doc_ref = mock.Mock()
        fake_doc_ref.id = "abcdefghijklmnop123456"
        fake_collection = mock.Mock()
        fake_collection.document.return_value = fake_doc_ref
        with mock.patch.object(taoyuan_dispatch_service, "postings_ref", return_value=fake_collection):
            posting_id = taoyuan_dispatch_service.create_posting(
                "桃園火車站", 100.0, 200.0, 3, ["restocking", "not-a-real-code"], created_by="alice"
            )
        self.assertEqual(posting_id, "abcdefghijklmnop123456")
        payload = fake_doc_ref.set.call_args.args[0]
        self.assertEqual(payload["required_qualifications"], ["restocking"])
        self.assertEqual(payload["status"], taoyuan_dispatch_service.POSTING_STATUS_OPEN)
        self.assertEqual(payload["short_code"], "123456")


class FindPostingByShortCodeTests(unittest.TestCase):
    def test_blank_code_returns_none_without_querying(self):
        with mock.patch.object(taoyuan_dispatch_service, "postings_ref") as mock_ref:
            self.assertIsNone(taoyuan_dispatch_service.find_posting_by_short_code(""))
        mock_ref.assert_not_called()

    def test_lowercase_input_matches_uppercase_code(self):
        snapshot = _fake_doc_snapshot(True, {"short_code": "ABC123"}, doc_id="pid1")
        fake_query = mock.Mock()
        fake_query.limit.return_value.stream.return_value = [snapshot]
        fake_collection = mock.Mock()
        fake_collection.where.return_value = fake_query
        with mock.patch.object(taoyuan_dispatch_service, "postings_ref", return_value=fake_collection):
            result = taoyuan_dispatch_service.find_posting_by_short_code("abc123")
        self.assertEqual(result["id"], "pid1")
        fake_collection.where.assert_called_once_with("short_code", "==", "ABC123")


class SetPostingStatusTests(unittest.TestCase):
    def test_rejects_unknown_status(self):
        fake_collection, fake_doc_ref = _fake_collection(_fake_doc_snapshot(True))
        with mock.patch.object(taoyuan_dispatch_service, "postings_ref", return_value=fake_collection):
            self.assertFalse(taoyuan_dispatch_service.set_posting_status("p1", "not-a-real-status"))
        fake_doc_ref.update.assert_not_called()

    def test_returns_false_when_missing(self):
        fake_collection, fake_doc_ref = _fake_collection(_fake_doc_snapshot(False))
        with mock.patch.object(taoyuan_dispatch_service, "postings_ref", return_value=fake_collection):
            self.assertFalse(
                taoyuan_dispatch_service.set_posting_status("p1", taoyuan_dispatch_service.POSTING_STATUS_CLOSED)
            )

    def test_updates_existing(self):
        fake_collection, fake_doc_ref = _fake_collection(_fake_doc_snapshot(True))
        with mock.patch.object(taoyuan_dispatch_service, "postings_ref", return_value=fake_collection):
            self.assertTrue(
                taoyuan_dispatch_service.set_posting_status("p1", taoyuan_dispatch_service.POSTING_STATUS_CLOSED)
            )
        payload = fake_doc_ref.update.call_args.args[0]
        self.assertEqual(payload["status"], taoyuan_dispatch_service.POSTING_STATUS_CLOSED)


class CountAndListRegistrationsTests(unittest.TestCase):
    """跟配送部 rider_repository 的 count_registrations()/list_registrations()
    寫法一致：整包 stream() 下來，在 Python 這邊依狀態篩選/排序。"""

    def _snapshot(self, doc_id, data):
        snapshot = mock.Mock()
        snapshot.id = doc_id
        snapshot.to_dict.return_value = data
        return snapshot

    def _fake_query(self, snapshots):
        fake_query = mock.Mock()
        fake_query.stream.return_value = snapshots
        fake_collection = mock.Mock()
        fake_collection.where.return_value = fake_query
        return fake_collection

    def test_count_registrations_filters_by_status(self):
        snapshots = [
            self._snapshot("r1", {"status": "pending"}),
            self._snapshot("r2", {"status": "approved"}),
        ]
        with mock.patch.object(taoyuan_dispatch_service, "registrations_ref", return_value=self._fake_query(snapshots)):
            self.assertEqual(
                taoyuan_dispatch_service.count_registrations(
                    "p1", status=taoyuan_dispatch_service.REGISTRATION_STATUS_APPROVED
                ),
                1,
            )

    def test_list_registrations_sorted_oldest_first(self):
        snapshots = [
            self._snapshot("r1", {"registered_at": 200}),
            self._snapshot("r2", {"registered_at": 100}),
        ]
        with mock.patch.object(taoyuan_dispatch_service, "registrations_ref", return_value=self._fake_query(snapshots)):
            results = taoyuan_dispatch_service.list_registrations("posting1")
        self.assertEqual([r["id"] for r in results], ["r2", "r1"])

    def test_list_registrations_by_personnel_sorted_newest_first_and_limited(self):
        snapshots = [
            self._snapshot("r1", {"registered_at": 100}),
            self._snapshot("r2", {"registered_at": 300}),
            self._snapshot("r3", {"registered_at": 200}),
        ]
        with mock.patch.object(taoyuan_dispatch_service, "registrations_ref", return_value=self._fake_query(snapshots)):
            results = taoyuan_dispatch_service.list_registrations_by_personnel("p1", limit=2)
        self.assertEqual([r["id"] for r in results], ["r2", "r3"])

    def test_list_registrations_by_personnel_no_limit_returns_all(self):
        snapshots = [self._snapshot("r1", {"registered_at": 100}), self._snapshot("r2", {"registered_at": 200})]
        with mock.patch.object(taoyuan_dispatch_service, "registrations_ref", return_value=self._fake_query(snapshots)):
            results = taoyuan_dispatch_service.list_registrations_by_personnel("p1", limit=None)
        self.assertEqual(len(results), 2)


class EvaluateRegistrationTests(unittest.TestCase):
    """_evaluate_registration() 是報名 transaction 的核心決策，抽成純
    函式方便測試（跟配送部 rider_repository._evaluate_registration() 一樣
    的理由，見該檔案同名函式的說明）。"""

    def _posting(self, status=taoyuan_dispatch_service.POSTING_STATUS_OPEN):
        return {"status": status}

    def test_closed_posting_rejected(self):
        ok, message = taoyuan_dispatch_service._evaluate_registration(self._posting(status="closed"), [], "p1")
        self.assertFalse(ok)
        self.assertIn("關閉", message)

    def test_already_registered_rejected(self):
        ok, message = taoyuan_dispatch_service._evaluate_registration(self._posting(), ["p1", "p2"], "p1")
        self.assertFalse(ok)
        self.assertIn("已經報名過", message)

    def test_open_and_not_registered_succeeds(self):
        ok, message = taoyuan_dispatch_service._evaluate_registration(self._posting(), ["p2"], "p1")
        self.assertTrue(ok)
        self.assertIn("已收到您的報名", message)


class UpdateRegistrationStatusTests(unittest.TestCase):
    def test_rejects_unknown_status(self):
        fake_collection, fake_doc_ref = _fake_collection(_fake_doc_snapshot(True))
        with mock.patch.object(taoyuan_dispatch_service, "registrations_ref", return_value=fake_collection):
            self.assertFalse(taoyuan_dispatch_service.update_registration_status("r1", "not-a-real-status"))
        fake_doc_ref.update.assert_not_called()

    def test_returns_false_when_missing(self):
        fake_collection, fake_doc_ref = _fake_collection(_fake_doc_snapshot(False))
        with mock.patch.object(taoyuan_dispatch_service, "registrations_ref", return_value=fake_collection):
            self.assertFalse(
                taoyuan_dispatch_service.update_registration_status(
                    "r1", taoyuan_dispatch_service.REGISTRATION_STATUS_APPROVED
                )
            )

    def test_updates_existing(self):
        fake_collection, fake_doc_ref = _fake_collection(_fake_doc_snapshot(True))
        with mock.patch.object(taoyuan_dispatch_service, "registrations_ref", return_value=fake_collection):
            self.assertTrue(
                taoyuan_dispatch_service.update_registration_status(
                    "r1", taoyuan_dispatch_service.REGISTRATION_STATUS_REJECTED
                )
            )
        payload = fake_doc_ref.update.call_args.args[0]
        self.assertEqual(payload["status"], taoyuan_dispatch_service.REGISTRATION_STATUS_REJECTED)


class ListOpenPostingsForPersonnelTests(unittest.TestCase):
    """人員在 LINE 上查需求列表：只看得到資格符合、還沒報名過的開放需求。"""

    def _posting_snapshot(self, doc_id, data):
        snapshot = mock.Mock()
        snapshot.id = doc_id
        snapshot.to_dict.return_value = data
        return snapshot

    def test_returns_empty_when_personnel_not_found(self):
        with mock.patch.object(taoyuan_dispatch_service, "get_personnel", return_value=None):
            self.assertEqual(taoyuan_dispatch_service.list_open_postings_for_personnel("p1"), [])

    def test_filters_by_qualification_and_already_registered(self):
        personnel = {"id": "p1", "qualifications": ["restocking"]}
        postings = [
            self._posting_snapshot(
                "post1", {"status": "open", "required_qualifications": ["restocking"], "start_time": 200}
            ),
            self._posting_snapshot(
                "post2", {"status": "open", "required_qualifications": ["operator"], "start_time": 100}
            ),
            self._posting_snapshot(
                "post3", {"status": "open", "required_qualifications": ["restocking"], "start_time": 50}
            ),
        ]
        fake_query = mock.Mock()
        fake_query.stream.return_value = postings
        fake_collection = mock.Mock()
        fake_collection.where.return_value = fake_query

        with mock.patch.object(taoyuan_dispatch_service, "get_personnel", return_value=personnel):
            with mock.patch.object(
                taoyuan_dispatch_service,
                "list_registrations_by_personnel",
                return_value=[{"posting_id": "post3"}],
            ):
                with mock.patch.object(taoyuan_dispatch_service, "postings_ref", return_value=fake_collection):
                    results = taoyuan_dispatch_service.list_open_postings_for_personnel("p1")
        # post2 資格不符被排除，post3 已經報名過被排除，只剩 post1
        self.assertEqual([p["id"] for p in results], ["post1"])

    def test_posting_with_no_required_qualifications_visible_to_everyone(self):
        personnel = {"id": "p1", "qualifications": ["restocking"]}
        postings = [self._posting_snapshot("post1", {"status": "open", "required_qualifications": [], "start_time": 1})]
        fake_query = mock.Mock()
        fake_query.stream.return_value = postings
        fake_collection = mock.Mock()
        fake_collection.where.return_value = fake_query

        with mock.patch.object(taoyuan_dispatch_service, "get_personnel", return_value=personnel):
            with mock.patch.object(taoyuan_dispatch_service, "list_registrations_by_personnel", return_value=[]):
                with mock.patch.object(taoyuan_dispatch_service, "postings_ref", return_value=fake_collection):
                    results = taoyuan_dispatch_service.list_open_postings_for_personnel("p1")
        self.assertEqual(len(results), 1)


class ListPostingsFilterTests(unittest.TestCase):
    """管理後台清單的地點/日期篩選——跟配送部 list_shift_postings() 同一種
    在 Python 這邊整包篩選的做法。"""

    def _posting_snapshot(self, doc_id, data):
        snapshot = mock.Mock()
        snapshot.id = doc_id
        snapshot.to_dict.return_value = data
        return snapshot

    def test_filters_by_location_and_date(self):
        from datetime import datetime

        from config import TAIPEI_TZ

        target_ts = TAIPEI_TZ.localize(datetime(2026, 9, 25, 9, 0)).timestamp()
        other_ts = TAIPEI_TZ.localize(datetime(2026, 9, 26, 9, 0)).timestamp()
        postings = [
            self._posting_snapshot(
                "post1", {"location_name": "桃園火車站", "start_time": target_ts, "status": "open"}
            ),
            self._posting_snapshot(
                "post2", {"location_name": "中壢", "start_time": target_ts, "status": "open"}
            ),
            self._posting_snapshot(
                "post3", {"location_name": "桃園火車站", "start_time": other_ts, "status": "open"}
            ),
        ]
        fake_collection = mock.Mock()
        fake_collection.stream.return_value = postings
        fake_reg_query = mock.Mock()
        fake_reg_query.stream.return_value = []
        fake_reg_collection = mock.Mock()
        fake_reg_collection.where.return_value = fake_reg_query

        with mock.patch.object(taoyuan_dispatch_service, "postings_ref", return_value=fake_collection):
            with mock.patch.object(taoyuan_dispatch_service, "registrations_ref", return_value=fake_reg_collection):
                results = taoyuan_dispatch_service.list_postings(location_name="桃園火車站", date_str="2026-09-25")
        self.assertEqual([p["id"] for p in results], ["post1"])


if __name__ == "__main__":
    unittest.main()
