import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401  (匯入即副作用：見 _env.py 說明)
from tests import _stub_gcp
_stub_gcp.install()

import main
from delivery.routes import rider_routes, webhook_routes
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

    def test_shift_locations_page_redirects_to_login_when_not_authenticated(self):
        resp = self.client.get("/delivery/rider/shift-locations", follow_redirects=False)
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


class _FakeSession(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)


class _FakeRequest:
    def __init__(self, user):
        self.session = _FakeSession({"user": user})


def _admin_account():
    return {"username": "alice", "name": "Alice", "modules": {"delivery": "staff"}, "is_platform_admin": True, "rank": "manager"}


class RiderRidersPageFeatureCategoryTests(unittest.TestCase):
    """2026-09-21 新增：騎士名單管理頁面附上即時算出的合作身份（承攬/
    雇傭/未對應），方便管理員核對誰能用哪個功能。"""

    def test_attaches_feature_category_per_rider(self):
        riders = [{"user_id": "U1", "employee_id": "E001", "name": "小明", "status": "active"}]
        with mock.patch.object(rider_routes.rider_repository, "list_riders", return_value=riders):
            with mock.patch.object(rider_routes.rider_repository, "rider_feature_category", return_value="contract") as mock_cat:
                with mock.patch.object(rider_routes, "templates") as mock_templates:
                    rider_routes.rider_riders_page(_FakeRequest(_admin_account()), redirect=None)
        mock_cat.assert_called_once_with(riders[0])
        context = mock_templates.TemplateResponse.call_args[0][2]
        self.assertEqual(context["riders"][0]["feature_category"], "contract")


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


class PersonnelEmployeeNoSyncWebhookTests(unittest.TestCase):
    """工號一次性搬移端點（2026-09-21 新增）：共用 RIDER_WEBHOOK_SECRET，
    實際比對邏輯在 repository.match_shopee_personnel_employee_no()，這裡
    只驗證密鑰檢查跟把結果原樣回傳。"""

    def setUp(self):
        self.client = TestClient(main.app)

    def test_without_secret_configured_returns_403(self):
        with mock.patch.object(webhook_routes, "RIDER_WEBHOOK_SECRET", ""):
            resp = self.client.post(
                "/delivery/api/personnel-employee-no-sync",
                json={"rows": []},
                headers={"X-Delivery-Rider-Secret": "anything"},
            )
        self.assertEqual(resp.status_code, 403)

    def test_with_wrong_secret_returns_403(self):
        with mock.patch.object(webhook_routes, "RIDER_WEBHOOK_SECRET", "correct-secret"):
            resp = self.client.post(
                "/delivery/api/personnel-employee-no-sync",
                json={"rows": []},
                headers={"X-Delivery-Rider-Secret": "wrong"},
            )
        self.assertEqual(resp.status_code, 403)

    def test_rows_not_a_list_returns_400(self):
        with mock.patch.object(webhook_routes, "RIDER_WEBHOOK_SECRET", "correct-secret"):
            resp = self.client.post(
                "/delivery/api/personnel-employee-no-sync",
                json={"rows": "not-a-list"},
                headers={"X-Delivery-Rider-Secret": "correct-secret"},
            )
        self.assertEqual(resp.status_code, 400)

    def test_with_correct_secret_calls_repository_and_returns_result(self):
        fake_result = {"matched": [{"employee_no": "E001", "name": "小明"}], "ambiguous": [], "not_found": [], "already_set": []}
        with mock.patch.object(webhook_routes, "RIDER_WEBHOOK_SECRET", "correct-secret"):
            with mock.patch.object(webhook_routes.repository, "match_shopee_personnel_employee_no", return_value=fake_result) as mock_match:
                resp = self.client.post(
                    "/delivery/api/personnel-employee-no-sync",
                    json={"rows": [{"employee_no": "E001", "name": "小明"}]},
                    headers={"X-Delivery-Rider-Secret": "correct-secret"},
                )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), fake_result)
        mock_match.assert_called_once_with([{"employee_no": "E001", "name": "小明"}])


if __name__ == "__main__":
    unittest.main()
