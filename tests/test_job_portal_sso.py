import hashlib
import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401  (匯入即副作用：見 _env.py 說明)
from tests import _stub_gcp
_stub_gcp.install()

import job_portal_sso


class SsoTokenTests(unittest.TestCase):
    """mint_sso_token()/verify_sso_token() 是純函式（不碰 Firestore），
    確保：正常情況下能原樣換回姓名/PIN、被竄改或偽造的代碼一律拒絕、
    超過 45 秒有效期後也一律拒絕（過期跟偽造刻意回傳一樣的 None，呼叫端
    不需要區分原因）。"""

    def test_roundtrip(self):
        token = job_portal_sso.mint_sso_token("王小明", "1234")
        result = job_portal_sso.verify_sso_token(token)
        self.assertEqual(result, {"name": "王小明", "pin": "1234"})

    def test_tampered_token_is_rejected(self):
        # 刻意竄改簽章區段（第一個 "." 之前是內容，之後才是時間戳記+簽章）
        # 中間的字元，不要動最後一個字元——base64url 最後一碼常常有幾個
        # bit 是不影響解碼結果的 padding，偶爾竄改最後一碼還是會解出一樣的
        # 內容，導致這個測試不穩定（flaky）。改中間字元才能保證雜湊值一定
        # 跟著改變。
        token = job_portal_sso.mint_sso_token("王小明", "1234")
        mid = len(token) // 2
        tampered = token[:mid] + ("a" if token[mid] != "a" else "b") + token[mid + 1 :]
        self.assertIsNone(job_portal_sso.verify_sso_token(tampered))

    def test_garbage_token_is_rejected(self):
        self.assertIsNone(job_portal_sso.verify_sso_token("not-a-real-token"))

    def test_empty_token_is_rejected(self):
        self.assertIsNone(job_portal_sso.verify_sso_token(""))

    def test_expired_token_is_rejected(self):
        token = job_portal_sso.mint_sso_token("王小明", "1234")
        future = time.time() + job_portal_sso.SSO_TOKEN_MAX_AGE_SECONDS + 1
        with mock.patch("time.time", return_value=future):
            self.assertIsNone(job_portal_sso.verify_sso_token(token))

    def test_token_from_different_salt_is_rejected(self):
        from itsdangerous import URLSafeTimedSerializer

        from delivery.config import SESSION_SECRET_KEY

        other_serializer = URLSafeTimedSerializer(SESSION_SECRET_KEY, salt="some-other-purpose")
        foreign_token = other_serializer.dumps({"name": "王小明", "pin": "1234"})
        self.assertIsNone(job_portal_sso.verify_sso_token(foreign_token))


class ResolvePlaintextPinTests(unittest.TestCase):
    """_resolve_plaintext_pin() 要處理職缺系統組織表 PIN 欄位的兩種可能
    格式：舊資料是明文 4 碼數字、新資料是 sha256Hash(明文 pin) 算出來的
    無鹽 SHA-256 雜湊值（見那個系統主程式的 EmployeeRegistrationService.
    processRegistration()）。VERIFY_LOGIN 端點只收明文 pin，所以雜湊值
    一定要換算回明文才能用。"""

    def test_legacy_plaintext_pin_passthrough(self):
        self.assertEqual(job_portal_sso._resolve_plaintext_pin("1234"), "1234")

    def test_plaintext_pin_with_leading_zero(self):
        self.assertEqual(job_portal_sso._resolve_plaintext_pin("0007"), "0007")

    def test_hashed_pin_resolves_to_plaintext(self):
        hashed = hashlib.sha256(b"5678").hexdigest()
        self.assertEqual(job_portal_sso._resolve_plaintext_pin(hashed), "5678")

    def test_hashed_pin_uppercase_still_resolves(self):
        hashed = hashlib.sha256(b"5678").hexdigest().upper()
        self.assertEqual(job_portal_sso._resolve_plaintext_pin(hashed), "5678")

    def test_unresolvable_value_returns_none(self):
        self.assertIsNone(job_portal_sso._resolve_plaintext_pin("not-a-pin-or-hash"))

    def test_empty_value_returns_none(self):
        self.assertIsNone(job_portal_sso._resolve_plaintext_pin(""))
        self.assertIsNone(job_portal_sso._resolve_plaintext_pin(None))


if __name__ == "__main__":
    unittest.main()
