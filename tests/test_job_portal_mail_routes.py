import base64
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

import job_portal_mail_routes as mail_routes
import main
from fastapi.testclient import TestClient

_SECRET = "test-mail-secret"


class SendMailAuthTests(unittest.TestCase):
    """這支端點是給 GAS 呼叫的，沒有登入 session，全靠 header 上的共用
    密鑰把關——密鑰沒設定（測試環境預設狀態）時一律 403，等同這個端點
    不存在，跟 main.py 其他內部端點同一種寫法。"""

    def setUp(self):
        self.client = TestClient(main.app)

    def test_no_secret_configured_returns_403(self):
        with mock.patch.object(mail_routes, "JOB_PORTAL_MAIL_WEBHOOK_SECRET", ""):
            resp = self.client.post("/api/job-portal/send-mail", json={}, headers={"X-Job-Portal-Mail-Secret": "x"})
        self.assertEqual(resp.status_code, 403)

    def test_missing_header_returns_403(self):
        with mock.patch.object(mail_routes, "JOB_PORTAL_MAIL_WEBHOOK_SECRET", _SECRET):
            resp = self.client.post("/api/job-portal/send-mail", json={})
        self.assertEqual(resp.status_code, 403)

    def test_wrong_secret_returns_403_without_sending(self):
        with mock.patch.object(mail_routes, "JOB_PORTAL_MAIL_WEBHOOK_SECRET", _SECRET):
            with mock.patch.object(mail_routes.email_service, "send_email") as mock_send:
                resp = self.client.post(
                    "/api/job-portal/send-mail", json={}, headers={"X-Job-Portal-Mail-Secret": "wrong"}
                )
        self.assertEqual(resp.status_code, 403)
        mock_send.assert_not_called()


class SendMailPayloadTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        patcher = mock.patch.object(mail_routes, "JOB_PORTAL_MAIL_WEBHOOK_SECRET", _SECRET)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _post(self, body):
        return self.client.post("/api/job-portal/send-mail", json=body, headers={"X-Job-Portal-Mail-Secret": _SECRET})

    def test_recipients_accept_comma_string_and_are_deduped(self):
        with mock.patch.object(mail_routes.email_service, "send_email", return_value=(True, "")) as mock_send:
            resp = self._post({"to": " a@b.com , c@d.com , a@b.com ", "subject": "主旨", "html": "<p>x</p>"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "success")
        self.assertEqual(mock_send.call_args.args[0], ["a@b.com", "c@d.com"])

    def test_recipients_accept_list(self):
        with mock.patch.object(mail_routes.email_service, "send_email", return_value=(True, "")) as mock_send:
            self._post({"to": ["a@b.com", "c@d.com"], "subject": "主旨", "html": "<p>x</p>"})
        self.assertEqual(mock_send.call_args.args[0], ["a@b.com", "c@d.com"])

    def test_missing_recipients_returns_error_without_sending(self):
        with mock.patch.object(mail_routes.email_service, "send_email") as mock_send:
            resp = self._post({"to": "", "subject": "主旨", "html": "<p>x</p>"})
        self.assertEqual(resp.json()["status"], "error")
        mock_send.assert_not_called()

    def test_missing_subject_or_html_returns_error_without_sending(self):
        with mock.patch.object(mail_routes.email_service, "send_email") as mock_send:
            self.assertEqual(self._post({"to": "a@b.com", "subject": "", "html": "<p>x</p>"}).json()["status"], "error")
            self.assertEqual(self._post({"to": "a@b.com", "subject": "主旨", "html": ""}).json()["status"], "error")
        mock_send.assert_not_called()

    def test_attachments_and_inline_images_are_decoded(self):
        with mock.patch.object(mail_routes.email_service, "send_email", return_value=(True, "")) as mock_send:
            self._post(
                {
                    "to": "a@b.com",
                    "subject": "主旨",
                    "html": '<img src="cid:salaryProofImg">',
                    "attachments": [
                        {
                            "filename": "存查單.pdf",
                            "mime_type": "application/pdf",
                            "base64": base64.b64encode(b"%PDF-fake").decode(),
                        }
                    ],
                    "inline_images": [
                        {
                            "content_id": "salaryProofImg",
                            "filename": "proof.jpg",
                            "mime_type": "image/jpeg",
                            "base64": base64.b64encode(b"fake-jpeg").decode(),
                        }
                    ],
                }
            )
        attachments = mock_send.call_args.kwargs["attachments"]
        inline_images = mock_send.call_args.kwargs["inline_images"]
        self.assertEqual(attachments[0]["content"], b"%PDF-fake")
        self.assertEqual(attachments[0]["filename"], "存查單.pdf")
        self.assertEqual(inline_images[0]["content"], b"fake-jpeg")
        self.assertEqual(inline_images[0]["content_id"], "salaryProofImg")

    def test_inline_image_without_content_id_is_skipped(self):
        # 沒有 content_id 的內嵌圖片對不到 HTML 裡的 cid，留著也沒用，直接跳過。
        with mock.patch.object(mail_routes.email_service, "send_email", return_value=(True, "")) as mock_send:
            self._post(
                {
                    "to": "a@b.com",
                    "subject": "主旨",
                    "html": "<p>x</p>",
                    "inline_images": [{"base64": base64.b64encode(b"x").decode()}],
                }
            )
        self.assertEqual(mock_send.call_args.kwargs["inline_images"], [])

    def test_broken_base64_attachment_is_skipped_but_mail_still_sent(self):
        # 寧可少一個附件也要把信寄出去（見 _decode_parts() 的說明）。
        with mock.patch.object(mail_routes.email_service, "send_email", return_value=(True, "")) as mock_send:
            resp = self._post(
                {
                    "to": "a@b.com",
                    "subject": "主旨",
                    "html": "<p>x</p>",
                    "attachments": [{"filename": "壞檔.pdf", "base64": "這不是base64"}],
                }
            )
        self.assertEqual(resp.json()["status"], "success")
        self.assertEqual(mock_send.call_args.kwargs["attachments"], [])

    def test_send_failure_returns_plain_message_from_email_service(self):
        with mock.patch.object(
            mail_routes.email_service, "send_email", return_value=(False, "今天的寄信額度已經用完，請明天再試")
        ):
            resp = self._post({"to": "a@b.com", "subject": "主旨", "html": "<p>x</p>"})
        self.assertEqual(resp.json()["status"], "error")
        self.assertIn("寄信額度", resp.json()["message"])


if __name__ == "__main__":
    unittest.main()
