"""台北所(派遣組)／(國際組)專區：廠商/班別維護、待進人員（2026-09-25 新增）。全部假資料。"""
import datetime
import os
import sys
import unittest
from unittest import mock
from urllib.parse import unquote

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

from fastapi.testclient import TestClient

import main
import platform_accounts
from hr import insurance_draft_repository as drafts
from hr import insurance_options as options
from hr import insurance_pending as pending
from hr import insurance_repository as repo
from hr.insurance_excel import build_department_workbook
from tests._fake_firestore import FakeFirestore

DEPT = "台北所(派遣組)"
INTL = "台北所(國際組)"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _account(name, department=DEPT, rank="specialist"):
    return {"username": name, "name": name, "department": department, "modules": [], "is_platform_admin": False, "rank": rank}


STAFF = _account("staff")
BOSS = _account("boss", rank="deputy_supervisor")
INTL_BOSS = _account("intl", department=INTL, rank="manager")
TAOYUAN = _account("ty", department="桃園所")


class _Base(unittest.TestCase):
    def setUp(self):
        self.db = FakeFirestore()
        self.account = STAFF
        for p in (
            mock.patch.object(drafts, "insurance_drafts_ref", side_effect=lambda: self.db.collection("d")),
            mock.patch.object(options, "insurance_options_ref", side_effect=lambda: self.db.collection("o")),
            mock.patch.object(repo, "insurance_uploads_ref", side_effect=lambda: self.db.collection("u")),
            mock.patch.object(repo, "insurance_day_locks_ref", side_effect=lambda: self.db.collection("l")),
            mock.patch.object(platform_accounts, "current_account", side_effect=lambda request: self.account),
            mock.patch("platform_vendors.list_vendors", return_value=[]),
        ):
            p.start()
            self.addCleanup(p.stop)
        self.client = TestClient(main.app)
        options.add_option(DEPT, "vendor", "蝦皮(台南)(時薪)-外籍", BOSS)
        options.add_option(DEPT, "shift", "外籍夜班", BOSS)

    def _post(self, url, data, **kw):
        return self.client.post(url, data=data, follow_redirects=False, **kw)

    def _new(self, **fields):
        data = {"mode": "single", "vendor": "蝦皮(台南)(時薪)-外籍", "shift": "外籍夜班", "name": "王小明",
                "id_number": "a123456789", **fields}
        return self._post("/hr/zone/pending/new", data)

    def _rows(self, department=DEPT):
        return [d for d in drafts.list_drafts(department) if d["status"] != drafts.STATUS_CANCELLED]


class OptionTests(_Base):
    def test_only_managers_can_maintain(self):
        response = self.client.get("/hr/zone/options/vendor", follow_redirects=False)
        self.assertIn("err=", response.headers["location"])
        self.account = BOSS
        self.assertIn("廠商維護", self.client.get("/hr/zone/options/vendor").text)
        self._post("/hr/zone/options/vendor/new", {"name": "蝦皮(威獅)(時薪)-外籍"})
        self.assertIn("蝦皮(威獅)(時薪)-外籍", options.active_names(DEPT, "vendor"))

    def test_duplicate_name_rejected_and_departments_are_separate(self):
        self.assertIn("已經有了", options.add_option(DEPT, "vendor", "蝦皮(台南)(時薪)-外籍", BOSS))
        self.assertEqual(options.add_option(INTL, "vendor", "蝦皮(台南)(時薪)-外籍", INTL_BOSS), "")
        self.assertEqual(len(options.list_options(DEPT, "vendor")), 1)

    def test_deactivated_option_disappears_from_dropdown(self):
        option = options.list_options(DEPT, "vendor")[0]
        self.account = BOSS
        self._post(f"/hr/zone/options/item/{option['id']}/active", {"active": "0"})
        self.assertEqual(options.active_names(DEPT, "vendor"), [])
        self.account = STAFF
        response = self._new(insured_date="2026-09-26", withdrawn_date="2026-09-26")
        self.assertEqual(response.status_code, 400)
        self.assertIn("不在廠商清單裡", response.text)

    def test_platform_vendor_choices_follow_service_departments(self):
        vendors = [
            {"name": "蝦皮", "service_departments": ["台北所（派遣組）"]},
            {"name": "蝦皮", "service_departments": ["台北所(派遣組)"]},
            {"name": "別家", "service_departments": ["桃園所"]},
        ]
        with mock.patch("platform_vendors.list_vendors", return_value=vendors):
            self.assertEqual(options.platform_vendor_choices(DEPT), ["蝦皮"])


class PendingEntryTests(_Base):
    def test_same_day_add_and_remove_is_one_row(self):
        response = self._new(insured_date="2026-09-26", withdrawn_date="2026-09-26")
        self.assertIn("msg=", response.headers["location"])
        [row] = self._rows()
        self.assertEqual((row["id_number"], row["kind"], row["insured_date"], row["withdrawn_date"]),
                         ("A123456789", "zone", "2026-09-26", "2026-09-26"))

    def test_different_days_are_split(self):
        self._new(insured_date="2026-09-25", withdrawn_date="2026-10-05", recovery_date="2026-10-06")
        rows = sorted(self._rows(), key=lambda r: r["insured_date"] or "9")
        self.assertEqual([(r["insured_date"], r["withdrawn_date"], r["recovery_date"]) for r in rows],
                         [("2026-09-25", "", ""), ("", "2026-10-05", "2026-10-06")])

    def test_required_fields(self):
        self.assertIn("身分證", self._new(id_number="", insured_date="2026-09-26").text)
        self.assertIn("至少要填一個", self._new().text)
        self.assertIn("不在班別清單裡", self._new(shift="亂打", insured_date="2026-09-26").text)
        self.assertEqual(self._new(shift="", insured_date="2026-09-26").status_code, 303)  # 班別可空

    def test_duplicate_same_person_same_day_same_type_is_blocked(self):
        self._new(insured_date="2026-09-26", withdrawn_date="2026-09-26")
        response = self._new(insured_date="2026-09-26")
        self.assertEqual(response.status_code, 400)
        self.assertIn("2026-09-26 已經登記過加保", response.text)
        self.assertIn("flash-dialog", response.text)
        # 同一天但只登記退保以外的日期不衝突；另一天可以
        self.assertEqual(self._new(insured_date="2026-09-27", withdrawn_date="2026-09-27").status_code, 303)
        self.assertEqual(len(self._rows()), 2)

    def test_cancelled_rows_do_not_count_as_duplicates(self):
        self._new(insured_date="2026-09-26")
        drafts.cancel_draft(self._rows()[0]["id"], STAFF, "x")
        self.assertEqual(self._new(insured_date="2026-09-26").status_code, 303)

    def test_multi_date(self):
        data = {"mode": "multi", "multi_type": "both", "vendor": "蝦皮(台南)(時薪)-外籍", "name": "王小明",
                "id_number": "A123456789", "dates": ["2026-09-28", "2026-09-26", "2026-09-27"]}
        self._post("/hr/zone/pending/new", data)
        rows = sorted(self._rows(), key=lambda r: r["insured_date"])
        self.assertEqual([(r["insured_date"], r["withdrawn_date"]) for r in rows],
                         [("2026-09-26", "2026-09-26"), ("2026-09-27", "2026-09-27"), ("2026-09-28", "2026-09-28")])
        data.update({"multi_type": "remove", "dates": ["2026-09-27", "2026-09-29"]})
        response = self._post("/hr/zone/pending/new", data)
        self.assertEqual(response.status_code, 400)  # 9/27 已經有退保，整批不存
        self.assertEqual(len(self._rows()), 3)

    def test_edit_can_split_and_checks_duplicates_excluding_itself(self):
        self._new(insured_date="2026-09-26", withdrawn_date="2026-09-26")
        row = self._rows()[0]
        data = {"vendor": row["vendor"], "shift": row["shift"], "name": "王小明", "id_number": "A123456789",
                "insured_date": "2026-09-26", "withdrawn_date": "2026-09-30"}
        response = self._post(f"/hr/zone/pending/{row['id']}/edit", data)
        self.assertIn("拆成兩列", unquote(response.headers["location"]))
        self.assertEqual(sorted((r["insured_date"], r["withdrawn_date"]) for r in self._rows()),
                         [("", "2026-09-30"), ("2026-09-26", "")])

    def test_cancel_keeps_record(self):
        self._new(insured_date="2026-09-26")
        row = self._rows()[0]
        self._post(f"/hr/zone/pending/{row['id']}/cancel", {})
        self.assertEqual(drafts.get_draft(row["id"])["status"], drafts.STATUS_CANCELLED)

    def test_back_param_returns_to_daily_page(self):
        response = self._new(insured_date="2026-09-26", back="/hr/insurance/upload?work_date=2026-09-26")
        self.assertTrue(response.headers["location"].startswith("/hr/insurance/upload?work_date=2026-09-26&msg="))
        response = self._new(insured_date="2026-09-27", back="https://evil.example.com/")
        self.assertTrue(response.headers["location"].startswith("/hr/zone/pending?msg="))


class ImportTests(_Base):
    def _import(self, rows):
        content = build_department_workbook(rows)
        return self.client.post("/hr/zone/pending/import", files={"file": ("a.xlsx", content, XLSX)}, follow_redirects=False)

    def test_good_rows_imported_bad_rows_listed(self):
        rows = [
            {"廠商": "蝦皮(台南)(時薪)-外籍", "班別": "外籍夜班", "姓名": "王小明", "身分證": "A123456789",
             "勞保加保日期": "2026/9/26", "勞保退保日期": "2026/9/26"},
            {"廠商": "不存在的廠商", "姓名": "陳小華", "身分證": "B123456789", "勞保加保日期": "2026/9/26"},
            {"廠商": "蝦皮(台南)(時薪)-外籍", "姓名": "林小美", "身分證": "", "勞保加保日期": "2026/9/26"},
            {"廠商": "蝦皮(台南)(時薪)-外籍", "姓名": "王小明", "身分證": "A123456789", "勞保加保日期": "2026/9/26"},
            {"廠商": "蝦皮(台南)(時薪)-外籍", "姓名": "張三", "身分證": "C123456789", "勞保退保日期": "2026/9/27",
             "備註": "離職"},
        ]
        response = self._import(rows)
        self.assertEqual(response.status_code, 400)
        self.assertIn("第 3 列", response.text)   # 廠商不在清單
        self.assertIn("第 4 列", response.text)   # 沒身分證
        self.assertIn("第 5 列", response.text)   # 跟第 2 列重複
        names = sorted(r["name"] for r in self._rows())
        self.assertEqual(names, ["張三", "王小明"])

    def test_all_good_redirects_with_message(self):
        response = self._import([{"廠商": "蝦皮(台南)(時薪)-外籍", "姓名": "王小明", "身分證": "A123456789",
                                  "勞保加保日期": datetime.date(2026, 9, 26), "勞保退保日期": datetime.date(2026, 10, 1)}])
        self.assertIn("msg=", response.headers["location"])
        self.assertEqual(len(self._rows()), 2)  # 不同天自動拆

    def test_template_download(self):
        response = self.client.get("/hr/zone/pending/template.xlsx")
        self.assertEqual(response.status_code, 200)


class AccessAndPageTests(_Base):
    def test_other_departments_cannot_use_zone(self):
        self.account = TAOYUAN
        self.assertEqual(self.client.get("/hr/zone/pending", follow_redirects=False).headers["location"], "/portal")

    def test_daily_page_shows_tabs_and_hides_drafts_until_date_feature(self):
        self._new(insured_date="2026-09-26")
        html = self.client.get("/hr/insurance/upload?work_date=2026-09-26").text
        self.assertIn('class="zone-tabs"', html)
        self.assertIn("待進人員", html)
        self.assertNotIn("待送出清單（", html)
        self.assertNotIn("廠商維護", html)  # 專員看不到
        self.account = BOSS
        self.assertIn("廠商維護", self.client.get("/hr/insurance/upload").text)

    def test_list_page_filters(self):
        self._new(insured_date="2026-09-26", withdrawn_date="2026-09-26")
        self._new(name="陳小華", id_number="B123456789", insured_date="2026-09-30", withdrawn_date="2026-09-30")
        html = self.client.get("/hr/zone/pending?date_from=2026-09-29").text
        self.assertIn("陳小華", html)
        self.assertNotIn("A123456789", html)
        html = self.client.get("/hr/zone/pending?name=A1234").text
        self.assertIn("王小明", html)

    def test_international_group_has_its_own_data(self):
        self._new(insured_date="2026-09-26")
        self.account = INTL_BOSS
        html = self.client.get("/hr/zone/pending").text
        self.assertIn("台北所(國際組)專區", html)
        self.assertNotIn("A123456789", html)


class PendingRuleUnitTests(unittest.TestCase):
    def test_find_duplicates_within_the_batch(self):
        entries = [{"name": "甲", "id_number": "A1", "insured_date": "2026-09-26"},
                   {"name": "甲", "id_number": "a1", "insured_date": "2026-09-26"}]
        self.assertEqual(len(pending.find_duplicates(DEPT, entries, existing=[])), 1)

    def test_iso_dates(self):
        self.assertEqual(pending._iso("2026/9/6"), "2026-09-06")
        self.assertIsNone(pending._iso("九月"))


if __name__ == "__main__":
    unittest.main()
