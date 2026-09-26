"""補款改由平台處理：收單、核准卡片、核准／退回、總機分流、切換開關（2026-09-26）。測試資料全部是假的。"""
import base64
import hashlib
import hmac
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

import requests
from fastapi.testclient import TestClient

import finance_routes
import main
import me_routes
import platform_accounts
from services import email_service
from services import job_portal_line_relay as relay
from services import salary_platform as sp
from services import salary_repayment_photos as photos
from services import salary_repayment_report as report
from services import salary_repayment_service as svc
from services import salary_repayment_sheet_writer as writer
from services import salary_repayment_store as store
from tests._fake_firestore import FakeFirestore

HEADERS = sp.GAS_HEADERS
ORG_HEADERS = ["員工姓名", "員工 LINE ID", "主管姓名", "主管 LINE ID", "主管 Email", "F", "G", "H", "I", "員工Email"]
APPLICANT, MANAGER, MANAGER2, ADMIN_LID, STRANGER = "Uapplicant0001", "Umanager00001", "Umanager00002", "Uadmin0000001", "Ustranger0001"
ADMIN = {"username": "boss", "name": "老闆", "department": "", "is_platform_admin": True, "modules": []}
STAFF = {"username": "wang", "name": "王小明", "department": "新北所", "is_platform_admin": False, "modules": []}


def org_values():
    rows = [
        ["王小明", APPLICANT, "李主管,張主管", f"{MANAGER},{MANAGER2}", "li@example.com,chang@example.com", "", "", "", "", "wang@example.com"],
        ["李主管", MANAGER, "", "", "", "", "", "", "", ""],
        ["未綁定", "", "李主管", MANAGER, "li@example.com", "", "", "", "", ""],
        ["沒主管", "Unoboss000001", "", "", "", "", "", "", "", ""],
    ]
    return [ORG_HEADERS] + rows


def info(**overrides):
    data = {"applicant_name": "王小明", "name": "陳小華", "id_card": "A123456789", "vendor": "測試店家",
            "apply_date": "2026-09-26", "pay_date": "", "deduct_month": "", "compensate_month": "2026-09",
            "is_claimable": "可", "pay_type": "立即補款", "notes": "漏發加班費"}
    data.update(overrides)
    return data


class PlatformTestCase(unittest.TestCase):
    def setUp(self):
        self.db = FakeFirestore()
        self.line_calls = []
        self.sheet = {"append": [], "review": []}
        self.mails = []
        for p in (
            mock.patch.object(store, "get_db", return_value=self.db),
            mock.patch.object(sp, "JOB_PORTAL_LINE_CHANNEL_ACCESS_TOKEN", "token"),
            mock.patch.object(sp, "SALARY_ADMIN_LINE_USER_IDS", ADMIN_LID),
            mock.patch.object(sp, "SALARY_ADMIN_EMAILS", "admin@example.com"),
            mock.patch.object(report, "SALARY_HR_ACCOUNTING_EMAILS", "fin@example.com"),
            mock.patch.object(requests, "post", side_effect=self._line_post),
            mock.patch.object(writer, "append_record", side_effect=lambda f: (self.sheet["append"].append(f) or (True, "ok"))),
            mock.patch.object(writer, "update_review", side_effect=lambda *a: (self.sheet["review"].append(a) or (True, "ok"))),
            mock.patch.object(svc, "fetch_sheet_values", side_effect=lambda: (org_values(), [HEADERS], None)),
            mock.patch.object(photos, "upload_photo", side_effect=lambda doc_id, c, t: f"salary/photos/{doc_id}/x.jpg"),
            mock.patch.object(photos, "download_photo", return_value=(b"\xff\xd8\xffjpeg", "image/jpeg")),
            mock.patch.object(report, "build_pdf", return_value=b"%PDF-fake"),
            mock.patch.object(email_service, "send_email", side_effect=self._send),
        ):
            p.start()
            self.addCleanup(p.stop)
        store.sync_from_sheet(org_values(), [HEADERS], ADMIN)

    def _line_post(self, url, json=None, headers=None, timeout=None, **kw):
        self.line_calls.append((url.rsplit("/", 1)[-1], json))
        return type("R", (), {"status_code": 200, "text": ""})()

    def _send(self, to, subject, html, attachments=None, inline_images=None):
        self.mails.append({"to": to, "subject": subject, "html": html, "attachments": attachments or [], "inline": inline_images or []})
        return True, ""

    def pushes(self, to=None):
        return [body for kind, body in self.line_calls if kind == "push" and (to is None or body["to"] == to)]

    def replies(self):
        return [body["messages"][0]["text"] for kind, body in self.line_calls if kind == "reply"]

    def submit(self, **overrides):
        return sp.submit(info(**overrides), {"salary": 1200}, {"remit_fee": 30})

    def postback(self, salary_id, status="approve", operator=MANAGER):
        return {"type": "postback", "replyToken": "rt", "source": {"userId": operator},
                "postback": {"data": f"action=review_salary&status={status}&salary_id={salary_id}&applicant_id={APPLICANT}"}}

    def doc(self, doc_id):
        return self.db.docs(store.RECORDS_COLLECTION)[doc_id]


class SubmitTests(PlatformTestCase):
    def test_success_writes_firestore_sheet_and_pushes_cards(self):
        result = self.submit()
        self.assertEqual(result["status"], "success")
        sid = result["salaryId"]
        self.assertRegex(sid, r"^SAL-\d{14}$")
        doc = self.doc(sid)
        self.assertEqual(doc["source"], "platform")
        self.assertEqual(doc["fields"]["實補總額"], "1170")
        self.assertEqual(doc["fields"]["審核狀態"], "待審核")
        self.assertEqual(doc["fields"]["申請人 LINE ID"], APPLICANT)
        self.assertEqual(doc["fields"]["匯費"], "30")
        self.assertEqual(doc["sheet_status"], "ok")
        self.assertEqual(self.sheet["append"][0]["補款單號"], sid)
        cards = [p for p in self.pushes() if p["messages"][0]["type"] == "flex"]
        self.assertEqual(sorted(p["to"] for p in cards), sorted([MANAGER, MANAGER2]))
        footer = cards[0]["messages"][0]["contents"]["footer"]["contents"]
        self.assertEqual(footer[0]["action"]["data"], f"action=review_salary&status=approve&salary_id={sid}&applicant_id={APPLICANT}")

    def test_card_masks_id_and_has_no_public_photo(self):
        sid = sp.submit(info(), {"salary": 1000}, {}, {"content": b"\xff\xd8\xffx", "content_type": "image/jpeg"})["salaryId"]
        card = json.dumps(self.pushes(MANAGER)[0], ensure_ascii=False)
        self.assertIn("A12****789", card)
        self.assertNotIn('"type": "image"', card)
        self.assertIn(f"/me/salary-repayment/{sid}/photo", card)
        self.assertEqual(photos.photo_status(self.doc(sid)), photos.STATUS_DONE)

    def test_duplicate_is_blocked_but_rejected_is_not(self):
        sid = self.submit()["salaryId"]
        result = self.submit()
        self.assertEqual(result["status"], "error")
        self.assertIn(sid, result["message"])
        self.db.collection(store.RECORDS_COLLECTION).document(sid).set({**self.doc(sid), "rejected": True})
        self.assertEqual(self.submit()["status"], "success")

    def test_duplicate_matches_sheet_month_formats(self):
        old = dict(zip(HEADERS, ["SAL-OLD"] + [""] * 21))
        old.update({"員工姓名": "陳小華", "身分證": "a123456789", "補請款月份": "2026/9/1"})
        store.sync_from_sheet(org_values(), [HEADERS, [old[h] for h in HEADERS]], ADMIN)
        self.assertIn("SAL-OLD", self.submit()["message"])

    def test_unbound_applicant(self):
        result = self.submit(applicant_name="未綁定")
        self.assertEqual(result["status"], "unauthorized")
        self.assertIn("綁定+未綁定+4位PIN碼", result["message"])

    def test_no_supervisor_falls_back_to_admin(self):
        self.assertEqual(self.submit(applicant_name="沒主管")["status"], "success")
        self.assertTrue(self.pushes(ADMIN_LID))

    def test_no_supervisor_and_no_admin(self):
        with mock.patch.object(sp, "SALARY_ADMIN_LINE_USER_IDS", ""):
            result = self.submit(applicant_name="沒主管")
        self.assertEqual(result["status"], "supervisor_unassigned")
        self.assertEqual(self.pushes("Unoboss000001")[0]["messages"][0]["altText"], "⚠️ 【送審未成功】尚未指派審核主管")

    def test_sheet_failure_is_marked_for_retry(self):
        with mock.patch.object(writer, "append_record", return_value=(False, "沒有權限")):
            sid = self.submit()["salaryId"]
        self.assertEqual(self.doc(sid)["sheet_status"], "failed")
        self.assertEqual([f["doc_id"] for f in sp.sheet_failures()], [sid])
        self.assertTrue(sp.retry_sheet(sid)[0])
        self.assertEqual(sp.sheet_failures(), [])

    def test_same_second_gets_suffix(self):
        first = self.submit()["salaryId"]
        second = self.submit(name="林小美")["salaryId"]
        if second.startswith(first):
            self.assertEqual(second, f"{first}-2")

    def test_required_fields(self):
        self.assertEqual(self.submit(notes=" ")["status"], "error")
        self.assertEqual(self.submit(applicant_name="")["status"], "unauthorized")


class ReviewTests(PlatformTestCase):
    def setUp(self):
        super().setUp()
        self.sid = self.submit()["salaryId"]
        self.line_calls.clear()

    def test_approve_updates_everything_and_mails(self):
        sp.handle_review(self.postback(self.sid))
        doc = self.doc(self.sid)
        self.assertEqual(doc["fields"]["審核狀態"], "已核准")
        self.assertEqual(doc["fields"]["核准主管"], MANAGER)
        self.assertEqual(self.sheet["review"][0][:3], (self.sid, "已核准", MANAGER))
        mail = self.mails[0]
        self.assertEqual(mail["to"], ["fin@example.com", "li@example.com", "chang@example.com", "wang@example.com"])
        self.assertEqual([a["filename"] for a in mail["attachments"]], [f"薪資補款存查單_{self.sid}.pdf"])
        self.assertIn("已核准！\n系統已自動寄出", self.replies()[0])
        self.assertIn("已通過主管核准", self.pushes(APPLICANT)[0]["messages"][0]["text"])
        self.assertIn("【審核同步】", self.pushes(MANAGER2)[0]["messages"][0]["text"])
        self.assertEqual(self.pushes(MANAGER), [])

    def test_reject_deletes_sheet_row_and_hides_from_me(self):
        sp.handle_review(self.postback(self.sid, "reject"))
        self.assertTrue(self.doc(self.sid)["rejected"])
        self.assertEqual(self.sheet["review"][0][:2], (self.sid, "已退回"))
        self.assertEqual(self.mails, [])
        self.assertIn("已退回", self.replies()[0])
        _, rows, _ = store.load_rows()
        self.assertNotIn(self.sid, [r["補款單號"] for r in rows])

    def test_only_once(self):
        sp.handle_review(self.postback(self.sid))
        sp.handle_review(self.postback(self.sid, operator=MANAGER2))
        self.assertEqual(len(self.mails), 1)
        self.assertIn("無法重複簽核", self.replies()[-1])

    def test_admin_email_used_when_applicant_has_no_supervisor(self):
        sid = self.submit(applicant_name="沒主管", name="林小美")["salaryId"]
        sp.handle_review(self.postback(sid, operator=ADMIN_LID))
        self.assertEqual(self.mails[-1]["to"], ["fin@example.com", "admin@example.com"])

    def test_stranger_is_refused_admin_is_allowed(self):
        sp.handle_review(self.postback(self.sid, operator=STRANGER))
        self.assertIn("無權限執行簽核", self.replies()[0])
        self.assertEqual(self.doc(self.sid)["fields"]["審核狀態"], "待審核")
        sp.handle_review(self.postback(self.sid, operator=ADMIN_LID))
        self.assertEqual(self.doc(self.sid)["fields"]["審核狀態"], "已核准")

    def test_mail_failure_is_reported_honestly(self):
        with mock.patch.object(email_service, "send_email", return_value=(False, "SMTP 密碼錯誤")):
            sp.handle_review(self.postback(self.sid))
        self.assertIn("通知信寄送失敗（SMTP 密碼錯誤）", self.replies()[0])
        self.assertEqual(self.doc(self.sid)["mail_status"], "failed")

    def test_errors_never_raise(self):
        with mock.patch.object(sp, "load_org", side_effect=RuntimeError("boom")):
            sp.handle_review(self.postback(self.sid))


class RelaySplitTests(PlatformTestCase):
    def test_platform_postback_stays_other_events_are_forwarded(self):
        sid = self.submit()["salaryId"]
        body = json.dumps({"destination": "U0", "events": [
            self.postback(sid), self.postback("SAL-FROM-GAS"), {"type": "message", "message": {"type": "text", "text": "hi"}},
        ]}).encode()
        mine, rest = relay.split_events(body, sp.owns_event)
        self.assertEqual([e["postback"]["data"].split("salary_id=")[1].split("&")[0] for e in mine], [sid])
        self.assertEqual(len(json.loads(rest)["events"]), 2)

    def test_untouched_body_when_nothing_is_ours(self):
        body = json.dumps({"events": [self.postback("SAL-FROM-GAS")]}).encode()
        self.assertEqual(relay.split_events(body, sp.owns_event), ([], body))

    def test_webhook_handles_platform_card_without_calling_gas(self):
        sid = self.submit()["salaryId"]
        body = json.dumps({"destination": "U0", "events": [self.postback(sid)]}).encode()
        sig = base64.b64encode(hmac.new(b"s", body, hashlib.sha256).digest()).decode()
        with mock.patch.object(relay, "JOB_PORTAL_LINE_CHANNEL_SECRET", "s"), \
                mock.patch.object(relay, "JOB_PORTAL_LINE_RELAY_TARGET_URL", "https://gas.example/exec"), \
                mock.patch.object(relay, "get_db", return_value=self.db), \
                mock.patch.object(relay, "forward_to_gas") as forward:
            resp = TestClient(main.app).post("/api/job-portal/line-webhook", content=body, headers={"X-Line-Signature": sig})
            self.assertEqual(relay.recent()[0]["note"], "平台處理")
        self.assertEqual(resp.status_code, 200)
        forward.assert_not_called()
        self.assertEqual(self.doc(sid)["fields"]["審核狀態"], "已核准")


class SyncCoexistenceTests(PlatformTestCase):
    def test_sync_never_overwrites_platform_records(self):
        sid = self.submit()["salaryId"]
        sheet_row = dict(self.sheet["append"][0])
        sheet_row["實補總額"] = "1,170"  # 試算表顯示格式不同
        result = store.sync_from_sheet(org_values(), [HEADERS, [sheet_row.get(h, "") for h in HEADERS]], ADMIN)
        self.assertEqual(self.doc(sid)["source"], "platform")
        self.assertEqual(self.doc(sid)["fields"]["實補總額"], "1170")
        self.assertEqual(result["records"]["platform"], 1)


class RouteTests(PlatformTestCase):
    def setUp(self):
        super().setUp()
        self.account = ADMIN
        for p in (
            mock.patch.object(platform_accounts, "current_account", side_effect=lambda request: self.account),
            mock.patch.object(platform_accounts, "list_accounts", return_value=[{"username": "wang", "name": "王小明"}]),
            mock.patch.object(email_service, "is_configured", return_value=True),
            mock.patch.object(photos, "SALARY_PHOTO_GCS_BUCKET", "bucket"),
            mock.patch.object(relay, "JOB_PORTAL_LINE_CHANNEL_SECRET", "s"),
            mock.patch.object(relay, "JOB_PORTAL_LINE_RELAY_TARGET_URL", "https://gas.example/exec"),
            mock.patch.object(finance_routes, "fetch_sheet_values", side_effect=lambda: (org_values(), [HEADERS], None)),
        ):
            p.start()
            self.addCleanup(p.stop)
        self.client = TestClient(main.app)

    def test_toggle_on_and_off(self):
        resp = self.client.post("/finance/migration/platform-mode", data={"enabled": "1"}, follow_redirects=False)
        self.assertEqual(resp.headers["location"], "/finance/migration?notice=platform_on")
        self.assertTrue(sp.is_enabled())
        self.assertEqual(store.read_source(), "firestore")
        self.assertIn("✅ 由平台處理", self.client.get("/finance/migration").text)
        self.client.post("/finance/migration/platform-mode", data={"enabled": "0"})
        self.assertFalse(sp.is_enabled())
        self.assertEqual(store.read_source(), "sheet")

    def test_cannot_turn_on_with_missing_settings(self):
        with mock.patch.object(sp, "JOB_PORTAL_LINE_CHANNEL_ACCESS_TOKEN", ""):
            resp = self.client.post("/finance/migration/platform-mode", data={"enabled": "1"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Channel access token", resp.text)
        self.assertFalse(sp.is_enabled())

    def test_me_form_goes_to_platform_only_when_enabled(self):
        self.account = STAFF
        form = {"name": "陳小華", "id_card": "A123456789", "vendor": "店", "apply_date": "2026-09-26", "compensate_month": "2026-09",
                "is_claimable": "可", "pay_type": "立即補款", "notes": "測試", "earning_salary": "500"}
        with mock.patch.object(me_routes, "submit_salary_repayment", return_value={"status": "success", "salaryId": "SAL-GAS"}) as gas:
            resp = self.client.post("/me/salary-repayment/new", data=form, follow_redirects=False)
            self.assertEqual(resp.headers["location"], "/me?submitted=SAL-GAS")
            sp.set_enabled(True, ADMIN)
            resp = self.client.post("/me/salary-repayment/new", data={**form, "compensate_month": "2026-10"}, follow_redirects=False)
        self.assertEqual(gas.call_count, 1)
        self.assertRegex(resp.headers["location"], r"^/me\?submitted=SAL-\d{14}")

    def test_photo_page_permissions(self):
        sid = sp.submit(info(), {"salary": 1}, {}, {"content": b"\xff\xd8\xffx", "content_type": "image/jpeg"})["salaryId"]
        with mock.patch.object(photos, "get_photo_for_doc", return_value=(b"\xff\xd8\xffx", "image/jpeg")):
            self.account = STAFF  # 申請人本人
            store.set_read_source("firestore", ADMIN)
            self.assertEqual(self.client.get(f"/me/salary-repayment/{sid}/photo").status_code, 200)
            self.account = {"username": "li", "name": "李主管", "department": "", "is_platform_admin": False, "modules": []}
            self.assertEqual(self.client.get(f"/me/salary-repayment/{sid}/photo").status_code, 200)
            self.account = {"username": "x", "name": "路人", "department": "新北所", "is_platform_admin": False, "modules": []}
            self.assertEqual(self.client.get(f"/me/salary-repayment/{sid}/photo").status_code, 403)

    def test_resend_for_platform_record_uses_platform_mail(self):
        sid = self.submit()["salaryId"]
        sp.handle_review(self.postback(sid))
        self.mails.clear()
        self.account = STAFF
        store.set_read_source("firestore", ADMIN)
        with mock.patch.object(me_routes, "resend_salary_repayment_email") as gas:
            resp = self.client.post(f"/me/salary-repayment/{sid}/resend-email", follow_redirects=False)
        gas.assert_not_called()
        self.assertEqual(resp.headers["location"], "/me?resend_ok=1")
        self.assertEqual(len(self.mails), 1)


if __name__ == "__main__":
    unittest.main()
