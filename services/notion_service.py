import os
import requests
import time
import json
import re
import urllib.request
import urllib.parse
from config import (
    NOTION_API_KEY, NOTION_JOBS_DB_ID, NOTION_FAQ_DB_ID, 
    ALLOWED_PROPERTIES, CACHE_TTL
)

_cached_jobs, _last_jobs_fetch = None, 0
_cached_faqs, _last_faqs_fetch = None, 0

def clean_text_for_search(text: str) -> str:
    """清理文字以便進行精準搜尋與特徵比對"""
    t = str(text or "").lower().replace("台", "臺")
    return re.sub(r'[\(\)（）\/\s\-_,，、\?!？！。🛵☀️🌙📦🏭🏬🍽️🔄]+', '', t)

def sanitize_uri(url: str) -> str:
    """確保 URI 格式安全有效"""
    default_fallback = "https://tsaipei.netlify.app/#jobs"
    if not url or not isinstance(url, str):
        return default_fallback
    url = url.strip().replace("\r", "").replace("\n", "").replace(" ", "")
    if not (url.startswith("http://") or url.startswith("https://") or url.startswith("line://")):
        return default_fallback
    return url

def parse_notion_property(prop: dict) -> str:
    """解析 Notion 各種屬性型態為純文字"""
    if not isinstance(prop, dict):
        return str(prop or "").strip()
    
    p_type = prop.get("type", "")
    
    if p_type == "title":
        return "".join([t.get("plain_text", "") for t in prop.get("title", [])]).strip()
    elif p_type == "multi_select":
        return ",".join([opt.get("name", "").strip() for opt in prop.get("multi_select", []) if opt.get("name")])
    elif p_type == "rich_text":
        return "".join([t.get("plain_text", "") for t in prop.get("rich_text", [])]).strip()
    elif p_type == "select":
        return prop.get("select", {}).get("name", "").strip() if prop.get("select") else ""
    elif p_type == "status":
        return prop.get("status", {}).get("name", "").strip() if prop.get("status") else ""
    elif p_type == "url":
        return prop.get("url", "") or ""
    elif p_type == "number":
        return str(prop.get("number", "")) if prop.get("number") is not None else ""
    elif p_type == "checkbox":
        return "true" if prop.get("checkbox") else "false"
    elif p_type == "rollup":
        r_data = prop.get("rollup", {})
        r_type = r_data.get("type", "")
        if r_type == "array":
            extracted = [parse_notion_property(item) for item in r_data.get("array", []) if parse_notion_property(item)]
            return ",".join(extracted)
        elif r_type in ["string", "text"]:
            return str(r_data.get("string", "") or "").strip()
    elif p_type == "formula":
        f_data = prop.get("formula", {})
        f_type = f_data.get("type", "")
        if f_type in ["string", "text"]:
            return str(f_data.get("string", "") or "").strip()
            
    return ""

def query_notion_database_direct(database_id: str) -> list:
    """以原生 HTTP POST 請求 Notion 資料庫，支援分頁讀取"""
    if not NOTION_API_KEY or not database_id:
        return []

    clean_db_id = database_id.replace("-", "").strip()
    url = f"https://api.notion.com/v1/databases/{clean_db_id}/query"
    headers = {
        "Authorization": f"Bearer {NOTION_API_KEY.strip()}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json"
    }

    all_results = []
    has_more = True
    start_cursor = None

    while has_more:
        body_data = {"page_size": 100}
        if start_cursor:
            body_data["start_cursor"] = start_cursor

        req = urllib.request.Request(url, data=json.dumps(body_data).encode("utf-8"), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as res:
                res_json = json.loads(res.read().decode("utf-8"))
                all_results.extend(res_json.get("results", []))
                has_more = res_json.get("has_more", False)
                start_cursor = res_json.get("next_cursor", None)
        except Exception as e:
            print(f"[Notion Direct Query 異常]: {e}")
            break

    return all_results

def fetch_jobs_data() -> list:
    """取得招募中職缺資料（完整納入休假方式與廠商名稱）"""
    global _cached_jobs, _last_jobs_fetch
    now = time.time()
    if _cached_jobs is not None and (now - _last_jobs_fetch < CACHE_TTL):
        return _cached_jobs

    active_jobs = []
    try:
        results = query_notion_database_direct(NOTION_JOBS_DB_ID)

        for page in results:
            props = page.get("properties", {})
            page_id = page.get("id", "")
            job_dict = {"_page_id": page_id}
            raw_text_parts = []

            # 1. 職缺名稱 (Title)
            title_val = ""
            for p_name, p_val in props.items():
                if isinstance(p_val, dict) and p_val.get("type") == "title":
                    title_val = parse_notion_property(p_val)
                    break
            job_dict["職缺名稱"] = title_val

            # 2. 職務類別 (Multi-Select)
            category_val = ""
            for p_name, p_val in props.items():
                if "類別" in p_name or "職務" in p_name:
                    category_val = parse_notion_property(p_val)
                    break
            job_dict["職務類別"] = category_val

            # 3. 讀取其餘白名單屬性 (含 休假方式、系統廠商名稱、精華亮點、排版工作說明)
            for field_name in ALLOWED_PROPERTIES:
                if field_name in props and field_name not in ["職缺名稱", "職務類別"]:
                    val_str = parse_notion_property(props[field_name])
                    job_dict[field_name] = val_str

            # 4. 嚴格過濾「停招」
            status = str(job_dict.get("狀態", "")).strip()
            if status == "停招":
                continue

            for k, v in job_dict.items():
                if isinstance(v, str) and v and k != "狀態" and not k.startswith("_"):
                    raw_text_parts.append(v)

            public_title = job_dict.get("職缺名稱(對外)") or ""
            internal_title = job_dict.get("職缺名稱") or ""
            job_category = job_dict.get("職務類別") or ""
            vendor_name = job_dict.get("系統廠商名稱") or ""
            leave_type = job_dict.get("休假方式") or ""
            display_title = public_title or internal_title or job_category
            
            if display_title:
                job_dict["_parsed_title"] = display_title
                job_dict["_internal_title"] = internal_title
                job_dict["_internal_title_clean"] = clean_text_for_search(internal_title)
                job_dict["_job_category"] = job_category
                job_dict["_job_category_clean"] = clean_text_for_search(job_category)
                job_dict["_vendor_name"] = vendor_name
                job_dict["_vendor_name_clean"] = clean_text_for_search(vendor_name)
                job_dict["_leave_type"] = leave_type
                job_dict["_leave_type_clean"] = clean_text_for_search(leave_type)
                job_dict["_raw_row_text"] = " ".join(raw_text_parts)
                job_dict["_search_text"] = clean_text_for_search(" ".join(raw_text_parts))
                active_jobs.append(job_dict)

        print(f"[Notion 職缺載入成功] 共載入 {len(active_jobs)} 筆招募中職缺！")
        _cached_jobs = active_jobs
        _last_jobs_fetch = now
        return active_jobs
    except Exception as e:
        print(f"[Notion 職缺讀取失敗]: {e}")
        return _cached_jobs or []

def fetch_faqs_data() -> list:
    """取得常見問答 FAQ 資料"""
    global _cached_faqs, _last_faqs_fetch
    now = time.time()
    if _cached_faqs is not None and (now - _last_faqs_fetch < CACHE_TTL):
        return _cached_faqs

    faqs = []
    try:
        results = query_notion_database_direct(NOTION_FAQ_DB_ID)
        for page in results:
            props = page.get("properties", {})
            q_text, a_text, status = "", "", "啟用"
            
            for k, v in props.items():
                val = parse_notion_property(v)
                k_lower = k.lower()
                if any(x in k_lower for x in ["問", "題目", "問題", "question", "title"]):
                    q_text = val
                elif any(x in k_lower for x in ["答", "回覆", "內容", "answer", "content"]):
                    a_text = val
                elif any(x in k_lower for x in ["狀態", "啟用", "status"]):
                    status = val

            if status not in ["停用", "關閉", "false"] and q_text and a_text:
                faqs.append({"question": q_text, "answer": a_text})

        print(f"[Notion FAQ 載入成功] 共載入 {len(faqs)} 筆常見問答！")
        _cached_faqs = faqs
        _last_faqs_fetch = now
        return faqs
    except Exception as e:
        print(f"[Notion FAQ 讀取失敗]: {e}")
        return _cached_faqs or []


def fetch_pending_faq_candidates() -> list:
    """取得 FAQ 資料庫中「標準回覆內容」空白、且「啟用狀態」沒有被同仁手動標記
    「停用」的問句清單，供每週報告的 FAQ 候選清單使用（見 HANDOFF.md「監控與
    告警機制」／「FAQ 週報」）。

    「啟用狀態」欄位在這裡是重複使用、不是另外新增專用欄位：
      - 空白 = 待審（同仁還沒看過，會出現在候選清單）
      - 手動設「停用」= 已審核但決定不採用（即使還沒填答案，也不會再出現）
      - 填了「標準回覆內容」= 已採用，會被 fetch_faqs_data() 撈去當正式 FAQ
    同仁若決定不採用某個候選問題，記得手動把「啟用狀態」設成「停用」，
    不然這題會因為「還沒答案」持續被判定為待審，每週都重複出現。

    刻意不套用 CACHE_TTL 快取（跟 fetch_faqs_data() 不同）：這支只有每日/週報
    的排程端點會呼叫，不是每次求職者訊息都會觸發，不需要快取。"""
    results = query_notion_database_direct(NOTION_FAQ_DB_ID)
    pending = []
    for page in results:
        props = page.get("properties", {})
        q_text, a_text, status = "", "", ""

        for k, v in props.items():
            val = parse_notion_property(v)
            k_lower = k.lower()
            if any(x in k_lower for x in ["問", "題目", "問題", "question", "title"]):
                q_text = val
            elif any(x in k_lower for x in ["答", "回覆", "內容", "answer", "content"]):
                a_text = val
            elif any(x in k_lower for x in ["狀態", "啟用", "status"]):
                status = val

        if q_text and not a_text and "停用" not in status:
            pending.append(q_text)

    return pending


_cached_faq_titles, _last_faq_titles_fetch = None, 0


def _fetch_all_faq_question_titles() -> list:
    """取得 FAQ 資料庫中所有頁面的『問題/關鍵字』標題文字，不篩選狀態或是否已有解答，
    供未收錄問題寫入前的去重比對使用（已寫入但尚未補答的問題，狀態/解答通常是空的，
    不會出現在 fetch_faqs_data() 篩選過的結果裡，所以這裡另外直接查一次原始資料）。

    這支每次有人問到未收錄的規章類問題（action="UNKNOWN_FAQ"）就會被呼叫一次，
    跟 fetch_faqs_data() 一樣套 CACHE_TTL 快取（原本沒有快取，正式上線流量一大、
    加上我們刻意讓 FAQ 候選問句量快速增加之後，這支每次都整份掃描 FAQ 資料庫，
    會變慢也可能撞到 Notion API 速率限制）。快取視窗內如果剛好有兩題非常相似的
    未收錄問題前後腳出現，去重可能會晚一輪才生效（下一則訊息才會抓到），這跟
    先前沒有快取時「兩個並發請求同時讀到同一份舊資料」本來就會發生的情況一樣，
    不是這次改動新增的風險。"""
    global _cached_faq_titles, _last_faq_titles_fetch
    now = time.time()
    if _cached_faq_titles is not None and (now - _last_faq_titles_fetch < CACHE_TTL):
        return _cached_faq_titles

    results = query_notion_database_direct(NOTION_FAQ_DB_ID)
    titles = []
    for page in results:
        title_prop = page.get("properties", {}).get("問題/關鍵字")
        if title_prop:
            title_text = parse_notion_property(title_prop)
            if title_text:
                titles.append(title_text)

    _cached_faq_titles = titles
    _last_faq_titles_fetch = now
    return titles


def _is_duplicate_faq_question(question_text: str, existing_titles: list, min_match_length: int = 4) -> bool:
    """雙向包含比對：只要新問題跟資料庫裡任一筆既有問題互相包含（且比對長度
    達 min_match_length），就視為重複，避免同一個/相似問題被反覆寫入待補答清單。"""
    question_clean = clean_text_for_search(question_text)
    if not question_clean:
        return False
    for existing in existing_titles:
        existing_clean = clean_text_for_search(existing)
        if not existing_clean or len(existing_clean) < min_match_length:
            continue
        if existing_clean in question_clean or question_clean in existing_clean:
            return True
    return False


def append_unresolved_faq_to_notion(question_text: str) -> bool:
    """將未收錄問題寫入 Notion FAQ 資料庫的『問題/關鍵字』欄位（寫入前先去重）"""
    if not NOTION_API_KEY or not NOTION_FAQ_DB_ID or not question_text:
        return False

    existing_titles = _fetch_all_faq_question_titles()
    if _is_duplicate_faq_question(question_text, existing_titles):
        print(f"[Notion FAQ 去重跳過] 「{question_text}」已有相似問題記錄在案，不重複寫入")
        return False

    url = "https://api.notion.com/v1/pages"
    headers = {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28"
    }

    # 僅寫入必填的標題欄位『問題/關鍵字』，其餘欄位留空供管理員後續補上解答
    payload = {
        "parent": {"database_id": NOTION_FAQ_DB_ID},
        "properties": {
            "問題/關鍵字": {
                "title": [
                    {"text": {"content": question_text.strip()}}
                ]
            }
        }
    }

    try:
        res = requests.post(url, headers=headers, json=payload, timeout=5)
        if res.status_code in [200, 201]:
            print(f"[Notion FAQ 自動擴充成功] 已記錄新問題至『問題/關鍵字』: 「{question_text}」")
            # 寫入成功就順手把這題加進快取，不用等快取過期才看得到——避免快取視窗內
            # 幾乎一樣的問題被連續問兩次時，第二次因為讀到快取裡還沒反映最新寫入
            # 的舊資料而重複寫入（見 _fetch_all_faq_question_titles() 的快取說明）。
            if _cached_faq_titles is not None:
                _cached_faq_titles.append(question_text.strip())
            return True
        else:
            print(f"[Notion FAQ 寫入失敗 {res.status_code}]: {res.text}")
            return False
    except Exception as e:
        print(f"[Notion FAQ 寫入異常]: {e}")
        return False
