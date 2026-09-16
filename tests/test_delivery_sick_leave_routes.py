import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

from delivery.routes import sick_leave_routes


class _FakeSession(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)


class _FakeRequest:
    def __init__(self, user):
        self.session = _FakeSession({"user": user})


def _admin_account():
    return {"username": "alice", "modules": {"delivery": "staff"}, "is_platform_admin": False, "rank": "manager"}


_VALID_RECORD = {
    "id": "s1",
    "vendor": "ud",
    "personnel_name": "林子椉",
    "leave_type": "sick",
    "leave_date": "2026-09-01",
    "hours": 8.0,
    "reason": "感冒",
}


class SickLeaveEditFormTests(unittest.TestCase):
    def test_renders_when_record_exists(self):
        with mock.patch.object(sick_leave_routes.repository, "get_sick_leave", return_value=_VALID_RECORD):
            with mock.patch.object(sick_leave_routes, "templates") as mock_templates:
                sick_leave_routes.sick_leave_edit_form("s1", _FakeRequest(_admin_account()), redirect=None)
        mock_templates.TemplateResponse.assert_called_once()
        context = mock_templates.TemplateResponse.call_args[0][2]
        self.assertEqual(context["record"], _VALID_RECORD)

    def test_redirects_when_record_missing(self):
        with mock.patch.object(sick_leave_routes.repository, "get_sick_leave", return_value=None):
            resp = sick_leave_routes.sick_leave_edit_form("missing", _FakeRequest(_admin_account()), redirect=None)
        self.assertEqual(resp.status_code, 303)

    def test_old_format_record_prefills_leave_date_from_start_date(self):
        """2026-09-16 使用者要求：舊格式（只有 start_date/end_date）的
        紀錄也要能編輯，申請日期欄位要用 start_date 當預設值，而不是
        完全不給編輯或顯示空白日期。"""
        old_record = {
            "id": "s1",
            "vendor": "ud",
            "personnel_name": "林子椉",
            "leave_type": "sick",
            "start_date": "2026-01-05",
            "end_date": "2026-01-06",
            "reason": "感冒",
        }
        with mock.patch.object(sick_leave_routes.repository, "get_sick_leave", return_value=old_record):
            with mock.patch.object(sick_leave_routes, "templates") as mock_templates:
                sick_leave_routes.sick_leave_edit_form("s1", _FakeRequest(_admin_account()), redirect=None)
        context = mock_templates.TemplateResponse.call_args[0][2]
        self.assertEqual(context["record"]["leave_date"], "2026-01-05")
        # 原始傳入的 dict 不能被就地改掉。
        self.assertNotIn("leave_date", old_record)


class SickLeaveEditSubmitTests(unittest.TestCase):
    def test_valid_submit_updates_and_redirects(self):
        with mock.patch.object(sick_leave_routes.repository, "get_sick_leave", return_value=_VALID_RECORD):
            with mock.patch.object(sick_leave_routes.repository, "update_sick_leave", return_value=True) as mock_update:
                resp = sick_leave_routes.sick_leave_edit_submit(
                    "s1",
                    _FakeRequest(_admin_account()),
                    vendor="ud",
                    personnel_name="林子椉",
                    leave_type="personal",
                    leave_date="2026-09-02",
                    hours="4",
                    reason="看醫生",
                    redirect=None,
                )
        mock_update.assert_called_once()
        self.assertEqual(mock_update.call_args.args[0], "s1")
        updated = mock_update.call_args.args[1]
        self.assertEqual(updated["hours"], 4.0)
        self.assertEqual(updated["leave_type"], "personal")
        self.assertEqual(resp.status_code, 303)

    def test_invalid_hours_shows_error_without_updating(self):
        with mock.patch.object(sick_leave_routes.repository, "get_sick_leave", return_value=_VALID_RECORD):
            with mock.patch.object(sick_leave_routes.repository, "update_sick_leave") as mock_update:
                with mock.patch.object(sick_leave_routes, "templates") as mock_templates:
                    sick_leave_routes.sick_leave_edit_submit(
                        "s1",
                        _FakeRequest(_admin_account()),
                        vendor="ud",
                        personnel_name="林子椉",
                        leave_type="sick",
                        leave_date="2026-09-02",
                        hours="0",
                        reason="",
                        redirect=None,
                    )
        mock_update.assert_not_called()
        context = mock_templates.TemplateResponse.call_args[0][2]
        self.assertTrue(context["error"])

    def test_invalid_leave_type_shows_error_without_updating(self):
        with mock.patch.object(sick_leave_routes.repository, "get_sick_leave", return_value=_VALID_RECORD):
            with mock.patch.object(sick_leave_routes.repository, "update_sick_leave") as mock_update:
                with mock.patch.object(sick_leave_routes, "templates"):
                    sick_leave_routes.sick_leave_edit_submit(
                        "s1",
                        _FakeRequest(_admin_account()),
                        vendor="ud",
                        personnel_name="林子椉",
                        leave_type="not-a-real-type",
                        leave_date="2026-09-02",
                        hours="4",
                        reason="",
                        redirect=None,
                    )
        mock_update.assert_not_called()

    def test_record_missing_redirects(self):
        with mock.patch.object(sick_leave_routes.repository, "get_sick_leave", return_value=None):
            resp = sick_leave_routes.sick_leave_edit_submit(
                "missing",
                _FakeRequest(_admin_account()),
                vendor="ud",
                personnel_name="林子椉",
                leave_type="sick",
                leave_date="2026-09-02",
                hours="4",
                reason="",
                redirect=None,
            )
        self.assertEqual(resp.status_code, 303)


if __name__ == "__main__":
    unittest.main()
