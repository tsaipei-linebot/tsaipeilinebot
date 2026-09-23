"""小雞上工（chickpt）：用 /?keyword= 搜尋頁找出職缺連結（job-<slug>），
再到每筆職缺詳情頁讀 schema.org JobPosting 結構化資料（JSON-LD）。"""
import json
import logging
import re

from salesdev.classify import match_dispatch_company
from salesdev.normalize import COUNTIES
from salesdev.scrapers.base import SEARCH_KEYWORDS, DeadlineReached, HttpClient, JobLead
from salesdev.scrapers.contact_extract import extract_contacts

logger = logging.getLogger(__name__)

SOURCE = "chickpt"
SEARCH_URL = "https://www.chickpt.com.tw/?keyword={keyword}"
_DETAIL_LINK_RE = re.compile(r'href="(https://www\.chickpt\.com\.tw/job-[A-Za-z0-9]+)"')
_JSONLD_RE = re.compile(
    r"<script[^>]*type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>", re.IGNORECASE | re.DOTALL
)


def _utf8_text(resp) -> str:
    """小雞上工的頁面沒有在 HTTP 標頭宣告 charset，requests 會照規格用
    latin-1 解碼，標題裡的表情符號就變成「ð¥」這種亂碼（中文因為是
    \\uXXXX 跳脫寫法所以剛好沒事）。直接指定 UTF-8 從源頭修好。"""
    resp.encoding = "utf-8"
    return resp.text


def _walk_jsonld(node):
    if isinstance(node, list):
        for item in node:
            yield from _walk_jsonld(item)
    elif isinstance(node, dict):
        node_type = node.get("@type")
        if node_type == "JobPosting" or (isinstance(node_type, list) and "JobPosting" in node_type):
            yield node
        if "@graph" in node:
            yield from _walk_jsonld(node["@graph"])


def extract_job_posting(html: str):
    for raw in _JSONLD_RE.findall(html or ""):
        raw = raw.strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for posting in _walk_jsonld(data):
            return posting
    return None


def posting_company_name(posting: dict) -> str:
    org = posting.get("hiringOrganization")
    if isinstance(org, dict):
        return org.get("name", "") or ""
    return org if isinstance(org, str) else ""


def posting_description_text(posting: dict) -> str:
    text = re.sub(r"<[^>]+>", " ", posting.get("description", "") or "")
    return re.sub(r"\s+", " ", text).strip()


def _posting_address_dict(posting: dict) -> dict:
    loc = posting.get("jobLocation")
    if isinstance(loc, list):
        loc = loc[0] if loc else {}
    if not isinstance(loc, dict):
        return {}
    addr = loc.get("address", {})
    return addr if isinstance(addr, dict) else {}


def posting_area(posting: dict) -> str:
    addr = _posting_address_dict(posting)
    return addr.get("addressLocality", "") or addr.get("addressRegion", "") or ""


def posting_full_address(posting: dict) -> str:
    """縣市＋行政區＋街道。

    街道欄位本身已經含縣市時，直接用街道欄位，不再把縣市/行政區疊上去。
    原本只檢查「街道是不是以縣市/行政區開頭」，結果像
    addressLocality=彰化市、streetAddress=新北市三重區自強路5段110號 這種
    （刊登者把公司地址填進街道欄），就被接成
    「彰化縣彰化市新北市三重區自強路5段110號」——現在只要街道欄裡出現任何
    一個縣市名稱就單獨使用街道欄。"""
    addr = _posting_address_dict(posting)
    if not addr:
        return ""
    region = addr.get("addressRegion", "") or ""
    locality = addr.get("addressLocality", "") or ""
    street = (addr.get("streetAddress", "") or "").strip()
    normalized_street = street.replace("臺", "台")
    if street and (
        any(county in normalized_street for county in COUNTIES)
        or (region and street.startswith(region))
        or (locality and street.startswith(locality))
    ):
        return street
    return "".join(part for part in (region, locality, street) if part)


def parse_posting(job_url: str, posting: dict):
    company_name = posting_company_name(posting)
    matched = match_dispatch_company(company_name)
    if not matched:
        return None
    contacts = extract_contacts(posting_description_text(posting))
    return JobLead(
        source=SOURCE,
        job_id=job_url,
        job_title=posting.get("title", "") or "",
        company_name=company_name,
        job_url=job_url,
        area=posting_area(posting),
        work_address=posting_full_address(posting),
        update_date=str(posting.get("datePosted", "") or ""),
        phone=contacts["phone"],
        phone_ext=contacts["phone_ext"],
        email=contacts["email"],
        matched_keyword=matched,
    )


def collect_leads(deadline: float = None, keywords: list = None) -> list:
    client = HttpClient(deadline=deadline)
    leads = {}
    seen_urls = set()
    try:
        for keyword in keywords or SEARCH_KEYWORDS:
            try:
                resp = client.get(SEARCH_URL.format(keyword=keyword))
            except DeadlineReached:
                raise
            except Exception as exc:
                logger.warning("chickpt search failed keyword=%s: %s", keyword, exc)
                continue
            links = sorted(set(_DETAIL_LINK_RE.findall(_utf8_text(resp))))
            if not links:
                logger.warning("chickpt 診斷：keyword=%s 搜尋頁沒有職缺連結", keyword)
                continue
            for job_url in links:
                if job_url in seen_urls:
                    continue
                seen_urls.add(job_url)
                try:
                    detail = client.get(job_url)
                except DeadlineReached:
                    raise
                except Exception as exc:
                    logger.warning("chickpt detail fetch failed %s: %s", job_url, exc)
                    continue
                posting = extract_job_posting(_utf8_text(detail))
                if not posting:
                    logger.warning("chickpt 診斷：%s 抓不到 JobPosting 結構化資料", job_url)
                    continue
                lead = parse_posting(job_url, posting)
                if lead:
                    leads[job_url] = lead
    except DeadlineReached:
        logger.warning("chickpt 抓取時間到，先保留已抓到的 %d 筆", len(leads))
    return list(leads.values())
