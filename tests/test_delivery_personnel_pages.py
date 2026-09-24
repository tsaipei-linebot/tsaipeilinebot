"""查詢人員頁、人員詳細頁、新增人員頁、主頁實際渲染一次（2026-09-24 改版），
確認按鈕、到期狀況標籤、拿掉的東西（選擇廠商卡片、身分證字號、上傳照片）。"""
import os
import sys
import unittest
from datetime import date, timedelta
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

import main
import platform_accounts
from delivery import repository
from fastapi.testclient import TestClient

ACCOUNT = {"username": "amy", "name": "Amy", "modules": ["delivery"], "is_platform_admin": True, "rank": ""}
SOON = (date.today() + timedelta(days=10)).isoformat()

PEOPLE = [
    {"id": "p1", "name": "待報到的人", "phone": "0911", "vendor": "ud", "employment_status": "pending_onboard", "documents": {}},
    {"id": "p2", "name": "在職的人", "phone": "0922", "vendor": "sf", "employment_status": "employed", "hire_date": "2026-01-02",
     "documents": {"sf_insurance": {"expiry_date": SOON}}},
    {"id": "p3", "name": "三輪的人", "phone": "0933", "vendor": "shopee", "employment_status": "employed", "documents": {}},
]


class DeliveryPersonnelPagesTests(unittest.TestCase):
    def setUp(self):
        for patcher in (
            mock.patch.object(platform_accounts, "current_account", return_value=ACCOUNT),
            mock.patch.object(repository, "search_personnel", return_value=PEOPLE),
            mock.patch.object(repository, "list_equipment_debt", return_value=[{"personnel_id": "p2", "item_id": "basket", "quantity_owed": 1}]),
            mock.patch.object(repository, "get_personnel", side_effect=lambda pid: next((p for p in PEOPLE if p["id"] == pid), None)),
            mock.patch.object(repository, "list_cooperation_types", return_value=[]),
            mock.patch.object(repository, "cooperation_types_by_vendor", return_value={}),
            mock.patch.object(repository, "list_open_incident_events", return_value=[]),
            mock.patch.object(repository, "list_equipment_items", return_value=[]),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = TestClient(main.app)

    def test_search_page_rows_have_the_right_buttons(self):
        html = self.client.get("/delivery/search").text
        self.assertIn('data-name="待報到的人"', html)  # 報到按鈕
        self.assertIn("/delivery/personnel/p1/withdraw", html)
        self.assertNotIn("/delivery/personnel/p1/resign", html)
        self.assertIn("/delivery/personnel/p2/resign", html)
        self.assertNotIn("/delivery/personnel/p2/withdraw", html)
        self.assertIn('data-owed="1"', html)

    def test_search_page_expiry_badges(self):
        html = self.client.get("/delivery/search").text
        self.assertIn("良民證　未填", html)
        self.assertIn(f"強制險　即將到期（{SOON}）", html)
        self.assertIn("公會加保證明　未填（選填）", html)
        self.assertIn("不需追蹤", html)

    def test_search_page_has_moved_buttons_and_phone_filter(self):
        html = self.client.get("/delivery/search").text
        for text in ("/delivery/personnel/new", "/delivery/import", "/delivery/cooperation-types", 'name="phone"'):
            self.assertIn(text, html)
        self.assertNotIn("身分證", html)

    def test_detail_page_is_date_only(self):
        html = self.client.get("/delivery/personnel/p2").text
        self.assertIn('name="expiry_date_sf_insurance"', html)
        self.assertIn('name="expiry_date_sf_guild_insurance"', html)
        self.assertNotIn('type="file"', html)
        self.assertNotIn("身分證", html)
        self.assertIn("/delivery/personnel/p2/delete", html)

    def test_new_personnel_form_has_vendor_choice_and_no_id_number(self):
        html = self.client.get("/delivery/personnel/new?vendor=sf").text
        self.assertIn('<option value="sf" selected>', html)
        self.assertNotIn("id_number", html)

    def test_home_has_no_vendor_card(self):
        html = self.client.get("/delivery/").text
        self.assertNotIn("選擇廠商", html)
        self.assertIn("查詢人員", html)


if __name__ == "__main__":
    unittest.main()
