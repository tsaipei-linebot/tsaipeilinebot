import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401  (匯入即副作用：見 _env.py 說明)
from tests import _stub_gcp
_stub_gcp.install()

import main
from fastapi.testclient import TestClient


class ApplyClickRedirectTests(unittest.TestCase):
    """/apply-click：職缺卡片「填寫線上履歷」按鈕實際指向的轉址端點。驗證
    三件事——(1) 一定會 302 轉去正確的履歷網站、(2) 帶 uid 時會嘗試記錄
    點擊、(3) 記錄失敗或沒帶 uid 都絕對不能擋住轉址本身。"""

    def setUp(self):
        self.client = TestClient(main.app)

    def test_redirects_to_manufacture_url_by_default_type(self):
        with patch("main.line_bot_api", None), \
             patch("main.test_line_bot_api", None), \
             patch("main.record_resume_click") as mock_record:
            resp = self.client.get(
                "/apply-click?uid=U1234&type=Manufacture&job=%E6%B8%AC%E8%A9%A6%E8%81%B7%E7%BC%BA",
                follow_redirects=False,
            )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp.headers["location"].startswith("https://resume.tsaipei.com.tw"))
        mock_record.assert_called_once()

    def test_unknown_type_falls_back_to_manufacture_instead_of_erroring(self):
        with patch("main.line_bot_api", None), \
             patch("main.test_line_bot_api", None), \
             patch("main.record_resume_click"):
            resp = self.client.get(
                "/apply-click?uid=U1234&type=NotARealKey&job=test",
                follow_redirects=False,
            )
        self.assertEqual(resp.status_code, 302)
        from config import DEFAULT_RESUME_URLS
        self.assertTrue(resp.headers["location"].startswith(DEFAULT_RESUME_URLS["Manufacture"][:40]))

    def test_records_click_with_display_name_from_production_line_bot_api(self):
        fake_profile = MagicMock(display_name="小明")
        with patch("main.line_bot_api") as mock_line_bot_api, \
             patch("main.test_line_bot_api", None), \
             patch("main.record_resume_click") as mock_record:
            mock_line_bot_api.get_profile.return_value = fake_profile
            resp = self.client.get(
                "/apply-click?uid=U1234&type=Spx&job=%E8%9D%A6%E7%9A%AE%E9%96%80%E5%B8%82",
                follow_redirects=False,
            )

        self.assertEqual(resp.status_code, 302)
        mock_record.assert_called_once_with("U1234", "小明", "蝦皮門市", "Spx")

    def test_falls_back_to_test_channel_profile_when_production_lookup_fails(self):
        fake_profile = MagicMock(display_name="小華")
        with patch("main.line_bot_api") as mock_line_bot_api, \
             patch("main.test_line_bot_api") as mock_test_line_bot_api, \
             patch("main.record_resume_click") as mock_record:
            mock_line_bot_api.get_profile.side_effect = RuntimeError("not a friend of this channel")
            mock_test_line_bot_api.get_profile.return_value = fake_profile
            resp = self.client.get("/apply-click?uid=U9999&type=Service&job=test", follow_redirects=False)

        self.assertEqual(resp.status_code, 302)
        mock_record.assert_called_once_with("U9999", "小華", "test", "Service")

    def test_redirect_still_succeeds_even_if_profile_lookup_and_logging_both_fail(self):
        # record_resume_click() 自己內部本來就有 try/except，理論上不會拋出
        # 例外，但這裡刻意模擬「萬一它還是拋了」的最壞情況，確認端點自己
        # 也包了一層防護——轉址永遠是第一優先，記錄點擊失敗絕對不能連帶
        # 讓求職者連不到履歷網站。
        with patch("main.line_bot_api") as mock_line_bot_api, \
             patch("main.test_line_bot_api", None), \
             patch("main.record_resume_click", side_effect=Exception("Notion 掛了")):
            mock_line_bot_api.get_profile.side_effect = RuntimeError("boom")
            resp = self.client.get("/apply-click?uid=U1234&type=Spx&job=test", follow_redirects=False)

        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp.headers["location"].startswith("https://resume.tsaipei.com.tw"))

    def test_missing_uid_skips_logging_but_still_redirects(self):
        with patch("main.record_resume_click") as mock_record:
            resp = self.client.get("/apply-click?type=Spx&job=test", follow_redirects=False)

        self.assertEqual(resp.status_code, 302)
        mock_record.assert_not_called()


if __name__ == "__main__":
    unittest.main()
