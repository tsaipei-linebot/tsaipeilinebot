import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401  (匯入即副作用：見 _env.py 說明)

from services import flex_service as f


def _detail_texts(bubble):
    detail_box = bubble.body.contents[-1]
    return [c.text for c in detail_box.contents]


class CreateJobFlexCardPayMethodTests(unittest.TestCase):
    def test_shows_pay_method_line_when_present(self):
        job = {
            "職缺名稱(對外)": "測試職缺A", "職缺名稱": "測試職缺A",
            "薪資": "時薪200", "領薪方式": "日領", "班別": "早班",
            "縣市": "新北市", "行政區": "新莊區",
        }
        card = f.create_job_flex_card([job], "user1", "新莊")
        texts = _detail_texts(card.contents.contents[0])
        self.assertTrue(any("領薪方式：日領" in t for t in texts))

    def test_omits_pay_method_line_when_missing(self):
        job = {
            "職缺名稱(對外)": "測試職缺B", "職缺名稱": "測試職缺B",
            "薪資": "月薪32000", "班別": "早班",
            "縣市": "新北市", "行政區": "新莊區",
        }
        card = f.create_job_flex_card([job], "user1", "新莊")
        texts = _detail_texts(card.contents.contents[0])
        self.assertFalse(any("領薪方式" in t for t in texts))


if __name__ == "__main__":
    unittest.main()
