import asyncio
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
        """2026-09-23 改成用整批對照表（build_rider_category_map）取代每位騎士
        查一次的 rider_feature_category()——判斷結果一樣，但整頁只打 2 趟
        Firestore 而不是 2N 趟，見那支函式開頭的說明。"""
        riders = [{"user_id": "U1", "employee_id": "E001", "name": "小明", "status": "active"}]
        with mock.patch.object(rider_routes.rider_repository, "list_riders", return_value=riders):
            with mock.patch.object(
                rider_routes.rider_repository, "build_rider_category_map", return_value={"E001": "contract"}
            ) as mock_map:
                with mock.patch.object(rider_routes, "templates") as mock_templates:
                    rider_routes.rider_riders_page(_FakeRequest(_admin_account()), redirect=None)
        # 整份清單只建一次對照表，不是每位騎士建一次
        mock_map.assert_called_once_with()
        context = mock_templates.TemplateResponse.call_args[0][2]
        self.assertEqual(context["riders"][0]["feature_category"], "contract")

    def test_rider_without_a_matching_employee_no_is_left_blank(self):
        riders = [{"user_id": "U1", "employee_id": "沒這個工號", "name": "小明", "status": "active"}]
        with mock.patch.object(rider_routes.rider_repository, "list_riders", return_value=riders):
            with mock.patch.object(
                rider_routes.rider_repository, "build_rider_category_map", return_value={"E001": "contract"}
            ):
                with mock.patch.object(rider_routes, "templates") as mock_templates:
                    rider_routes.rider_riders_page(_FakeRequest(_admin_account()), redirect=None)
        context = mock_templates.TemplateResponse.call_args[0][2]
        self.assertEqual(context["riders"][0]["feature_category"], "")


class RiderRidersPageFilterTests(unittest.TestCase):
    """2026-09-23 新增：工號/姓名篩選。使用者回報騎士多了之後找不到人，
    而且工號欄位太窄看不到完整工號。"""

    def _riders(self):
        return [
            {"user_id": "U1", "employee_id": "大廷_A001", "name": "饒明書", "status": "active"},
            {"user_id": "U2", "employee_id": "大廷_B002", "name": "江明哲", "status": "active"},
        ]

    def _run(self, **kwargs):
        with mock.patch.object(rider_routes.rider_repository, "list_riders", return_value=self._riders()):
            with mock.patch.object(rider_routes.rider_repository, "build_rider_category_map", return_value={}):
                with mock.patch.object(rider_routes, "templates") as mock_templates:
                    rider_routes.rider_riders_page(_FakeRequest(_admin_account()), redirect=None, **kwargs)
        return mock_templates.TemplateResponse.call_args[0][2]

    def test_no_filter_shows_everyone(self):
        context = self._run()
        self.assertEqual(len(context["riders"]), 2)

    def test_employee_id_partial_match(self):
        # 同仁常常只記得工號後面幾碼，要求打完整組不合實際
        context = self._run(employee_id="A001")
        self.assertEqual([r["name"] for r in context["riders"]], ["饒明書"])

    def test_name_partial_match(self):
        context = self._run(name="明哲")
        self.assertEqual([r["name"] for r in context["riders"]], ["江明哲"])

    def test_both_filters_must_match_the_same_rider(self):
        context = self._run(employee_id="A001", name="江明哲")
        self.assertEqual(context["riders"], [])

    def test_has_any_rider_reflects_the_unfiltered_list(self):
        """篩選沒中跟「一個騎士都還沒綁定」要分得開，畫面上顯示的提示不一樣。"""
        context = self._run(name="查無此人")
        self.assertEqual(context["riders"], [])
        self.assertTrue(context["has_any_rider"])


class UpdateRiderInfoTests(unittest.TestCase):
    """2026-09-21 新增：騎士名單管理加上編輯工號/姓名的功能——原本只能靠
    LINE「綁定+工號+姓名」私訊帶進來，打錯字沒地方能直接修正。直接沿用
    跟 GAS 綁定同步同一支 upsert_rider_binding()，行為要完全跟騎士自己
    重新私訊綁定一次一致（保留既有 status、重新核對工號對應的人員名冊）。"""

    def test_calls_upsert_rider_binding_with_stripped_values(self):
        with mock.patch.object(rider_routes.rider_repository, "upsert_rider_binding") as mock_upsert:
            rider_routes.update_rider_info("U1", employee_id="  E002  ", name="  小華  ", redirect=None)
        mock_upsert.assert_called_once_with("U1", "E002", "小華")

    def test_redirects_to_riders_page(self):
        with mock.patch.object(rider_routes.rider_repository, "upsert_rider_binding"):
            result = rider_routes.update_rider_info("U1", employee_id="E002", name="小華", redirect=None)
        self.assertEqual(result.status_code, 303)
        self.assertTrue(result.headers["location"].endswith("/delivery/rider/riders"))


class RiderShiftsPageFilterTests(unittest.TestCase):
    """2026-09-21 新增：報班時段清單要能依地點/日期篩選。"""

    def test_passes_filters_through_to_repository_and_context(self):
        with mock.patch.object(rider_routes.rider_repository, "list_shift_postings", return_value=[]) as mock_list:
            with mock.patch.object(rider_routes.rider_repository, "list_shift_locations", return_value=[]):
                with mock.patch.object(rider_routes, "templates") as mock_templates:
                    rider_routes.rider_shifts_page(
                        _FakeRequest(_admin_account()), location="台北車站", date="2025-01-01", redirect=None
                    )
        mock_list.assert_called_once_with(location="台北車站", date_str="2025-01-01")
        context = mock_templates.TemplateResponse.call_args[0][2]
        self.assertEqual(context["filter_location"], "台北車站")
        self.assertEqual(context["filter_date"], "2025-01-01")

    def test_no_filters_defaults_to_blank(self):
        with mock.patch.object(rider_routes.rider_repository, "list_shift_postings", return_value=[]) as mock_list:
            with mock.patch.object(rider_routes.rider_repository, "list_shift_locations", return_value=[]):
                with mock.patch.object(rider_routes, "templates"):
                    rider_routes.rider_shifts_page(_FakeRequest(_admin_account()), redirect=None)
        mock_list.assert_called_once_with(location="", date_str="")


class UpdateRiderShiftRegistrationStatusTests(unittest.TestCase):
    """2026-09-21 新增：報班改成人工審核制，管理員在報名名單頁面核准/
    駁回單一筆報名。"""

    def test_calls_update_registration_status_and_redirects(self):
        with mock.patch.object(rider_routes.rider_repository, "update_registration_status") as mock_update:
            result = rider_routes.update_rider_shift_registration_status(
                "shift1", "reg1", status="approved", redirect=None
            )
        mock_update.assert_called_once_with("reg1", "approved")
        self.assertEqual(result.status_code, 303)
        self.assertTrue(result.headers["location"].endswith("/delivery/rider/shifts/shift1/registrations"))


class _FakeUploadFile:
    def __init__(self, content: bytes):
        self._content = content

    async def read(self):
        return self._content


class RiderShiftsImportSubmitTests(unittest.TestCase):
    """2026-09-21 新增：報班時段管理需要批次匯入的功能。"""

    def test_creates_postings_for_valid_rows_and_collects_failures(self):
        csv_content = (
            "地點,開始時間,結束時間,需求人數\n"
            "台北車站,2024-01-31 09:00,2024-01-31 18:00,3\n"
            "查無地點,2024-01-31 09:00,2024-01-31 18:00,3\n"
        ).encode("utf-8")
        locations = [{"id": "l1", "name": "台北車站", "lat": 25.0478, "lng": 121.5170}]
        with mock.patch.object(rider_routes.rider_repository, "list_shift_locations", return_value=locations):
            with mock.patch.object(rider_routes.rider_repository, "list_shift_postings", return_value=[]):
                with mock.patch.object(rider_routes.rider_repository, "create_shift_posting") as mock_create:
                    with mock.patch.object(rider_routes, "templates") as mock_templates:
                        asyncio.run(
                            rider_routes.rider_shifts_import_submit(
                                _FakeRequest(_admin_account()), file=_FakeUploadFile(csv_content), redirect=None
                            )
                        )
        mock_create.assert_called_once()
        context = mock_templates.TemplateResponse.call_args[0][2]
        self.assertEqual(len(context["import_result"]["created"]), 1)
        self.assertEqual(len(context["import_result"]["failed"]), 1)
        self.assertIn("找不到", context["import_result"]["failed"][0]["error"])

    def test_header_error_is_reported_without_creating_anything(self):
        csv_content = "不是正確的表頭\n1,2\n".encode("utf-8")
        with mock.patch.object(rider_routes.rider_repository, "list_shift_locations", return_value=[]):
            with mock.patch.object(rider_routes.rider_repository, "list_shift_postings", return_value=[]):
                with mock.patch.object(rider_routes.rider_repository, "create_shift_posting") as mock_create:
                    with mock.patch.object(rider_routes, "templates") as mock_templates:
                        asyncio.run(
                            rider_routes.rider_shifts_import_submit(
                                _FakeRequest(_admin_account()), file=_FakeUploadFile(csv_content), redirect=None
                            )
                        )
        mock_create.assert_not_called()
        context = mock_templates.TemplateResponse.call_args[0][2]
        self.assertIsNotNone(context["import_result"]["header_error"])


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
