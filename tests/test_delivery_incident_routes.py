import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

from delivery.routes import incident_routes


class _FakeSession(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)


class _FakeRequest:
    def __init__(self, user):
        self.session = _FakeSession({"user": user})


def _admin_account():
    # 職級到門檻（見 platform_accounts.MANAGER_RANK_THRESHOLD）才算模組管理員，
    # 編輯意外事件回報內容比照風險等級／結案，只開放管理員。
    return {"username": "alice", "modules": {"delivery": "staff"}, "is_platform_admin": False, "rank": "manager"}


def _staff_account():
    # 一般專員職級，不是管理員——新增意外事件比照 LINE 群組任何人都能
    # 回報，不限管理員，跟編輯/風險等級/結案是不同層級的操作。
    return {"username": "bob", "modules": {"delivery": "staff"}, "is_platform_admin": False, "rank": "specialist"}


_VALID_FORM = dict(
    vendor="ud",
    identity_type="雇傭",
    personnel_name="林子椉",
    occurred_at="2026-09-04 11:00",
    location="金山南路一段126號",
    duty_status="執行勤務中",
    police_called="是",
    injury="無",
    family_contacted="無",
    third_party_involved="有",
    description="行進其間與汽車後照鏡擦撞",
    license_plate="",
)


class NewIncidentFormTests(unittest.TestCase):
    def test_renders_with_blank_form_data(self):
        with mock.patch.object(incident_routes, "templates") as mock_templates:
            incident_routes.new_incident_form(_FakeRequest(_staff_account()), redirect=None)
        mock_templates.TemplateResponse.assert_called_once()
        context = mock_templates.TemplateResponse.call_args[0][2]
        self.assertEqual(context["form_data"], {})


class NewIncidentSubmitTests(unittest.TestCase):
    """2026-09-14 新增：意外事件除了 LINE 群組回報，也能直接在網站新增，
    跟車輛管理（LINE 回報 + 網站手動補登）看齊。"""

    def test_valid_submit_creates_and_redirects_to_detail(self):
        form = dict(_VALID_FORM, occurred_at="2026-09-04T11:00")  # datetime-local 輸入格式
        with mock.patch.object(
            incident_routes.repository, "create_incident_event", return_value=("inc1", True)
        ) as mock_create:
            resp = incident_routes.new_incident_submit(_FakeRequest(_staff_account()), **form, redirect=None)
        mock_create.assert_called_once()
        created_data = mock_create.call_args.args[0]
        # "T" 分隔符要換成空白，跟 LINE 群組回報正規化出來的格式對齊。
        self.assertEqual(created_data["occurred_at"], "2026-09-04 11:00")
        self.assertEqual(resp.status_code, 303)
        self.assertTrue(resp.headers["location"].endswith("/delivery/incidents/inc1"))

    def test_non_admin_staff_can_create(self):
        # 跟編輯/風險等級/結案不同，新增比照 LINE 群組任何人都能回報，
        # 不限管理員（呼叫時 redirect=None 已經跳過 admin_required 的判斷，
        # 這裡驗證的是路由本身沒有另外用 module_role 之類的邏輯二次擋人）。
        with mock.patch.object(incident_routes.repository, "create_incident_event", return_value=("inc1", True)):
            resp = incident_routes.new_incident_submit(_FakeRequest(_staff_account()), **_VALID_FORM, redirect=None)
        self.assertEqual(resp.status_code, 303)

    def test_invalid_vendor_shows_error_without_creating(self):
        bad_form = dict(_VALID_FORM, vendor="黑貓")
        with mock.patch.object(incident_routes.repository, "create_incident_event") as mock_create:
            with mock.patch.object(incident_routes, "templates") as mock_templates:
                incident_routes.new_incident_submit(_FakeRequest(_staff_account()), **bad_form, redirect=None)
        mock_create.assert_not_called()
        context = mock_templates.TemplateResponse.call_args[0][2]
        self.assertTrue(context["error"])
        self.assertEqual(context["form_data"]["vendor"], "黑貓")

    def test_missing_field_shows_error_without_creating(self):
        bad_form = dict(_VALID_FORM, description="   ")
        with mock.patch.object(incident_routes.repository, "create_incident_event") as mock_create:
            with mock.patch.object(incident_routes, "templates"):
                incident_routes.new_incident_submit(_FakeRequest(_staff_account()), **bad_form, redirect=None)
        mock_create.assert_not_called()

    def test_license_plate_passed_through_to_repository(self):
        """車牌號碼（選填、2026-09-15 新增）要原封不動交給
        repository.create_incident_event()。"""
        form = dict(_VALID_FORM, occurred_at="2026-09-04T11:00", license_plate="ABC-1234")
        with mock.patch.object(
            incident_routes.repository, "create_incident_event", return_value=("inc1", True)
        ) as mock_create:
            incident_routes.new_incident_submit(_FakeRequest(_staff_account()), **form, redirect=None)
        created_data = mock_create.call_args.args[0]
        self.assertEqual(created_data["license_plate"], "ABC-1234")

    def test_form_context_includes_police_called_values(self):
        with mock.patch.object(incident_routes, "templates") as mock_templates:
            incident_routes.new_incident_form(_FakeRequest(_staff_account()), redirect=None)
        context = mock_templates.TemplateResponse.call_args[0][2]
        self.assertEqual(context["police_called_values"], ["是", "否"])

    def test_repository_dedup_result_still_redirects_normally(self):
        # repository.create_incident_event 本身已經有「人員名稱＋發生時間」
        # 相同就覆寫既有那筆（created=False）的邏輯，這裡只驗證路由把表單
        # 資料原封不動交給它，並且用它回傳的 id 導頁，不會自己另外判斷。
        with mock.patch.object(
            incident_routes.repository, "create_incident_event", return_value=("existing-inc", False)
        ) as mock_create:
            resp = incident_routes.new_incident_submit(_FakeRequest(_staff_account()), **_VALID_FORM, redirect=None)
        mock_create.assert_called_once()
        self.assertTrue(resp.headers["location"].endswith("/delivery/incidents/existing-inc"))


class EditIncidentFormTests(unittest.TestCase):
    def test_renders_when_incident_exists(self):
        incident = {"id": "inc1", **_VALID_FORM}
        with mock.patch.object(incident_routes.repository, "get_incident_event", return_value=incident):
            with mock.patch.object(incident_routes, "templates") as mock_templates:
                incident_routes.edit_incident_form("inc1", _FakeRequest(_admin_account()), redirect=None)
        mock_templates.TemplateResponse.assert_called_once()
        context = mock_templates.TemplateResponse.call_args[0][2]
        self.assertEqual(context["incident"], incident)

    def test_redirects_when_incident_missing(self):
        with mock.patch.object(incident_routes.repository, "get_incident_event", return_value=None):
            resp = incident_routes.edit_incident_form("inc1", _FakeRequest(_admin_account()), redirect=None)
        self.assertEqual(resp.status_code, 303)

    def test_legacy_you_wu_police_called_displayed_as_new_value(self):
        """2026-09-15 前建立的舊紀錄，police_called 還存著「有」／「無」；
        編輯表單開啟時要視同新值「是」／「否」預選，避免下拉選單看起來
        沒有任何選項被選中——不影響原始 incident dict 以外的資料庫內容，
        純粹是這個畫面渲染用的顯示折衷。"""
        incident = {"id": "inc1", **dict(_VALID_FORM, police_called="有")}
        with mock.patch.object(incident_routes.repository, "get_incident_event", return_value=incident):
            with mock.patch.object(incident_routes, "templates") as mock_templates:
                incident_routes.edit_incident_form("inc1", _FakeRequest(_admin_account()), redirect=None)
        context = mock_templates.TemplateResponse.call_args[0][2]
        self.assertEqual(context["incident"]["police_called"], "是")
        # 原始傳入的 dict 不能被就地改掉（否則呼叫端手上的資料也會被污染）。
        self.assertEqual(incident["police_called"], "有")


class EditIncidentSubmitTests(unittest.TestCase):
    def test_valid_submit_updates_and_redirects(self):
        incident = {"id": "inc1", **_VALID_FORM}
        with mock.patch.object(incident_routes.repository, "get_incident_event", return_value=incident):
            with mock.patch.object(incident_routes.repository, "update_incident_event", return_value=True) as mock_update:
                resp = incident_routes.edit_incident_submit(
                    "inc1", _FakeRequest(_admin_account()), **_VALID_FORM, redirect=None
                )
        mock_update.assert_called_once()
        self.assertEqual(mock_update.call_args.args[0], "inc1")
        updated_data = mock_update.call_args.args[1]
        self.assertEqual(updated_data["personnel_name"], "林子椉")
        self.assertEqual(resp.status_code, 303)

    def test_risk_level_and_status_are_not_touched_by_edit(self):
        # update_incident_event 只更新回報欄位，不該動 risk_level/status，
        # 那兩個欄位各自有獨立的操作入口。
        incident = {"id": "inc1", "risk_level": "高", "status": "closed", **_VALID_FORM}
        with mock.patch.object(incident_routes.repository, "get_incident_event", return_value=incident):
            with mock.patch.object(incident_routes.repository, "update_incident_event", return_value=True) as mock_update:
                incident_routes.edit_incident_submit("inc1", _FakeRequest(_admin_account()), **_VALID_FORM, redirect=None)
        updated_data = mock_update.call_args.args[1]
        self.assertNotIn("risk_level", updated_data)
        self.assertNotIn("status", updated_data)

    def test_invalid_identity_type_shows_error(self):
        incident = {"id": "inc1", **_VALID_FORM}
        bad_form = dict(_VALID_FORM, identity_type="正職")
        with mock.patch.object(incident_routes.repository, "get_incident_event", return_value=incident):
            with mock.patch.object(incident_routes.repository, "update_incident_event") as mock_update:
                with mock.patch.object(incident_routes, "templates") as mock_templates:
                    incident_routes.edit_incident_submit("inc1", _FakeRequest(_admin_account()), **bad_form, redirect=None)
        mock_update.assert_not_called()
        context = mock_templates.TemplateResponse.call_args[0][2]
        self.assertTrue(context["error"])

    def test_invalid_duty_status_shows_error(self):
        incident = {"id": "inc1", **_VALID_FORM}
        bad_form = dict(_VALID_FORM, duty_status="休假中")
        with mock.patch.object(incident_routes.repository, "get_incident_event", return_value=incident):
            with mock.patch.object(incident_routes.repository, "update_incident_event") as mock_update:
                with mock.patch.object(incident_routes, "templates"):
                    incident_routes.edit_incident_submit("inc1", _FakeRequest(_admin_account()), **bad_form, redirect=None)
        mock_update.assert_not_called()

    def test_invalid_yes_no_value_shows_error(self):
        incident = {"id": "inc1", **_VALID_FORM}
        bad_form = dict(_VALID_FORM, police_called="不確定")
        with mock.patch.object(incident_routes.repository, "get_incident_event", return_value=incident):
            with mock.patch.object(incident_routes.repository, "update_incident_event") as mock_update:
                with mock.patch.object(incident_routes, "templates"):
                    incident_routes.edit_incident_submit("inc1", _FakeRequest(_admin_account()), **bad_form, redirect=None)
        mock_update.assert_not_called()

    def test_invalid_vendor_shows_error(self):
        incident = {"id": "inc1", **_VALID_FORM}
        bad_form = dict(_VALID_FORM, vendor="黑貓")
        with mock.patch.object(incident_routes.repository, "get_incident_event", return_value=incident):
            with mock.patch.object(incident_routes.repository, "update_incident_event") as mock_update:
                with mock.patch.object(incident_routes, "templates"):
                    incident_routes.edit_incident_submit("inc1", _FakeRequest(_admin_account()), **bad_form, redirect=None)
        mock_update.assert_not_called()

    def test_missing_field_shows_error(self):
        incident = {"id": "inc1", **_VALID_FORM}
        bad_form = dict(_VALID_FORM, description="   ")
        with mock.patch.object(incident_routes.repository, "get_incident_event", return_value=incident):
            with mock.patch.object(incident_routes.repository, "update_incident_event") as mock_update:
                with mock.patch.object(incident_routes, "templates"):
                    incident_routes.edit_incident_submit("inc1", _FakeRequest(_admin_account()), **bad_form, redirect=None)
        mock_update.assert_not_called()

    def test_incident_missing_redirects(self):
        with mock.patch.object(incident_routes.repository, "get_incident_event", return_value=None):
            resp = incident_routes.edit_incident_submit(
                "inc1", _FakeRequest(_admin_account()), **_VALID_FORM, redirect=None
            )
        self.assertEqual(resp.status_code, 303)


if __name__ == "__main__":
    unittest.main()
