import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401

from services import email_service


class IsConfiguredTests(unittest.TestCase):
    def test_missing_any_setting_counts_as_not_configured(self):
        for missing in ("SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD"):
            with mock.patch.object(email_service, "SMTP_HOST", "smtp.example.com"):
                with mock.patch.object(email_service, "SMTP_USERNAME", "a@b.com"):
                    with mock.patch.object(email_service, "SMTP_PASSWORD", "pw"):
                        with mock.patch.object(email_service, missing, ""):
                            self.assertFalse(email_service.is_configured(), missing)

    def test_all_settings_present_counts_as_configured(self):
        with mock.patch.object(email_service, "SMTP_HOST", "smtp.example.com"):
            with mock.patch.object(email_service, "SMTP_USERNAME", "a@b.com"):
                with mock.patch.object(email_service, "SMTP_PASSWORD", "pw"):
                    self.assertTrue(email_service.is_configured())


class BuildMessageTests(unittest.TestCase):
    """組信件是純邏輯，不用真的連 SMTP 就能測——重點在內嵌圖片一定要掛在
    HTML 那一份底下（掛錯層 <img src="cid:..."> 會變破圖，見
    email_service.py 的說明）。"""

    def _message(self, attachments=None, inline_images=None):
        with mock.patch.object(email_service, "MAIL_FROM_NAME", "材霈招募薪資系統"):
            with mock.patch.object(email_service, "MAIL_FROM_ADDRESS", "finance@tsaipei.com.tw"):
                return email_service._build_message(
                    ["a@b.com", "c@d.com"], "測試主旨", "<p>內容</p>", attachments or [], inline_images or []
                )

    def test_headers(self):
        message = self._message()
        self.assertEqual(message["Subject"], "測試主旨")
        self.assertEqual(message["To"], "a@b.com, c@d.com")
        self.assertIn("材霈招募薪資系統", message["From"])
        self.assertIn("finance@tsaipei.com.tw", message["From"])

    def test_falls_back_to_smtp_username_when_no_from_address(self):
        with mock.patch.object(email_service, "MAIL_FROM_ADDRESS", ""):
            with mock.patch.object(email_service, "SMTP_USERNAME", "sender@tsaipei.com.tw"):
                self.assertEqual(email_service._sender_address(), "sender@tsaipei.com.tw")

    def test_html_body_is_present(self):
        message = self._message()
        html_part = message.get_body(preferencelist=("html",))
        self.assertIn("<p>內容</p>", html_part.get_content())

    def test_attachment_is_attached_with_filename(self):
        message = self._message(
            attachments=[{"content": b"%PDF-fake", "filename": "存查單.pdf", "mime_type": "application/pdf"}]
        )
        filenames = [part.get_filename() for part in message.iter_attachments()]
        self.assertIn("存查單.pdf", filenames)

    def test_inline_image_gets_content_id_and_is_not_a_plain_attachment(self):
        message = self._message(
            inline_images=[
                {"content": b"fake-jpeg", "content_id": "salaryProofImg", "filename": "proof.jpg", "mime_type": "image/jpeg"}
            ]
        )
        content_ids = [part.get("Content-ID") for part in message.walk() if part.get("Content-ID")]
        self.assertIn("<salaryProofImg>", content_ids)
        # 內嵌圖片不該同時出現在一般附件清單裡（那會變成信末多一個檔案）
        self.assertEqual([part.get_filename() for part in message.iter_attachments()], [])


class SendEmailGuardTests(unittest.TestCase):
    def test_not_configured_returns_error_without_connecting(self):
        with mock.patch.object(email_service, "SMTP_HOST", ""):
            with mock.patch.object(email_service.smtplib, "SMTP") as mock_smtp:
                ok, message = email_service.send_email(["a@b.com"], "主旨", "<p>x</p>")
        self.assertFalse(ok)
        self.assertIn("SMTP", message)
        mock_smtp.assert_not_called()

    def test_no_recipients_returns_error_without_connecting(self):
        with mock.patch.object(email_service, "SMTP_HOST", "smtp.example.com"):
            with mock.patch.object(email_service, "SMTP_USERNAME", "a@b.com"):
                with mock.patch.object(email_service, "SMTP_PASSWORD", "pw"):
                    with mock.patch.object(email_service.smtplib, "SMTP") as mock_smtp:
                        ok, message = email_service.send_email([], "主旨", "<p>x</p>")
        self.assertFalse(ok)
        mock_smtp.assert_not_called()


class SendEmailTransportTests(unittest.TestCase):
    def setUp(self):
        self.patchers = [
            mock.patch.object(email_service, "SMTP_HOST", "smtp.example.com"),
            mock.patch.object(email_service, "SMTP_USERNAME", "a@b.com"),
            mock.patch.object(email_service, "SMTP_PASSWORD", "pw"),
        ]
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_port_587_uses_starttls(self):
        with mock.patch.object(email_service, "SMTP_PORT", 587):
            with mock.patch.object(email_service.smtplib, "SMTP") as mock_smtp:
                ok, _ = email_service.send_email(["a@b.com"], "主旨", "<p>x</p>")
        self.assertTrue(ok)
        server = mock_smtp.return_value.__enter__.return_value
        server.starttls.assert_called_once()
        server.login.assert_called_once_with("a@b.com", "pw")
        server.send_message.assert_called_once()

    def test_port_465_uses_ssl_without_starttls(self):
        with mock.patch.object(email_service, "SMTP_PORT", 465):
            with mock.patch.object(email_service.smtplib, "SMTP_SSL") as mock_smtp_ssl:
                ok, _ = email_service.send_email(["a@b.com"], "主旨", "<p>x</p>")
        self.assertTrue(ok)
        server = mock_smtp_ssl.return_value.__enter__.return_value
        self.assertFalse(hasattr(server.starttls, "assert_called") and server.starttls.called)
        server.send_message.assert_called_once()

    def test_auth_error_returns_plain_message(self):
        import smtplib as smtplib_module

        with mock.patch.object(email_service, "SMTP_PORT", 587):
            with mock.patch.object(
                email_service.smtplib, "SMTP", side_effect=smtplib_module.SMTPAuthenticationError(535, b"bad")
            ):
                ok, message = email_service.send_email(["a@b.com"], "主旨", "<p>x</p>")
        self.assertFalse(ok)
        self.assertIn("應用程式密碼", message)

    def test_quota_error_is_translated_to_plain_chinese(self):
        import smtplib as smtplib_module

        with mock.patch.object(email_service, "SMTP_PORT", 587):
            with mock.patch.object(
                email_service.smtplib,
                "SMTP",
                side_effect=smtplib_module.SMTPDataError(550, b"5.4.5 Daily user sending quota exceeded."),
            ):
                ok, message = email_service.send_email(["a@b.com"], "主旨", "<p>x</p>")
        self.assertFalse(ok)
        self.assertIn("寄信額度", message)
        self.assertIn("明天", message)

    def test_unexpected_error_never_raises(self):
        with mock.patch.object(email_service, "SMTP_PORT", 587):
            with mock.patch.object(email_service.smtplib, "SMTP", side_effect=RuntimeError("boom")):
                ok, message = email_service.send_email(["a@b.com"], "主旨", "<p>x</p>")
        self.assertFalse(ok)
        self.assertIn("boom", message)


if __name__ == "__main__":
    unittest.main()
