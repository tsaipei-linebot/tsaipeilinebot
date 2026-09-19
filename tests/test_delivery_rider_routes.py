import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401  (匯入即副作用：見 _env.py 說明)
from tests import _stub_gcp
_stub_gcp.install()

import main
from delivery.routes import webhook_routes
from fastapi.testclient import TestClient


class RiderRoutingSmokeTests(unittest.TestCase):
    """未登入時，門市當日量管理/報班時段管理（login_required）、騎士名單
    管理（admin_required）都要導向登入頁——跟現有 /delivery/help 那組
    smoke test 同一種寫法（見 tests/test_delivery_routes.py）。"""

    def setUp(self):
        self.client = TestClient(main.app)

    def test_store_deliveries_page_redirects_to_login_when_not_authenticated(self):
        resp = self.client.get("/delivery/rider/store-deliveries", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertTrue(resp.headers["location"].endswith("/delivery/login"))

    def test_locations_page_redirects_to_login_when_not_authenticated(self):
        resp = self.client.get("/delivery/rider/locations", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertTrue(resp.headers["location"].endswith("/delivery/login"))

    def test_shifts_page_redirects_to_login_when_not_authenticated(self):
        resp = self.client.get("/delivery/rider/shifts", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertTrue(resp.headers["location"].endswith("/delivery/login"))

    def test_riders_page_redirects_to_login_when_not_authenticated(self):
        resp = self.client.get("/delivery/rider/riders", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertTrue(resp.headers["location"].endswith("/delivery/login"))


class RiderEventsWebhookSecretTests(unittest.TestCase):
    """/delivery/api/rider-events、/delivery/api/rider-binding-sync 都是
    GAS 伺服器對伺服器呼叫，不經過登入 session，改用共用密鑰驗證——跟
    /delivery/api/vehicle-report 是同一種做法（見 webhook_routes.py）。"""

    def setUp(self):
        self.client = TestClient(main.app)

    def test_rider_events_without_secret_configured_returns_403(self):
        with mock.patch.object(webhook_routes, "RIDER_WEBHOOK_SECRET", ""):
            resp = self.client.post("/delivery/api/rider-events", json={"userId": "U1"}, headers={"X-Delivery-Rider-Secret": "anything"})
        self.assertEqual(resp.status_code, 403)

    def test_rider_events_with_wrong_secret_returns_403(self):
        with mock.patch.object(webhook_routes, "RIDER_WEBHOOK_SECRET", "correct-secret"):
            resp = self.client.post("/delivery/api/rider-events", json={"userId": "U1"}, headers={"X-Delivery-Rider-Secret": "wrong"})
        self.assertEqual(resp.status_code, 403)

    def test_rider_events_with_correct_secret_dispatches_event(self):
        with mock.patch.object(webhook_routes, "RIDER_WEBHOOK_SECRET", "correct-secret"):
            with mock.patch.object(webhook_routes, "handle_rider_event", return_value=[{"type": "text", "text": "ok"}]) as mock_handle:
                resp = self.client.post(
                    "/delivery/api/rider-events",
                    json={"userId": "U1", "type": "message"},
                    headers={"X-Delivery-Rider-Secret": "correct-secret"},
                )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"messages": [{"type": "text", "text": "ok"}]})
        mock_handle.assert_called_once_with({"userId": "U1", "type": "message"})

    def test_binding_sync_without_secret_configured_returns_403(self):
        with mock.patch.object(webhook_routes, "RIDER_WEBHOOK_SECRET", ""):
            resp = self.client.post(
                "/delivery/api/rider-binding-sync",
                json={"userId": "U1", "employeeId": "A1", "name": "小明"},
                headers={"X-Delivery-Rider-Secret": "anything"},
            )
        self.assertEqual(resp.status_code, 403)

    def test_binding_sync_with_correct_secret_upserts_binding(self):
        with mock.patch.object(webhook_routes, "RIDER_WEBHOOK_SECRET", "correct-secret"):
            with mock.patch.object(webhook_routes, "rider_repository") as mock_repo:
                resp = self.client.post(
                    "/delivery/api/rider-binding-sync",
                    json={"userId": "U1", "employeeId": "A1", "name": "小明"},
                    headers={"X-Delivery-Rider-Secret": "correct-secret"},
                )
        self.assertEqual(resp.status_code, 200)
        mock_repo.upsert_rider_binding.assert_called_once_with("U1", "A1", "小明")

    def test_binding_sync_missing_user_id_returns_400(self):
        with mock.patch.object(webhook_routes, "RIDER_WEBHOOK_SECRET", "correct-secret"):
            resp = self.client.post(
                "/delivery/api/rider-binding-sync",
                json={"employeeId": "A1", "name": "小明"},
                headers={"X-Delivery-Rider-Secret": "correct-secret"},
            )
        self.assertEqual(resp.status_code, 400)


if __name__ == "__main__":
    unittest.main()
