import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

import main
import taoyuan_dispatch_webhook_routes
from fastapi.testclient import TestClient


class TaoyuanDispatchLineWebhookTests(unittest.TestCase):
    """桃園所派遣專屬 LINE Webhook：沒設定 Channel Secret（測試環境預設
    狀態）一律回傳 503，等同這個功能還沒啟用；有設定但缺簽章 header 則是
    400。不驗證真正的簽章比對邏輯——那是 line-bot-sdk 本身的責任，跟
    management 那組帳號的 webhook 測試是同一種寫法（見
    tests/test_management_routes.py 的 ManagementLineWebhookTests）。"""

    def setUp(self):
        self.client = TestClient(main.app)

    def test_returns_503_when_channel_not_configured(self):
        resp = self.client.post("/taoyuan-dispatch/line/callback", content=b"{}")
        self.assertEqual(resp.status_code, 503)

    def test_returns_400_when_signature_header_missing_but_configured(self):
        original_handler = taoyuan_dispatch_webhook_routes.handler
        taoyuan_dispatch_webhook_routes.handler = object()  # 只需要是 truthy，不會真的被呼叫到
        try:
            resp = self.client.post("/taoyuan-dispatch/line/callback", content=b"{}")
            self.assertEqual(resp.status_code, 400)
        finally:
            taoyuan_dispatch_webhook_routes.handler = original_handler


if __name__ == "__main__":
    unittest.main()
