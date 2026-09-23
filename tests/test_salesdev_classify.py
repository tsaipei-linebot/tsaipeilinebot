import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401

from salesdev import classify


class DispatchCompanyTests(unittest.TestCase):
    def test_dispatch_company_matches(self):
        self.assertEqual(classify.match_dispatch_company("悅盛人力資源有限公司"), "人力資源")

    def test_hr_department_is_excluded(self):
        self.assertIsNone(classify.match_dispatch_company("某某科技公司人力資源部"))

    def test_own_company_is_never_a_lead(self):
        self.assertIsNone(classify.match_dispatch_company("材霈人力資源有限公司"))

    def test_regular_company_is_not_dispatch(self):
        self.assertIsNone(classify.match_dispatch_company("台灣積體電路製造股份有限公司"))


class InternalJobTests(unittest.TestCase):
    """派遣公司徵自己內部員工的職缺（實際在試算表看到的標題）。"""

    def test_real_internal_titles(self):
        for title in (
            "人力仲介行政人員（具經驗）-台北地區",
            "傑報人力資源服務集團內部職缺 - 人資招募顧問【桃園區】",
            "「外勞仲介」業務助理",
            "【長宏人力集團】誠徵 文件管理師 -台中",
            "人才顧問 Talent Consultant｜儲備培訓職",
        ):
            self.assertTrue(classify.internal_job_reason(title), title)

    def test_real_dispatched_jobs_are_not_internal(self):
        for title in (
            "★9/7(一)來報到★【平鎮/楊梅 派駐momo貼標理貨員】",
            "日領5200🔥領到錢的時候做夢都會笑🔥",
            "【中部IC封測大廠】招募管理師_台中中科",
        ):
            self.assertEqual(classify.internal_job_reason(title), "", title)


if __name__ == "__main__":
    unittest.main()
