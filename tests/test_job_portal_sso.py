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


class PepperedPinHashTableTests(unittest.TestCase):
    """對方系統把 PIN 雜湊格式從「無鹽 SHA-256」升級成「加鹽 SHA-256」
    （sha256(pin + PIN_PEPPER)）之後，_build_pin_hash_table() 要能同時
    反查兩種格式，且沒有設定 pepper 時要跟修之前的行為完全一樣（不能
    因為這次修改就讓原本查得到的資料反而查不到）。"""

    def test_no_pepper_only_resolves_legacy_unsalted_hash(self):
        table = job_portal_sso._build_pin_hash_table("")
        legacy_hash = hashlib.sha256(b"5678").hexdigest()
        self.assertEqual(table.get(legacy_hash), "5678")
        peppered_hash = hashlib.sha256("5678some-pepper".encode("utf-8")).hexdigest()
        self.assertNotIn(peppered_hash, table)

    def test_pepper_resolves_peppered_hash(self):
        table = job_portal_sso._build_pin_hash_table("some-pepper")
        peppered_hash = hashlib.sha256("5678some-pepper".encode("utf-8")).hexdigest()
        self.assertEqual(table.get(peppered_hash), "5678")

    def test_pepper_still_resolves_legacy_unsalted_hash(self):
        # 加了 pepper 之後,舊資料(還沒被對方系統升級過的無鹽雜湊)還是要
        # 查得到,不能因為多了加鹽格式就漏掉舊格式。
        table = job_portal_sso._build_pin_hash_table("some-pepper")
        legacy_hash = hashlib.sha256(b"5678").hexdigest()
        self.assertEqual(table.get(legacy_hash), "5678")

    def test_resolve_plaintext_pin_uses_peppered_table_when_configured(self):
        peppered_table = job_portal_sso._build_pin_hash_table("some-pepper")
        peppered_hash = hashlib.sha256("9012some-pepper".encode("utf-8")).hexdigest()
        with mock.patch.object(job_portal_sso, "_PIN_HASH_TO_PLAINTEXT", peppered_table):
            self.assertEqual(job_portal_sso._resolve_plaintext_pin(peppered_hash), "9012")

    def test_wrong_pepper_does_not_resolve_and_does_not_crash(self):
        # pepper 設定錯誤(跟對方系統對不上)時,只是查不到、回傳 None,
        # 不能整個掛掉——這是「先求不出錯」的最低標準。
        table = job_portal_sso._build_pin_hash_table("wrong-pepper")
        peppered_hash = hashlib.sha256("5678some-pepper".encode("utf-8")).hexdigest()
        self.assertIsNone(table.get(peppered_hash))


if __name__ == "__main__":
    unittest.main()
