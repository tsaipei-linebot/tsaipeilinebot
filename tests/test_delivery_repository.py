import os
import sys
import unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

from delivery.repository import all_document_statuses, doc_status, missing_documents


class DocStatusTests(unittest.TestCase):
    def test_missing_file_counts_as_missing(self):
        status = doc_status("id_card", {})
        self.assertTrue(status["missing"])
        self.assertFalse(status["has_file"])

    def test_file_present_without_expiry_type_is_not_missing(self):
        documents = {"id_card": {"file_path": "delivery/personnel-docs/1/a.jpg"}}
        status = doc_status("id_card", documents)
        self.assertFalse(status["missing"])

    def test_expiry_type_with_future_date_is_not_missing(self):
        future = (date.today() + timedelta(days=30)).isoformat()
        documents = {"insurance": {"file_path": "x.jpg", "expiry_date": future}}
        status = doc_status("insurance", documents)
        self.assertFalse(status["missing"])
        self.assertFalse(status["expired"])

    def test_expiry_type_with_past_date_is_missing_and_expired(self):
        past = (date.today() - timedelta(days=1)).isoformat()
        documents = {"police_clearance": {"file_path": "x.jpg", "expiry_date": past}}
        status = doc_status("police_clearance", documents)
        self.assertTrue(status["missing"])
        self.assertTrue(status["expired"])

    def test_malformed_expiry_date_is_ignored_not_crashed(self):
        documents = {"insurance": {"file_path": "x.jpg", "expiry_date": "not-a-date"}}
        status = doc_status("insurance", documents)
        self.assertFalse(status["expired"])


class MissingDocumentsTests(unittest.TestCase):
    def test_no_documents_means_everything_missing(self):
        missing = missing_documents({})
        self.assertEqual(len(missing), 4)

    def test_all_docs_present_and_valid_means_nothing_missing(self):
        future = (date.today() + timedelta(days=30)).isoformat()
        documents = {
            "id_card": {"file_path": "a.jpg"},
            "driver_license": {"file_path": "b.jpg"},
            "insurance": {"file_path": "c.jpg", "expiry_date": future},
            "police_clearance": {"file_path": "d.jpg", "expiry_date": future},
        }
        self.assertEqual(missing_documents(documents), [])

    def test_all_document_statuses_returns_four_entries_in_order(self):
        statuses = all_document_statuses({})
        self.assertEqual([s["code"] for s in statuses], ["id_card", "driver_license", "insurance", "police_clearance"])


if __name__ == "__main__":
    unittest.main()
