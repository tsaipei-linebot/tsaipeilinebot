import asyncio
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

import dispatch_routes
import main
from fastapi.testclient import TestClient

SITE = "taoyuan"


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


def _kaohsiung_account():
    return {"username": "carol", "name": "Carol", "department": "高雄所", "is_platform_admin": False}


def _other_department_account():
    return {"username": "bob", "name": "Bob", "department": "新北所", "is_platform_admin": False}


class DispatchRoutingSmokeTests(unittest.TestCase):
    """未登入時導去登入頁——跟其他根層級模組（/contract-summary 等）
    同一種寫法，涵蓋兩個所各自的網址前綴。"""

    def setUp(self):
        self.client = TestClient(main.app)

    def test_home_redirects_to_login_when_not_authenticated(self):
        resp = self.client.get("/dispatch/taoyuan", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertEqual(resp.headers["location"], "/login?next=/dispatch/taoyuan")

    def test_kaohsiung_home_redirects_to_login_when_not_authenticated(self):
        resp = self.client.get("/dispatch/kaohsiung", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertEqual(resp.headers["location"], "/login?next=/dispatch/kaohsiung")

    def test_unknown_site_redirects_to_portal(self):
        """所別代碼不存在時導去 /portal，不會走到登入頁再洩漏頁面結構。"""
        resp = self.client.get("/dispatch/not-a-real-site", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertEqual(resp.headers["location"], "/portal")

    def test_personnel_page_redirects_to_login_when_not_authenticated(self):
        resp = self.client.get("/dispatch/taoyuan/personnel", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)

    def test_locations_page_redirects_to_login_when_not_authenticated(self):
        resp = self.client.get("/dispatch/taoyuan/locations", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)

    def test_postings_page_redirects_to_login_when_not_authenticated(self):
        resp = self.client.get("/dispatch/taoyuan/postings", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)

    def test_posting_registrations_page_redirects_to_login_when_not_authenticated(self):
        resp = self.client.get("/dispatch/taoyuan/postings/post1/registrations", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)


class RequireAccessDependencyTests(unittest.TestCase):
    """_require_access()：所別代碼不存在導回 /portal；沒登入導去登入頁；
    登入了但部門不符這個所（也不是全平台管理員）導回 /portal；符合的話
    放行。也驗證跨所隔離：桃園所帳號進不了高雄所的頁面，反之亦然。"""

    def test_unknown_site_redirects_to_portal(self):
        result = dispatch_routes._require_access("not-a-real-site", _FakeRequest(_taoyuan_account()))
        self.assertIsNotNone(result)
        self.assertEqual(result.headers["location"], "/portal")

    def test_no_session_redirects_to_login(self):
        result = dispatch_routes._require_access(SITE, _FakeRequest())
        self.assertIsNotNone(result)
        self.assertEqual(result.headers["location"], "/login?next=/dispatch/taoyuan")

    def test_other_department_redirects_to_portal(self):
        result = dispatch_routes._require_access(SITE, _FakeRequest(_other_department_account()))
        self.assertIsNotNone(result)
        self.assertEqual(result.headers["location"], "/portal")

    def test_taoyuan_department_allows_through_on_taoyuan_site(self):
        result = dispatch_routes._require_access(SITE, _FakeRequest(_taoyuan_account()))
        self.assertIsNone(result)

    def test_taoyuan_department_blocked_on_kaohsiung_site(self):
        result = dispatch_routes._require_access("kaohsiung", _FakeRequest(_taoyuan_account()))
        self.assertIsNotNone(result)
        self.assertEqual(result.headers["location"], "/portal")

    def test_kaohsiung_department_allows_through_on_kaohsiung_site(self):
        result = dispatch_routes._require_access("kaohsiung", _FakeRequest(_kaohsiung_account()))
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
            dispatch_routes.create_dispatch_personnel(
                SITE, self._FakeFormRequest(_taoyuan_account(), form), redirect=None
            )
        )
        self.assertEqual(result.status_code, 303)
        self.assertIn("error=", result.headers["location"])

    def test_valid_submission_calls_create_personnel(self):
        form = self._FakeFormData({"name": "王小明", "phone": "0912345678"}, {"qualifications": ["restocking", "operator"]})
        with mock.patch.object(dispatch_routes, "create_personnel") as mock_create:
            result = asyncio.run(
                dispatch_routes.create_dispatch_personnel(
                    SITE, self._FakeFormRequest(_taoyuan_account(), form), redirect=None
                )
            )
        mock_create.assert_called_once_with(
            SITE, "王小明", "0912345678", ["restocking", "operator"], created_by="alice"
        )
        self.assertEqual(result.status_code, 303)
        self.assertTrue(result.headers["location"].endswith("/dispatch/taoyuan/personnel"))


class CreateLocationRouteTests(unittest.TestCase):
    def test_non_numeric_coordinates_redirect_with_error(self):
        result = dispatch_routes.create_dispatch_location(
            SITE, _FakeRequest(_taoyuan_account()), name="桃園火車站", lat="不是數字", lng="121.31", redirect=None
        )
        self.assertEqual(result.status_code, 303)
        self.assertIn("error=", result.headers["location"])

    def test_valid_submission_calls_create_location(self):
        with mock.patch.object(dispatch_routes, "create_location") as mock_create:
            result = dispatch_routes.create_dispatch_location(
                SITE, _FakeRequest(_taoyuan_account()), name="桃園火車站", lat="24.98", lng="121.31", redirect=None
            )
        mock_create.assert_called_once_with(SITE, "桃園火車站", 24.98, 121.31, created_by="alice")
        self.assertTrue(result.headers["location"].endswith("/dispatch/taoyuan/locations"))


class CreatePostingRouteTests(unittest.TestCase):
    """開需求時段路由：地點/資格缺一不可，時間格式要對，資格用
    form.getlist() 讀（跟新增人員的 qualifications checkbox 同一種做法）。"""

    def test_missing_location_redirects_with_error(self):
        form = CreatePersonnelRouteTests._FakeFormData(
            {"location_name": "", "start_time": "2026-09-25T09:00", "end_time": "2026-09-25T12:00", "headcount": "2"},
            {"required_qualifications": ["restocking"]},
        )
        result = asyncio.run(
            dispatch_routes.create_dispatch_posting(
                SITE, CreatePersonnelRouteTests._FakeFormRequest(_taoyuan_account(), form), redirect=None
            )
        )
        self.assertEqual(result.status_code, 303)
        self.assertIn("error=", result.headers["location"])

    def test_missing_qualifications_redirects_with_error(self):
        form = CreatePersonnelRouteTests._FakeFormData(
            {"location_name": "桃園火車站", "start_time": "2026-09-25T09:00", "end_time": "2026-09-25T12:00", "headcount": "2"}
        )
        result = asyncio.run(
            dispatch_routes.create_dispatch_posting(
                SITE, CreatePersonnelRouteTests._FakeFormRequest(_taoyuan_account(), form), redirect=None
            )
        )
        self.assertEqual(result.status_code, 303)
        self.assertIn("error=", result.headers["location"])

    def test_invalid_time_format_redirects_with_error(self):
        form = CreatePersonnelRouteTests._FakeFormData(
            {"location_name": "桃園火車站", "start_time": "不是時間", "end_time": "2026-09-25T12:00", "headcount": "2"},
            {"required_qualifications": ["restocking"]},
        )
        result = asyncio.run(
            dispatch_routes.create_dispatch_posting(
                SITE, CreatePersonnelRouteTests._FakeFormRequest(_taoyuan_account(), form), redirect=None
            )
        )
        self.assertEqual(result.status_code, 303)
        self.assertIn("error=", result.headers["location"])

    def test_valid_submission_calls_create_posting(self):
        form = CreatePersonnelRouteTests._FakeFormData(
            {"location_name": "桃園火車站", "start_time": "2026-09-25T09:00", "end_time": "2026-09-25T12:00", "headcount": "2"},
            {"required_qualifications": ["restocking", "operator"]},
        )
        with mock.patch.object(dispatch_routes, "create_posting") as mock_create:
            result = asyncio.run(
                dispatch_routes.create_dispatch_posting(
                    SITE, CreatePersonnelRouteTests._FakeFormRequest(_taoyuan_account(), form), redirect=None
                )
            )
        mock_create.assert_called_once()
        args = mock_create.call_args.args
        self.assertEqual(args[0], SITE)
        self.assertEqual(args[1], "桃園火車站")
        self.assertEqual(args[4], 2)
        self.assertEqual(args[5], ["restocking", "operator"])
        self.assertEqual(mock_create.call_args.kwargs, {"created_by": "alice"})
        self.assertTrue(result.headers["location"].endswith("/dispatch/taoyuan/postings"))


class UpdateRegistrationStatusRouteTests(unittest.TestCase):
    """核准/駁回：狀態真的有改變、而且有留 line_user_id 才推播，避免重複
    按同一個按鈕造成重複推播。"""

    def test_approve_pushes_line_message(self):
        before = {"id": "r1", "status": "pending", "line_user_id": "U1"}
        posting = {"id": "post1", "location_name": "桃園火車站", "start_time": 0}
        with mock.patch.object(dispatch_routes, "get_registration", return_value=before):
            with mock.patch.object(dispatch_routes, "update_registration_status", return_value=True):
                with mock.patch.object(dispatch_routes, "get_posting", return_value=posting):
                    with mock.patch.object(dispatch_routes, "push_message") as mock_push:
                        result = dispatch_routes.update_dispatch_registration_status(
                            SITE, "post1", "r1", status="approved", redirect=None
                        )
        mock_push.assert_called_once()
        self.assertEqual(mock_push.call_args.args[0], SITE)
        self.assertEqual(mock_push.call_args.args[1], "U1")
        self.assertTrue(result.headers["location"].endswith("/dispatch/taoyuan/postings/post1/registrations"))

    def test_no_push_when_status_unchanged(self):
        before = {"id": "r1", "status": "approved", "line_user_id": "U1"}
        with mock.patch.object(dispatch_routes, "get_registration", return_value=before):
            with mock.patch.object(dispatch_routes, "update_registration_status", return_value=True):
                with mock.patch.object(dispatch_routes, "push_message") as mock_push:
                    dispatch_routes.update_dispatch_registration_status(
                        SITE, "post1", "r1", status="approved", redirect=None
                    )
        mock_push.assert_not_called()

    def test_no_push_when_no_line_user_id(self):
        before = {"id": "r1", "status": "pending", "line_user_id": ""}
        with mock.patch.object(dispatch_routes, "get_registration", return_value=before):
            with mock.patch.object(dispatch_routes, "update_registration_status", return_value=True):
                with mock.patch.object(dispatch_routes, "push_message") as mock_push:
                    dispatch_routes.update_dispatch_registration_status(
                        SITE, "post1", "r1", status="approved", redirect=None
                    )
        mock_push.assert_not_called()


if __name__ == "__main__":
    unittest.main()
