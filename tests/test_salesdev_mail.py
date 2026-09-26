"""台灣就業通寄信（2026-09-26 新增，方案 A：開 Gmail 撰寫畫面）：手動 Email/電話、
信件範本（只有內文，公司簡介 PDF 使用者自己夾）、寄送紀錄、公司頁與範本頁。"""
import os
import sys
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp

_stub_gcp.install()

from salesdev import mail_templates, repository
from salesdev import taiwanjobs_repository as tj_repo
from tests._fake_firestore import FakeFirestore

BOSS = {"username": "boss", "name": "胡少凱", "modules": [], "is_platform_admin": True, "rank": ""}


class _FakeDbMixin:
    def setUp(self):
        self.db = FakeFirestore()
        patcher = mock.patch.object(repository, "get_db", return_value=self.db)
        patcher.start()
        self.addCleanup(patcher.stop)
        tj_repo.upsert_listing(
            [{"company_name": "宣德科技股份有限公司", "job_title": "SMT技術員", "job_url": "https://x/J?EMPLOYER_ID=1&HIRE_ID=1",
              "employer_id": "1", "hire_id": "1", "city": "桃園市龜山區"}]
        )
        job = tj_repo.pending_detail_jobs()[0]
        tj_repo.save_job_contact(job, {"emails": ["hr@st.com.tw", "old@st.com.tw"], "contact_name": "林小姐", "contact_phone": "03-1"})


class ContactTests(_FakeDbMixin, unittest.TestCase):
    def test_manual_and_hidden_values(self):
        self.assertEqual(tj_repo.update_contact("1", "email", "add", "boss@st.com.tw"), "")
        self.assertEqual(tj_repo.update_contact("1", "email", "hide", "old@st.com.tw"), "")
        self.assertEqual(tj_repo.update_contact("1", "phone", "add", "03-9999"), "")
        company = tj_repo.get_company("1")
        self.assertEqual(tj_repo.effective_emails(company), ["hr@st.com.tw", "boss@st.com.tw"])
        self.assertEqual(tj_repo.effective_phones(company), ["03-1", "03-9999"])
        # 自動抓取再跑一次，不會蓋掉手動的、不會讓隱藏的冒出來
        job = {"id": "1_1", "company_key": "1", "job_url": "u"}
        tj_repo.save_job_contact(job, {"emails": ["old@st.com.tw", "new@st.com.tw"]})
        company = tj_repo.get_company("1")
        self.assertEqual(tj_repo.effective_emails(company), ["hr@st.com.tw", "new@st.com.tw", "boss@st.com.tw"])
        tj_repo.update_contact("1", "email", "unhide", "old@st.com.tw")
        tj_repo.update_contact("1", "email", "remove", "boss@st.com.tw")
        self.assertEqual(tj_repo.effective_emails(tj_repo.get_company("1")), ["hr@st.com.tw", "old@st.com.tw", "new@st.com.tw"])

    def test_invalid_input(self):
        self.assertIn("不是 Email", tj_repo.update_contact("1", "email", "add", "not-an-email"))
        self.assertTrue(tj_repo.update_contact("nope", "email", "add", "a@b.com"))
        self.assertTrue(tj_repo.update_contact("1", "email", "add", ""))

    def test_send_log_and_recent_check(self):
        with mock.patch.object(repository, "today_str", return_value="2026-09-26"):
            tj_repo.record_send("1", "hr@st.com.tw", "胡少凱", "產線缺工")
        company = tj_repo.get_company("1")
        self.assertEqual(company["last_sent_date"], "2026-09-26")
        self.assertEqual(tj_repo.last_sent_by_email(company), {"hr@st.com.tw": "2026-09-26"})
        self.assertTrue(tj_repo.sent_recently("2026-09-26", today="2026-11-24"))  # 59 天
        self.assertFalse(tj_repo.sent_recently("2026-09-26", today="2026-11-25"))  # 60 天
        self.assertFalse(tj_repo.sent_recently(""))


class TemplateTests(_FakeDbMixin, unittest.TestCase):
    def test_seed_once(self):
        self.assertTrue(mail_templates.ensure_seeded("胡少凱"))
        self.assertFalse(mail_templates.ensure_seeded("胡少凱"))
        templates = mail_templates.list_templates()
        self.assertEqual(len(templates), 1)
        self.assertEqual(templates[0]["attachment_name"], "材霈公司簡介.pdf")

    def test_render_fills_company(self):
        body = {"subject": "{公司名稱} 人力支援", "content": "{聯絡人} 您好，看到「{職缺名稱}」（{地區}）。\n{不認得}"}
        result = mail_templates.render(body, tj_repo.get_company("1"))
        self.assertEqual(result["subject"], "宣德科技股份有限公司 人力支援")
        self.assertEqual(result["content"], "林小姐 您好，看到「SMT技術員」（桃園市龜山區）。\n{不認得}")

    def test_render_fallbacks(self):
        result = mail_templates.render({"subject": "", "content": "{聯絡人}/{職缺名稱}"}, {"company_name": "X"})
        self.assertEqual(result["content"], "人資負責人/產線人員")

    def test_save_edit_delete(self):
        with self.assertRaises(ValueError):
            mail_templates.save_template("", "名稱", "", "內容", "", "boss")
        template_id = mail_templates.save_template("", "產線缺工", "主旨", "內容", "簡介.pdf", "boss")
        mail_templates.save_template(template_id, "產線缺工 v2", "主旨2", "內容2", "", "boss")
        template = mail_templates.get_template(template_id)
        self.assertEqual((template["name"], template["subject"], template["attachment_name"]), ("產線缺工 v2", "主旨2", ""))
        self.assertTrue(mail_templates.delete_template(template_id))
        self.assertIsNone(mail_templates.get_template(template_id))
        self.assertFalse(mail_templates.delete_template(template_id))
        with self.assertRaises(ValueError):
            mail_templates.save_template(template_id, "x", "y", "z", "", "boss")

    def test_gmail_url(self):
        url = mail_templates.gmail_compose_url("gary@tsaipei.com", "hr@st.com.tw", "主旨 & 測試", "第一行\n第二行")
        query = parse_qs(urlparse(url).query)
        self.assertEqual(query["authuser"], ["gary@tsaipei.com"])
        self.assertEqual(query["to"], ["hr@st.com.tw"])
        self.assertEqual(query["su"], ["主旨 & 測試"])
        self.assertEqual(query["body"], ["第一行\n第二行"])


class PagesTests(_FakeDbMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        import main
        import platform_accounts
        from fastapi.testclient import TestClient

        self.platform_accounts = platform_accounts
        patcher = mock.patch.object(platform_accounts, "current_account", return_value=BOSS)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(main.app)

    def test_list_has_send_button_and_history_column(self):
        page = self.client.get("/salesdev?tab=taiwanjobs").text
        self.assertIn('href="/salesdev/taiwanjobs/companies/1#send"', page)
        self.assertIn("尚未寄送", page)
        self.assertIn("還沒寄過", page)

    def test_company_page_renders_template_and_each_email(self):
        page = self.client.get("/salesdev/taiwanjobs/companies/1").text
        self.assertIn("宣德科技股份有限公司 產線人力支援｜材霈有限公司", page)
        self.assertIn("林小姐 您好", page)
        self.assertEqual(page.count("js-open-gmail\""), 2)
        self.assertIn("材霈公司簡介.pdf", page)
        self.assertIn('"gary@tsaipei.com"', page)

    def test_record_send_via_json(self):
        page = self.client.get("/salesdev/taiwanjobs/companies/1")
        body_id = mail_templates.list_templates()[0]["id"]
        resp = self.client.post("/salesdev/taiwanjobs/companies/1/sent", json={"email": "hr@st.com.tw", "template_id": body_id})
        self.assertTrue(resp.json()["ok"])
        company = tj_repo.get_company("1")
        self.assertEqual(company["send_log"][0]["template"], mail_templates.SEED_BODY["name"])
        self.assertEqual(company["send_log"][0]["by"], "胡少凱")
        self.assertIn("天內寄過", self.client.get("/salesdev/taiwanjobs/companies/1").text)
        self.assertIn("共 1 次", self.client.get("/salesdev?tab=taiwanjobs").text)
        self.assertIn("宣德科技股份有限公司", self.client.get("/salesdev?tab=taiwanjobs&sent=sent").text)
        self.assertNotIn("宣德科技股份有限公司", self.client.get("/salesdev?tab=taiwanjobs&sent=unsent").text)
        self.assertEqual(page.status_code, 200)

    def test_record_rejects_unknown_email(self):
        resp = self.client.post("/salesdev/taiwanjobs/companies/1/sent", json={"email": "x@y.com"})
        self.assertEqual(resp.status_code, 400)

    def test_contacts_form(self):
        resp = self.client.post(
            "/salesdev/taiwanjobs/companies/1/contacts", data={"kind": "email", "action": "add", "value": "boss@st.com.tw"},
            follow_redirects=False,
        )
        self.assertIn("msg=", resp.headers["location"])
        self.assertIn("boss@st.com.tw", self.client.get("/salesdev?tab=taiwanjobs").text)

    def test_inline_email_on_list_moves_company_to_with_email(self):
        tj_repo.update_contact("1", "email", "hide", "hr@st.com.tw")
        tj_repo.update_contact("1", "email", "hide", "old@st.com.tw")
        page = self.client.get("/salesdev?tab=taiwanjobs&email=no").text
        self.assertIn('class="tj-inline-email"', page)
        resp = self.client.post(
            "/salesdev/taiwanjobs/companies/1/contacts",
            data={"kind": "email", "action": "add", "value": "found@st.com.tw", "back": "list", "dispatch": "hide"},
            follow_redirects=False,
        )
        location = resp.headers["location"]
        self.assertTrue(location.startswith("/salesdev?tab=taiwanjobs&email=yes&dispatch=hide&msg="))
        self.assertTrue(location.endswith("#c-1"))
        self.assertIn('id="c-1"', self.client.get("/salesdev?tab=taiwanjobs&email=yes").text)
        resp = self.client.post(
            "/salesdev/taiwanjobs/companies/1/contacts",
            data={"kind": "email", "action": "add", "value": "壞掉的", "back": "list"},
            follow_redirects=False,
        )
        self.assertIn("email=no", resp.headers["location"])
        self.assertIn("err=", resp.headers["location"])

    def test_templates_page_create_edit_delete(self):
        self.assertIn("暫用範本", self.client.get("/salesdev/templates").text)
        resp = self.client.post(
            "/salesdev/templates/save",
            data={"name": "產線缺工支援", "subject": "{公司名稱} 缺工", "content": "您好", "attachment_name": "DM.pdf"},
            follow_redirects=False,
        )
        self.assertIn("msg=", resp.headers["location"])
        new = [t for t in mail_templates.list_templates() if t["name"] == "產線缺工支援"][0]
        page = self.client.get(f"/salesdev/taiwanjobs/companies/1?t={new['id']}").text
        self.assertIn("宣德科技股份有限公司 缺工", page)
        self.assertIn("DM.pdf", page)
        resp = self.client.post("/salesdev/templates/save", data={"name": "", "content": ""}, follow_redirects=False)
        self.assertIn("err=", resp.headers["location"])
        resp = self.client.post(f"/salesdev/templates/{new['id']}/delete", follow_redirects=False)
        self.assertIn("msg=", resp.headers["location"])
        self.assertIsNone(mail_templates.get_template(new["id"]))
        self.assertNotIn(f"/salesdev/templates/{new['id']}/delete", self.client.get("/salesdev/templates").text)

    def test_other_accounts_blocked(self):
        other = {"username": "amy", "name": "Amy", "modules": ["salesdev"], "rank": "manager", "is_platform_admin": False}
        with mock.patch.object(self.platform_accounts, "current_account", return_value=other):
            self.assertEqual(self.client.get("/salesdev/templates", follow_redirects=False).headers["location"], "/portal")
            self.assertEqual(
                self.client.post("/salesdev/taiwanjobs/companies/1/sent", json={"email": "hr@st.com.tw"}).status_code, 403
            )


if __name__ == "__main__":
    unittest.main()
