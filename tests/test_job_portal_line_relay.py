"""職缺維護 LINE 官方帳號「總機」：驗簽章後原封不動轉給 GAS（2026-09-25）。"""
import base64
import hashlib
import hmac
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

import requests
from fastapi.testclient import TestClient

import main
import platform_accounts
from services import job_portal_line_relay as relay
from services import salary_repayment_store as store
from tests._fake_firestore import FakeFirestore

SECRET = "channel-secret-for-test"
TARGET = "https://script.google.com/macros/s/fake/exec?webhook_secret=abc"
ADMIN = {"username": "boss", "name": "老闆", "department": "", "is_platform_admin": True, "modules": []}


def sign(body: bytes, secret=SECRET) -> str:
    return base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()


def body_of(*events) -> bytes:
    return json.dumps({"destination": "U0", "events": list(events)}, ensure_ascii=False).encode("utf-8")


class _Resp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload if payload is not None else {"status": "success", "message": "LINE Webhook 已處理"}

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class RelayTestCase(unittest.TestCase):
    def setUp(self):
        self.db = FakeFirestore()
        for p in (
            mock.patch.object(relay, "JOB_PORTAL_LINE_CHANNEL_SECRET", SECRET),
            mock.patch.object(relay, "JOB_PORTAL_LINE_RELAY_TARGET_URL", TARGET),
            mock.patch.object(relay, "get_db", return_value=self.db),
            mock.patch.object(store, "get_db", return_value=self.db),
        ):
            p.start()
            self.addCleanup(p.stop)


class SignatureAndEventsTests(RelayTestCase):
    def test_signature(self):
        body = body_of()
        self.assertTrue(relay.verify_signature(body, sign(body)))
        self.assertFalse(relay.verify_signature(body, sign(body, "other")))
        self.assertFalse(relay.verify_signature(body, ""))

    def test_describe_events_has_no_content(self):
        body = body_of(
            {"type": "message", "message": {"type": "text", "text": "綁定+王小明+1234"}},
            {"type": "postback", "postback": {"data": "action=review_salary&status=approve&salary_id=SAL-1"}},
            {"type": "follow"},
        )
        kinds = relay.describe_events(body)
        self.assertEqual(kinds, ["message", "postback:review_salary", "follow"])
        self.assertNotIn("王小明", json.dumps(kinds, ensure_ascii=False))


class ForwardTests(RelayTestCase):
    def test_body_is_forwarded_byte_for_byte(self):
        body = body_of({"type": "message", "message": {"type": "text", "text": "你好"}})
        with mock.patch.object(requests, "post", return_value=_Resp()) as post:
            self.assertEqual(relay.forward_to_gas(body), (True, "GAS 已處理"))
        self.assertEqual(post.call_args.args[0], TARGET)
        self.assertEqual(post.call_args.kwargs["data"], body)

    def test_gas_rejects_wrong_url_secret(self):
        with mock.patch.object(requests, "post", return_value=_Resp(payload={"status": "error", "message": "unauthorized"})):
            ok, note = relay.forward_to_gas(body_of())
        self.assertFalse(ok)
        self.assertIn("unauthorized", note)

    def test_network_error_and_non_json(self):
        with mock.patch.object(requests, "post", side_effect=requests.ConnectionError()):
            self.assertFalse(relay.forward_to_gas(body_of())[0])
        with mock.patch.object(requests, "post", return_value=_Resp(status=500, payload=ValueError())):
            self.assertEqual(relay.forward_to_gas(body_of()), (False, "GAS 回應 HTTP 500"))

    def test_log_keeps_latest_entries(self):
        for i in range(relay.LOG_LIMIT + 5):
            relay.record([f"e{i}"], True, "ok")
        logs = relay.recent()
        self.assertEqual(len(logs), relay.LOG_LIMIT)
        self.assertEqual(logs[0]["events"], [f"e{relay.LOG_LIMIT + 4}"])


class WebhookRouteTests(RelayTestCase):
    def setUp(self):
        super().setUp()
        self.client = TestClient(main.app)

    def post(self, body, signature):
        return self.client.post("/api/job-portal/line-webhook", content=body,
                                headers={"X-Line-Signature": signature, "Content-Type": "application/json"})

    def test_valid_request_is_relayed(self):
        body = body_of({"type": "postback", "postback": {"data": "action=review_job&x=1"}})
        with mock.patch.object(requests, "post", return_value=_Resp()) as post:
            resp = self.post(body, sign(body))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(post.call_args.kwargs["data"], body)
        self.assertEqual(relay.recent()[0]["events"], ["postback:review_job"])

    def test_bad_signature_is_not_relayed(self):
        body = body_of()
        with mock.patch.object(requests, "post") as post:
            resp = self.post(body, "wrong")
        self.assertEqual(resp.status_code, 403)
        post.assert_not_called()

    def test_not_configured(self):
        with mock.patch.object(relay, "JOB_PORTAL_LINE_CHANNEL_SECRET", ""), mock.patch.object(requests, "post") as post:
            resp = self.post(body_of(), "x")
        self.assertEqual(resp.status_code, 503)
        post.assert_not_called()

    def test_migration_page_shows_logs(self):
        body = body_of()
        with mock.patch.object(requests, "post", return_value=_Resp()):
            self.post(body, sign(body))
        with mock.patch.object(platform_accounts, "current_account", return_value=ADMIN):
            html = self.client.get("/finance/migration").text
        self.assertIn("（LINE 後台驗證）", html)
        self.assertIn("GAS 已處理", html)

    def test_migration_page_when_not_configured(self):
        with mock.patch.object(relay, "JOB_PORTAL_LINE_RELAY_TARGET_URL", ""), \
                mock.patch.object(platform_accounts, "current_account", return_value=ADMIN):
            html = self.client.get("/finance/migration").text
        self.assertIn("總機還沒啟用", html)


if __name__ == "__main__":
    unittest.main()
