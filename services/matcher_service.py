import re
from linebot.models import QuickReplyButton, MessageAction
from services.session_service import get_user_slots
from services.notion_service import clean_text_for_search

def _tokenize_search_terms(text: str) -> list:
    """將自然語言拆成可用於本地候選職缺/FAQ 篩選的詞彙"""
    normalized = clean_text_for_search(text)
    candidates = [
        "板橋", "新莊", "三重", "中和", "永和", "土城", "蘆洲", "樹林", "汐止", "林口", "泰山", "五股", "三峽", "鶯歌",
        "桃園", "中壢", "龜山", "蘆竹", "大園", "八德", "平鎮", "楊梅", "龍潭",
        "台北", "臺北", "新北", "台中", "臺中", "台南", "臺南", "高雄", "新竹", "彰化", "嘉義", "苗栗", "宜蘭", "屏東", "基隆",
        "早班", "早上", "白班", "日班", "晚班", "小夜", "大夜", "夜班", "假日", "彈性",
        "週休", "周休", "見紅休", "休六日", "四休二", "4休2", "排休", "輪休",
        "高時薪", "高薪", "時薪高", "日領", "週領",
        "外送", "司機", "配送", "送貨", "門市", "店員", "店到店", "智取店", "蝦皮", "momo", "富邦", "富昇", "美光", "欣興", "宏達電", "coupang", "酷澎",
        "理貨", "揀貨", "倉管", "作業員", "包裝", "產線", "倉儲", "餐飲", "服飾", "服務",
    ]
    return [k for k in candidates if clean_text_for_search(k) in normalized]

def extract_current_target_location(raw_msg: str, history_text: str = "") -> str:
    """從對話擷取鎖定地區（【最新訊息絕對優先】，支援求職者隨時切換縣市）"""
    locs = [
        "板橋", "新莊", "三重", "中和", "永和", "土城", "蘆洲", "樹林", "汐止", "林口", "泰山", "五股", "三峽", "鶯歌",
        "桃園", "中壢", "龜山", "蘆竹", "大園", "八德", "平鎮", "楊梅", "龍潭",
        "台北", "臺北", "新北", "台中", "臺中", "台南", "臺南", "高雄", "新竹", "彰化", "嘉義", "苗栗", "宜蘭", "屏東", "基隆"
    ]
    # 1. 優先檢查求職者當前最新輸入的一句話
    for loc in locs:
        if loc in raw_msg:
            return loc.replace("臺", "台")

    # 2. 當前未提及時，才由近到遠從歷史紀錄回溯
    for loc in locs:
        if loc in history_text:
            return loc.replace("臺", "台")
            
    return ""

def extract_shift_preference(text: str) -> str:
    """從文字中判斷求職者偏好的時段/班別"""
    shift_map = {
        "早班": ["早班", "早上班", "白班", "日班"],
        "晚班": ["晚班", "小夜"],
        "大夜班": ["大夜", "夜班", "大夜班"],
        "假日班": ["假日班", "假日"],
        "彈性排班": ["彈性排班", "自由排班", "排班彈性", "時段彈性", "不限時段"]
    }
    for label, keys in shift_map.items():
        if any(k in text for k in keys):
            return label
    return ""

def extract_leave_preference(text: str) -> str:
    """從文字中判斷求職者偏好的休假制度"""
    clean = clean_text_for_search(text)
    if any(k in clean for k in ["週休", "周休", "見紅休", "固定休六日", "休六日", "休假日"]):
        return "週休二日"
    if any(k in clean for k in ["四休二", "4休2", "作四休二", "做四休二"]):
        return "四休二"
    if any(k in clean for k in ["排休", "輪休", "排班休"]):
        return "排休"
    return ""

def extract_salary_preference(text: str) -> bool:
    """判斷求職者是否特別指定高時薪/高薪偏好"""
    clean = clean_text_for_search(text)
    return any(k in clean for k in ["高時薪", "時薪高", "高薪", "時薪最高", "薪水高", "時薪多少"])

def detect_category_label(clean_input: str) -> str:
    """從文字中判斷求職者偏好的工作類別"""
    if any(k in clean_input for k in ["外送", "外送員", "配送員", "巡貨司機", "送貨司機", "外送工作", "司機"]):
        return "外送"
    if any(k in clean_input for k in ["門市", "店員", "門市人員", "蝦皮門市", "智取店", "店到店"]):
        return "門市"
    if any(k in clean_input for k in ["理貨", "揀貨", "倉管", "作業員", "包裝", "產線"]):
        return "理貨/倉儲"
    return ""

def category_search_keywords(category_label: str) -> list:
    """依已知工作類別 slot 回傳對應關鍵字清單"""
    mapping = {
        "外送": ["外送", "外送員", "司機", "配送", "配送員", "送貨"],
        "門市": ["門市", "店員", "門市人員", "店到店", "智取店"],
        "理貨/倉儲": ["理貨", "揀貨", "倉管", "作業員", "包裝", "產線", "倉儲"]
    }
    return mapping.get(category_label, [])

def detect_brand_label(text: str, active_jobs: list = None) -> str:
    """動態從訊息辨識求職者詢問之特定廠商或品牌（嚴格過濾疑問詞與形容詞）"""
    normalized = clean_text_for_search(text)
    
    # 1. 優先精準比對 Notion 資料庫中現有的所有系統廠商名稱
    if active_jobs:
        for j in active_jobs:
            v_name = str(j.get("系統廠商名稱") or "").strip()
            if v_name and len(v_name) >= 2:
                v_clean = clean_text_for_search(v_name)
                if v_clean and v_clean in normalized:
                    return v_name

    # 2. 常見知名廠商白名單
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

    # 3. 自然語言動態抽取（嚴格黑名單過濾，杜絕誤抓「什麼高時薪」）
    match = re.search(r'(?:有|想找|請問有|有沒有)\s*([a-zA-Z0-9\u4e00-\u9fa5]{2,8}?)\s*(?:嗎|的工作|職缺|廠|$)', text)
    if match:
        extracted = match.group(1).strip()
        invalid_tokens = [
            "什麼", "甚麼", "哪些", "哪種", "哪裡", "哪家", "高薪", "高時薪", "時薪", "月薪", "薪水", 
            "工作", "職缺", "機會", "缺額", "早班", "晚班", "夜班", "日班", "白班", "大夜", "兼職", "全職", "pt",
            "週休", "周休", "見紅", "排休", "輪班", "四休二", "門市", "外送", "理貨", "倉儲", "作業員", "技術員",
            "台北", "新北", "桃園", "新竹", "台中", "台南", "高雄", "基隆", "宜蘭", "苗栗", "彰化", "嘉義", "屏東",
            "板橋", "新莊", "三重", "中和", "永和", "土城", "蘆洲", "樹林", "汐止", "林口", "中壢", "龜山"
        ]
        if extracted and not any(token in extracted for token in invalid_tokens):
            return extracted

    return ""

def _job_title_and_category_text(job: dict) -> tuple:
    """提取主要精準欄位：職缺名稱（內/對外）與職務類別"""
    internal_title = clean_text_for_search(job.get("_internal_title", ""))
    public_title = clean_text_for_search(job.get("職缺名稱(對外)", ""))
    category = clean_text_for_search(job.get("_job_category", "") or job.get("職務類別", ""))
    return internal_title, public_title, category

def _job_extended_search_text(job: dict) -> str:
    """提取公開延伸欄位"""
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
    """門市類別的安全排除：只依職缺名稱/職務類別判斷外送衝突"""
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
    """分級判斷職缺是否符合工作類別/品牌"""
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
    """執行兩級階梯式職缺篩選"""
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
    """候選排序計分函式（加強高時薪、休假制度、廠商與地區加減分權重）"""
    slots = slots or {}
    search_text = job.get("_search_text", "")
    leave_text = str(job.get("休假方式") or "")
    salary_text = str(job.get("薪資") or "")
    score = 0
    query_clean = clean_text_for_search(query_text)

    # 1. 地區命中
    if current_location:
        loc = clean_text_for_search(current_location)
        if loc and loc in search_text:
            score += 40

    # 2. 廠商權重加分
    vendor_clean = clean_text_for_search(job.get("系統廠商名稱", ""))
    brand_slot = slots.get("brand", "")
    if brand_slot and brand_slot.lower() in search_text:
        score += 60
    elif vendor_clean and vendor_clean in query_clean:
        score += 50

    # 3. 高時薪意圖加分
    if extract_salary_preference(query_text):
        if "時薪" in salary_text or "2" in salary_text:
            score += 35

    # 4. 休假制度加減分
    leave_slot = slots.get("leave", "")
    if leave_slot == "週休二日" or "週休" in query_clean or "周休" in query_clean:
        if any(k in leave_text for k in ["週休", "周休", "見紅", "六日"]):
            score += 45
        elif any(k in leave_text for k in ["四休二", "4休2", "輪班", "排休", "做四休二"]):
            score -= 100

    title_clean = clean_text_for_search(job.get("_parsed_title", ""))
    category_clean = clean_text_for_search(job.get("職務類別", ""))
    if title_clean and title_clean in query_clean:
        score += 30
    if category_clean and category_clean in query_clean:
        score += 20

    category = slots.get("category", "")
    for keyword in category_search_keywords(category):
        if clean_text_for_search(keyword) in search_text:
            score += 15

    for term in _tokenize_search_terms(query_text):
        term_clean = clean_text_for_search(term)
        if term_clean and term_clean in search_text:
            score += 8

    return score

def build_ai_job_candidates(active_jobs: list, query_text: str, current_location: str = "", slots: dict = None, limit: int = 40) -> list:
    """在送 Gemini 前縮小候選集合（具備物理級地區與休假隔離）"""
    if not active_jobs:
        return []

    slots = slots or {}
    target_pool = active_jobs

    # 1. 地區實體隔離
    if current_location:
        loc_clean = current_location.replace("台", "臺")
        location_pool = [j for j in active_jobs if current_location in j.get("_search_text", "") or loc_clean in j.get("_search_text", "")]
        # 若該地區有職缺，則只傳該地區的職缺；若該地區無職缺，target_pool 即為空清單，讓系統精準觸發無缺額
        target_pool = location_pool

    # 2. 週休制度實體隔離
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
    """漸進式引導：依序確認地區 → 工作類別 → 時段"""
    slots = get_user_slots(user_id)
    known_location = current_location or slots.get("location", "")
    known_category = slots.get("category", "")
    known_shift = slots.get("shift", "")

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

    if not known_category:
        text = f"好的，鎖定在【{known_location}】附近幫您找工作 📍\n\n請問您比較想找哪一種工作類型呢？"
        buttons = [
            QuickReplyButton(action=MessageAction(label="🛵 外送/司機", text=f"{known_location}外送")),
            QuickReplyButton(action=MessageAction(label="🏬 門市/店到店", text=f"{known_location}門市")),
            QuickReplyButton(action=MessageAction(label="📦 理貨/倉儲作業員", text=f"{known_location}理貨")),
            QuickReplyButton(action=MessageAction(label="👀 都可以，先看看", text="都給我看看"))
        ]
        return text, buttons

    if not known_shift:
        text = f"了解！【{known_location}】的【{known_category}】職缺為您安排 😊\n\n請問時段班別上您有偏好嗎？（沒有特別限制也沒關係喔）"
        buttons = [
            QuickReplyButton(action=MessageAction(label="☀️ 早班", text=f"{known_location}{known_category}早班")),
            QuickReplyButton(action=MessageAction(label="🌙 晚班/大夜", text=f"{known_location}{known_category}晚班")),
            QuickReplyButton(action=MessageAction(label="🔄 不限時段，先看看", text="都給我看看"))
        ]
        return text, buttons

    return "", []