import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

import dispatch_line
import main
from fastapi.testclient import TestClient


class DispatchLineWebhookTests(unittest.TestCase):
    """多所派遣專屬 LINE Webhook（2026-09-22 重構自
    test_taoyuan_dispatch_webhook_routes.py）：沒設定 Channel Secret
    （測試環境預設狀態）一律回傳 503，等同這個所的功能還沒啟用；有設定
    但缺簽章 header 則是 400；沒在 dispatch_sites.py 登記過的所別代碼，
    行為跟「沒設定」一樣回 503，不會洩漏這個所存在與否。不驗證真正的
    簽章比對邏輯——那是 line-bot-sdk 本身的責任，跟 management 那組帳號
    的 webhook 測試是同一種寫法（見 tests/test_management_routes.py 的
    ManagementLineWebhookTests）。"""

    def setUp(self):
        self.client = TestClient(main.app)

    def test_returns_503_when_channel_not_configured(self):
        resp = self.client.post("/dispatch/taoyuan/line/callback", content=b"{}")
        self.assertEqual(resp.status_code, 503)

    def test_returns_503_for_unknown_site(self):
        resp = self.client.post("/dispatch/not-a-real-site/line/callback", content=b"{}")
        self.assertEqual(resp.status_code, 503)

    def test_returns_400_when_signature_header_missing_but_configured(self):
        original_handler = dispatch_line._handlers.get("taoyuan")
        dispatch_line._handlers["taoyuan"] = object()  # 只需要是 truthy，不會真的被呼叫到
        try:
            resp = self.client.post("/dispatch/taoyuan/line/callback", content=b"{}")
            self.assertEqual(resp.status_code, 400)
        finally:
            dispatch_line._handlers["taoyuan"] = original_handler


if __name__ == "__main__":
    unittest.main()
