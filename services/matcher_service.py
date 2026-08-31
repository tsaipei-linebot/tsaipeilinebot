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
        "台北", "臺北", "新北", "台中", "臺中", "台南", "臺南", "高雄", "新竹", "彰化", "嘉義", "苗栗", "宜蘭", "屏東",
        "早班", "早上", "白班", "日班", "晚班", "小夜", "大夜", "夜班", "假日", "彈性",
        "外送", "司機", "配送", "送貨", "門市", "店員", "店到店", "智取店", "蝦皮", "momo", "富邦", "富昇", "美光", "欣興", "宏達電",
        "理貨", "揀貨", "倉管", "作業員", "包裝", "產線", "倉儲", "餐飲", "服飾", "服務",
    ]
    return [k for k in candidates if clean_text_for_search(k) in normalized]

def extract_current_target_location(history_and_msg: str) -> str:
    """從對話上下文擷取目前鎖定的行政區或縣市"""
    locs = [
        "板橋", "新莊", "三重", "中和", "永和", "土城", "蘆洲", "樹林", "汐止", "林口", "泰山", "五股", "三峽", "鶯歌",
        "桃園", "中壢", "龜山", "蘆竹", "大園", "八德", "平鎮", "楊梅", "龍潭",
        "台北", "臺北", "新北", "台中", "臺中", "台南", "臺南", "高雄", "新竹", "彰化", "嘉義", "苗栗", "宜蘭", "屏東"
    ]
    for loc in locs:
        if loc in history_and_msg:
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

def detect_category_label(clean_input: str) -> str:
    """從文字中判斷求職者偏好的工作類別"""
    if any(k in clean_input for k in ["外送", "外送員", "配送員", "巡貨司機", "送貨司機", "外送工作", "司機"]):
        return "外送"
    if any(k in clean_input for k in ["門市", "店員", "門市人員", "蝦皮門市", "智取店", "店到店"]):
        return "門市"
    if any(k in clean_input for k in ["momo", "富邦", "富昇"]):
        return "momo"
    if any(k in clean_input for k in ["理貨", "揀貨", "倉管", "作業員", "包裝", "產線"]):
        return "理貨/倉儲"
    return ""

def category_search_keywords(category_label: str) -> list:
    """依已知工作類別 slot 回傳對應關鍵字清單"""
    mapping = {
        "外送": ["外送", "外送員", "司機", "配送", "配送員", "送貨"],
        "門市": ["門市", "店員", "門市人員", "店到店", "智取店"],
        "momo": ["momo", "富邦", "富昇"],
        "理貨/倉儲": ["理貨", "揀貨", "倉管", "作業員", "包裝", "產線", "倉儲"]
    }
    return mapping.get(category_label, [])

def detect_brand_label(text: str) -> str:
    """從目前訊息辨識明確提到的廠商或品牌"""
    normalized = clean_text_for_search(text)
    if any(k in normalized for k in ["蝦皮", "spx"]):
        return "蝦皮"
    if any(k in normalized for k in ["momo", "富邦", "富昇"]):
        return "momo"
    if "美光" in normalized:
        return "美光"
    if "欣興" in normalized:
        return "欣興"
    return ""

def _job_title_and_category_text(job: dict) -> tuple:
    """提取主要精準欄位：職缺名稱（內/對外）與職務類別"""
    internal_title = clean_text_for_search(job.get("_internal_title", ""))
    public_title = clean_text_for_search(job.get("職缺名稱(對外)", ""))
    category = clean_text_for_search(job.get("_job_category", "") or job.get("職務類別", ""))
    return internal_title, public_title, category

def _job_extended_search_text(job: dict) -> str:
    """提取公開延伸欄位 (系統廠商名稱 + 行業別 + 對外工作內容)"""
    fields = [
        job.get("職缺名稱", ""),
        job.get("系統廠商名稱", ""),
        job.get("職缺名稱(對外)", ""),
        job.get("職務類別", ""),
        job.get("行業別", ""),
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
    if brand_label == "蝦皮":
        return any(k in text for k in ["蝦皮", "spx"])
    if brand_label == "momo":
        return any(k in text for k in ["momo", "富邦", "富昇"])
    if brand_label:
        return brand_label.lower() in text
    return True

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

    if category_label == "momo":
        if _brand_matches_text(primary_text, "momo"):
            return True
        return allow_relaxed and _brand_matches_text(extended_text, "momo")

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
    """候選排序計分函式（加強廠商名稱與地區權重）"""
    slots = slots or {}
    search_text = job.get("_search_text", "")
    score = 0
    query_clean = clean_text_for_search(query_text)

    if current_location:
        loc = clean_text_for_search(current_location)
        if loc and loc in search_text:
            score += 40

    vendor_clean = clean_text_for_search(job.get("系統廠商名稱", ""))
    if vendor_clean and vendor_clean in query_clean:
        score += 50

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
    """在送 Gemini 前縮小候選集合（若有指定地區則實體隔離）"""
    if not active_jobs:
        return []

    # 地區實體隔離：若已鎖定地區，候選集優先 100% 來自該地區
    target_pool = active_jobs
    if current_location:
        loc_clean = current_location.replace("台", "臺")
        location_pool = [j for j in active_jobs if current_location in j.get("_search_text", "") or loc_clean in j.get("_search_text", "")]
        if location_pool:
            target_pool = location_pool

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
    """漸進式引導：依序確認地區 → 工作類別 → 時段，並產生 QuickReply 按鈕"""
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