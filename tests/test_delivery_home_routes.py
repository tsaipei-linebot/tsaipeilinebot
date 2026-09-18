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


if __name__ == "__main__":
    unittest.main()
