"""全系統提醒改用彈出視窗（2026-09-25）：base.html 都載入 flash.js、操作結果訊息有 js-flash、
固定的狀態提示沒有。"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

from fastapi.testclient import TestClient

import main

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASES = ["templates/base.html", "delivery/templates/base.html", "hr/templates/base.html", "management/templates/base.html"]


def _read(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as f:
        return f.read()


class FlashPopupTests(unittest.TestCase):
    def test_every_base_template_loads_the_script(self):
        for path in BASES:
            self.assertIn('/delivery/static/flash.js', _read(path), path)

    def test_script_is_served(self):
        response = TestClient(main.app).get("/delivery/static/flash.js")
        self.assertEqual(response.status_code, 200)
        self.assertIn("window.showAlert", response.text)

    def test_result_messages_are_marked_and_status_notices_are_not(self):
        self.assertIn('class="success js-flash"', _read("delivery/templates/search.html"))
        self.assertIn('class="error js-flash"', _read("templates/login.html"))
        # 固定的狀態提示：每次打開頁面都會有，不要彈出
        self.assertNotIn("js-flash", re.search(r'.*open_incident_count.*\n.*', _read("delivery/templates/home.html")).group(0))
        self.assertNotIn("js-flash", _read("templates/contract_summary.html").split("near_limit_warning")[1].split("\n")[0])

    def test_no_unmarked_result_messages_left(self):
        """{% if error %}／{% if msg %} 這類操作結果訊息都要有 js-flash（新頁面照這個規則寫）。"""
        pattern = re.compile(r'\{%\s*if (error|err|msg)\s*%\}\s*<(p|div) class="(success|error|warning)"')
        for base, _, files in os.walk(ROOT):
            if "node_modules" in base or "/." in base:
                continue
            for name in files:
                if name.endswith(".html"):
                    path = os.path.join(base, name)
                    with open(path, encoding="utf-8") as f:
                        self.assertIsNone(pattern.search(f.read()), path)


if __name__ == "__main__":
    unittest.main()
