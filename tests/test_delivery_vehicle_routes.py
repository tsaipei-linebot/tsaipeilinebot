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


def _admin_account():
    return {"username": "alice", "modules": {"delivery": "staff"}, "is_platform_admin": False, "rank": "manager"}


_TAIPEI_AREA = {"id": "taipei", "name": "台北", "active": True}
_KAOHSIUNG_AREA = {"id": "kaohsiung", "name": "高雄", "active": True}


class CreateVehicleWheelTypeTests(unittest.TestCase):
    """2026-09-14 新增：新增車輛時要能選輪別（三輪／二輪），預設三輪。"""

    def test_valid_wheel_type_is_passed_to_repository(self):
        with mock.patch.object(vehicle_routes.repository, "get_vehicle_service_area", return_value=_TAIPEI_AREA):
            with mock.patch.object(vehicle_routes.repository, "create_vehicle", return_value=True) as mock_create:
                resp = vehicle_routes.create_vehicle_submit(
                    _FakeRequest(_staff_account()),
                    vehicle_no="ERV-1",
                    vendor="ud",
                    wheel_type="two_wheel",
                    service_area="taipei",
                    redirect=None,
                )
        mock_create.assert_called_once_with("ERV-1", "ud", "bob", wheel_type="two_wheel", service_area="taipei")
        self.assertEqual(resp.status_code, 303)

    def test_default_wheel_type_constant_is_three_wheel(self):
        self.assertEqual(vehicle_routes.DEFAULT_WHEEL_TYPE, "three_wheel")

    def test_invalid_wheel_type_is_rejected_without_creating(self):
        with mock.patch.object(vehicle_routes.repository, "list_vehicle_service_areas", return_value=[_TAIPEI_AREA]):
            with mock.patch.object(vehicle_routes.repository, "create_vehicle") as mock_create:
                with mock.patch.object(vehicle_routes, "templates") as mock_templates:
                    vehicle_routes.create_vehicle_submit(
                        _FakeRequest(_staff_account()),
                        vehicle_no="ERV-1",
                        vendor="ud",
                        wheel_type="four_wheel",
                        service_area="taipei",
                        redirect=None,
                    )
        mock_create.assert_not_called()
        context = mock_templates.TemplateResponse.call_args[0][2]
        self.assertTrue(context["error"])


class CreateVehicleServiceAreaTests(unittest.TestCase):
    """2026-09-14 新增：新增車輛時要選服務區域（必填）。2026-09-18 起服務
    區域改成動態清單，「合法」代表 repository.get_vehicle_service_area()
    查得到這個 ID（不再是查固定的 SERVICE_AREA_MAP）。"""

    def test_valid_service_area_is_passed_to_repository(self):
        with mock.patch.object(
            vehicle_routes.repository, "get_vehicle_service_area", return_value=_KAOHSIUNG_AREA
        ):
            with mock.patch.object(vehicle_routes.repository, "create_vehicle", return_value=True) as mock_create:
                resp = vehicle_routes.create_vehicle_submit(
                    _FakeRequest(_staff_account()),
                    vehicle_no="ERV-1",
                    vendor="ud",
                    wheel_type="three_wheel",
                    service_area="kaohsiung",
                    redirect=None,
                )
        mock_create.assert_called_once_with(
            "ERV-1", "ud", "bob", wheel_type="three_wheel", service_area="kaohsiung"
        )
        self.assertEqual(resp.status_code, 303)

    def test_invalid_service_area_is_rejected_without_creating(self):
        with mock.patch.object(vehicle_routes.repository, "get_vehicle_service_area", return_value=None):
            with mock.patch.object(vehicle_routes.repository, "list_vehicle_service_areas", return_value=[]):
                with mock.patch.object(vehicle_routes.repository, "create_vehicle") as mock_create:
                    with mock.patch.object(vehicle_routes, "templates") as mock_templates:
                        vehicle_routes.create_vehicle_submit(
                            _FakeRequest(_staff_account()),
                            vehicle_no="ERV-1",
                            vendor="ud",
                            wheel_type="three_wheel",
                            service_area="chiayi",
                            redirect=None,
                        )
        mock_create.assert_not_called()
        context = mock_templates.TemplateResponse.call_args[0][2]
        self.assertTrue(context["error"])

    def test_blank_service_area_is_rejected_without_creating(self):
        with mock.patch.object(vehicle_routes.repository, "list_vehicle_service_areas", return_value=[]):
            with mock.patch.object(vehicle_routes.repository, "create_vehicle") as mock_create:
                with mock.patch.object(vehicle_routes, "templates"):
                    vehicle_routes.create_vehicle_submit(
                        _FakeRequest(_staff_account()),
                        vehicle_no="ERV-1",
                        vendor="ud",
                        wheel_type="three_wheel",
                        service_area="",
                        redirect=None,
                    )
        mock_create.assert_not_called()


class UpdateVehicleServiceAreaTests(unittest.TestCase):
    def test_calls_repository_and_redirects(self):
        with mock.patch.object(vehicle_routes.repository, "set_vehicle_service_area", return_value=True) as mock_set:
            resp = vehicle_routes.update_vehicle_service_area(
                "ERV-1", _FakeRequest(_staff_account()), service_area="tainan", redirect=None
            )
        mock_set.assert_called_once_with("ERV-1", "tainan")
        self.assertEqual(resp.status_code, 303)

    def test_blank_service_area_clears_it(self):
        # 空字串是合法值（代表「未設定」），用來清掉之前選錯的服務區域。
        with mock.patch.object(vehicle_routes.repository, "set_vehicle_service_area", return_value=True) as mock_set:
            resp = vehicle_routes.update_vehicle_service_area(
                "ERV-1", _FakeRequest(_staff_account()), service_area="", redirect=None
            )
        mock_set.assert_called_once_with("ERV-1", "")
        self.assertEqual(resp.status_code, 303)


class VehicleStatusReportPageTests(unittest.TestCase):
    def test_fetches_all_vehicles_and_renders_report(self):
        vehicles = [{"vendor": "ud", "status": "available", "service_area": "taipei"}]
        with mock.patch.object(vehicle_routes.repository, "list_vehicles", return_value=vehicles) as mock_list:
            with mock.patch.object(
                vehicle_routes.repository, "list_vehicle_service_areas", return_value=[_TAIPEI_AREA]
            ) as mock_areas:
                with mock.patch.object(vehicle_routes, "templates") as mock_templates:
                    vehicle_routes.vehicle_status_report_page(_FakeRequest(_staff_account()), redirect=None)
        mock_list.assert_called_once_with()
        mock_areas.assert_called_once_with(include_inactive=True)
        context = mock_templates.TemplateResponse.call_args[0][2]
        self.assertIn("UD", context["report_text"])


class UpdateVehicleWheelTypeTests(unittest.TestCase):
    def test_calls_repository_and_redirects(self):
        with mock.patch.object(vehicle_routes.repository, "set_vehicle_wheel_type", return_value=True) as mock_set:
            resp = vehicle_routes.update_vehicle_wheel_type(
                "ERV-1", _FakeRequest(_staff_account()), wheel_type="two_wheel", redirect=None
            )
        mock_set.assert_called_once_with("ERV-1", "two_wheel")
        self.assertEqual(resp.status_code, 303)


class UpdateVehicleVendorTests(unittest.TestCase):
    """2026-09-16 新增：車輛所屬廠商原本只有新增車輛當下能設定，之後
    沒有地方可以改，補上跟輪別/服務區域一樣的網頁編輯入口。"""

    def test_calls_repository_and_redirects(self):
        with mock.patch.object(vehicle_routes.repository, "set_vehicle_vendor", return_value=True) as mock_set:
            resp = vehicle_routes.update_vehicle_vendor(
                "ERV-1", _FakeRequest(_staff_account()), vendor="ud", redirect=None
            )
        mock_set.assert_called_once_with("ERV-1", "ud")
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
                        phone="",
                        note="",
                        needs_maintenance="",
                        redirect=None,
                    )
        mock_update.assert_called_once_with(
            event_id="evt1",
            vendor="ud",
            personnel_name="王小明",
            event_type="return",
            event_date="2026-01-02",
            location="台北市",
            phone="",
            note="",
            needs_maintenance=False,
        )
        self.assertEqual(resp.status_code, 303)

    def test_valid_submit_with_maintenance_flagged(self):
        # 2026-09-16 新增：編輯表單也能勾選待維修、填電話備註。
        vehicle, event = self._vehicle_and_event()
        with mock.patch.object(vehicle_routes.repository, "get_vehicle", return_value=vehicle):
            with mock.patch.object(vehicle_routes.repository, "get_vehicle_event", return_value=event):
                with mock.patch.object(vehicle_routes.repository, "update_vehicle_event", return_value=True) as mock_update:
                    vehicle_routes.edit_vehicle_event_submit(
                        "ERV-1",
                        "evt1",
                        _FakeRequest(_staff_account()),
                        vendor="ud",
                        personnel_name="王小明",
                        event_type="checkout",
                        event_date="2026-01-02",
                        location="台北市",
                        phone=" 0912345678 ",
                        note=" 輪胎異音 ",
                        needs_maintenance="1",
                        redirect=None,
                    )
        mock_update.assert_called_once_with(
            event_id="evt1",
            vendor="ud",
            personnel_name="王小明",
            event_type="checkout",
            event_date="2026-01-02",
            location="台北市",
            phone="0912345678",
            note="輪胎異音",
            needs_maintenance=True,
        )

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
                            phone="",
                            note="",
                            needs_maintenance="",
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
                            phone="",
                            note="",
                            needs_maintenance="",
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
                            phone="",
                            note="",
                            needs_maintenance="",
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


class ManualVehicleEventGroupNotifyTests(unittest.TestCase):
    """2026-09-15 新增：網站手動補登領車/還車成功後，要額外推播一則通知到
    配送組作業群組（見 delivery/group_notify.py），跟 LINE 群組回報的體驗
    一致；失敗（被擋下）的補登不推播。"""

    def test_successful_checkout_notifies_group(self):
        with mock.patch.object(vehicle_routes.repository, "record_vehicle_event", return_value=(True, "")):
            with mock.patch.object(vehicle_routes.group_notify, "notify_group") as mock_notify:
                vehicle_routes.manual_vehicle_event(
                    "ERV-1",
                    _FakeRequest(_staff_account()),
                    vendor="ud",
                    personnel_name="王小明",
                    event_type="checkout",
                    event_date="2026-09-15",
                    location="台北市",
                    phone="",
                    note="",
                    needs_maintenance="",
                    redirect=None,
                )
        mock_notify.assert_called_once()
        text = mock_notify.call_args.args[0]
        self.assertIn("領車", text)
        self.assertIn("ERV-1", text)
        self.assertIn("王小明", text)

    def test_successful_return_notifies_group_with_return_label(self):
        with mock.patch.object(vehicle_routes.repository, "record_vehicle_event", return_value=(True, "")):
            with mock.patch.object(vehicle_routes.group_notify, "notify_group") as mock_notify:
                vehicle_routes.manual_vehicle_event(
                    "ERV-1",
                    _FakeRequest(_staff_account()),
                    vendor="ud",
                    personnel_name="王小明",
                    event_type="return",
                    event_date="2026-09-15",
                    location="台北市",
                    phone="",
                    note="",
                    needs_maintenance="",
                    redirect=None,
                )
        text = mock_notify.call_args.args[0]
        self.assertIn("還車", text)

    def test_maintenance_flagged_event_notifies_with_maintenance_note(self):
        # 2026-09-16 新增：手動補登也能勾選待維修、填電話備註，推播訊息要
        # 一併帶上，跟 LINE 群組回報體驗一致。
        with mock.patch.object(vehicle_routes.repository, "record_vehicle_event", return_value=(True, "")) as mock_record:
            with mock.patch.object(vehicle_routes.group_notify, "notify_group") as mock_notify:
                vehicle_routes.manual_vehicle_event(
                    "ERV-1",
                    _FakeRequest(_staff_account()),
                    vendor="ud",
                    personnel_name="王小明",
                    event_type="checkout",
                    event_date="2026-09-15",
                    location="台北市",
                    phone="0912345678",
                    note="輪胎異音",
                    needs_maintenance="1",
                    redirect=None,
                )
        mock_record.assert_called_once_with(
            vehicle_no="ERV-1",
            vendor="ud",
            personnel_name="王小明",
            event_type="checkout",
            event_date="2026-09-15",
            location="台北市",
            source="manual",
            reported_by="bob",
            phone="0912345678",
            note="輪胎異音",
            needs_maintenance=True,
        )
        text = mock_notify.call_args.args[0]
        self.assertIn("待維修", text)
        self.assertIn("0912345678", text)
        self.assertIn("輪胎異音", text)

    def test_blocked_event_does_not_notify_group(self):
        with mock.patch.object(
            vehicle_routes.repository, "record_vehicle_event", return_value=(False, "already_in_use")
        ):
            with mock.patch.object(vehicle_routes.group_notify, "notify_group") as mock_notify:
                vehicle_routes.manual_vehicle_event(
                    "ERV-1",
                    _FakeRequest(_staff_account()),
                    vendor="ud",
                    personnel_name="王小明",
                    event_type="checkout",
                    event_date="2026-09-15",
                    location="台北市",
                    phone="",
                    note="",
                    needs_maintenance="",
                    redirect=None,
                )
        mock_notify.assert_not_called()


class DeleteVehicleEventTests(unittest.TestCase):
    """主管在車輛詳細頁歷史紀錄的「刪除」按鈕：只開放管理員，且要確認
    這筆事件確實屬於這台車（跟編輯共用 _get_vehicle_and_own_event()），
    避免用別台車的事件 ID 硬湊網址刪到不相干車輛的歷史紀錄。"""

    def _event(self, vehicle_no="ERV-1"):
        return {"id": "evt1", "vehicle_no": vehicle_no}

    def test_deletes_when_event_belongs_to_vehicle(self):
        vehicle = {"vehicle_no": "ERV-1"}
        event = self._event()
        with mock.patch.object(vehicle_routes.repository, "get_vehicle", return_value=vehicle):
            with mock.patch.object(vehicle_routes.repository, "get_vehicle_event", return_value=event):
                with mock.patch.object(vehicle_routes.repository, "delete_vehicle_event", return_value=True) as mock_delete:
                    resp = vehicle_routes.delete_vehicle_event(
                        "ERV-1", "evt1", _FakeRequest(_admin_account()), redirect=None
                    )
        mock_delete.assert_called_once_with("evt1")
        self.assertEqual(resp.status_code, 303)
        self.assertEqual(resp.headers["location"], "/delivery/vehicles/ERV-1")

    def test_does_not_delete_when_event_belongs_to_different_vehicle(self):
        vehicle = {"vehicle_no": "ERV-1"}
        event = self._event(vehicle_no="ERV-9")
        with mock.patch.object(vehicle_routes.repository, "get_vehicle", return_value=vehicle):
            with mock.patch.object(vehicle_routes.repository, "get_vehicle_event", return_value=event):
                with mock.patch.object(vehicle_routes.repository, "delete_vehicle_event") as mock_delete:
                    vehicle_routes.delete_vehicle_event("ERV-1", "evt1", _FakeRequest(_admin_account()), redirect=None)
        mock_delete.assert_not_called()


class VehicleListRiderCooperationTypeTests(unittest.TestCase):
    """騎手身份欄位/篩選（2026-09-18 新增）：車輛清單頁每一列都要反查一次
    目前使用人的合作方式（見 repository.resolve_vehicle_rider_cooperation_type()
    的說明），篩選是靠反查出來的結果比對，不是車輛主檔本身的欄位。"""

    def _vehicle(self, vehicle_no="ERV-1", **overrides):
        base = {"vehicle_no": vehicle_no, "vendor": "shopee", "current_holder": "小明"}
        base.update(overrides)
        return base

    def test_attaches_resolved_cooperation_type_to_each_vehicle(self):
        vehicles = [self._vehicle()]
        coop = {"id": "two_wheel_contract", "name": "二輪承攬"}
        with mock.patch.object(vehicle_routes.repository, "list_vehicles", return_value=vehicles):
            with mock.patch.object(
                vehicle_routes.repository, "resolve_vehicle_rider_cooperation_type", return_value=coop
            ):
                with mock.patch.object(vehicle_routes.repository, "list_vehicle_service_areas", return_value=[]):
                    with mock.patch.object(vehicle_routes.repository, "list_cooperation_types", return_value=[]):
                        with mock.patch.object(vehicle_routes, "templates") as mock_templates:
                            vehicle_routes.vehicle_list(_FakeRequest(_staff_account()), redirect=None)
        context = mock_templates.TemplateResponse.call_args[0][2]
        self.assertEqual(context["vehicles"][0]["rider_cooperation_type"], coop)

    def test_cooperation_type_filter_keeps_only_matching_vehicles(self):
        vehicles = [self._vehicle("ERV-1"), self._vehicle("ERV-2")]
        coop_a = {"id": "two_wheel_contract", "name": "二輪承攬"}
        coop_b = {"id": "two_wheel_employed", "name": "二輪雇傭"}

        def fake_resolve(v):
            return coop_a if v["vehicle_no"] == "ERV-1" else coop_b

        with mock.patch.object(vehicle_routes.repository, "list_vehicles", return_value=vehicles):
            with mock.patch.object(
                vehicle_routes.repository, "resolve_vehicle_rider_cooperation_type", side_effect=fake_resolve
            ):
                with mock.patch.object(vehicle_routes.repository, "list_vehicle_service_areas", return_value=[]):
                    with mock.patch.object(vehicle_routes.repository, "list_cooperation_types", return_value=[]):
                        with mock.patch.object(vehicle_routes, "templates") as mock_templates:
                            vehicle_routes.vehicle_list(
                                _FakeRequest(_staff_account()), cooperation_type="two_wheel_contract", redirect=None
                            )
        context = mock_templates.TemplateResponse.call_args[0][2]
        self.assertEqual([v["vehicle_no"] for v in context["vehicles"]], ["ERV-1"])

    def test_vehicles_with_no_match_are_excluded_when_filtering(self):
        vehicles = [self._vehicle("ERV-1")]
        with mock.patch.object(vehicle_routes.repository, "list_vehicles", return_value=vehicles):
            with mock.patch.object(
                vehicle_routes.repository, "resolve_vehicle_rider_cooperation_type", return_value=None
            ):
                with mock.patch.object(vehicle_routes.repository, "list_vehicle_service_areas", return_value=[]):
                    with mock.patch.object(vehicle_routes.repository, "list_cooperation_types", return_value=[]):
                        with mock.patch.object(vehicle_routes, "templates") as mock_templates:
                            vehicle_routes.vehicle_list(
                                _FakeRequest(_staff_account()), cooperation_type="two_wheel_contract", redirect=None
                            )
        context = mock_templates.TemplateResponse.call_args[0][2]
        self.assertEqual(context["vehicles"], [])

    def test_no_filter_keeps_vehicles_without_a_match(self):
        vehicles = [self._vehicle("ERV-1")]
        with mock.patch.object(vehicle_routes.repository, "list_vehicles", return_value=vehicles):
            with mock.patch.object(
                vehicle_routes.repository, "resolve_vehicle_rider_cooperation_type", return_value=None
            ):
                with mock.patch.object(vehicle_routes.repository, "list_vehicle_service_areas", return_value=[]):
                    with mock.patch.object(vehicle_routes.repository, "list_cooperation_types", return_value=[]):
                        with mock.patch.object(vehicle_routes, "templates") as mock_templates:
                            vehicle_routes.vehicle_list(_FakeRequest(_staff_account()), redirect=None)
        context = mock_templates.TemplateResponse.call_args[0][2]
        self.assertEqual([v["vehicle_no"] for v in context["vehicles"]], ["ERV-1"])


if __name__ == "__main__":
    unittest.main()
