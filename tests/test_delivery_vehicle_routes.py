import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

from delivery.routes import vehicle_routes


class _FakeSession(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)


class _FakeRequest:
    def __init__(self, user):
        self.session = _FakeSession({"user": user})


def _staff_account():
    return {"username": "bob", "modules": {"delivery": "staff"}, "is_platform_admin": False, "rank": "specialist"}


class CreateVehicleWheelTypeTests(unittest.TestCase):
    """2026-09-14 新增：新增車輛時要能選輪別（三輪／二輪），預設三輪。"""

    def test_valid_wheel_type_is_passed_to_repository(self):
        with mock.patch.object(vehicle_routes.repository, "create_vehicle", return_value=True) as mock_create:
            resp = vehicle_routes.create_vehicle_submit(
                _FakeRequest(_staff_account()), vehicle_no="ERV-1", vendor="ud", wheel_type="two_wheel", redirect=None
            )
        mock_create.assert_called_once_with("ERV-1", "ud", "bob", wheel_type="two_wheel")
        self.assertEqual(resp.status_code, 303)

    def test_default_wheel_type_constant_is_three_wheel(self):
        self.assertEqual(vehicle_routes.DEFAULT_WHEEL_TYPE, "three_wheel")

    def test_invalid_wheel_type_is_rejected_without_creating(self):
        with mock.patch.object(vehicle_routes.repository, "create_vehicle") as mock_create:
            with mock.patch.object(vehicle_routes, "templates") as mock_templates:
                vehicle_routes.create_vehicle_submit(
                    _FakeRequest(_staff_account()), vehicle_no="ERV-1", vendor="ud", wheel_type="four_wheel", redirect=None
                )
        mock_create.assert_not_called()
        context = mock_templates.TemplateResponse.call_args[0][2]
        self.assertTrue(context["error"])


class UpdateVehicleWheelTypeTests(unittest.TestCase):
    def test_calls_repository_and_redirects(self):
        with mock.patch.object(vehicle_routes.repository, "set_vehicle_wheel_type", return_value=True) as mock_set:
            resp = vehicle_routes.update_vehicle_wheel_type(
                "ERV-1", _FakeRequest(_staff_account()), wheel_type="two_wheel", redirect=None
            )
        mock_set.assert_called_once_with("ERV-1", "two_wheel")
        self.assertEqual(resp.status_code, 303)


class EditVehicleEventFormTests(unittest.TestCase):
    """2026-09-14 新增：歷史紀錄（領還車事件）可編輯。"""

    def _event(self, vehicle_no="ERV-1"):
        return {
            "id": "evt1",
            "vehicle_no": vehicle_no,
            "vendor": "ud",
            "personnel_name": "李四",
            "event_type": "checkout",
            "event_date": "2026-01-01",
            "location": "台北市",
        }

    def test_renders_when_event_belongs_to_vehicle(self):
        vehicle = {"vehicle_no": "ERV-1", "vendor": "ud"}
        event = self._event()
        with mock.patch.object(vehicle_routes.repository, "get_vehicle", return_value=vehicle):
            with mock.patch.object(vehicle_routes.repository, "get_vehicle_event", return_value=event):
                with mock.patch.object(vehicle_routes, "templates") as mock_templates:
                    vehicle_routes.edit_vehicle_event_form("ERV-1", "evt1", _FakeRequest(_staff_account()), redirect=None)
        mock_templates.TemplateResponse.assert_called_once()
        context = mock_templates.TemplateResponse.call_args[0][2]
        self.assertEqual(context["event"], event)

    def test_redirects_when_event_belongs_to_a_different_vehicle(self):
        # 避免同仁用別台車的事件 ID 硬湊網址，編輯到不相干車輛的歷史紀錄。
        vehicle = {"vehicle_no": "ERV-1", "vendor": "ud"}
        event = self._event(vehicle_no="ERV-9")
        with mock.patch.object(vehicle_routes.repository, "get_vehicle", return_value=vehicle):
            with mock.patch.object(vehicle_routes.repository, "get_vehicle_event", return_value=event):
                resp = vehicle_routes.edit_vehicle_event_form("ERV-1", "evt1", _FakeRequest(_staff_account()), redirect=None)
        self.assertEqual(resp.status_code, 303)

    def test_redirects_when_vehicle_missing(self):
        with mock.patch.object(vehicle_routes.repository, "get_vehicle", return_value=None):
            resp = vehicle_routes.edit_vehicle_event_form("ERV-1", "evt1", _FakeRequest(_staff_account()), redirect=None)
        self.assertEqual(resp.status_code, 303)

    def test_redirects_when_event_missing(self):
        vehicle = {"vehicle_no": "ERV-1", "vendor": "ud"}
        with mock.patch.object(vehicle_routes.repository, "get_vehicle", return_value=vehicle):
            with mock.patch.object(vehicle_routes.repository, "get_vehicle_event", return_value=None):
                resp = vehicle_routes.edit_vehicle_event_form("ERV-1", "evt1", _FakeRequest(_staff_account()), redirect=None)
        self.assertEqual(resp.status_code, 303)


class EditVehicleEventSubmitTests(unittest.TestCase):
    def _vehicle_and_event(self):
        vehicle = {"vehicle_no": "ERV-1", "vendor": "ud"}
        event = {"id": "evt1", "vehicle_no": "ERV-1"}
        return vehicle, event

    def test_valid_submit_updates_and_redirects(self):
        vehicle, event = self._vehicle_and_event()
        with mock.patch.object(vehicle_routes.repository, "get_vehicle", return_value=vehicle):
            with mock.patch.object(vehicle_routes.repository, "get_vehicle_event", return_value=event):
                with mock.patch.object(vehicle_routes.repository, "update_vehicle_event", return_value=True) as mock_update:
                    resp = vehicle_routes.edit_vehicle_event_submit(
                        "ERV-1",
                        "evt1",
                        _FakeRequest(_staff_account()),
                        vendor="ud",
                        personnel_name=" 王小明 ",
                        event_type="return",
                        event_date="2026-01-02",
                        location=" 台北市 ",
                        redirect=None,
                    )
        mock_update.assert_called_once_with(
            event_id="evt1",
            vendor="ud",
            personnel_name="王小明",
            event_type="return",
            event_date="2026-01-02",
            location="台北市",
        )
        self.assertEqual(resp.status_code, 303)

    def test_missing_personnel_name_shows_error_and_does_not_update(self):
        vehicle, event = self._vehicle_and_event()
        with mock.patch.object(vehicle_routes.repository, "get_vehicle", return_value=vehicle):
            with mock.patch.object(vehicle_routes.repository, "get_vehicle_event", return_value=event):
                with mock.patch.object(vehicle_routes.repository, "update_vehicle_event") as mock_update:
                    with mock.patch.object(vehicle_routes, "templates") as mock_templates:
                        vehicle_routes.edit_vehicle_event_submit(
                            "ERV-1",
                            "evt1",
                            _FakeRequest(_staff_account()),
                            vendor="ud",
                            personnel_name="   ",
                            event_type="return",
                            event_date="2026-01-02",
                            location="台北市",
                            redirect=None,
                        )
        mock_update.assert_not_called()
        context = mock_templates.TemplateResponse.call_args[0][2]
        self.assertTrue(context["error"])

    def test_invalid_vendor_shows_error(self):
        vehicle, event = self._vehicle_and_event()
        with mock.patch.object(vehicle_routes.repository, "get_vehicle", return_value=vehicle):
            with mock.patch.object(vehicle_routes.repository, "get_vehicle_event", return_value=event):
                with mock.patch.object(vehicle_routes.repository, "update_vehicle_event") as mock_update:
                    with mock.patch.object(vehicle_routes, "templates"):
                        vehicle_routes.edit_vehicle_event_submit(
                            "ERV-1",
                            "evt1",
                            _FakeRequest(_staff_account()),
                            vendor="黑貓",
                            personnel_name="王小明",
                            event_type="return",
                            event_date="2026-01-02",
                            location="台北市",
                            redirect=None,
                        )
        mock_update.assert_not_called()

    def test_invalid_event_type_shows_error(self):
        vehicle, event = self._vehicle_and_event()
        with mock.patch.object(vehicle_routes.repository, "get_vehicle", return_value=vehicle):
            with mock.patch.object(vehicle_routes.repository, "get_vehicle_event", return_value=event):
                with mock.patch.object(vehicle_routes.repository, "update_vehicle_event") as mock_update:
                    with mock.patch.object(vehicle_routes, "templates"):
                        vehicle_routes.edit_vehicle_event_submit(
                            "ERV-1",
                            "evt1",
                            _FakeRequest(_staff_account()),
                            vendor="ud",
                            personnel_name="王小明",
                            event_type="lost",
                            event_date="2026-01-02",
                            location="台北市",
                            redirect=None,
                        )
        mock_update.assert_not_called()

    def test_event_not_belonging_to_vehicle_redirects_without_updating(self):
        vehicle = {"vehicle_no": "ERV-1", "vendor": "ud"}
        event = {"id": "evt1", "vehicle_no": "ERV-9"}
        with mock.patch.object(vehicle_routes.repository, "get_vehicle", return_value=vehicle):
            with mock.patch.object(vehicle_routes.repository, "get_vehicle_event", return_value=event):
                with mock.patch.object(vehicle_routes.repository, "update_vehicle_event") as mock_update:
                    resp = vehicle_routes.edit_vehicle_event_submit(
                        "ERV-1",
                        "evt1",
                        _FakeRequest(_staff_account()),
                        vendor="ud",
                        personnel_name="王小明",
                        event_type="return",
                        event_date="2026-01-02",
                        location="台北市",
                        redirect=None,
                    )
        mock_update.assert_not_called()
        self.assertEqual(resp.status_code, 303)


if __name__ == "__main__":
    unittest.main()
