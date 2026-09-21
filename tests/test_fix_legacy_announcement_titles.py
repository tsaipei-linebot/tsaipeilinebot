import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

from scripts.fix_legacy_announcement_titles import plan_fix


class PlanFixTests(unittest.TestCase):
    """一次性遷移腳本的規劃邏輯（不碰 Firestore 寫入）：只修正標題是
    「Merge pull request #NNN from ...」這種技術性合併說明的公告，把
    塞在內文裡的真正標題救回來。"""

    def test_merge_title_with_content_is_fixed(self):
        announcements = [
            {
                "id": "a1",
                "title": "Merge pull request #174 from tsaipei-linebot/claude/taoyuan-bind-plus-format",
                "content": "桃園所派遣 LINE 綁定指令格式改成「綁定+姓名+電話」",
            }
        ]
        plan = plan_fix(announcements)
        self.assertEqual(len(plan["to_fix"]), 1)
        self.assertEqual(plan["to_fix"][0]["id"], "a1")
        self.assertEqual(plan["to_fix"][0]["new_title"], "桃園所派遣 LINE 綁定指令格式改成「綁定+姓名+電話」")
        self.assertEqual(plan["to_fix"][0]["new_content"], "")
        self.assertEqual(plan["already_correct"], [])
        self.assertEqual(plan["no_content"], [])

    def test_normal_title_is_skipped(self):
        announcements = [{"id": "a1", "title": "系統更新：新增報班批次匯入功能", "content": "詳細說明"}]
        plan = plan_fix(announcements)
        self.assertEqual(plan["to_fix"], [])
        self.assertEqual([a["id"] for a in plan["already_correct"]], ["a1"])

    def test_merge_title_with_blank_content_needs_manual_review(self):
        announcements = [
            {"id": "a1", "title": "Merge pull request #100 from tsaipei-linebot/some-branch", "content": ""}
        ]
        plan = plan_fix(announcements)
        self.assertEqual(plan["to_fix"], [])
        self.assertEqual([a["id"] for a in plan["no_content"]], ["a1"])

    def test_content_with_extra_lines_keeps_remainder_as_new_content(self):
        announcements = [
            {
                "id": "a1",
                "title": "Merge pull request #50 from tsaipei-linebot/some-branch",
                "content": "真正的標題\n\n額外的說明段落",
            }
        ]
        plan = plan_fix(announcements)
        self.assertEqual(plan["to_fix"][0]["new_title"], "真正的標題")
        self.assertEqual(plan["to_fix"][0]["new_content"], "額外的說明段落")

    def test_mixed_batch_sorts_into_correct_buckets(self):
        announcements = [
            {
                "id": "a1",
                "title": "Merge pull request #1 from tsaipei-linebot/x",
                "content": "真正標題1",
            },
            {"id": "a2", "title": "正常標題", "content": "正常內文"},
            {"id": "a3", "title": "Merge pull request #2 from tsaipei-linebot/y", "content": ""},
        ]
        plan = plan_fix(announcements)
        self.assertEqual([a["id"] for a in plan["to_fix"]], ["a1"])
        self.assertEqual([a["id"] for a in plan["already_correct"]], ["a2"])
        self.assertEqual([a["id"] for a in plan["no_content"]], ["a3"])


if __name__ == "__main__":
    unittest.main()
