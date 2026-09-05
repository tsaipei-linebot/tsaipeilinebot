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
        token = job_portal_sso.mint_sso_token("王小明", "1234")
        tampered = token[:-1] + ("a" if token[-1] != "a" else "b")
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


if __name__ == "__main__":
    unittest.main()
