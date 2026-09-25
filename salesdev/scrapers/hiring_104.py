"""104「產線徵才公司」（2026-09-25 新增）：找**工廠自己**在 104 刊登的產線職缺，
跟 `jobs_104.py` 正好相反——那支只留派遣公司刊的，這支把派遣公司排除掉。

背景：使用者原本手動在 104 搜「作業員」「技術員」、挑電子／製造業、員工 100 人
以上的公司，再請 Cowork 找公司信箱寄介紹信（見 HANDOFF.md「104 產線徵才公司」）。
這支把「挑公司」這一步自動化。

跟 `jobs_104.py` 一樣打 104 前端自己在用的搜尋 JSON API（不是官方公開 API），
欄位可能變動，所以一律寬鬆讀取。欄位意思參考開源專案 a7512cs/104-mcp-server
實測整理的說明（2026-09-25 查的）：
- 每筆職缺自己就帶 `employeeCount`（員工人數），**0 或沒有這個欄位＝公司沒公開**
  （約半數公司不提供），不是 0 人。第一版曾經另外打公司頁
  `/company/ajax/content/…` 取人數，正式環境 55 間全部取不到，已拿掉。
- `jobType=1` 是 104 的廣告位，會無視關鍵字硬塞在最前面，要排除。
- `order=16` 才是「最新更新在前」（15 是相關性）。
- 職缺名稱裡 104 用 `[[[關鍵字]]]` 標記命中的字，要清掉。
- 產業篩選參數 `indcat`：2026-09-25 第一次正式跑，排除派遣公司 0 筆（派遣公司
  的產業是人力仲介，被 indcat 擋在 104 那端）、產業名稱不符 9 筆，看起來有生效。
"""
import json
import logging
import re
from urllib.parse import quote

from salesdev.classify import is_own_company, match_dispatch_company
from salesdev.normalize import clean_text, job_id_from_url
from salesdev.scrapers.base import DeadlineReached, HttpClient
from salesdev.scrapers.jobs_104 import SEARCH_API, SEARCH_PAGE_URL, _extract_items_and_total_page, _normalize_link

logger = logging.getLogger(__name__)

SOURCE = "104"

# 使用者 2026-09-25 決定的條件（關鍵字、員工人數門檻可以在平台上改，見
# repository.get_hiring_settings()；地區＝全台，所以搜尋不帶地區參數）
DEFAULT_KEYWORDS = ["作業員", "技術員", "包裝員", "倉管", "品檢"]
DEFAULT_MIN_EMPLOYEES = 100
DEFAULT_MAX_PAGES = 5

# 104 產業大類代碼：電子資訊／軟體／半導體相關業、一般製造業（塑膠、金屬、機械
# 都在一般製造業底下）。多個代碼用逗號串起來，跟 104 網頁版網址的寫法一樣。
INDUSTRY_CODES = ["1001000000", "1002000000"]
INDUSTRY_LABEL = "電子資訊／半導體、一般製造業（含塑膠、金屬、機械等）"
# 保險：搜尋結果帶的產業名稱要包含這些字才留下（例如「半導體製造業」「塑膠製品
# 製造業」「機械設備製造修配業」「其他電子零組件相關業」）。結果沒帶產業名稱
# 就不擋，交給上面的 indcat 篩選。
INDUSTRY_DESC_KEYWORDS = (
    "製造", "電子", "半導體", "光電", "電腦", "通訊", "零組件", "修配",
    "機械", "金屬", "塑膠", "橡膠", "化學", "材料", "器材",
)
_INDUSTRY_FIELDS = ("coIndustryDesc", "coIndDesc", "industryDesc", "indcatDesc")
_AD_JOB_TYPE = 1
_HIGHLIGHT_RE = re.compile(r"\[\[\[|\]\]\]")


def _search_page(client: HttpClient, keyword: str, page: int) -> dict:
    params = {
        "ro": 0,
        "keyword": keyword,
        "indcat": ",".join(INDUSTRY_CODES),
        "order": 16,  # 最新更新在前，每週只要看最近更新的前幾頁（15 是相關性）
        "asc": 0,
        "page": page,
        "mode": "s",
        "jobsource": "2018indexpoc",
    }
    headers = {"Referer": f"{SEARCH_PAGE_URL}?keyword={quote(keyword)}"}
    return client.get(SEARCH_API, params=params, headers=headers).json()


def _industry_of(item: dict) -> str:
    for key in _INDUSTRY_FIELDS:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return clean_text(value)
    return ""


def is_target_industry(industry: str) -> bool:
    return not industry or any(k in industry for k in INDUSTRY_DESC_KEYWORDS)


def cust_id_from_url(company_url: str) -> str:
    """104 公司頁網址最後一段（例如 /company/1a2x6blud9 的 1a2x6blud9）。"""
    url = (company_url or "").strip().rstrip("/")
    if "/company/" not in url:
        return ""
    return url.rsplit("/", 1)[-1].split("?", 1)[0]


def parse_item(item: dict):
    """回傳 (職缺 dict, 略過原因)；要留下的略過原因是空字串。純函式。"""
    if item.get("jobType") == _AD_JOB_TYPE:
        return None, "ad"
    company_name = clean_text(item.get("custName", "") or "")
    if not company_name:
        return None, "no_company"
    if is_own_company(company_name) or match_dispatch_company(company_name):
        return None, "dispatch"
    industry = _industry_of(item)
    if not is_target_industry(industry):
        return None, "industry"

    link = item.get("link", {}) or {}
    company_url = _normalize_link(link.get("cust", ""))
    cust_id = cust_id_from_url(company_url) or str(item.get("custNo", "") or "")
    job_url = _normalize_link(link.get("job", ""))
    job_id = job_id_from_url(SOURCE, job_url) or str(item.get("jobNo", "") or "")
    if not cust_id or not job_id:
        return None, "no_id"
    return {
        "cust_id": cust_id,
        "company_name": company_name,
        "company_url": company_url,
        "industry": industry,
        "job_id": job_id,
        "job_title": clean_text(_HIGHLIGHT_RE.sub("", item.get("jobName", "") or "")),
        "job_url": job_url,
        "area": clean_text(item.get("jobAddrNoDesc", "") or ""),
        "appear_date": str(item.get("appearDate", "") or ""),
        "employee_count": parse_employee_count(item.get("employeeCount")),
    }, ""


def collect_hiring_jobs(client: HttpClient, keywords: list, max_pages: int = DEFAULT_MAX_PAGES):
    """搜尋每個關鍵字的前幾頁，回傳 (職缺 list, 統計)。時間到就停，保留已經
    抓到的，stats["deadline_hit"] 設成 True。"""
    jobs = {}
    stats = {
        "raw_items": 0, "ad_skipped": 0, "dispatch_skipped": 0, "industry_skipped": 0,
        "errors": [], "deadline_hit": False,
    }
    try:
        for keyword in keywords:
            for page in range(1, max_pages + 1):
                try:
                    payload = _search_page(client, keyword, page)
                except DeadlineReached:
                    raise
                except Exception as exc:
                    logger.warning("104 產線公司搜尋失敗 keyword=%s page=%s: %s", keyword, page, exc)
                    stats["errors"].append(f"搜尋「{keyword}」第 {page} 頁失敗：{exc}")
                    break
                items, total_page = _extract_items_and_total_page(payload)
                if not items:
                    if page == 1:
                        logger.warning(
                            "104 產線公司診斷：keyword=%s 沒有職缺資料，內容片段=%s",
                            keyword, json.dumps(payload, ensure_ascii=False)[:500],
                        )
                    break
                stats["raw_items"] += len(items)
                for item in items:
                    job, reason = parse_item(item)
                    if reason == "ad":
                        stats["ad_skipped"] += 1
                    elif reason == "dispatch":
                        stats["dispatch_skipped"] += 1
                    elif reason == "industry":
                        stats["industry_skipped"] += 1
                    if job and job["job_id"] not in jobs:
                        job["keyword"] = keyword
                        jobs[job["job_id"]] = job
                if page >= total_page:
                    break
    except DeadlineReached:
        logger.warning("104 產線公司搜尋時間到，先保留已抓到的 %d 筆", len(jobs))
        stats["deadline_hit"] = True
    return list(jobs.values()), stats


# ---------------------------------------------------------------------------
# 員工人數
# ---------------------------------------------------------------------------

def parse_employee_count(raw) -> int:
    """搜尋結果的 employeeCount：350、"350"、"1,200人" → 數字；0、空白、「暫不提供」
    → None（公司沒公開）。純函式。"""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return int(raw) if raw > 0 else None
    digits = re.search(r"\d[\d,]*", str(raw or ""))
    if not digits:
        return None
    value = int(digits.group(0).replace(",", ""))
    return value if value > 0 else None
