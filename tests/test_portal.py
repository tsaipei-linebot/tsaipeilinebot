import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401  (匯入即副作用：見 _env.py 說明)
from tests import _stub_gcp
_stub_gcp.install()

import main
from fastapi.testclient import TestClient


class PortalPageTests(unittest.TestCase):
    """/portal 是登入前選擇要進哪個內部系統的導覽頁：確認頁面正常出現、
    兩個系統的連結都在，而且沒有動到既有的健康檢查路由（/）。"""

    def setUp(self):
        self.client = TestClient(main.app)

    def test_health_check_unaffected(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")

    def test_portal_page_loads(self):
        resp = self.client.get("/portal")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.headers["content-type"])

    def test_portal_links_to_delivery_login(self):
        resp = self.client.get("/portal")
        self.assertIn('href="/delivery/login"', resp.text)
        self.assertIn("配送部系統", resp.text)

    def test_portal_links_to_job_listing_system(self):
        resp = self.client.get("/portal")
        self.assertIn('href="https://ubiquitous-choux-38eefb.netlify.app/"', resp.text)
        self.assertIn("職缺維護系統", resp.text)

    def test_portal_shows_coming_soon_placeholder(self):
        resp = self.client.get("/portal")
        self.assertIn("即將推出", resp.text)


if __name__ == "__main__":
    unittest.main()
