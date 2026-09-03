import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

from delivery.ocr import extract_expiry_date


class ExtractExpiryDateTests(unittest.TestCase):
    def _mock_client_returning(self, text):
        fake_response = MagicMock()
        fake_response.text = text
        fake_client = MagicMock()
        fake_client.models.generate_content.return_value = fake_response
        return fake_client

    def test_returns_date_on_valid_json_response(self):
        with patch("delivery.ocr._get_client", return_value=self._mock_client_returning('{"expiry_date": "2026-12-31"}')):
            result = extract_expiry_date(b"fake bytes", "image/jpeg")
        self.assertEqual(result, "2026-12-31")

    def test_returns_empty_string_for_non_date_value(self):
        with patch("delivery.ocr._get_client", return_value=self._mock_client_returning('{"expiry_date": "看不清楚"}')):
            result = extract_expiry_date(b"x", "image/jpeg")
        self.assertEqual(result, "")

    def test_returns_empty_string_for_blank_value(self):
        with patch("delivery.ocr._get_client", return_value=self._mock_client_returning('{"expiry_date": ""}')):
            result = extract_expiry_date(b"x", "image/jpeg")
        self.assertEqual(result, "")

    def test_returns_empty_string_when_response_has_no_text(self):
        fake_response = MagicMock()
        fake_response.text = None
        fake_client = MagicMock()
        fake_client.models.generate_content.return_value = fake_response
        with patch("delivery.ocr._get_client", return_value=fake_client):
            result = extract_expiry_date(b"x", "image/jpeg")
        self.assertEqual(result, "")

    def test_returns_empty_string_when_client_creation_raises(self):
        with patch("delivery.ocr._get_client", side_effect=RuntimeError("no credentials in this environment")):
            result = extract_expiry_date(b"x", "image/jpeg")
        self.assertEqual(result, "")

    def test_returns_empty_string_when_response_is_not_json(self):
        with patch("delivery.ocr._get_client", return_value=self._mock_client_returning("not json at all")):
            result = extract_expiry_date(b"x", "image/jpeg")
        self.assertEqual(result, "")


if __name__ == "__main__":
    unittest.main()
