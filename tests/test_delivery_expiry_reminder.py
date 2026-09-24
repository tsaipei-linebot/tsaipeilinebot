"""到期提醒（2026-09-24 改版）：排程維持每天 9 點打過來，但只有週一推播；
到期前 30 天內或已過期、還沒更新日期的全部推；放棄報到/離職的人不推。"""
import os
import sys
import unittest
from datetime import date
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

from delivery import repository
from delivery.routes import reminder_routes
from tests._fake_firestore import FakeFirestore

MONDAY = date(2026, 9, 28)
TUESDAY = date(2026, 9, 29)


class ListExpiringDocumentsTests(unittest.TestCase):
    def setUp(self):
        self.db = FakeFirestore()
        people = self.db.collection("delivery_personnel")
        people.document("a").set({"name": "甲", "vendor": "sf", "status": "active", "employment_status": "employed",
                                  "documents": {"sf_insurance": {"expiry_date": "2026-10-20"},
                                                "sf_guild_insurance": {"expiry_date": "2027-06-01"}}})
        people.document("b").set({"name": "乙", "vendor": "ud", "status": "active", "employment_status": "pending_onboard",
                                  "documents": {"police_clearance": {"expiry_date": "2026-09-01"}}})
        people.document("c").set({"name": "丙", "vendor": "ud", "status": "active", "employment_status": "resigned",
                                  "documents": {"police_clearance": {"expiry_date": "2026-09-01"}}})
        people.document("d").set({"name": "丁", "vendor": "shopee", "status": "active",
                                  "documents": {"insurance": {"expiry_date": "2026-09-01"}}})
        patcher = mock.patch.object(repository, "personnel_ref", side_effect=lambda: self.db.collection("delivery_personnel"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_within_30_days_and_expired_are_listed_hidden_statuses_are_not(self):
        items = repository.list_expiring_documents(30, today=MONDAY)
        self.assertEqual([(i["personnel_name"], i["doc_name"], i["expired"]) for i in items],
                         [("乙", "良民證", True), ("甲", "強制險", False)])

    def test_items_not_tracked_for_the_vendor_are_ignored(self):
        """蝦皮三輪已經不追蹤任何證明，舊資料裡的強制險日期不會再被提醒。"""
        names = {i["personnel_name"] for i in repository.list_expiring_documents(30, today=MONDAY)}
        self.assertNotIn("丁", names)


class ReminderRouteTests(unittest.TestCase):
    def _call(self, today, items):
        with mock.patch.object(reminder_routes, "REMINDER_TRIGGER_SECRET", "s"), \
                mock.patch.object(reminder_routes, "_taipei_today", return_value=today), \
                mock.patch.object(reminder_routes.repository, "list_expiring_documents", return_value=items) as mock_list, \
                mock.patch.object(reminder_routes, "push_reminder_message", return_value=True) as mock_push:
            result = reminder_routes.expiry_reminder_check(x_delivery_reminder_secret="s")
        return result, mock_list, mock_push

    def test_not_monday_does_nothing(self):
        result, mock_list, mock_push = self._call(TUESDAY, [])
        self.assertEqual(result["skipped"], "not_reminder_day")
        mock_list.assert_not_called()
        mock_push.assert_not_called()

    def test_monday_pushes_every_item(self):
        items = [{"personnel_name": "甲", "vendor": "sf", "doc_name": "強制險", "expiry_date": "2026-10-20", "expired": False}]
        result, _, mock_push = self._call(MONDAY, items)
        self.assertEqual(result["reminded"], 1)
        self.assertIn("甲（順豐）- 強制險，到期日 2026-10-20", mock_push.call_args[0][0])

    def test_monday_with_nothing_due_does_not_push(self):
        result, _, mock_push = self._call(MONDAY, [])
        self.assertEqual(result["reminded"], 0)
        mock_push.assert_not_called()


if __name__ == "__main__":
    unittest.main()
