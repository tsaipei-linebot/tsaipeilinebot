import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

from delivery.routes import home_routes


class _FakeSession(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)


class _FakeRequest:
    def __init__(self, user):
        self.session = _FakeSession({"user": user})


def _staff_account():
    return {"username": "bob", "modules": {"delivery": "staff"}, "is_platform_admin": False, "rank": "specialist"}


def _department_only_account():
    return {"username": "fay", "department": "新北所(配送組)", "modules": [], "is_platform_admin": False}


class HomeInsurancePanelTests(unittest.TestCase):
    """home()：2026-09-22 新增 show_insurance_panel 這個 context 變數，
    決定要不要在配送部系統首頁多顯示「每日加退保」這個功能區塊（見
    delivery/templates/home.html），判斷邏輯直接沿用
    hr.insurance_repository.can_upload()，不用另外寫一份。"""

    def test_shows_for_upload_department(self):
        account = _department_only_account()
        with mock.patch.object(home_routes.repository, "list_open_incident_events", return_value=[]):
            with mock.patch.object(home_routes, "templates") as mock_templates:
                home_routes.home(_FakeRequest(account), redirect=None)
        context = mock_templates.TemplateResponse.call_args[0][2]
        self.assertTrue(context["show_insurance_panel"])

    def test_hidden_for_non_upload_department(self):
        account = _staff_account()
        with mock.patch.object(home_routes.repository, "list_open_incident_events", return_value=[]):
            with mock.patch.object(home_routes, "templates") as mock_templates:
                home_routes.home(_FakeRequest(account), redirect=None)
        context = mock_templates.TemplateResponse.call_args[0][2]
        self.assertFalse(context["show_insurance_panel"])


class HelpPageTests(unittest.TestCase):
    """使用說明頁（2026-09-18 新增）：走跟 home() 同一組 login_required
    （配送部模組權限），跟 /portal 卡片顯不顯示「使用說明」按鈕是同一組
    權限判斷（見 portal_routes.py 的說明），不會有「按鈕沒有但網址還是
    看得到內容」的落差。"""

    def test_renders_help_template_with_user_context(self):
        account = _staff_account()
        with mock.patch.object(home_routes, "templates") as mock_templates:
            home_routes.help_page(_FakeRequest(account), redirect=None)
        args = mock_templates.TemplateResponse.call_args[0]
        self.assertEqual(args[1], "help.html")
        self.assertEqual(args[2]["user"]["username"], "bob")

    def test_onboarding_flow_section_renders(self):
        """「從應徵到報到（完整流程）」段落（2026-09-24 新增）：實際渲染一次
        模板，確認段落跟頁首的跳轉按鈕都在。"""
        account = _staff_account()
        response = home_routes.help_page(_FakeRequest(account), redirect=None)
        html = response.body.decode("utf-8")
        self.assertIn('id="onboarding-flow"', html)
        self.assertIn('href="#onboarding-flow"', html)
        self.assertIn("按「報到」＝完成報到", html)


if __name__ == "__main__":
    unittest.main()
