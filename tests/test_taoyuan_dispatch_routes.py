import asyncio
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

import main
import taoyuan_dispatch_routes
from fastapi.testclient import TestClient


class _FakeSession(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)


class _FakeRequest:
    def __init__(self, user=None):
        self.session = _FakeSession()
        if user is not None:
            self.session["user"] = user


def _taoyuan_account():
    return {"username": "alice", "name": "Alice", "department": "桃園所", "is_platform_admin": False}


def _other_department_account():
    return {"username": "bob", "name": "Bob", "department": "新北所", "is_platform_admin": False}


class TaoyuanDispatchRoutingSmokeTests(unittest.TestCase):
    """未登入時導去登入頁——跟其他根層級模組（/contract-summary 等）
    同一種寫法。"""

    def setUp(self):
        self.client = TestClient(main.app)

    def test_home_redirects_to_login_when_not_authenticated(self):
        resp = self.client.get("/taoyuan-dispatch", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertEqual(resp.headers["location"], "/login?next=/taoyuan-dispatch")

    def test_personnel_page_redirects_to_login_when_not_authenticated(self):
        resp = self.client.get("/taoyuan-dispatch/personnel", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)

    def test_locations_page_redirects_to_login_when_not_authenticated(self):
        resp = self.client.get("/taoyuan-dispatch/locations", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)


class RequireAccessDependencyTests(unittest.TestCase):
    """_require_access()：沒登入導去登入頁，登入了但部門不是桃園所（也不是
    全平台管理員）導回 /portal，符合的話放行。"""

    def test_no_session_redirects_to_login(self):
        result = taoyuan_dispatch_routes._require_access(_FakeRequest())
        self.assertIsNotNone(result)
        self.assertEqual(result.headers["location"], "/login?next=/taoyuan-dispatch")

    def test_other_department_redirects_to_portal(self):
        result = taoyuan_dispatch_routes._require_access(_FakeRequest(_other_department_account()))
        self.assertIsNotNone(result)
        self.assertEqual(result.headers["location"], "/portal")

    def test_taoyuan_department_allows_through(self):
        result = taoyuan_dispatch_routes._require_access(_FakeRequest(_taoyuan_account()))
        self.assertIsNone(result)


class CreatePersonnelRouteTests(unittest.TestCase):
    """新增人員路由：姓名/電話缺一不可，資格複選（checkbox）用
    form.getlist() 讀，跟合作方式管理「適用廠商」checkbox 是同一種做法。"""

    class _FakeFormData:
        def __init__(self, data: dict, lists: dict = None):
            self._data = data
            self._lists = lists or {}

        def get(self, key, default=None):
            return self._data.get(key, default)

        def getlist(self, key):
            return self._lists.get(key, [])

    class _FakeFormRequest:
        def __init__(self, user, form_data):
            self.session = _FakeSession({"user": user})
            self._form_data = form_data

        async def form(self):
            return self._form_data

    def test_missing_name_redirects_with_error(self):
        form = self._FakeFormData({"name": "", "phone": "0912345678"})
        result = asyncio.run(
            taoyuan_dispatch_routes.create_taoyuan_dispatch_personnel(
                self._FakeFormRequest(_taoyuan_account(), form), redirect=None
            )
        )
        self.assertEqual(result.status_code, 303)
        self.assertIn("error=", result.headers["location"])

    def test_valid_submission_calls_create_personnel(self):
        form = self._FakeFormData({"name": "王小明", "phone": "0912345678"}, {"qualifications": ["restocking", "operator"]})
        with mock.patch.object(taoyuan_dispatch_routes, "create_personnel") as mock_create:
            result = asyncio.run(
                taoyuan_dispatch_routes.create_taoyuan_dispatch_personnel(
                    self._FakeFormRequest(_taoyuan_account(), form), redirect=None
                )
            )
        mock_create.assert_called_once_with("王小明", "0912345678", ["restocking", "operator"], created_by="alice")
        self.assertEqual(result.status_code, 303)
        self.assertTrue(result.headers["location"].endswith("/taoyuan-dispatch/personnel"))


class CreateLocationRouteTests(unittest.TestCase):
    def test_non_numeric_coordinates_redirect_with_error(self):
        result = taoyuan_dispatch_routes.create_taoyuan_dispatch_location(
            _FakeRequest(_taoyuan_account()), name="桃園火車站", lat="不是數字", lng="121.31", redirect=None
        )
        self.assertEqual(result.status_code, 303)
        self.assertIn("error=", result.headers["location"])

    def test_valid_submission_calls_create_location(self):
        with mock.patch.object(taoyuan_dispatch_routes, "create_location") as mock_create:
            result = taoyuan_dispatch_routes.create_taoyuan_dispatch_location(
                _FakeRequest(_taoyuan_account()), name="桃園火車站", lat="24.98", lng="121.31", redirect=None
            )
        mock_create.assert_called_once_with("桃園火車站", 24.98, 121.31, created_by="alice")
        self.assertTrue(result.headers["location"].endswith("/taoyuan-dispatch/locations"))


if __name__ == "__main__":
    unittest.main()
