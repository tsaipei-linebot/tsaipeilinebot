import os
import sys
import unittest
from datetime import datetime, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401  (匯入即副作用：見 _env.py 說明)
from tests import _stub_gcp
_stub_gcp.install()

import services.chicken_points_service as cp_service


class ComputeAmountTests(unittest.TestCase):
    def test_exact_multiple(self):
        self.assertEqual(cp_service.compute_amount(5000), 3250)

    def test_rounds_to_nearest_integer(self):
        self.assertEqual(cp_service.compute_amount(101), round(101 * 0.65))

    def test_zero_points(self):
        self.assertEqual(cp_service.compute_amount(0), 0)


class SaveRequestTests(unittest.TestCase):
    """save_request() 寫入 Firestore——用假的 collection/document 物件驗證
    寫進去的欄位內容跟回傳值，不需要真的連線 GCP。"""

    def test_writes_expected_fields_and_returns_id(self):
        fake_doc_ref = mock.Mock()
        fake_doc_ref.id = "abc123"
        fake_collection = mock.Mock()
        fake_collection.document.return_value = fake_doc_ref

        with mock.patch.object(cp_service, "requests_ref", return_value=fake_collection):
            result = cp_service.save_request(
                applicant_username="hu",
                applicant_name="胡少凱",
                department="桃園所",
                purchase_month="2026-08",
                points=5000,
                signed_image_base64="data:image/png;base64,AAAA",
            )

        fake_doc_ref.set.assert_called_once()
        written = fake_doc_ref.set.call_args[0][0]
        self.assertEqual(written["applicant_username"], "hu")
        self.assertEqual(written["applicant_name"], "胡少凱")
        self.assertEqual(written["department"], "桃園所")
        self.assertEqual(written["purchase_month"], "2026-08")
        self.assertEqual(written["points"], 5000)
        self.assertEqual(written["amount"], 3250)
        self.assertEqual(written["signed_image_base64"], "data:image/png;base64,AAAA")
        self.assertIn("created_at", written)
        self.assertEqual(result["id"], "abc123")
        self.assertEqual(result["amount"], 3250)


class ListRequestsTests(unittest.TestCase):
    def _fake_doc(self, doc_id, data):
        doc = mock.Mock()
        doc.id = doc_id
        doc.to_dict.return_value = data
        return doc

    def test_list_all_requests_orders_by_created_at_desc(self):
        fake_collection = mock.Mock()
        fake_query = mock.Mock()
        fake_collection.order_by.return_value = fake_query
        fake_query.stream.return_value = [
            self._fake_doc("d1", {"applicant_name": "小明", "points": 100}),
        ]

        with mock.patch.object(cp_service, "requests_ref", return_value=fake_collection):
            result = cp_service.list_all_requests()

        fake_collection.order_by.assert_called_once_with("created_at", direction="DESCENDING")
        self.assertEqual(result, [{"applicant_name": "小明", "points": 100, "id": "d1"}])

    def test_list_requests_by_username_filters_and_sorts_newest_first(self):
        fake_collection = mock.Mock()
        fake_query = mock.Mock()
        fake_collection.where.return_value = fake_query
        older = datetime(2026, 1, 1, tzinfo=timezone.utc)
        newer = datetime(2026, 6, 1, tzinfo=timezone.utc)
        fake_query.stream.return_value = [
            self._fake_doc("d1", {"applicant_username": "hu", "created_at": older}),
            self._fake_doc("d2", {"applicant_username": "hu", "created_at": newer}),
        ]

        with mock.patch.object(cp_service, "requests_ref", return_value=fake_collection):
            result = cp_service.list_requests_by_username("hu")

        fake_collection.where.assert_called_once_with("applicant_username", "==", "hu")
        self.assertEqual([r["id"] for r in result], ["d2", "d1"])


if __name__ == "__main__":
    unittest.main()
