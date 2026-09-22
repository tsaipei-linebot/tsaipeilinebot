import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

from delivery import repository


def _fake_doc_snapshot(exists: bool, data: dict = None, doc_id: str = "ERV-1"):
    snapshot = mock.Mock(exists=exists)
    snapshot.id = doc_id
    snapshot.to_dict.return_value = data or {}
    return snapshot


def _fake_collection(snapshot):
    fake_doc_ref = mock.Mock()
    fake_doc_ref.get.return_value = snapshot
    fake_collection = mock.Mock()
    fake_collection.document.return_value = fake_doc_ref
    return fake_collection, fake_doc_ref


class SetVehicleSiteTests(unittest.TestCase):
    """2026-09-22 新增：車輛的「站所」是自由文字，不像服務區域要檢查合法性。"""

    def test_updates_and_returns_true_when_exists(self):
        snapshot = _fake_doc_snapshot(True, {"vehicle_no": "ERV-1"})
        fake_collection, fake_doc_ref = _fake_collection(snapshot)
        with mock.patch.object(repository, "vehicles_ref", return_value=fake_collection):
            result = repository.set_vehicle_site("ERV-1", "  NS2  ")
        self.assertTrue(result)
        fake_doc_ref.update.assert_called_once_with({"site": "NS2"})

    def test_returns_false_when_missing(self):
        snapshot = _fake_doc_snapshot(False)
        fake_collection, fake_doc_ref = _fake_collection(snapshot)
        with mock.patch.object(repository, "vehicles_ref", return_value=fake_collection):
            result = repository.set_vehicle_site("missing", "NS2")
        self.assertFalse(result)
        fake_doc_ref.update.assert_not_called()

    def test_blank_site_clears_it(self):
        snapshot = _fake_doc_snapshot(True, {"vehicle_no": "ERV-1"})
        fake_collection, fake_doc_ref = _fake_collection(snapshot)
        with mock.patch.object(repository, "vehicles_ref", return_value=fake_collection):
            repository.set_vehicle_site("ERV-1", "")
        fake_doc_ref.update.assert_called_once_with({"site": ""})


class GetVehicleSiteDefaultTests(unittest.TestCase):
    def test_missing_site_defaults_to_empty_string(self):
        snapshot = _fake_doc_snapshot(True, {"vehicle_no": "ERV-1", "vendor": "ud"})
        fake_collection, _ = _fake_collection(snapshot)
        with mock.patch.object(repository, "vehicles_ref", return_value=fake_collection):
            vehicle = repository.get_vehicle("ERV-1")
        self.assertEqual(vehicle["site"], "")

    def test_existing_site_is_preserved(self):
        snapshot = _fake_doc_snapshot(True, {"vehicle_no": "ERV-1", "vendor": "ud", "site": "NS2"})
        fake_collection, _ = _fake_collection(snapshot)
        with mock.patch.object(repository, "vehicles_ref", return_value=fake_collection):
            vehicle = repository.get_vehicle("ERV-1")
        self.assertEqual(vehicle["site"], "NS2")


class RecordVehicleEventUdSyncTriggerTests(unittest.TestCase):
    """2026-09-22 新增：UD 廠商的領車/還車事件要觸發試算表同步，其他廠商
    不能觸發（見 delivery/ud_vehicle_sheet_sync.py 的說明）。同步邏輯本身
    另外在 tests/test_ud_vehicle_sheet_sync.py 測，這裡只測「有沒有在對
    的時機被呼叫、帶對的參數」。"""

    def _fake_vehicle_snapshot(self, vendor="ud", status="available", site="NS2", service_area="taipei"):
        return _fake_doc_snapshot(
            True,
            {
                "vehicle_no": "ERV-1",
                "vendor": vendor,
                "status": status,
                "site": site,
                "service_area": service_area,
            },
        )

    def _fake_events_collection(self):
        fake_events = mock.Mock()
        fake_events.document.return_value.set = mock.Mock()
        return fake_events

    def test_ud_checkout_triggers_sync_with_expected_args(self):
        vehicles_collection, vehicles_doc_ref = _fake_collection(self._fake_vehicle_snapshot())
        events_collection = self._fake_events_collection()
        with mock.patch.object(repository, "vehicles_ref", return_value=vehicles_collection):
            with mock.patch.object(repository, "vehicle_events_ref", return_value=events_collection):
                with mock.patch.object(
                    repository, "get_vehicle_service_area", return_value={"id": "taipei", "name": "台北"}
                ):
                    with mock.patch.object(repository, "_sync_ud_vehicle_sheet") as mock_sync:
                        ok, error = repository.record_vehicle_event(
                            vehicle_no="ERV-1",
                            vendor="ud",
                            personnel_name="王小明",
                            event_type="checkout",
                            event_date="2026-09-22",
                            location="台北市信義區",
                            source="manual",
                            phone="0912345678",
                            note="備註內容",
                        )
        self.assertTrue(ok)
        self.assertEqual(error, "")
        mock_sync.assert_called_once_with(
            vehicle_no="ERV-1",
            service_area="taipei",
            site="NS2",
            personnel_name="王小明",
            phone="0912345678",
            status="in_use",
            location="台北市信義區",
            event_type="checkout",
            event_date="2026-09-22",
            note="備註內容",
        )

    def test_non_ud_vendor_does_not_trigger_sync(self):
        vehicles_collection, _ = _fake_collection(self._fake_vehicle_snapshot(vendor="shopee"))
        events_collection = self._fake_events_collection()
        with mock.patch.object(repository, "vehicles_ref", return_value=vehicles_collection):
            with mock.patch.object(repository, "vehicle_events_ref", return_value=events_collection):
                with mock.patch.object(repository, "_sync_ud_vehicle_sheet") as mock_sync:
                    ok, error = repository.record_vehicle_event(
                        vehicle_no="ERV-1",
                        vendor="shopee",
                        personnel_name="王小明",
                        event_type="checkout",
                        event_date="2026-09-22",
                        location="台北市信義區",
                        source="manual",
                    )
        self.assertTrue(ok)
        mock_sync.assert_not_called()

    def test_rejected_event_does_not_trigger_sync(self):
        # 車輛已經在使用中，領車事件會被擋下，不應該觸發同步。
        vehicles_collection, _ = _fake_collection(self._fake_vehicle_snapshot(status="in_use"))
        with mock.patch.object(repository, "vehicles_ref", return_value=vehicles_collection):
            with mock.patch.object(repository, "_sync_ud_vehicle_sheet") as mock_sync:
                ok, error = repository.record_vehicle_event(
                    vehicle_no="ERV-1",
                    vendor="ud",
                    personnel_name="王小明",
                    event_type="checkout",
                    event_date="2026-09-22",
                    location="台北市信義區",
                    source="manual",
                )
        self.assertFalse(ok)
        self.assertEqual(error, "not_available")
        mock_sync.assert_not_called()

class SyncUdVehicleSheetHelperTests(unittest.TestCase):
    """repository._sync_ud_vehicle_sheet() 本身吞掉所有例外的行為。"""

    def test_swallows_exceptions_from_ud_vehicle_sheet_sync_module(self):
        with mock.patch.object(
            repository, "get_vehicle_service_area", side_effect=RuntimeError("firestore down")
        ):
            # 不應該往外拋例外。
            repository._sync_ud_vehicle_sheet(
                vehicle_no="ERV-1",
                service_area="taipei",
                site="NS2",
                personnel_name="王小明",
                phone="0912345678",
                status="in_use",
                location="台北市信義區",
                event_type="checkout",
                event_date="2026-09-22",
                note="",
            )


if __name__ == "__main__":
    unittest.main()
