import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401

import dispatch_sites


class DispatchSitesTests(unittest.TestCase):
    """所別設定清單（2026-09-22 新增）：純資料查詢，不碰 Firestore。"""

    def test_list_sites_includes_taoyuan_and_kaohsiung(self):
        codes = [s["code"] for s in dispatch_sites.list_sites()]
        self.assertIn("taoyuan", codes)
        self.assertIn("kaohsiung", codes)

    def test_get_site_returns_config(self):
        site = dispatch_sites.get_site("taoyuan")
        self.assertEqual(site["name"], "桃園所")
        self.assertEqual(site["department"], "桃園所")

    def test_get_site_returns_none_for_unknown_code(self):
        self.assertIsNone(dispatch_sites.get_site("not-a-real-site"))

    def test_get_site_handles_blank_code(self):
        self.assertIsNone(dispatch_sites.get_site(""))
        self.assertIsNone(dispatch_sites.get_site(None))

    def test_site_exists(self):
        self.assertTrue(dispatch_sites.site_exists("kaohsiung"))
        self.assertFalse(dispatch_sites.site_exists("not-a-real-site"))

    def test_site_for_department_reverse_lookup(self):
        site = dispatch_sites.site_for_department("高雄所")
        self.assertEqual(site["code"], "kaohsiung")

    def test_site_for_department_returns_none_when_no_match(self):
        self.assertIsNone(dispatch_sites.site_for_department("新北所"))

    def test_each_site_has_distinct_line_env_var_names(self):
        """每個所的 LINE 環境變數名稱不能重複，不然會互相覆蓋讀到同一組
        憑證——這是設定清單本身的完整性檢查，不是功能邏輯。"""
        token_envs = [s["line_token_env"] for s in dispatch_sites.list_sites()]
        secret_envs = [s["line_secret_env"] for s in dispatch_sites.list_sites()]
        self.assertEqual(len(token_envs), len(set(token_envs)))
        self.assertEqual(len(secret_envs), len(set(secret_envs)))


if __name__ == "__main__":
    unittest.main()
