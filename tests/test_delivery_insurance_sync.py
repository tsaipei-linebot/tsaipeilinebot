"""配送系統報到/離職 → 每日加退保待送出清單（2026-09-24 新增，delivery/insurance_sync.py）。"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

from delivery import insurance_sync
from hr import insurance_draft_repository as drafts
from tests._fake_firestore import FakeFirestore

AMY = {"username": "amy", "name": "Amy"}
PERSON = {"id": "p1", "name": "王小明", "vendor": "ud", "id_number": ""}


class SyncStatusChangeTests(unittest.TestCase):
    def setUp(self):
        self.db = FakeFirestore()
        patcher = mock.patch.object(drafts, "insurance_drafts_ref", side_effect=lambda: self.db.collection("hr_insurance_drafts"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def _sync(self, old, new, **dates):
        return insurance_sync.sync_status_change(PERSON, old, new, AMY, **dates)

    def _all(self):
        return drafts.list_drafts("新北所(配送組)")

    def test_onboard_adds_an_insurance_add_draft(self):
        result = self._sync("pending_onboard", "employed", hire_date="2026-09-25")
        self.assertIn("加保", result["msg"])
        [draft] = self._all()
        self.assertEqual((draft["kind"], draft["insured_date"], draft["vendor"], draft["name"], draft["id_number"]),
                         ("add", "2026-09-25", "UD", "王小明", ""))
        self.assertEqual(draft["personnel_id"], "p1")
        self.assertEqual(draft["created_by_name"], "Amy")

    def test_resign_adds_a_remove_draft(self):
        self._sync("employed", "resigned", resign_date="2026-09-30")
        [draft] = self._all()
        self.assertEqual((draft["kind"], draft["withdrawn_date"]), ("remove", "2026-09-30"))

    def test_withdrawn_does_nothing(self):
        self._sync("pending_onboard", "onboard_withdrawn")
        self.assertEqual(self._all(), [])

    def test_undo_resign_cancels_the_pending_remove(self):
        self._sync("employed", "resigned", resign_date="2026-09-30")
        result = self._sync("resigned", "employed")
        self.assertIn("退保已取消", result["msg"])
        [draft] = self._all()
        self.assertEqual(draft["status"], drafts.STATUS_CANCELLED)
        self.assertEqual(len(self._all()), 1)  # 紀錄保留

    def test_undo_onboard_cancels_the_pending_add(self):
        self._sync("pending_onboard", "employed", hire_date="2026-09-25")
        self._sync("employed", "pending_onboard")
        self.assertEqual(self._all()[0]["status"], drafts.STATUS_CANCELLED)

    def test_undo_after_sent_warns_instead(self):
        self._sync("employed", "resigned", resign_date="2026-09-30")
        drafts.mark_sent(self._all(), "2026-09-30", AMY)
        result = self._sync("resigned", "employed")
        self.assertIn("請聯絡人資", result["err"])
        self.assertEqual(self._all()[0]["status"], drafts.STATUS_SENT)

    def test_write_failure_does_not_raise(self):
        with mock.patch.object(drafts, "add_draft", side_effect=RuntimeError("boom")):
            result = self._sync("pending_onboard", "employed", hire_date="2026-09-25")
        self.assertIn("手動新增", result["err"])

    def test_no_change_does_nothing(self):
        self.assertEqual(self._sync("employed", "employed"), {"msg": "", "err": ""})
        self.assertEqual(self._all(), [])


if __name__ == "__main__":
    unittest.main()
