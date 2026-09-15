import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

from delivery import repository


def _line_report_data(**overrides):
    """模擬 LINE 群組回報解析後的 dict（見
    delivery.incident_report.parse_incident_report），刻意不含
    license_plate——LINE 範本沒有這一項。"""
    data = {
        "vendor": "ud",
        "identity_type": "雇傭",
        "personnel_name": "林子椉",
        "occurred_at": "2026-09-04 11:00",
        "location": "金山南路一段126號",
        "duty_status": "執行勤務中",
        "police_called": "是",
        "injury": "無",
        "family_contacted": "無",
        "third_party_involved": "有",
        "description": "行進其間與汽車後照鏡擦撞",
    }
    data.update(overrides)
    return data


class CreateIncidentEventLicensePlateTests(unittest.TestCase):
    """2026-09-15 新增：license_plate（車牌號碼）選填、只有網站表單會帶
    這個 key。"""

    def _mock_collection(self):
        fake_doc_ref = mock.Mock()
        fake_collection = mock.Mock()
        fake_collection.document.return_value = fake_doc_ref
        return fake_collection, fake_doc_ref

    def test_new_record_defaults_license_plate_blank_when_absent(self):
        fake_collection, fake_doc_ref = self._mock_collection()
        with mock.patch.object(repository, "_find_incident_event_by_key", return_value=None):
            with mock.patch.object(repository, "incident_events_ref", return_value=fake_collection):
                repository.create_incident_event(_line_report_data())
        payload = fake_doc_ref.set.call_args.args[0]
        self.assertEqual(payload["license_plate"], "")

    def test_new_record_from_website_form_keeps_explicit_license_plate(self):
        fake_collection, fake_doc_ref = self._mock_collection()
        data = _line_report_data(license_plate="ABC-1234")
        with mock.patch.object(repository, "_find_incident_event_by_key", return_value=None):
            with mock.patch.object(repository, "incident_events_ref", return_value=fake_collection):
                repository.create_incident_event(data)
        payload = fake_doc_ref.set.call_args.args[0]
        self.assertEqual(payload["license_plate"], "ABC-1234")

    def test_line_resubmit_does_not_blank_existing_license_plate(self):
        """同仁在 LINE 重傳同一起事件（人員名稱＋發生時間相同，被視為覆寫
        既有紀錄），這次的 data 完全沒有 license_plate 這個 key——不能因此
        把先前網站上補登的車牌號碼洗成空白。"""
        fake_collection, fake_doc_ref = self._mock_collection()
        with mock.patch.object(repository, "_find_incident_event_by_key", return_value="existing-id"):
            with mock.patch.object(repository, "incident_events_ref", return_value=fake_collection):
                incident_id, created = repository.create_incident_event(_line_report_data())
        self.assertEqual(incident_id, "existing-id")
        self.assertFalse(created)
        payload = fake_doc_ref.update.call_args.args[0]
        self.assertNotIn("license_plate", payload)

    def test_website_resubmit_can_explicitly_clear_license_plate(self):
        """網站表單編輯時刻意把車牌清空送出是明確的動作，要真的寫入空
        字串，跟 LINE 重傳「根本沒帶這個欄位」不同。"""
        fake_collection, fake_doc_ref = self._mock_collection()
        data = _line_report_data(license_plate="")
        with mock.patch.object(repository, "_find_incident_event_by_key", return_value="existing-id"):
            with mock.patch.object(repository, "incident_events_ref", return_value=fake_collection):
                repository.create_incident_event(data)
        payload = fake_doc_ref.update.call_args.args[0]
        self.assertIn("license_plate", payload)
        self.assertEqual(payload["license_plate"], "")


class UpdateIncidentEventLicensePlateTests(unittest.TestCase):
    """update_incident_event()（網站管理員編輯表單）一律明確帶
    license_plate，跟 create_incident_event() 的 LINE 覆寫情境不同，不需要
    特殊處理，直接覆寫即可。"""

    def test_update_writes_license_plate(self):
        fake_snapshot = mock.Mock(exists=True)
        fake_doc_ref = mock.Mock()
        fake_doc_ref.get.return_value = fake_snapshot
        fake_collection = mock.Mock()
        fake_collection.document.return_value = fake_doc_ref
        with mock.patch.object(repository, "incident_events_ref", return_value=fake_collection):
            result = repository.update_incident_event("inc1", _line_report_data(license_plate="XYZ-9999"))
        self.assertTrue(result)
        payload = fake_doc_ref.update.call_args.args[0]
        self.assertEqual(payload["license_plate"], "XYZ-9999")


class RepaymentEditRepositoryTests(unittest.TestCase):
    """`repository.get_repayment()` / `update_repayment()`：2026-09-15
    新增，讓補款登記能修正金額/日期等打錯的內容。"""

    def test_get_repayment_returns_none_when_missing(self):
        fake_snapshot = mock.Mock(exists=False)
        fake_doc_ref = mock.Mock()
        fake_doc_ref.get.return_value = fake_snapshot
        fake_collection = mock.Mock()
        fake_collection.document.return_value = fake_doc_ref
        with mock.patch.object(repository, "repayments_ref", return_value=fake_collection):
            self.assertIsNone(repository.get_repayment("missing"))

    def test_get_repayment_returns_data_with_id(self):
        fake_snapshot = mock.Mock(exists=True)
        fake_snapshot.id = "r1"
        fake_snapshot.to_dict.return_value = {"personnel_name": "林子椉", "amount": 500, "approved": True}
        fake_doc_ref = mock.Mock()
        fake_doc_ref.get.return_value = fake_snapshot
        fake_collection = mock.Mock()
        fake_collection.document.return_value = fake_doc_ref
        with mock.patch.object(repository, "repayments_ref", return_value=fake_collection):
            record = repository.get_repayment("r1")
        self.assertEqual(record["id"], "r1")
        self.assertEqual(record["personnel_name"], "林子椉")
        self.assertTrue(record["approved"])

    def test_update_repayment_writes_fields_and_returns_true(self):
        fake_snapshot = mock.Mock(exists=True)
        fake_doc_ref = mock.Mock()
        fake_doc_ref.get.return_value = fake_snapshot
        fake_collection = mock.Mock()
        fake_collection.document.return_value = fake_doc_ref
        with mock.patch.object(repository, "repayments_ref", return_value=fake_collection):
            result = repository.update_repayment(
                "r1", vendor="ud", personnel_name="林子椉", amount=500.0, reason="補款", occurred_date="2026-09-15"
            )
        self.assertTrue(result)
        fake_doc_ref.update.assert_called_once_with(
            {
                "vendor": "ud",
                "personnel_name": "林子椉",
                "amount": 500.0,
                "reason": "補款",
                "occurred_date": "2026-09-15",
            }
        )

    def test_update_repayment_returns_false_when_missing(self):
        fake_snapshot = mock.Mock(exists=False)
        fake_doc_ref = mock.Mock()
        fake_doc_ref.get.return_value = fake_snapshot
        fake_collection = mock.Mock()
        fake_collection.document.return_value = fake_doc_ref
        with mock.patch.object(repository, "repayments_ref", return_value=fake_collection):
            result = repository.update_repayment(
                "missing", vendor="ud", personnel_name="x", amount=1.0, reason="", occurred_date="2026-09-15"
            )
        self.assertFalse(result)
        fake_doc_ref.update.assert_not_called()


if __name__ == "__main__":
    unittest.main()
