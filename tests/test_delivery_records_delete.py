import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

from delivery import repository


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


class DeleteRepaymentTests(unittest.TestCase):
    """新增「主管刪除按鈕」需求：補款登記除了編輯，也要能刪除，但已核准
    的不能刪（見 repository.delete_repayment() 的說明）。"""

    def test_deletes_and_returns_true_when_not_approved(self):
        snapshot = _fake_doc_snapshot(True, {"approved": False})
        fake_collection, fake_doc_ref = _fake_collection(snapshot)
        with mock.patch.object(repository, "repayments_ref", return_value=fake_collection):
            result = repository.delete_repayment("r1")
        self.assertTrue(result)
        fake_doc_ref.delete.assert_called_once()

    def test_refuses_to_delete_when_approved(self):
        snapshot = _fake_doc_snapshot(True, {"approved": True})
        fake_collection, fake_doc_ref = _fake_collection(snapshot)
        with mock.patch.object(repository, "repayments_ref", return_value=fake_collection):
            result = repository.delete_repayment("r1")
        self.assertFalse(result)
        fake_doc_ref.delete.assert_not_called()

    def test_returns_false_when_missing(self):
        snapshot = _fake_doc_snapshot(False)
        fake_collection, fake_doc_ref = _fake_collection(snapshot)
        with mock.patch.object(repository, "repayments_ref", return_value=fake_collection):
            result = repository.delete_repayment("missing")
        self.assertFalse(result)
        fake_doc_ref.delete.assert_not_called()


class DeleteSickLeaveTests(unittest.TestCase):
    def test_deletes_and_returns_true_when_not_approved(self):
        snapshot = _fake_doc_snapshot(True, {"approved": False})
        fake_collection, fake_doc_ref = _fake_collection(snapshot)
        with mock.patch.object(repository, "sick_leaves_ref", return_value=fake_collection):
            result = repository.delete_sick_leave("s1")
        self.assertTrue(result)
        fake_doc_ref.delete.assert_called_once()

    def test_refuses_to_delete_when_approved(self):
        snapshot = _fake_doc_snapshot(True, {"approved": True})
        fake_collection, fake_doc_ref = _fake_collection(snapshot)
        with mock.patch.object(repository, "sick_leaves_ref", return_value=fake_collection):
            result = repository.delete_sick_leave("s1")
        self.assertFalse(result)
        fake_doc_ref.delete.assert_not_called()

    def test_returns_false_when_missing(self):
        snapshot = _fake_doc_snapshot(False)
        fake_collection, fake_doc_ref = _fake_collection(snapshot)
        with mock.patch.object(repository, "sick_leaves_ref", return_value=fake_collection):
            result = repository.delete_sick_leave("missing")
        self.assertFalse(result)


class DeleteVehicleEventTests(unittest.TestCase):
    def test_deletes_and_returns_true_when_exists(self):
        snapshot = _fake_doc_snapshot(True, {"vehicle_no": "ERV-1"})
        fake_collection, fake_doc_ref = _fake_collection(snapshot)
        with mock.patch.object(repository, "vehicle_events_ref", return_value=fake_collection):
            result = repository.delete_vehicle_event("evt1")
        self.assertTrue(result)
        fake_doc_ref.delete.assert_called_once()

    def test_returns_false_when_missing(self):
        snapshot = _fake_doc_snapshot(False)
        fake_collection, fake_doc_ref = _fake_collection(snapshot)
        with mock.patch.object(repository, "vehicle_events_ref", return_value=fake_collection):
            result = repository.delete_vehicle_event("missing")
        self.assertFalse(result)
        fake_doc_ref.delete.assert_not_called()


class DeleteIncidentEventTests(unittest.TestCase):
    def test_deletes_and_returns_true_when_exists(self):
        snapshot = _fake_doc_snapshot(True, {"personnel_name": "小明"})
        fake_collection, fake_doc_ref = _fake_collection(snapshot)
        with mock.patch.object(repository, "incident_events_ref", return_value=fake_collection):
            result = repository.delete_incident_event("inc1")
        self.assertTrue(result)
        fake_doc_ref.delete.assert_called_once()

    def test_returns_false_when_missing(self):
        snapshot = _fake_doc_snapshot(False)
        fake_collection, fake_doc_ref = _fake_collection(snapshot)
        with mock.patch.object(repository, "incident_events_ref", return_value=fake_collection):
            result = repository.delete_incident_event("missing")
        self.assertFalse(result)
        fake_doc_ref.delete.assert_not_called()


if __name__ == "__main__":
    unittest.main()
