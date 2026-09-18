import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

from delivery import repository
from delivery.routes import home_routes


def _fake_doc_snapshot(exists: bool, data: dict = None, doc_id: str = "id1"):
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


def _snapshot(doc_id, data):
    snapshot = mock.Mock()
    snapshot.id = doc_id
    snapshot.to_dict.return_value = data
    return snapshot


class ListActiveAnnouncementsTests(unittest.TestCase):
    """2026-09-18 新增：主頁公告欄，主管可自行發佈、到期自動下架（不用
    排程清資料，查詢當下比對 expires_at 就好）。"""

    def test_excludes_expired(self):
        now = time.time()
        fake_collection = mock.Mock()
        fake_collection.stream.return_value = [
            _snapshot("a", {"title": "還沒過期", "active": True, "created_at": now, "expires_at": now + 100}),
            _snapshot("b", {"title": "已過期", "active": True, "created_at": now, "expires_at": now - 100}),
        ]
        with mock.patch.object(repository, "announcements_ref", return_value=fake_collection):
            result = repository.list_active_announcements()
        self.assertEqual([a["id"] for a in result], ["a"])

    def test_excludes_inactive_even_if_not_expired(self):
        now = time.time()
        fake_collection = mock.Mock()
        fake_collection.stream.return_value = [
            _snapshot("a", {"title": "已下架", "active": False, "created_at": now, "expires_at": now + 100}),
        ]
        with mock.patch.object(repository, "announcements_ref", return_value=fake_collection):
            result = repository.list_active_announcements()
        self.assertEqual(result, [])

    def test_sorted_newest_first(self):
        now = time.time()
        fake_collection = mock.Mock()
        fake_collection.stream.return_value = [
            _snapshot("old", {"title": "舊", "active": True, "created_at": now - 10, "expires_at": now + 100}),
            _snapshot("new", {"title": "新", "active": True, "created_at": now, "expires_at": now + 100}),
        ]
        with mock.patch.object(repository, "announcements_ref", return_value=fake_collection):
            result = repository.list_active_announcements()
        self.assertEqual([a["id"] for a in result], ["new", "old"])

    def test_missing_active_field_defaults_to_true(self):
        now = time.time()
        fake_collection = mock.Mock()
        fake_collection.stream.return_value = [
            _snapshot("a", {"title": "舊資料", "created_at": now, "expires_at": now + 100}),
        ]
        with mock.patch.object(repository, "announcements_ref", return_value=fake_collection):
            result = repository.list_active_announcements()
        self.assertEqual(len(result), 1)


class ListAnnouncementsTests(unittest.TestCase):
    def test_includes_expired_and_marks_them(self):
        now = time.time()
        fake_collection = mock.Mock()
        fake_collection.stream.return_value = [
            _snapshot("a", {"title": "已過期", "active": True, "created_at": now, "expires_at": now - 100}),
            _snapshot("b", {"title": "還沒過期", "active": True, "created_at": now, "expires_at": now + 100}),
        ]
        with mock.patch.object(repository, "announcements_ref", return_value=fake_collection):
            result = repository.list_announcements()
        by_id = {a["id"]: a for a in result}
        self.assertTrue(by_id["a"]["expired"])
        self.assertFalse(by_id["b"]["expired"])


class CreateAnnouncementTests(unittest.TestCase):
    def test_sets_default_expiry_seven_days_out(self):
        fake_doc_ref = mock.Mock()
        fake_doc_ref.id = "ann1"
        fake_collection = mock.Mock()
        fake_collection.document.return_value = fake_doc_ref
        with mock.patch.object(repository, "announcements_ref", return_value=fake_collection):
            result = repository.create_announcement("標題", "說明", created_by="gary")
        self.assertEqual(result, "ann1")
        payload = fake_doc_ref.set.call_args.args[0]
        self.assertEqual(payload["title"], "標題")
        self.assertEqual(payload["content"], "說明")
        self.assertTrue(payload["active"])
        self.assertAlmostEqual(payload["expires_at"] - payload["created_at"], 7 * 86400, delta=2)

    def test_custom_days(self):
        fake_doc_ref = mock.Mock()
        fake_doc_ref.id = "ann1"
        fake_collection = mock.Mock()
        fake_collection.document.return_value = fake_doc_ref
        with mock.patch.object(repository, "announcements_ref", return_value=fake_collection):
            repository.create_announcement("標題", "", created_by="gary", days=1)
        payload = fake_doc_ref.set.call_args.args[0]
        self.assertAlmostEqual(payload["expires_at"] - payload["created_at"], 1 * 86400, delta=2)


class SetAnnouncementActiveTests(unittest.TestCase):
    def test_updates_active_flag(self):
        fake_collection, fake_doc_ref = _fake_collection(_fake_doc_snapshot(True))
        with mock.patch.object(repository, "announcements_ref", return_value=fake_collection):
            result = repository.set_announcement_active("a", False)
        self.assertTrue(result)
        fake_doc_ref.update.assert_called_once_with({"active": False})

    def test_returns_false_when_missing(self):
        fake_collection, fake_doc_ref = _fake_collection(_fake_doc_snapshot(False))
        with mock.patch.object(repository, "announcements_ref", return_value=fake_collection):
            result = repository.set_announcement_active("missing", True)
        self.assertFalse(result)
        fake_doc_ref.update.assert_not_called()


class DeleteAnnouncementTests(unittest.TestCase):
    def test_deletes_when_exists(self):
        fake_collection, fake_doc_ref = _fake_collection(_fake_doc_snapshot(True))
        with mock.patch.object(repository, "announcements_ref", return_value=fake_collection):
            result = repository.delete_announcement("a")
        self.assertTrue(result)
        fake_doc_ref.delete.assert_called_once()

    def test_returns_false_when_missing(self):
        fake_collection, fake_doc_ref = _fake_collection(_fake_doc_snapshot(False))
        with mock.patch.object(repository, "announcements_ref", return_value=fake_collection):
            result = repository.delete_announcement("missing")
        self.assertFalse(result)
        fake_doc_ref.delete.assert_not_called()


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


class HomeRouteAnnouncementTests(unittest.TestCase):
    def test_passes_active_announcements_with_display_date(self):
        now = time.time()
        announcements = [{"id": "a", "title": "標題", "content": "說明", "active": True, "created_at": now, "expires_at": now + 100}]
        with mock.patch.object(home_routes.repository, "list_open_incident_events", return_value=[]):
            with mock.patch.object(home_routes.repository, "list_active_announcements", return_value=announcements):
                with mock.patch.object(home_routes, "templates") as mock_templates:
                    home_routes.home(_FakeRequest(_staff_account()), redirect=None)
        context = mock_templates.TemplateResponse.call_args[0][2]
        self.assertEqual(context["announcements"][0]["title"], "標題")
        self.assertIn("created_at_display", context["announcements"][0])


class AnnouncementAdminRoutesTests(unittest.TestCase):
    def test_create_calls_repository(self):
        with mock.patch.object(home_routes.repository, "create_announcement") as mock_create:
            resp = home_routes.create_announcement_submit(
                _FakeRequest(_admin_account()), title="標題", content="說明", days=7, redirect=None
            )
        mock_create.assert_called_once_with("標題", "說明", created_by="alice", days=7)
        self.assertEqual(resp.status_code, 303)

    def test_blank_title_is_ignored(self):
        with mock.patch.object(home_routes.repository, "create_announcement") as mock_create:
            home_routes.create_announcement_submit(
                _FakeRequest(_admin_account()), title="   ", content="說明", days=7, redirect=None
            )
        mock_create.assert_not_called()

    def test_non_positive_days_falls_back_to_default(self):
        with mock.patch.object(home_routes.repository, "create_announcement") as mock_create:
            home_routes.create_announcement_submit(
                _FakeRequest(_admin_account()), title="標題", content="", days=0, redirect=None
            )
        mock_create.assert_called_once_with("標題", "", created_by="alice", days=home_routes.ANNOUNCEMENT_DEFAULT_DAYS)

    def test_toggle_active_calls_repository(self):
        with mock.patch.object(home_routes.repository, "set_announcement_active") as mock_set:
            resp = home_routes.toggle_announcement_active(
                "a", _FakeRequest(_admin_account()), active="0", redirect=None
            )
        mock_set.assert_called_once_with("a", False)
        self.assertEqual(resp.status_code, 303)

    def test_delete_calls_repository(self):
        with mock.patch.object(home_routes.repository, "delete_announcement", return_value=True) as mock_delete:
            resp = home_routes.delete_announcement_submit("a", _FakeRequest(_admin_account()), redirect=None)
        mock_delete.assert_called_once_with("a")
        self.assertEqual(resp.status_code, 303)


if __name__ == "__main__":
    unittest.main()
