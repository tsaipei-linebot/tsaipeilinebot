import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401  (匯入即副作用：見 _env.py 說明)
from tests import _stub_gcp
_stub_gcp.install()

import main
from fastapi.testclient import TestClient

LOAD_TEST_SECRET = os.environ["LOAD_TEST_SECRET"]


class LoadTestEndpointTests(unittest.TestCase):
    """驗證 /internal/load-test-message 這個內部壓力測試端點的安全閘門：
    沒帶密鑰或密鑰錯誤一律 403（等同端點不存在），密鑰正確才會真的執行
    process_user_message() 並回傳處理耗時與回覆內容摘要。"""

    def setUp(self):
        self.client = TestClient(main.app)

    def test_rejects_missing_secret_header(self):
        resp = self.client.post(
            "/internal/load-test-message",
            json={"user_id": "loadtest-1", "text": "五股有工作嗎"},
        )
        self.assertEqual(resp.status_code, 403)

    def test_rejects_wrong_secret(self):
        resp = self.client.post(
            "/internal/load-test-message",
            json={"user_id": "loadtest-1", "text": "五股有工作嗎"},
            headers={"X-Load-Test-Secret": "wrong-secret"},
        )
        self.assertEqual(resp.status_code, 403)

    def test_accepts_correct_secret_and_returns_timing_and_reply(self):
        resp = self.client.post(
            "/internal/load-test-message",
            json={"user_id": "loadtest-1", "text": "五股有工作嗎"},
            headers={"X-Load-Test-Secret": LOAD_TEST_SECRET},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("elapsed_seconds", body)
        self.assertIn("reply", body)
        self.assertIsInstance(body["reply"], list)


if __name__ == "__main__":
    unittest.main()
