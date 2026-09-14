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


_VALID_FORM = dict(
    vendor="ud",
    identity_type="雇傭",
    personnel_name="林子椉",
    occurred_at="2026-09-04 11:00",
    location="金山南路一段126號",
    duty_status="執行勤務中",
    police_called="有",
    injury="無",
    family_contacted="無",
    third_party_involved="有",
    description="行進其間與汽車後照鏡擦撞",
)


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
