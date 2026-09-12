import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

import main
from fastapi.testclient import TestClient


class DepartmentRoutingSmokeTests(unittest.TestCase):
    """/departments 是全平台管理員專用的部門主檔管理頁面（2026-09-12
    新增），跟 test_company_routes.py 的既有分工一致，只涵蓋不需要真的打
    Firestore 的部分：未登入時的導向。"""

    def setUp(self):
        self.client = TestClient(main.app)

    def test_departments_list_redirects_to_portal_when_not_authenticated(self):
        resp = self.client.get("/departments/", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertTrue(resp.headers["location"].endswith("/portal"))

    def test_new_department_form_redirects_to_portal_when_not_authenticated(self):
        resp = self.client.get("/departments/new", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertTrue(resp.headers["location"].endswith("/portal"))

    def test_reorder_redirects_to_portal_when_not_authenticated(self):
        resp = self.client.post("/departments/reorder", json={"ids": []}, follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertTrue(resp.headers["location"].endswith("/portal"))


if __name__ == "__main__":
    unittest.main()
