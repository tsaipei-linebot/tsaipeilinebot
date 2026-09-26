"""職缺 AI 文案搬到平台（2026-09-26，GAS 搬家階段 3 第 1 個 PR）。測試資料全部是假的。"""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

from fastapi.testclient import TestClient

import main
import platform_accounts
from services import ai_service
from services import job_copy

ADMIN = {"username": "boss", "name": "老闆", "department": "", "is_platform_admin": True, "modules": []}
STAFF = {"username": "wang", "name": "王小明", "department": "新北所", "is_platform_admin": False, "modules": []}

JOB = {"title": "倉儲理貨員", "city": "桃園市", "district": "蘆竹區", "salary": "時薪200元", "shift": "早班",
       "leave_type": "週休二日", "original_desc": "早上8點到下午5點，揀貨上架，搬運十五公斤，穿便服，無經驗可"}


def ai_reply(**overrides):
    data = {
        "external_title": "【🔥桃園蘆竹】倉儲理貨員【✨早班時薪200】",
        "external_desc": "🎯【工作內容】\n・揀貨上架，搬運約 15 公斤\n\n⏰【時間與休假】\n・早班 08:00-17:00，週休二日",
        "highlight": "桃園蘆竹倉儲理貨，早班 8 點上工、週休二日，無經驗可，歡迎您加入！",
        "formatted_detail": "📋【職缺名稱：倉儲理貨員】\n\n✨【應徵與配合條件】\n・無經驗可\n\n" + job_copy.LAW_NOTE,
    }
    data.update(overrides)
    return json.dumps(data, ensure_ascii=False)


class LocationTests(unittest.TestCase):
    def test_like_gas(self):
        self.assertEqual(job_copy.format_smart_location("桃園市", "蘆竹區,龜山區"), "桃園市（蘆竹區、龜山區）")
        self.assertEqual(job_copy.format_smart_location("新北市", "板橋區,中和區,永和區,新莊區,三重區"),
                         "新北市 各區門市據點（共 5 區，門市自選）")
        self.assertEqual(job_copy.format_smart_location("", ""), "依公司指定地點")


class ComplianceTests(unittest.TestCase):
    """違反就業服務法的字一定要刪（使用者 2026-09-26 要求），正常的時間、重量、天數不能被誤刪。"""

    f = staticmethod(job_copy.enforce_compliance_rules)

    def test_age_gender_military(self):
        text = self.f("限男 20-35歲 需役畢，年輕有活力，五官端正")
        for banned in ("限男", "35歲", "役畢", "年輕", "五官端正"):
            self.assertNotIn(banned, text)
        self.assertIn("充滿活力", text)

    def test_banned_wording_is_removed_completely(self):
        """每一條都要整句刪乾淨，不留「需」「性」「生」這類殘字。"""
        for banned in (
            # 年齡
            "限 18-35", "需18-35歲", "年齡 20-45", "限二十至三十五", "需 18-35歲之間", "需 18 歲以上",
            "須年滿18歲", "限 45 以下", "年齡 45 以下", "18歲以下", "限 7年級生", "年紀輕",
            # 性別
            "限男", "限女", "限男性", "限女生", "僅限女性", "女生佳", "男性優先錄取", "女士尤佳",
            # 兵役、容貌身家、身心
            "需役畢", "（限免役）", "五官端正", "容貌端莊", "身家清白", "無前科", "身心健全", "四肢健全",
            # 婚姻、籍貫／出生地、星座、血型
            "限未婚", "已婚者優先", "限台北人", "桃園人優先", "限本地人", "限處女座", "不收 B 型", "限O型者",
        ):
            self.assertEqual(self.f(banned), "", banned)

    def test_replacements(self):
        self.assertEqual(self.f("限男生搬重"), "需配合搬重貨物")
        self.assertEqual(self.f("限女性細心"), "需具備細心度")
        self.assertEqual(self.f("男女不拘"), "歡迎各界人才")
        self.assertEqual(self.f("適合女生"), "歡迎各界人才")
        self.assertEqual(self.f("年輕化團隊"), "充滿活力的團隊")
        self.assertEqual(self.f("女作業員"), "作業員")

    def test_banned_wording_inside_sentence(self):
        self.assertEqual(self.f("限18~40，歡迎您加入"), "歡迎您加入")
        self.assertEqual(self.f("倉儲理貨，男性優先，時薪200元"), "倉儲理貨、時薪200元")

    def test_normal_ranges_are_kept(self):
        """GAS 原本「需 8-17點」會被砍成「7點」、「須 10-20公斤」變「0公斤」（正規表示式往回退一位數字繞過單位
        判斷），2026-09-26 使用者同意修掉。"""
        for keep in ("需 8-17點 出勤", "須 10-20公斤", "需 20-30 分鐘", "需 2-3 年相關經驗", "需 1-2 個月培訓",
                     "需要 5-6 天", "適合 2-3 人", "需 8-17 出勤", "需 30-40K", "上班 8-17點", "搬運 10-20公斤",
                     "時薪 190-200 元", "需 3 個月以上", "需1年以上經驗", "限高中以上", "需 8.5-9 小時"):
            self.assertEqual(self.f(keep), keep)

    def test_not_over_filtered(self):
        for keep in ("福利制度健全", "限本人親自領取", "限本國籍"):
            self.assertEqual(self.f(keep), keep)

    def test_gas_quirk_block_titles_are_renamed(self):
        text = job_copy.enforce_compliance_rules("🎯【主要工作內容】\n・揀貨\n📍【工作地點與交通】\n・桃園")
        self.assertIn("🎯【工作內容】", text)
        self.assertIn("📍【地點資訊】", text)


class HallucinationCheckTests(unittest.TestCase):
    SRC = "倉儲理貨員\n桃園市（蘆竹區）\n時薪200元\n早班\n早上8點到下午5點，週休二日，搬運十五公斤，無經驗可"

    def test_rewording_is_not_hallucination(self):
        self.assertEqual(job_copy.find_unsupported("08:00-17:00，週休2日，搬運 15 公斤，免經驗", self.SRC), [])

    def test_made_up_numbers_and_benefits(self):
        issues = job_copy.find_unsupported("時薪 210 元，供餐，交通車", self.SRC)
        self.assertIn("原始資料沒有的數字：210", issues)
        self.assertIn("原始資料沒有提到「供餐」", issues)
        self.assertIn("原始資料沒有提到「交通車」", issues)


class GenerateTests(unittest.TestCase):
    def test_passes_first_time(self):
        with mock.patch.object(ai_service, "query_gemini_ai", return_value=ai_reply()) as ask:
            result = job_copy.generate(JOB)
        self.assertFalse(result["is_fallback"])
        self.assertEqual(result["attempts"], 1)
        prompt = ask.call_args.args[0]
        for rule in ("親切", "「您」", "✨【應徵與配合條件】", "整個不要出現", "知名半導體大廠", "休假方式：週休二日"):
            self.assertIn(rule, prompt)

    def test_retries_with_reasons_then_passes(self):
        replies = [ai_reply(highlight="時薪 250 元還有供餐，歡迎您！"), ai_reply()]
        with mock.patch.object(ai_service, "query_gemini_ai", side_effect=replies) as ask:
            result = job_copy.generate(JOB)
        self.assertEqual(result["attempts"], 2)
        self.assertIn("原始資料沒有的數字：250", ask.call_args_list[1].args[0])

    def test_falls_back_when_still_hallucinating(self):
        with mock.patch.object(ai_service, "query_gemini_ai", return_value=ai_reply(highlight="時薪 999 元！")):
            result = job_copy.generate(JOB)
        self.assertTrue(result["is_fallback"])
        self.assertIn("999", result["check_issues"][0])
        self.assertIn("時薪200元", result["formatted_detail"])
        self.assertNotIn("✨【應徵與配合條件】", result["formatted_detail"])  # 保底文案不湊條件

    def test_ai_down(self):
        with mock.patch.object(ai_service, "query_gemini_ai", return_value=""):
            result = job_copy.generate(JOB)
        self.assertTrue(result["is_fallback"])
        self.assertEqual(result["check_issues"], ["AI 沒有回應"])

    def test_compliance_applied_to_ai_output(self):
        with mock.patch.object(ai_service, "query_gemini_ai", return_value=ai_reply(external_desc="🎯【工作內容】\n・揀貨，限女")):
            result = job_copy.generate(JOB)
        self.assertNotIn("限女", result["external_desc"])


class NotionReadTests(unittest.TestCase):
    def test_page_to_job_and_skip_stopped(self):
        def prop_text(text):
            return {"type": "rich_text", "rich_text": [{"plain_text": text}]}
        pages = [
            {"id": "p1", "properties": {"職缺名稱": {"type": "title", "title": [{"plain_text": "理貨員"}]},
                                        "縣市": {"type": "multi_select", "multi_select": [{"name": "桃園市"}]},
                                        "工作內容(對外)": prop_text("揀貨"), "狀態": {"type": "status", "status": {"name": "招募中"}}}},
            {"id": "p2", "properties": {"職缺名稱": {"type": "title", "title": [{"plain_text": "停掉的"}]},
                                        "狀態": {"type": "status", "status": {"name": "停招"}}}},
        ]
        with mock.patch("services.notion_service.query_notion_database_direct", return_value=pages):
            jobs = job_copy.list_jobs()
        self.assertEqual([(j["id"], j["city"], j["external_desc"]) for j in jobs], [("p1", "桃園市", "揀貨")])


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.account = ADMIN
        patcher = mock.patch.object(platform_accounts, "current_account", side_effect=lambda request: self.account)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(main.app)

    def test_admin_only(self):
        self.account = STAFF
        self.assertEqual(self.client.get("/job-listings/migration", follow_redirects=False).headers["location"], "/portal")

    def test_preview_side_by_side(self):
        job = {**JOB, "id": "p1", "external_title": "", "external_desc": JOB["original_desc"], "highlight": "GAS 的亮點",
               "formatted_detail": "GAS 的排版", "status": "招募中", "review_status": "已核准", "publisher": "王小明"}
        job.pop("original_desc")
        with mock.patch.object(job_copy, "get_job", return_value=job), \
                mock.patch.object(ai_service, "query_gemini_ai", return_value=ai_reply()):
            html = self.client.get("/job-listings/migration/preview/p1").text
        self.assertIn("GAS 的亮點", html)
        self.assertIn("歡迎您加入", html)
        self.assertIn("只是預覽，不會改 Notion", html)

    def test_try_form(self):
        with mock.patch.object(ai_service, "query_gemini_ai", return_value=ai_reply()):
            resp = self.client.post("/job-listings/migration/try", data={k: v for k, v in JOB.items()})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("通過防腦補檢查", resp.text)


if __name__ == "__main__":
    unittest.main()
