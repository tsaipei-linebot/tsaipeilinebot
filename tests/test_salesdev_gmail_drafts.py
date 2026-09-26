"""寄信「做法二」（2026-09-26 新增）：連結 Gmail（OAuth）、建立含 PDF 的草稿、範本上傳 PDF。
開發環境連不到 Google，所有對外請求都用假的。"""
import email
import os
import sys
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp

_stub_gcp.install()

from salesdev import gmail_drafts, mail_templates, repository
from salesdev import taiwanjobs_repository as tj_repo
from tests._fake_firestore import FakeFirestore

BOSS = {"username": "boss", "name": "胡少凱", "modules": [], "is_platform_admin": True, "rank": ""}
PDF = b"%PDF-1.4 fake pdf content"


class _Resp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload or {}
        self.content = b"x"

    def json(self):
        return self._payload


class _Base(unittest.TestCase):
    def setUp(self):
        self.db = FakeFirestore()
        for patcher in (
            mock.patch.object(repository, "get_db", return_value=self.db),
            mock.patch.object(gmail_drafts, "SALESDEV_GMAIL_OAUTH_CLIENT_ID", "cid"),
            mock.patch.object(gmail_drafts, "SALESDEV_GMAIL_OAUTH_CLIENT_SECRET", "csecret"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _connect(self):
        gmail_drafts._settings_ref().set({"refresh_token": "rt", "email": "gary@tsaipei.com", "connected_at": "x"})


class OAuthTests(_Base):
    def test_authorization_url(self):
        url = gmail_drafts.authorization_url("https://host/salesdev/gmail/callback", "st")
        query = parse_qs(urlparse(url).query)
        self.assertEqual(query["scope"], [gmail_drafts.SCOPE])
        self.assertEqual(query["login_hint"], ["gary@tsaipei.com"])
        self.assertEqual(query["hd"], ["tsaipei.com"])
        self.assertEqual((query["state"], query["access_type"], query["prompt"]), (["st"], ["offline"], ["consent"]))
        self.assertIn("gmail.compose", gmail_drafts.SCOPE)
        self.assertNotIn("readonly", gmail_drafts.SCOPE)

    def test_complete_authorization_saves_token(self):
        with mock.patch.object(gmail_drafts.requests, "post", return_value=_Resp(payload={"refresh_token": "rt", "access_token": "at"})), \
                mock.patch.object(gmail_drafts.requests, "get", return_value=_Resp(payload={"emailAddress": "Gary@tsaipei.com"})):
            self.assertEqual(gmail_drafts.complete_authorization("code", "uri", "胡少凱"), "Gary@tsaipei.com")
        status = gmail_drafts.connection_status()
        self.assertTrue(status["connected"])
        self.assertNotIn("refresh_token", status)

    def test_wrong_account_is_rejected_and_revoked(self):
        posts = []

        def fake_post(url, **kwargs):
            posts.append(url)
            return _Resp(payload={"refresh_token": "rt", "access_token": "at"})

        with mock.patch.object(gmail_drafts.requests, "post", side_effect=fake_post), \
                mock.patch.object(gmail_drafts.requests, "get", return_value=_Resp(payload={"emailAddress": "someone@gmail.com"})):
            with self.assertRaises(RuntimeError) as ctx:
                gmail_drafts.complete_authorization("code", "uri", "胡少凱")
        self.assertIn("someone@gmail.com", str(ctx.exception))
        self.assertIn(gmail_drafts.REVOKE_URL, posts)
        self.assertFalse(gmail_drafts.connection_status()["connected"])

    def test_not_connected(self):
        with self.assertRaises(gmail_drafts.GmailNotConnected):
            gmail_drafts.create_draft("a@b.com", "s", "c")

    def test_revoked_grant_asks_to_reconnect(self):
        self._connect()
        with mock.patch.object(gmail_drafts.requests, "post", return_value=_Resp(400, {"error": "invalid_grant"})):
            with self.assertRaises(gmail_drafts.GmailNotConnected):
                gmail_drafts.create_draft("a@b.com", "s", "c")


class DraftTests(_Base):
    def test_message_has_single_recipient_and_pdf(self):
        raw = gmail_drafts.build_message("hr@st.com.tw", "主旨", "您好\n內文", ("材霈公司簡介.pdf", PDF))
        message = email.message_from_bytes(raw)
        self.assertEqual(message["To"], "hr@st.com.tw")
        self.assertIn("gary@tsaipei.com", message["From"])
        parts = list(message.walk())
        attachment = [p for p in parts if p.get_filename()][0]
        self.assertEqual(attachment.get_content_type(), "application/pdf")
        self.assertEqual(attachment.get_payload(decode=True), PDF)
        self.assertEqual(email.header.decode_header(attachment.get_filename())[0][0], "材霈公司簡介.pdf")

    def test_create_draft_calls_upload_endpoint(self):
        self._connect()
        calls = []

        def fake_post(url, **kwargs):
            calls.append((url, kwargs))
            if url == gmail_drafts.TOKEN_URL:
                return _Resp(payload={"access_token": "at"})
            return _Resp(payload={"id": "r-1", "message": {"id": "18abc"}})

        with mock.patch.object(gmail_drafts.requests, "post", side_effect=fake_post):
            draft = gmail_drafts.create_draft("hr@st.com.tw", "主旨", "內文", ("a.pdf", PDF))
        self.assertEqual(draft["message_id"], "18abc")
        self.assertIn("#drafts?compose=18abc", draft["url"])
        self.assertIn("authuser=gary@tsaipei.com", draft["url"])
        url, kwargs = calls[-1]
        self.assertEqual(url, gmail_drafts.DRAFT_UPLOAD_URL)
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer at")
        self.assertEqual(kwargs["headers"]["Content-Type"], "message/rfc822")

    def test_looks_like_pdf(self):
        self.assertTrue(gmail_drafts.looks_like_pdf(PDF))
        self.assertFalse(gmail_drafts.looks_like_pdf(b"PK\x03\x04 zip"))


class PagesTests(_Base):
    def setUp(self):
        super().setUp()
        import main
        import platform_accounts
        from fastapi.testclient import TestClient

        patcher = mock.patch.object(platform_accounts, "current_account", return_value=BOSS)
        patcher.start()
        self.addCleanup(patcher.stop)
        tj_repo.upsert_listing(
            [{"company_name": "宣德科技股份有限公司", "job_title": "SMT技術員", "job_url": "u1", "employer_id": "1", "hire_id": "1"}]
        )
        tj_repo.save_job_contact(tj_repo.pending_detail_jobs()[0], {"emails": ["hr@st.com.tw"]})
        # 登入 session cookie 設了 https_only，測試也要用 https 才會帶回來
        self.client = TestClient(main.app, base_url="https://testserver")
        mail_templates.ensure_seeded("胡少凱")
        self.template_id = mail_templates.list_templates()[0]["id"]

    def test_templates_page_shows_connect_and_redirect_uri(self):
        page = self.client.get("/salesdev/templates").text
        self.assertIn('href="/salesdev/gmail/connect"', page)
        with mock.patch.object(gmail_drafts, "SALESDEV_GMAIL_OAUTH_CLIENT_ID", ""):
            page = self.client.get("/salesdev/templates").text
        self.assertIn("https://testserver/salesdev/gmail/callback", page)

    def test_oauth_round_trip_checks_state(self):
        resp = self.client.get("/salesdev/gmail/connect", follow_redirects=False)
        location = resp.headers["location"]
        self.assertTrue(location.startswith(gmail_drafts.AUTH_URL))
        state = parse_qs(urlparse(location).query)["state"][0]
        resp = self.client.get("/salesdev/gmail/callback", params={"code": "c", "state": "wrong"}, follow_redirects=False)
        self.assertIn("err=", resp.headers["location"])
        # state 用過一次就作廢，要重新 connect
        self.client.get("/salesdev/gmail/connect", follow_redirects=False)
        state = parse_qs(urlparse(self.client.get("/salesdev/gmail/connect", follow_redirects=False).headers["location"]).query)["state"][0]
        with mock.patch.object(gmail_drafts, "complete_authorization", return_value="gary@tsaipei.com") as complete:
            resp = self.client.get("/salesdev/gmail/callback", params={"code": "c", "state": state}, follow_redirects=False)
        self.assertIn("msg=", resp.headers["location"])
        self.assertEqual(complete.call_args[0][1], "https://testserver/salesdev/gmail/callback")

    def test_upload_pdf_to_template(self):
        with mock.patch.object(gmail_drafts, "upload_attachment", return_value="salesdev/mail_templates/x/1.pdf") as upload:
            resp = self.client.post(
                "/salesdev/templates/save",
                data={"id": self.template_id, "name": "範本", "subject": "主旨", "content": "內文"},
                files={"attachment_file": ("材霈簡介.pdf", PDF, "application/pdf")},
                follow_redirects=False,
            )
        self.assertIn("msg=", resp.headers["location"])
        upload.assert_called_once()
        template = mail_templates.get_template(self.template_id)
        self.assertEqual((template["attachment_blob"], template["attachment_name"]), ("salesdev/mail_templates/x/1.pdf", "材霈簡介.pdf"))

    def test_non_pdf_is_rejected(self):
        with mock.patch.object(gmail_drafts, "upload_attachment") as upload:
            resp = self.client.post(
                "/salesdev/templates/save",
                data={"id": self.template_id, "name": "範本", "subject": "主旨", "content": "內文"},
                files={"attachment_file": ("x.pdf", b"not a pdf", "application/pdf")},
                follow_redirects=False,
            )
        self.assertIn("err=", resp.headers["location"])
        upload.assert_not_called()

    def test_company_page_shows_draft_button_only_when_connected(self):
        self.assertNotIn("建立草稿（含附件）</button>", self.client.get("/salesdev/taiwanjobs/companies/1").text)
        self._connect()
        self.assertIn("建立草稿（含附件）", self.client.get("/salesdev/taiwanjobs/companies/1").text)

    def test_draft_endpoint_attaches_pdf_and_records(self):
        self._connect()
        mail_templates.set_attachment(self.template_id, "salesdev/mail_templates/x/1.pdf", "簡介.pdf", len(PDF), "boss")
        with mock.patch.object(gmail_drafts, "download_attachment", return_value=PDF), \
                mock.patch.object(gmail_drafts, "create_draft", return_value={"url": "https://mail.google.com/x", "message_id": "m"}) as create:
            resp = self.client.post(
                "/salesdev/taiwanjobs/companies/1/draft",
                json={"email": "hr@st.com.tw", "template_id": self.template_id, "subject": "改過的主旨", "content": "內文"},
            )
        self.assertTrue(resp.json()["ok"])
        self.assertEqual(resp.json()["url"], "https://mail.google.com/x")
        args = create.call_args[0]
        self.assertEqual(args[:3], ("hr@st.com.tw", "改過的主旨", "內文"))
        self.assertEqual(args[3], ("簡介.pdf", PDF))
        self.assertIn("（草稿）", tj_repo.get_company("1")["send_log"][0]["template"])

    def test_draft_endpoint_not_connected(self):
        resp = self.client.post(
            "/salesdev/taiwanjobs/companies/1/draft",
            json={"email": "hr@st.com.tw", "template_id": self.template_id, "subject": "s", "content": "c"},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertTrue(resp.json()["need_connect"])
        self.assertFalse(tj_repo.get_company("1").get("send_log"))

    def test_draft_rejects_unknown_email(self):
        self._connect()
        resp = self.client.post(
            "/salesdev/taiwanjobs/companies/1/draft",
            json={"email": "x@y.com", "template_id": self.template_id, "subject": "s", "content": "c"},
        )
        self.assertEqual(resp.status_code, 400)


if __name__ == "__main__":
    unittest.main()
