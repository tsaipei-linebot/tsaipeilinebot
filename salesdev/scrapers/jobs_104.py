"""104 人力銀行：直接打 104 前端自己在用的搜尋 JSON API（不是官方公開
API，欄位可能變動，所以一律 .get + 預設值寬鬆處理）。"""
import json
import logging
from urllib.parse import quote

from salesdev.classify import match_dispatch_company
from salesdev.normalize import job_id_from_url
from salesdev.scrapers.base import MAX_PAGES_PER_SITE, SEARCH_KEYWORDS, DeadlineReached, HttpClient, JobLead
from salesdev.scrapers.contact_extract import extract_contacts

logger = logging.getLogger(__name__)

SOURCE = "104"
SEARCH_API = "https://www.104.com.tw/jobs/search/api/jobs"
SEARCH_PAGE_URL = "https://www.104.com.tw/jobs/search/"


def _search_page(client: HttpClient, keyword: str, page: int) -> dict:
    params = {
        "ro": 0,
        "keyword": keyword,
        "order": 15,  # 依更新日期排序
        "asc": 0,
        "page": page,
        "mode": "s",
        "jobsource": "2018indexpoc",
    }
    headers = {"Referer": f"{SEARCH_PAGE_URL}?keyword={quote(keyword)}"}
    return client.get(SEARCH_API, params=params, headers=headers).json()


def _extract_items_and_total_page(payload) -> tuple:
    """data 欄位實際看過兩種形狀：直接是陣列，或 {"list": [...], "totalPage": N}。

    2026-09-25 修正：data 是陣列時，總頁數放在 `metadata.pagination`（`lastPage`，
    或只有符合總筆數 `total`），原本一律當成只有 1 頁，每個關鍵字都只抓了第 1 頁
    （104 產線徵才公司第一次正式跑，5 個關鍵字剛好 160 筆＝每頁 32 筆 × 5 才發現）。"""
    data_field = payload.get("data", []) if isinstance(payload, dict) else []
    if isinstance(data_field, list):
        return data_field, _total_page_from_metadata(payload.get("metadata"), len(data_field))
    if isinstance(data_field, dict):
        return data_field.get("list", []), data_field.get("totalPage", 1)
    return [], 1


def _total_page_from_metadata(metadata, page_size: int) -> int:
    pagination = metadata.get("pagination") if isinstance(metadata, dict) else None
    if not isinstance(pagination, dict):
        return 1
    last_page = pagination.get("lastPage")
    if isinstance(last_page, int) and last_page > 0:
        return last_page
    total = pagination.get("total")
    if isinstance(total, int) and total > 0 and page_size > 0:
        return -(-total // page_size)  # 無條件進位
    return 1


def _normalize_link(link: str) -> str:
    if not link:
        return ""
    if link.startswith("//"):
        link = f"https:{link}"
    elif not link.startswith("http"):
        link = f"https://www.104.com.tw{link}"
    # 拿掉 ?jobsource=… 這種追蹤參數，網址才會跟舊試算表裡存的一樣
    return link.split("?", 1)[0]


def parse_item(item: dict):
    """把搜尋結果的一筆轉成 JobLead；不是派遣公司刊的回傳 None。"""
    company_name = item.get("custName", "") or ""
    matched = match_dispatch_company(company_name)
    if not matched:
        return None

    link = item.get("link", {}) or {}
    job_url = _normalize_link(link.get("job", ""))
    # 職缺編號從網址取（例如 /job/7kcx5 的 7kcx5），不用 jobNo 這個純數字
    # 欄位：舊試算表只存了網址，兩邊要是同一個編號，匯入的舊資料跟新抓的
    # 才會被認成同一筆。
    job_id = job_id_from_url(SOURCE, job_url) or str(item.get("jobNo", "") or "")
    if not job_id:
        return None

    area = item.get("jobAddrNoDesc", "") or ""
    street = item.get("jobAddress", "") or ""
    contacts = extract_contacts(item.get("descSnippet", "") or item.get("description", "") or "")
    return JobLead(
        source=SOURCE,
        job_id=job_id,
        job_title=item.get("jobName", "") or "",
        company_name=company_name,
        job_url=job_url,
        company_url=_normalize_link(link.get("cust", "")),
        area=area,
        work_address=f"{area}{street}" if street else "",
        update_date=str(item.get("appearDate", "") or ""),
        phone=contacts["phone"],
        phone_ext=contacts["phone_ext"],
        email=contacts["email"],
        matched_keyword=matched,
    )


def collect_leads(deadline: float = None, keywords: list = None, max_pages: int = MAX_PAGES_PER_SITE) -> list:
    client = HttpClient(deadline=deadline)
    leads = {}
    try:
        for keyword in keywords or SEARCH_KEYWORDS:
            for page in range(1, max_pages + 1):
                try:
                    payload = _search_page(client, keyword, page)
                except DeadlineReached:
                    raise
                except Exception as exc:
                    logger.warning("104 search failed keyword=%s page=%s: %s", keyword, page, exc)
                    break

                items, total_page = _extract_items_and_total_page(payload)
                if not items:
                    logger.warning(
                        "104 診斷：keyword=%s page=%s 沒有職缺資料，內容片段=%s",
                        keyword, page, json.dumps(payload, ensure_ascii=False)[:500],
                    )
                    break
                for item in items:
                    lead = parse_item(item)
                    if lead and lead.job_id not in leads:
                        leads[lead.job_id] = lead
                if page >= total_page:
                    break
    except DeadlineReached:
        logger.warning("104 抓取時間到，先保留已抓到的 %d 筆", len(leads))
    return list(leads.values())
