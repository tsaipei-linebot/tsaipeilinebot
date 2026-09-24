"""1111 人力銀行：打 1111 前端自己在用的搜尋 JSON API。從 GitHub Actions
的主機打過去常常被 403 擋掉（搬進平台後從 Google Cloud 打過去會不會一樣
要實際跑過才知道），被擋就回傳空的，不影響另外兩個來源。"""
import json
import logging
from urllib.parse import quote

from salesdev.classify import match_dispatch_company
from salesdev.scrapers.base import MAX_PAGES_PER_SITE, SEARCH_KEYWORDS, DeadlineReached, HttpClient, JobLead
from salesdev.scrapers.contact_extract import extract_contacts

logger = logging.getLogger(__name__)

SOURCE = "1111"
SEARCH_API = "https://www.1111.com.tw/api/v1/search/jobs"
JOB_URL_TEMPLATE = "https://www.1111.com.tw/job/{job_id}"


def _search_page(client: HttpClient, keyword: str, page: int) -> dict:
    headers = {
        "Referer": f"https://www.1111.com.tw/search/job?ks={quote(keyword)}",
        "Accept": "application/json",
    }
    return client.get(SEARCH_API, params={"ks": keyword, "page": page}, headers=headers).json()


def parse_item(item: dict):
    company_name = item.get("companyName", "") or ""
    matched = match_dispatch_company(company_name)
    if not matched:
        return None
    job_id = str(item.get("jobId", "") or "")
    if not job_id:
        return None
    contacts = extract_contacts(item.get("description", "") or "")
    return JobLead(
        source=SOURCE,
        job_id=job_id,
        job_title=item.get("title", "") or "",
        company_name=company_name,
        job_url=JOB_URL_TEMPLATE.format(job_id=job_id),
        area=item.get("area", "") or item.get("jobArea", "") or "",
        update_date=str(item.get("updateAt", "") or ""),
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
                    logger.warning("1111 search failed keyword=%s page=%s: %s", keyword, page, exc)
                    break

                result = payload.get("result", {}) if isinstance(payload, dict) else {}
                hits = result.get("hits", []) or []
                if not hits:
                    logger.warning(
                        "1111 診斷：keyword=%s page=%s 沒有職缺資料，內容片段=%s",
                        keyword, page, json.dumps(payload, ensure_ascii=False)[:500],
                    )
                    break
                for item in hits:
                    lead = parse_item(item)
                    if lead and lead.job_id not in leads:
                        leads[lead.job_id] = lead
                if page >= (result.get("pagination", {}) or {}).get("totalPage", 1):
                    break
    except DeadlineReached:
        logger.warning("1111 抓取時間到，先保留已抓到的 %d 筆", len(leads))
    return list(leads.values())
