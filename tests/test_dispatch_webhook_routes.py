import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

import dispatch_line
import dispatch_webhook_routes
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


class ReplyHandlerSilenceTests(unittest.TestCase):
    """2026-09-22 修正：handle_message() 回傳空字串代表「這則訊息不是在跟
    派遣功能互動」（這幾個所的 LINE 官方帳號跟求職者共用），這時候連
    reply_message() 都不能呼叫——replyToken 自然過期，求職者那邊完全不會
    收到系統訊息。"""

    def _fake_event(self, text: str):
        event = mock.Mock()
        event.source.user_id = "U1"
        event.message.text = text
        event.reply_token = "token1"
        return event

    def test_empty_reply_does_not_call_line_api(self):
        reply_handler = dispatch_webhook_routes._make_reply_handler("taoyuan")
        with mock.patch.object(dispatch_webhook_routes.dispatch_bot, "handle_message", return_value=""):
            with mock.patch.object(dispatch_webhook_routes, "get_line_bot_api") as mock_api:
                reply_handler(self._fake_event("哈囉"))
        mock_api.assert_not_called()

    def test_non_empty_reply_is_sent(self):
        reply_handler = dispatch_webhook_routes._make_reply_handler("taoyuan")
        with mock.patch.object(dispatch_webhook_routes.dispatch_bot, "handle_message", return_value="綁定成功！"):
            with mock.patch.object(dispatch_webhook_routes, "get_line_bot_api") as mock_api:
                reply_handler(self._fake_event("綁定+王小明+0912345678"))
        mock_api.return_value.reply_message.assert_called_once()
        sent_message = mock_api.return_value.reply_message.call_args.args[1]
        self.assertEqual(sent_message.text, "綁定成功！")


if __name__ == "__main__":
    unittest.main()
