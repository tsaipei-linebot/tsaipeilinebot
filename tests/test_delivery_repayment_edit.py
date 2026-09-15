import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

from delivery.routes import repayment_routes


class _FakeSession(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)


class _FakeRequest:
    def __init__(self, user):
        self.session = _FakeSession({"user": user})


def _admin_account():
    # 補款登記牽涉薪資金額，比照意外事件編輯只開放管理員。
    return {"username": "alice", "modules": {"delivery": "staff"}, "is_platform_admin": False, "rank": "manager"}


_RECORD = {
    "id": "r1",
    "vendor": "ud",
    "personnel_name": "林子椉",
    "amount": 500,
    "reason": "誤扣補發",
    "occurred_date": "2026-09-10",
    "approved": False,
}


class RepaymentEditFormTests(unittest.TestCase):
    def test_renders_when_record_exists(self):
        with mock.patch.object(repayment_routes.repository, "get_repayment", return_value=_RECORD):
            with mock.patch.object(repayment_routes, "templates") as mock_templates:
                repayment_routes.repayment_edit_form("r1", _FakeRequest(_admin_account()), redirect=None)
        mock_templates.TemplateResponse.assert_called_once()
        context = mock_templates.TemplateResponse.call_args[0][2]
        self.assertEqual(context["record"], _RECORD)

    def test_redirects_when_record_missing(self):
        with mock.patch.object(repayment_routes.repository, "get_repayment", return_value=None):
            resp = repayment_routes.repayment_edit_form("missing", _FakeRequest(_admin_account()), redirect=None)
        self.assertEqual(resp.status_code, 303)
        self.assertEqual(resp.headers["location"], "/delivery/function/repayment/records")


class RepaymentEditSubmitTests(unittest.TestCase):
    def test_valid_submit_updates_and_redirects(self):
        with mock.patch.object(repayment_routes.repository, "get_repayment", return_value=_RECORD):
            with mock.patch.object(repayment_routes.repository, "update_repayment", return_value=True) as mock_update:
                resp = repayment_routes.repayment_edit_submit(
                    "r1",
                    _FakeRequest(_admin_account()),
                    vendor="ud",
                    personnel_name="林子椉",
                    amount="600",
                    reason="修正金額",
                    occurred_date="2026-09-11",
                    redirect=None,
                )
        mock_update.assert_called_once_with(
            "r1",
            vendor="ud",
            personnel_name="林子椉",
            amount=600.0,
            reason="修正金額",
            occurred_date="2026-09-11",
        )
        self.assertEqual(resp.status_code, 303)
        self.assertEqual(resp.headers["location"], "/delivery/function/repayment/records")

    def test_invalid_amount_shows_error_without_updating(self):
        with mock.patch.object(repayment_routes.repository, "get_repayment", return_value=_RECORD):
            with mock.patch.object(repayment_routes.repository, "update_repayment") as mock_update:
                with mock.patch.object(repayment_routes, "templates") as mock_templates:
                    repayment_routes.repayment_edit_submit(
                        "r1",
                        _FakeRequest(_admin_account()),
                        vendor="ud",
                        personnel_name="林子椉",
                        amount="不是數字",
                        reason="",
                        occurred_date="2026-09-11",
                        redirect=None,
                    )
        mock_update.assert_not_called()
        context = mock_templates.TemplateResponse.call_args[0][2]
        self.assertTrue(context["error"])

    def test_record_missing_redirects_without_updating(self):
        with mock.patch.object(repayment_routes.repository, "get_repayment", return_value=None):
            with mock.patch.object(repayment_routes.repository, "update_repayment") as mock_update:
                resp = repayment_routes.repayment_edit_submit(
                    "missing",
                    _FakeRequest(_admin_account()),
                    vendor="ud",
                    personnel_name="林子椉",
                    amount="600",
                    reason="",
                    occurred_date="2026-09-11",
                    redirect=None,
                )
        mock_update.assert_not_called()
        self.assertEqual(resp.status_code, 303)

    def test_returns_redirect_without_updating_when_not_authorized(self):
        """`redirect` 有值代表 admin_required 已經判定沒有主管權限，直接
        短路回傳，完全不呼叫 repository。"""
        from fastapi.responses import RedirectResponse
        blocking_redirect = RedirectResponse(url="/delivery/", status_code=303)
        with mock.patch.object(repayment_routes.repository, "get_repayment") as mock_get:
            resp = repayment_routes.repayment_edit_submit(
                "r1",
                _FakeRequest(_admin_account()),
                vendor="ud",
                personnel_name="林子椉",
                amount="600",
                reason="",
                occurred_date="2026-09-11",
                redirect=blocking_redirect,
            )
        mock_get.assert_not_called()
        self.assertIs(resp, blocking_redirect)


if __name__ == "__main__":
    unittest.main()
