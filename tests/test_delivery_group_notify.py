import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

from delivery import group_notify


class IsConfiguredTests(unittest.TestCase):
    def test_false_when_both_missing(self):
        with mock.patch.object(group_notify, "DELIVERY_NOTIFY_WEBHOOK_URL", ""):
            with mock.patch.object(group_notify, "DELIVERY_NOTIFY_WEBHOOK_SECRET", ""):
                self.assertFalse(group_notify.is_configured())

    def test_false_when_only_url_set(self):
        with mock.patch.object(group_notify, "DELIVERY_NOTIFY_WEBHOOK_URL", "https://example.com"):
            with mock.patch.object(group_notify, "DELIVERY_NOTIFY_WEBHOOK_SECRET", ""):
                self.assertFalse(group_notify.is_configured())

    def test_true_when_both_set(self):
        with mock.patch.object(group_notify, "DELIVERY_NOTIFY_WEBHOOK_URL", "https://example.com"):
            with mock.patch.object(group_notify, "DELIVERY_NOTIFY_WEBHOOK_SECRET", "s3cr3t"):
                self.assertTrue(group_notify.is_configured())


class NotifyGroupTests(unittest.TestCase):
    def test_returns_false_without_raising_when_not_configured(self):
        with mock.patch.object(group_notify, "DELIVERY_NOTIFY_WEBHOOK_URL", ""):
            with mock.patch.object(group_notify, "DELIVERY_NOTIFY_WEBHOOK_SECRET", ""):
                self.assertFalse(group_notify.notify_group("test"))

    def test_sends_get_request_with_type_secret_and_text(self):
        fake_response = mock.Mock(status_code=200)
        with mock.patch.object(group_notify, "DELIVERY_NOTIFY_WEBHOOK_URL", "https://example.com/exec"):
            with mock.patch.object(group_notify, "DELIVERY_NOTIFY_WEBHOOK_SECRET", "s3cr3t"):
                with mock.patch("requests.get", return_value=fake_response) as mock_get:
                    result = group_notify.notify_group("✅ 已登記領車")
        self.assertTrue(result)
        mock_get.assert_called_once_with(
            "https://example.com/exec",
            params={"type": "DELIVERY_NOTIFY", "secret": "s3cr3t", "text": "✅ 已登記領車"},
            timeout=group_notify._TIMEOUT_SECONDS,
        )

    def test_returns_false_on_non_200_response(self):
        fake_response = mock.Mock(status_code=500)
        with mock.patch.object(group_notify, "DELIVERY_NOTIFY_WEBHOOK_URL", "https://example.com/exec"):
            with mock.patch.object(group_notify, "DELIVERY_NOTIFY_WEBHOOK_SECRET", "s3cr3t"):
                with mock.patch("requests.get", return_value=fake_response):
                    self.assertFalse(group_notify.notify_group("test"))

    def test_returns_false_and_does_not_raise_on_network_error(self):
        with mock.patch.object(group_notify, "DELIVERY_NOTIFY_WEBHOOK_URL", "https://example.com/exec"):
            with mock.patch.object(group_notify, "DELIVERY_NOTIFY_WEBHOOK_SECRET", "s3cr3t"):
                with mock.patch("requests.get", side_effect=Exception("timeout")):
                    self.assertFalse(group_notify.notify_group("test"))

    def test_also_notify_incident_group_adds_query_param(self):
        """2026-09-16 新增：意外事件的網站通知要額外告訴 GAS 也推播到第二個
        （管理／督導）群組，不是直接傳群組 ID，只傳一個意圖旗標，實際的
        群組 ID 由 GAS 自己決定（見 group_notify.notify_group() 的說明）。"""
        fake_response = mock.Mock(status_code=200)
        with mock.patch.object(group_notify, "DELIVERY_NOTIFY_WEBHOOK_URL", "https://example.com/exec"):
            with mock.patch.object(group_notify, "DELIVERY_NOTIFY_WEBHOOK_SECRET", "s3cr3t"):
                with mock.patch("requests.get", return_value=fake_response) as mock_get:
                    group_notify.notify_group("✅ 已登記意外事件", also_notify_incident_group=True)
        mock_get.assert_called_once_with(
            "https://example.com/exec",
            params={
                "type": "DELIVERY_NOTIFY",
                "secret": "s3cr3t",
                "text": "✅ 已登記意外事件",
                "alsoNotify": "incident",
            },
            timeout=group_notify._TIMEOUT_SECONDS,
        )

    def test_default_does_not_add_also_notify_param(self):
        fake_response = mock.Mock(status_code=200)
        with mock.patch.object(group_notify, "DELIVERY_NOTIFY_WEBHOOK_URL", "https://example.com/exec"):
            with mock.patch.object(group_notify, "DELIVERY_NOTIFY_WEBHOOK_SECRET", "s3cr3t"):
                with mock.patch("requests.get", return_value=fake_response) as mock_get:
                    group_notify.notify_group("✅ 已登記領車")
        self.assertNotIn("alsoNotify", mock_get.call_args.kwargs["params"])


if __name__ == "__main__":
    unittest.main()
