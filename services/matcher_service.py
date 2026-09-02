import re
from linebot.models import QuickReplyButton, MessageAction
from services.session_service import get_user_slots
from services.notion_service import clean_text_for_search

def _tokenize_search_terms(text: str) -> list:
    """將自然語言拆成可用於本地候選職缺/FAQ 篩選的詞彙[cite: 1]"""
    normalized = clean_text_for_search(text)
    candidates = [
        "板橋", "新莊", "三重", "中和", "永和", "土城", "蘆洲", "樹林", "汐止", "林口", "泰山", "五股", "三峽", "鶯歌",
        "桃園", "中壢", "龜山", "蘆竹", "大園", "八德", "平鎮", "楊梅", "龍潭",
        "台北", "臺北", "新北", "台中", "臺中", "台南", "臺南", "高雄", "新竹", "彰化", "嘉義", "苗栗", "宜蘭", "屏東", "基隆",
        "早班", "早上", "白班", "日班", "常日班", "晚班", "小夜", "中班", "大夜", "夜班", "假日", "彈性", "輪班",
        "週休", "周休", "見紅休", "休六日", "四休二", "4休2", "做四休二", "作四休二", "做二休二", "四班二輪", "排休", "輪休",
        "高時薪", "高薪", "時薪高", "日領", "週領", "兼職", "工讀", "打工", "pt", "PT", "短期", "學生工讀",
        "外送", "司機", "配送", "送貨", "門市", "店員", "店到店", "智取店", "蝦皮", "momo", "富邦", "富昇", "美光", "欣興", "宏達電", "coupang", "酷澎",
        "製造", "製造業", "科技", "科技廠", "作業員", "技術員", "產線", "組裝", "包裝", "機台", "半導體", "工廠", "電子廠",
        "理貨", "揀貨", "倉管", "倉儲", "物流", "餐飲", "服飾", "服務",
    ]
    return [k for k in candidates if clean_text_for_search(k) in normalized]

def has_negative_intent(text: str) -> bool:
    """判斷是否帶有否定、排除或不要的語氣"""
    clean = clean_text_for_search(text)
    negative_words = ["除了", "不要", "不想", "排除", "不考慮", "不想要", "除了這個", "除了這些", "換別的", "非"]
    return any(w in clean for w in negative_words)


# ==========================================
# 否定詞位置感知：判斷某個關鍵字是不是「緊接在否定詞之後」出現
# 用來區分「不要新莊了改看桃園」裡的「新莊」（被排除）跟「桃園」（正向意圖）
# ==========================================
NEGATION_TRIGGERS = ["不要", "不想要", "不想", "除了", "排除", "不考慮", "非"]


def _keyword_is_negated(text: str, keyword: str) -> bool:
    """檢查 keyword 在 text 中的出現位置，往前 6 個字內有沒有出現否定詞。
    有的話代表使用者是在講「不要/除了 這個關鍵字」，屬於被排除的意圖，不應該當成正向需求採用。
    """
    idx = text.find(keyword)
    if idx == -1:
        return False
    window_start = max(0, idx - 6)
    window = text[window_start:idx]
    return any(trigger in window for trigger in NEGATION_TRIGGERS)

LOCATION_CANDIDATES = [
    "板橋", "新莊", "三重", "中和", "永和", "土城", "蘆洲", "樹林", "汐止", "林口", "泰山", "五股", "三峽", "鶯歌",
    "桃園", "中壢", "龜山", "蘆竹", "大園", "八德", "平鎮", "楊梅", "龍潭",
    "台北", "臺北", "新北", "台中", "臺中", "台南", "臺南", "高雄", "新竹", "彰化", "嘉義", "苗栗", "宜蘭", "屏東", "基隆"
]


def extract_current_target_location(raw_msg: str, history_text: str = "") -> str:
    """從使用者最新訊息擷取鎖定地區（避免被對話歷史中的範例字詞干擾，並跳過被否定的地名）[cite: 1]"""
    for loc in LOCATION_CANDIDATES:
        if loc in raw_msg and not _keyword_is_negated(raw_msg, loc):
            return loc.replace("臺", "台")

    return ""


def detect_negated_location(raw_msg: str) -> str:
    """偵測使用者是否明確表示排除某個地區（例如「不要新莊了」），回傳被排除的地名，沒有則回傳空字串"""
    for loc in LOCATION_CANDIDATES:
        if loc in raw_msg and _keyword_is_negated(raw_msg, loc):
            return loc.replace("臺", "台")
    return ""

def extract_shift_preference(text: str) -> str:
    """從文字中判斷求職者偏好的時段/班別（支援同義詞與工時縮寫）[cite: 1]"""
    clean = clean_text_for_search(text).lower()
    shift_map = {
        "早班": ["早班", "早上班", "白班", "日班", "常日班", "正常班"],
        "晚班": ["晚班", "小夜", "中班", "下午班"],
        "大夜班": ["大夜", "夜班", "大夜班", "深夜班", "通宵"],
        "假日班": ["假日班", "假日", "週末班", "周休兼職", "假日兼職"],
        "兼職/工讀": ["兼職", "打工", "工讀", "pt", "短期工讀", "學生工讀", "兼差"],
        "輪班": ["輪班", "四班二輪", "二班二輪", "輪三班"],
        "彈性排班": ["彈性排班", "自由排班", "排班彈性", "時段彈性", "不限時段"]
    }
    for label, keys in shift_map.items():
        if any(k.lower() in clean for k in keys):
            return label
    return ""

def extract_leave_preference(text: str) -> str:
    """從文字中判斷求職者偏好的休假制度（支援多種休假模式）[cite: 1]"""
    clean = clean_text_for_search(text)
    if any(k in clean for k in ["週休", "周休", "見紅休", "固定休六日", "休六日", "休假日", "休雙休"]):
        return "週休二日"
    if any(k in clean for k in ["四休二", "4休2", "作四休二", "做四休二", "四班二輪", "做二休二", "2休2"]):
        return "四休二"
    if any(k in clean for k in ["排休", "輪休", "排班休", "月休八天", "月休8天"]):
        return "排休"
    return ""

def extract_numeric_salary_preference(text: str) -> dict:
    """解析文字中的具體數值型薪資需求（例如：時薪200以上、月薪4萬以上）[cite: 1]"""
    clean = clean_text_for_search(text)
    
    # 時薪匹配 (例如: 時薪220, 時薪>200)
    hourly_match = re.search(r'時薪[^\d]*?(\d{3})', clean)
    if hourly_match:
        return {"type": "hourly", "min_amount": int(hourly_match.group(1))}
        
    # 月薪萬數匹配 (例如: 月薪4萬, 月薪3.8萬)
    monthly_wan_match = re.search(r'月薪[^\d]*?(\d+(?:\.\d+)?)萬', clean)
    if monthly_wan_match:
        return {"type": "monthly", "min_amount": int(float(monthly_wan_match.group(1)) * 10000)}
        
    # 月薪五位數字匹配 (例如: 月薪38000)
    monthly_match = re.search(r'月薪[^\d]*?(\d{5})', clean)
    if monthly_match:
        return {"type": "monthly", "min_amount": int(monthly_match.group(1))}
        
    return None

def extract_salary_preference(text: str) -> bool:
    """判斷求職者是否特別指定高時薪/高薪偏好[cite: 1]"""
    clean = clean_text_for_search(text)
    return any(k in clean for k in ["高時薪", "時薪高", "高薪", "時薪最高", "薪水高", "時薪多少", "200以上", "時薪破百"])

CATEGORY_KEYWORDS = {
    "外送": ["外送", "外送員", "配送員", "巡貨司機", "送貨司機", "外送工作", "司機", "隨車"],
    "門市": ["門市", "店員", "門市人員", "蝦皮門市", "智取店", "店到店", "櫃檯"],
    "製造/作業員": ["製造", "製造業", "作業員", "技術員", "產線", "組裝", "機台", "半導體", "工廠", "科技廠", "電子廠", "品管", "包裝員"],
    "理貨/倉儲": ["理貨", "揀貨", "倉管", "包裝", "倉儲", "物流", "堆高機", "貼標"],
    "餐飲/服務": ["餐飲", "服務", "廚房", "內場", "外場", "專櫃", "服飾", "洗碗", "助手"],
}


def detect_category_label(clean_input: str) -> str:
    """從文字中判斷求職者偏好的工作類別（完整支援製造業與多元工種，並跳過被否定的類別）[cite: 1]"""
    for label, keywords in CATEGORY_KEYWORDS.items():
        matched_kw = next((k for k in keywords if k in clean_input), None)
        if matched_kw and not _keyword_is_negated(clean_input, matched_kw):
            return label
    return ""


def detect_negated_category(clean_input: str) -> str:
    """偵測使用者是否明確表示排除某個工作類別（例如「除了外送」），回傳被排除的類別標籤，沒有則回傳空字串"""
    for label, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw in clean_input and _keyword_is_negated(clean_input, kw):
                return label
    return ""

def category_search_keywords(category_label: str) -> list:
    """依已知工作類別 slot 回傳對應關鍵字清單[cite: 1]"""
    mapping = {
        "外送": ["外送", "外送員", "司機", "配送", "配送員", "送貨", "隨車"],
        "門市": ["門市", "店員", "門市人員", "店到店", "智取店", "櫃檯"],
        "製造/作業員": ["製造", "作業員", "技術員", "產線", "組裝", "機台", "半導體", "工廠", "科技", "電子", "設備", "品檢", "包裝"],
        "理貨/倉儲": ["理貨", "揀貨", "倉管", "包裝", "倉儲", "物流", "堆高機", "進貨", "出貨"],
        "餐飲/服務": ["餐飲", "服務", "廚房", "內場", "外場", "專櫃", "服飾", "店員", "外送"]
    }
    return mapping.get(category_label, [])

def detect_brand_label(text: str, active_jobs: list = None) -> str:
    """動態從訊息辨識求職者詢問之特定廠商或品牌（嚴格排除行業別與疑問詞）[cite: 1]"""
    normalized = clean_text_for_search(text)
    
    # 1. 優先精準比對 Notion 資料庫中現有的所有系統廠商名稱[cite: 1]
    if active_jobs:
        for j in active_jobs:
            v_name = str(j.get("系統廠商名稱") or "").strip()
            if v_name and len(v_name) >= 2:
                v_clean = clean_text_for_search(v_name)
                if v_clean and v_clean in normalized:
                    return v_name

    # 2. 常見知名廠商白名單[cite: 1]
    known_brands = {
        "蝦皮": ["蝦皮", "spx"],
        "momo": ["momo", "富邦", "富昇"],
        "Coupang": ["coupang", "酷澎"],
        "美光": ["美光", "micron"],
        "欣興": ["欣興"],
        "台積電": ["台積電", "tsmc"],
        "宏達電": ["宏達電", "htc"]
    }
    for brand_key, synonyms in known_brands.items():
        if any(syn in normalized for syn in synonyms):
            return brand_key

    # 3. 自然語言動態抽取[cite: 1]
    match = re.search(r'(?:有|想找|請問有|有沒有)\s*([a-zA-Z0-9\u4e00-\u9fa5]{2,8}?)\s*(?:嗎|的工作|職缺|廠|$)', text)
    if match:
        extracted = match.group(1).strip()
        invalid_tokens = [
            "什麼", "甚麼", "哪些", "哪種", "哪裡", "哪家", "高薪", "高時薪", "時薪", "月薪", "薪水", 
            "工作", "職缺", "機會", "缺額", "早班", "晚班", "夜班", "日班", "白班", "大夜", "兼職", "全職", "pt", "工讀",
            "週休", "周休", "見紅", "排休", "輪班", "四休二", "做四休二",
            "製造業", "製造", "科技業", "服務業", "餐飲業", "物流業", "電子業", "半導體", "傳統產業", "傳產",
            "作業員", "技術員", "產線", "工程師", "設備", "助理", "主管", "司機", "外送員", "門市", "店員", "理貨", "倉儲", "客服", "內勤", "行政",
            "台北", "新北", "桃園", "新竹", "台中", "台南", "高雄", "基隆", "宜蘭", "苗栗", "彰化", "嘉義", "屏東",
            "板橋", "新莊", "三重", "中和", "永和", "土城", "蘆洲", "樹林", "汐止", "林口", "中壢", "龜山"
        ]
        # 額外跟 CATEGORY_KEYWORDS 交叉比對：抓到的詞如果「完全等於」某個已知工作類別關鍵字，
        # 一律不當成廠商名稱（例如「外送」本身不在上面的 invalid_tokens 手動清單裡，
        # 但它是 CATEGORY_KEYWORDS 裡「外送」類別的關鍵字，交叉比對能自動擋下來）。
        # 這裡刻意只做完全比對、不做子字串比對，避免誤傷「蝦皮」這種剛好是
        # 「蝦皮門市」子字串、但本身是真實廠商名稱的詞。
        all_category_keywords = {kw for keywords in CATEGORY_KEYWORDS.values() for kw in keywords}
        is_category_word = extracted in all_category_keywords

        # 這一步是「自然語言動態抽取」，本質上是從口語句型猜測公司名稱，
        # 光靠關鍵字排除清單長期一定會漏（例如「其他的」「別的」這種口語填充詞）。
        # 改成更根本的防呆：抓到的詞必須真的比對到 Notion 資料庫裡實際存在的廠商名稱，
        # 才採信為 brand；否則寧可不設定，讓後面的地區/類別篩選機制處理就好，
        # 不會因為使用者隨口說的詞被誤判成廠商，進而污染候選職缺清單。
        matches_known_vendor = False
        extracted_clean = clean_text_for_search(extracted)
        if active_jobs and extracted_clean:
            for j in active_jobs:
                v_clean = str(j.get("_vendor_name_clean") or "")
                if v_clean and (extracted_clean in v_clean or v_clean in extracted_clean):
                    matches_known_vendor = True
                    break

        if extracted and not is_category_word and matches_known_vendor and not any(token in extracted for token in invalid_tokens):
            return extracted

    return ""

def _job_title_and_category_text(job: dict) -> tuple:
    internal_title = clean_text_for_search(job.get("_internal_title", ""))
    public_title = clean_text_for_search(job.get("職缺名稱(對外)", ""))
    category = clean_text_for_search(job.get("_job_category", "") or job.get("職務類別", ""))
    return internal_title, public_title, category

def _job_extended_search_text(job: dict) -> str:
    fields = [
        job.get("職缺名稱", ""),
        job.get("系統廠商名稱", ""),
        job.get("職缺名稱(對外)", ""),
        job.get("職務類別", ""),
        job.get("行業別", ""),
        job.get("休假方式", ""),
        job.get("薪資", ""),
        job.get("工作內容(對外)", ""),
    ]
    return clean_text_for_search(" ".join(str(x or "") for x in fields))

def _job_has_delivery_conflict(job: dict) -> bool:
    internal_title, public_title, category = _job_title_and_category_text(job)
    primary_text = " ".join([internal_title, public_title, category])
    return any(k in primary_text for k in ["外送", "外送員", "配送", "配送員", "司機", "送貨"])

def _brand_matches_text(text: str, brand_label: str) -> bool:
    text = clean_text_for_search(text)
    if not brand_label:
        return True
    if brand_label == "蝦皮":
        return any(k in text for k in ["蝦皮", "spx"])
    if brand_label == "momo":
        return any(k in text for k in ["momo", "富邦", "富昇"])
    if brand_label == "Coupang":
        return any(k in text for k in ["coupang", "酷澎"])
    return clean_text_for_search(brand_label) in text

def _category_matches_text(text: str, category_label: str) -> bool:
    keywords = category_search_keywords(category_label)
    if not keywords:
        return True
    text = clean_text_for_search(text)
    return any(clean_text_for_search(k) in text for k in keywords)

def job_matches_category_filter(job: dict, category_label: str, brand_label: str = "", allow_relaxed: bool = True) -> bool:
    if not category_label or category_label == "不限":
        return True

    internal_title, public_title, category = _job_title_and_category_text(job)
    primary_text = " ".join([internal_title, public_title, category])
    extended_text = _job_extended_search_text(job)

    if category_label == "門市":
        if _job_has_delivery_conflict(job):
            return False

        primary_category_match = _category_matches_text(primary_text, "門市")
        primary_brand_match = True if not brand_label else _brand_matches_text(primary_text, brand_label)

        if primary_category_match and primary_brand_match:
            return True

        if not allow_relaxed:
            return False

        relaxed_category_match = _category_matches_text(extended_text, "門市")
        relaxed_brand_match = True if not brand_label else _brand_matches_text(extended_text, brand_label)
        return relaxed_category_match and relaxed_brand_match

    if _category_matches_text(primary_text, category_label):
        return True

    return allow_relaxed and _category_matches_text(extended_text, category_label)

def filter_jobs_by_category_tiered(jobs: list, category_label: str, brand_label: str = "") -> list:
    if not category_label or category_label == "不限":
        return list(jobs)

    strict_matches = [
        j for j in jobs
        if job_matches_category_filter(j, category_label, brand_label, allow_relaxed=False)
    ]
    if strict_matches:
        return strict_matches

    return [
        j for j in jobs
        if job_matches_category_filter(j, category_label, brand_label, allow_relaxed=True)
    ]

def _score_job_for_ai(job: dict, query_text: str, current_location: str = "", slots: dict = None) -> int:
    slots = slots or {}
    search_text = job.get("_search_text", "")
    leave_text = str(job.get("休假方式") or "")
    salary_text = str(job.get("薪資") or "")
    shift_text = str(job.get("班別") or "")
    score = 0
    query_clean = clean_text_for_search(query_text)

    # 1. 地區命中[cite: 1]
    if current_location:
        loc = clean_text_for_search(current_location)
        if loc and loc in search_text:
            score += 40

    # 2. 廠商權重加分[cite: 1]
    vendor_clean = clean_text_for_search(job.get("系統廠商名稱", ""))
    brand_slot = slots.get("brand", "")
    if brand_slot and brand_slot.lower() in search_text:
        score += 80
    elif vendor_clean and vendor_clean in query_clean:
        score += 70

    # 3. 數值型與意圖薪資加減分
    num_salary_pref = extract_numeric_salary_preference(query_text)
    if num_salary_pref:
        pref_type = num_salary_pref["type"]
        min_target = num_salary_pref["min_amount"]
        job_numbers = [int(n) for n in re.findall(r'\d+', salary_text)]
        
        if pref_type == "hourly" and "時薪" in salary_text:
            max_hourly = max(job_numbers) if job_numbers else 0
            if max_hourly >= min_target:
                score += 55
            else:
                score -= 40
        elif pref_type == "monthly" and ("月薪" in salary_text or any(n >= 25000 for n in job_numbers)):
            max_monthly = max(job_numbers) if job_numbers else 0
            if max_monthly >= min_target:
                score += 55
            else:
                score -= 40
    elif extract_salary_preference(query_text):
        if "時薪" in salary_text or any(k in salary_text for k in ["2", "3", "4"]):
            score += 35

    # 4. 班別精準加減分
    shift_slot = slots.get("shift", "")
    if shift_slot and shift_slot != "不限":
        if shift_slot == "早班" and any(k in shift_text for k in ["早", "日", "白", "常日"]):
            score += 40
        elif shift_slot in ["晚班", "大夜班"] and any(k in shift_text for k in ["晚", "夜", "小夜", "大夜"]):
            score += 40
        elif shift_slot == "假日班" and any(k in shift_text for k in ["假日", "兼職", "pt", "PT"]):
            score += 40

    # 5. 休假制度加減分[cite: 1]
    leave_slot = slots.get("leave", "")
    if leave_slot == "週休二日" or "週休" in query_clean or "周休" in query_clean:
        if any(k in leave_text for k in ["週休", "周休", "見紅", "六日"]):
            score += 45
        elif any(k in leave_text for k in ["四休二", "4休2", "輪班", "排休", "做四休二"]):
            score -= 100

    # 6. 產業別與類別加分[cite: 1]
    category = slots.get("category", "")
    for keyword in category_search_keywords(category):
        if clean_text_for_search(keyword) in search_text:
            score += 30

    title_clean = clean_text_for_search(job.get("_parsed_title", ""))
    category_clean = clean_text_for_search(job.get("職務類別", ""))
    industry_clean = clean_text_for_search(job.get("行業別", ""))

    if title_clean and title_clean in query_clean:
        score += 30
    if category_clean and category_clean in query_clean:
        score += 25
    if industry_clean and industry_clean in query_clean:
        score += 25

    for term in _tokenize_search_terms(query_text):
        term_clean = clean_text_for_search(term)
        if term_clean and term_clean in search_text:
            score += 8

    return score

def build_ai_job_candidates(active_jobs: list, query_text: str, current_location: str = "", slots: dict = None, limit: int = 40) -> list:
    """在送 Gemini 前縮小候選集合（具備品牌跨區保底、週休隔離與零結果條件退讓機制）[cite: 1]"""
    if not active_jobs:
        return []

    slots = slots or {}
    brand_slot = slots.get("brand", "")
    target_pool = active_jobs

    # 1. 廠商優先保底：若指定品牌在指定地區查無缺額，自動放寬至全體職缺庫尋找該廠商[cite: 1]
    if brand_slot:
        brand_pool = [j for j in active_jobs if brand_slot.lower() in j.get("_search_text", "")]
        if brand_pool:
            target_pool = brand_pool
        elif current_location:
            loc_clean = current_location.replace("台", "臺")
            target_pool = [j for j in active_jobs if current_location in j.get("_search_text", "") or loc_clean in j.get("_search_text", "")]
    elif current_location:
        loc_clean = current_location.replace("台", "臺")
        location_pool = [j for j in active_jobs if current_location in j.get("_search_text", "") or loc_clean in j.get("_search_text", "")]
        target_pool = location_pool

    # 2. 週休制度隔離與條件退讓機制[cite: 1]
    leave_slot = slots.get("leave", "")
    query_clean = clean_text_for_search(query_text)
    if leave_slot == "週休二日" or "週休" in query_clean or "周休" in query_clean:
        weekend_pool = [j for j in target_pool if any(k in str(j.get("休假方式") or "") for k in ["週休", "周休", "見紅", "六日"])]
        if weekend_pool:
            target_pool = weekend_pool

    scored = [(_score_job_for_ai(job, query_text, current_location, slots), idx, job) for idx, job in enumerate(target_pool)]
    scored.sort(key=lambda x: (-x[0], x[1]))
    positive = [item for item in scored if item[0] > 0]
    selected = positive[:limit] if positive else scored[:limit]
    return [item[2] for item in selected]

def _score_faq_for_ai(faq: dict, query_text: str) -> int:
    q = clean_text_for_search(faq.get("question", ""))
    query = clean_text_for_search(query_text)
    if not q or not query:
        return 0
    score = 0
    if q in query or query in q:
        score += 50
    for term in _tokenize_search_terms(query_text):
        t = clean_text_for_search(term)
        if t and t in q:
            score += 10
    for i in range(max(0, len(query) - 1)):
        piece = query[i:i + 2]
        if piece and piece in q:
            score += 2
    return score

def build_ai_faq_candidates(faq_list: list, query_text: str, limit: int = 20) -> list:
    if not faq_list:
        return []
    scored = [(_score_faq_for_ai(faq, query_text), idx, faq) for idx, faq in enumerate(faq_list)]
    scored.sort(key=lambda x: (-x[0], x[1]))
    positive = [item for item in scored if item[0] > 0]
    selected = positive[:limit] if positive else scored[:limit]
    return [item[2] for item in selected]

def build_progressive_question(user_id: str, current_location: str) -> tuple:
    """單一焦點循序引導（地區 -> 班別 -> 工作類型）[cite: 1]"""
    slots = get_user_slots(user_id)
    known_location = current_location or slots.get("location", "")
    known_shift = slots.get("shift", "")
    known_category = slots.get("category", "")

    # 焦點 1：詢問地區[cite: 1]
    if not known_location:
        prefix = f"想找【{known_category}】類型的工作對嗎？😊\n\n" if known_category else "您好呀！我是招募顧問沛沛 😊\n\n"
        text = prefix + "請問您方便在【哪個地區】上班呢？（例如板橋、新莊、桃園等）"
        buttons = [
            QuickReplyButton(action=MessageAction(label="📍 板橋/新莊", text=f"新莊{known_category}".strip())),
            QuickReplyButton(action=MessageAction(label="📍 桃園/中壢", text=f"桃園{known_category}".strip())),
            QuickReplyButton(action=MessageAction(label="📍 台北/新北", text=f"台北{known_category}".strip())),
            QuickReplyButton(action=MessageAction(label="👀 都可以，先看看", text="都給我看看"))
        ]
        return text, buttons

    # 焦點 2：詢問班別時段[cite: 1]
    if not known_shift:
        text = f"好的，鎖定在【{known_location}】附近 📍\n\n請問您偏好哪種上班時段或班別呢？"
        buttons = [
            QuickReplyButton(action=MessageAction(label="☀️ 固定早班", text=f"{known_location}早班")),
            QuickReplyButton(action=MessageAction(label="🌙 固定夜班", text=f"{known_location}夜班")),
            QuickReplyButton(action=MessageAction(label="🏖️ 週休二日", text=f"{known_location}週休二日")),
            QuickReplyButton(action=MessageAction(label="🔄 不限時段，先看看", text="都給我看看"))
        ]
        return text, buttons

    # 焦點 3：詢問工作類別[cite: 1]
    if not known_category:
        text = f"收到！【{known_location} {known_shift}】為您安排 😊\n\n請問有特別想找哪種工作類型嗎？"
        buttons = [
            QuickReplyButton(action=MessageAction(label="🏭 製造/作業員", text=f"{known_location}製造業")),
            QuickReplyButton(action=MessageAction(label="🏬 門市/店到店", text=f"{known_location}門市")),
            QuickReplyButton(action=MessageAction(label="📦 理貨/倉儲", text=f"{known_location}理貨")),
            QuickReplyButton(action=MessageAction(label="🛵 外送/司機", text=f"{known_location}外送")),
            QuickReplyButton(action=MessageAction(label="👀 都可以，先看看", text="都給我看看"))
        ]
        return text, buttons

    return "", []
