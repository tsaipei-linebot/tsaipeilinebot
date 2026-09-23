"""判斷「刊登者是不是派遣公司」與「是不是派遣公司在徵自己的內部員工」
（2026-09-24 從 recruitment-leads-scraper 整併進來，後者是新增的）。

兩份關鍵字清單都放在 `salesdev/keywords.json`，要增修只改那個檔案，不用
改程式碼（改完要重新部署才會生效）。

**內部員工職缺為什麼要排除**：派遣公司自己要請行政、業務、招募顧問，
例如「人力仲介行政人員」「內部職缺－人資招募顧問」，這種職缺背後沒有
要派公司，不是客戶線索。**只做標記、不刪除**：畫面上預設不顯示，但資料
還在，萬一誤判可以在「非客戶線索」那頁按一下改回來。
"""
import json
from pathlib import Path

from salesdev.normalize import clean_text

_KEYWORDS_PATH = Path(__file__).resolve().parent / "keywords.json"


def _load_keywords() -> dict:
    with open(_KEYWORDS_PATH, encoding="utf-8") as f:
        return json.load(f)


_KEYWORDS = _load_keywords()
DISPATCH_INCLUDE = _KEYWORDS["dispatch_company_include"]
DISPATCH_EXCLUDE = _KEYWORDS["dispatch_company_exclude"]
INTERNAL_JOB_TITLE_KEYWORDS = _KEYWORDS["internal_job_title_keywords"]
OWN_COMPANY_NAMES = _KEYWORDS["own_company_names"]


def match_dispatch_company(company_name: str):
    """回傳比對到的關鍵字；公司名稱看起來不是派遣/人力仲介公司時回傳 None。
    自己公司（材霈）刊的職缺也回傳 None——那不是客戶線索。"""
    name = clean_text(company_name)
    if not name:
        return None
    if is_own_company(name):
        return None
    for keyword in DISPATCH_EXCLUDE:
        if keyword in name:
            return None
    lowered = name.lower()
    for keyword in DISPATCH_INCLUDE:
        if keyword.lower() in lowered:
            return keyword
    return None


def is_own_company(name: str) -> bool:
    name = clean_text(name)
    return any(own in name for own in OWN_COMPANY_NAMES)


def internal_job_reason(job_title: str) -> str:
    """是派遣公司內部職缺的話，回傳比對到的關鍵字（顯示在畫面上，讓人知道
    為什麼被歸到「非客戶線索」）；不是就回傳空字串。"""
    title = clean_text(job_title).lower()
    for keyword in INTERNAL_JOB_TITLE_KEYWORDS:
        if keyword.lower() in title:
            return keyword
    return ""
