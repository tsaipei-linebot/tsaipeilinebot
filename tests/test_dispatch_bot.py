import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

import dispatch_bot as bot
from services import dispatch_service as service

SITE = "taoyuan"


class ParseCommandTests(unittest.TestCase):
    """parse_command() 是純函式，不碰 Firestore、不牽涉所別——跟
    hr/incident_report.py 的 parse_incident_report() 同一種拆法，方便
    單元測試。"""

    def test_bind_command(self):
        result = bot.parse_command("綁定+王小明+0912345678")
        self.assertEqual(result, {"type": bot.CMD_BIND, "name": "王小明", "phone": "0912345678"})

    def test_bind_command_full_width_plus(self):
        result = bot.parse_command("綁定＋王小明＋0912345678")
        self.assertEqual(result["type"], bot.CMD_BIND)

    def test_bind_missing_phone_is_invalid(self):
        result = bot.parse_command("綁定+王小明")
        self.assertEqual(result, {"type": bot.CMD_BIND_INVALID})

    def test_bind_space_separated_is_invalid(self):
        result = bot.parse_command("綁定 王小明 0912345678")
        self.assertEqual(result, {"type": bot.CMD_BIND_INVALID})

    def test_list_postings_keywords(self):
        for text in ("需求列表", "需求", "查看需求", "查詢需求"):
            self.assertEqual(bot.parse_command(text)["type"], bot.CMD_LIST_POSTINGS)

    def test_register_command(self):
        result = bot.parse_command("報名 A1B2C3")
        self.assertEqual(result, {"type": bot.CMD_REGISTER, "short_code": "A1B2C3"})

    def test_my_registrations_keywords(self):
        for text in ("我的報名", "報名紀錄", "查詢報名", "查詢報名狀態"):
            self.assertEqual(bot.parse_command(text)["type"], bot.CMD_MY_REGISTRATIONS)

    def test_unknown_text_falls_back_to_help(self):
        self.assertEqual(bot.parse_command("哈囉"), {"type": bot.CMD_HELP})

    def test_blank_text_falls_back_to_help(self):
        self.assertEqual(bot.parse_command(""), {"type": bot.CMD_HELP})


class HandleMessageBindTests(unittest.TestCase):
    def test_bind_success(self):
        personnel = {"id": "p1", "name": "王小明", "active": True}
        with mock.patch.object(service, "find_personnel_by_name_and_phone", return_value=personnel):
            with mock.patch.object(service, "bind_line_user") as mock_bind:
                reply = bot.handle_message(SITE, "U1", "綁定+王小明+0912345678")
        mock_bind.assert_called_once_with(SITE, "U1", "p1", "王小明")
        self.assertIn("綁定成功", reply)

    def test_bind_no_match_found(self):
        with mock.patch.object(service, "find_personnel_by_name_and_phone", return_value=None):
            reply = bot.handle_message(SITE, "U1", "綁定+王小明+0912345678")
        self.assertIn("查無符合", reply)

    def test_bind_inactive_personnel_rejected(self):
        personnel = {"id": "p1", "name": "王小明", "active": False}
        with mock.patch.object(service, "find_personnel_by_name_and_phone", return_value=personnel):
            with mock.patch.object(service, "bind_line_user") as mock_bind:
                reply = bot.handle_message(SITE, "U1", "綁定+王小明+0912345678")
        mock_bind.assert_not_called()
        self.assertIn("停用", reply)

    def test_bind_invalid_format(self):
        reply = bot.handle_message(SITE, "U1", "綁定+王小明")
        self.assertEqual(reply, bot._BIND_INVALID_TEXT)


class HandleMessageRequiresBindingTests(unittest.TestCase):
    def test_unbound_user_prompted_to_bind(self):
        with mock.patch.object(service, "get_bound_personnel", return_value=None):
            reply = bot.handle_message(SITE, "U1", "需求列表")
        self.assertEqual(reply, bot._NOT_BOUND_TEXT)

    def test_unbound_user_unknown_text_prompted_to_bind_not_help(self):
        with mock.patch.object(service, "get_bound_personnel", return_value=None):
            reply = bot.handle_message(SITE, "U1", "哈囉")
        self.assertEqual(reply, bot._NOT_BOUND_TEXT)


class HandleMessageListPostingsTests(unittest.TestCase):
    def setUp(self):
        self.personnel = {"id": "p1", "name": "王小明"}
        self.patcher = mock.patch.object(service, "get_bound_personnel", return_value=self.personnel)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_no_open_postings(self):
        with mock.patch.object(service, "list_open_postings_for_personnel", return_value=[]):
            reply = bot.handle_message(SITE, "U1", "需求列表")
        self.assertIn("目前沒有符合", reply)

    def test_lists_postings_with_short_code(self):
        postings = [
            {
                "short_code": "ABC123",
                "location_name": "桃園火車站",
                "start_time": 0,
                "end_time": 0,
                "headcount": 2,
                "required_qualifications": ["restocking"],
            }
        ]
        with mock.patch.object(service, "list_open_postings_for_personnel", return_value=postings):
            reply = bot.handle_message(SITE, "U1", "需求列表")
        self.assertIn("ABC123", reply)
        self.assertIn("桃園火車站", reply)
        self.assertIn("理貨", reply)
        self.assertIn("報名 代碼", reply)


class HandleMessageRegisterTests(unittest.TestCase):
    def setUp(self):
        self.personnel = {"id": "p1", "name": "王小明"}
        self.patcher = mock.patch.object(service, "get_bound_personnel", return_value=self.personnel)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_unknown_short_code(self):
        with mock.patch.object(service, "find_posting_by_short_code", return_value=None):
            reply = bot.handle_message(SITE, "U1", "報名 XXXXXX")
        self.assertIn("找不到這個代碼", reply)

    def test_register_calls_service_with_matched_posting(self):
        posting = {"id": "post1", "short_code": "ABC123"}
        with mock.patch.object(service, "find_posting_by_short_code", return_value=posting):
            with mock.patch.object(
                service, "register_for_posting", return_value=(True, "已收到您的報名！")
            ) as mock_register:
                reply = bot.handle_message(SITE, "U1", "報名 abc123")
        mock_register.assert_called_once_with(SITE, "post1", "p1", "王小明", "U1")
        self.assertEqual(reply, "已收到您的報名！")


class HandleMessageMyRegistrationsTests(unittest.TestCase):
    def setUp(self):
        self.personnel = {"id": "p1", "name": "王小明"}
        self.patcher = mock.patch.object(service, "get_bound_personnel", return_value=self.personnel)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_no_registrations(self):
        with mock.patch.object(service, "list_registrations_by_personnel", return_value=[]):
            reply = bot.handle_message(SITE, "U1", "我的報名")
        self.assertIn("沒有任何報名紀錄", reply)

    def test_lists_registrations_with_status_label(self):
        registrations = [{"posting_id": "post1", "status": service.REGISTRATION_STATUS_APPROVED}]
        posting = {"location_name": "桃園火車站", "start_time": 0}
        with mock.patch.object(service, "list_registrations_by_personnel", return_value=registrations):
            with mock.patch.object(service, "get_posting", return_value=posting):
                reply = bot.handle_message(SITE, "U1", "我的報名")
        self.assertIn("桃園火車站", reply)
        self.assertIn("已核准", reply)


class HandleMessageHelpFallbackTests(unittest.TestCase):
    def test_bound_user_unknown_text_gets_help_with_site_name(self):
        with mock.patch.object(service, "get_bound_personnel", return_value={"id": "p1", "name": "王小明"}):
            reply = bot.handle_message(SITE, "U1", "哈囉")
        self.assertIn("桃園所派遣小幫手", reply)

    def test_help_text_uses_kaohsiung_name_for_that_site(self):
        with mock.patch.object(service, "get_bound_personnel", return_value={"id": "p1", "name": "李小華"}):
            reply = bot.handle_message("kaohsiung", "U2", "哈囉")
        self.assertIn("高雄所派遣小幫手", reply)


if __name__ == "__main__":
    unittest.main()
