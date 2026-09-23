"""AI 需求單：程式沒有精準命中的句子，交給 Gemini 整理成固定格式的「需求單」。

使用者 2026-09-23 決定的分工（見 HANDOFF.md 第 81 項）：
- 程式只處理「精準命中」的句子：按鈕送回來的固定句型、只由認得的地區／類型／
  廠商／班別等詞組成的短句。
- 其他句子交給 AI 判斷語意，但 AI 只能填這張需求單：每個欄位的選項都是固定的，
  程式會再檢查一次（不在選項裡、資料庫找不到的值一律丟掉），AI 不會自己挑職缺、
  也不會編出不存在的地名或類型。
- 程式把需求單轉成「程式一定看得懂的標準句子」（例如「桃園或新北 理貨/倉儲的工作，
  不要大夜班」），交給原本的流程找職缺、組回覆，所以條件跟卡片一定對得上。

這支檔案只負責「精準命中判斷」「呼叫 AI」「檢查需求單」「轉成標準句子」，不碰
LINE 回覆；怎麼接進對話流程在 handlers/message_handler.py 的「步驟 0-2b」。
"""
import json
import re
import time

from services.ai_service import query_gemini_ai
from services.matcher_service import (
    CATEGORY_KEYWORDS, SHIFT_SYNONYMS, LEAVE_BUCKETS, PAY_METHOD_SYNONYMS, WORKTYPE_SYNONYMS,
    HANDOFF_REASON_NAMES, NEGATION_TRIGGERS, LOCATION_CANDIDATES, _COUNTY_FULL_NAMES,
    build_district_county_full_index, build_benefit_keyword_index, detect_brand_label,
    detect_benefit_labels, extract_current_target_location, resolve_county_for_location,
)

CATEGORIES = list(CATEGORY_KEYWORDS)
SHIFTS = list(SHIFT_SYNONYMS)
LEAVES = [name for name, _ in LEAVE_BUCKETS]
PAYS = list(PAY_METHOD_SYNONYMS)
WORKTYPES = list(WORKTYPE_SYNONYMS)
# 「X都可以」按鈕用的維度名稱（跟 message_handler._drop_condition_buttons 一致）
BROADEN_DIMENSIONS = ["地區", "類型", "廠商", "全職/兼職", "班別", "休假方式", "發薪方式", "薪資", "福利", "排除的條件"]
INTENTS = ["找工作", "問問題", "轉專員", "閒聊", "不確定"]
HANDOFF_REASONS = list(HANDOFF_REASON_NAMES)

UNDERSTANDING_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "intent": {"type": "STRING", "enum": INTENTS},
        "locations": {"type": "ARRAY", "items": {"type": "STRING"}},
        "exclude_locations": {"type": "ARRAY", "items": {"type": "STRING"}},
        "categories": {"type": "ARRAY", "items": {"type": "STRING", "enum": CATEGORIES}},
        "exclude_categories": {"type": "ARRAY", "items": {"type": "STRING", "enum": CATEGORIES}},
        "unsupported_roles": {"type": "ARRAY", "items": {"type": "STRING"}},
        "brand": {"type": "STRING"},
        "shifts": {"type": "ARRAY", "items": {"type": "STRING", "enum": SHIFTS}},
        "exclude_shifts": {"type": "ARRAY", "items": {"type": "STRING", "enum": SHIFTS}},
        "leaves": {"type": "ARRAY", "items": {"type": "STRING", "enum": LEAVES}},
        "exclude_leaves": {"type": "ARRAY", "items": {"type": "STRING", "enum": LEAVES}},
        "pays": {"type": "ARRAY", "items": {"type": "STRING", "enum": PAYS}},
        "exclude_pays": {"type": "ARRAY", "items": {"type": "STRING", "enum": PAYS}},
        "worktype": {"type": "STRING", "enum": [""] + WORKTYPES},
        "salary_kind": {"type": "STRING", "enum": ["", "月薪", "時薪"]},
        "salary_min": {"type": "INTEGER"},
        "benefits": {"type": "ARRAY", "items": {"type": "STRING"}},
        "broaden": {"type": "ARRAY", "items": {"type": "STRING", "enum": BROADEN_DIMENSIONS}},
        "handoff_reason": {"type": "STRING", "enum": [""] + HANDOFF_REASONS},
        "clarify_question": {"type": "STRING"},
        "clarify_options": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["intent"],
}

_LIST_FIELDS = [
    "locations", "exclude_locations", "categories", "exclude_categories", "unsupported_roles",
    "shifts", "exclude_shifts", "leaves", "exclude_leaves", "pays", "exclude_pays",
    "benefits", "broaden", "clarify_options",
]
_STR_FIELDS = ["intent", "brand", "worktype", "salary_kind", "handoff_reason", "clarify_question"]

_SLOT_NAMES = {
    "location": "地區", "category": "類型", "brand": "廠商", "worktype": "全職/兼職", "shift": "班別",
    "leave": "休假方式", "pay": "發薪方式", "salary": "薪資", "benefit": "福利", "exclude": "排除的條件",
}

_PROMPT = """你是人力派遣公司「材霈」LINE 求職機器人「沛沛」的語意理解模組。你唯一的工作是把求職者這一句話
整理成一張「需求單」JSON。不要回覆求職者、不要推薦職缺、不要自己編選項以外的值。

【目前記住的條件】{slots}
【最近的對話】
{history}
【求職者這一句話】{message}

欄位規則：
- intent：
  找工作＝講了想要／不要的工作條件，或要看職缺。先講近況再找工作也算（「我們公司倒閉了，需要找新工作」「我要離職了，桃園有理貨嗎」）。
  「有小夜班嗎」「有日領的嗎」「有兼職嗎」「桃園有缺嗎」這種問「有沒有這種條件的職缺」的也是找工作，要把那個條件填進去。
  問問題＝在問規定、福利、面試、薪資怎麼算、公司制度、工作本身的細節等資訊，沒有要改條件（「有冷氣嗎」「薪水會扣勞健保嗎」「面試要穿什麼」「高雄餐飲假日要上班嗎」）。問句裡出現的詞不能當成條件。
  轉專員＝廠商要徵人或談合作、在職員工的薪資或出勤問題（「上個月薪水少了兩千」「薪水還沒匯」）、抱怨或投訴、明確要求真人或專員聯絡、要刪除個資或停止聯繫。單純講近況（公司倒閉、要離職、不喜歡跟人說話）不算。
  閒聊＝打招呼、道謝、跟找工作無關的話。
  不確定＝真的分不出是要還是不要、或分不出是指哪一項條件時才用；填 clarify_question（一句簡短的確認問題）跟 clarify_options（2～4 個選項，每個都要是這種固定句型：「晚班的工作」「不要晚班」「桃園的工作」「班別都可以」）。
- 條件欄位只填「這句話有講到、要改變」的維度；沒講到的維度留空（沿用目前記住的條件）。有講到的維度要填「改完之後的完整值」：
  目前記住桃園、這句說「新竹也可以」→ locations 填 ["桃園","新竹"]；說「那新竹呢」→ ["新竹"]。目前記住早班、說「夜班也可以」→ shifts 填 ["早班","大夜班"]。
- locations：想去上班的地點，填縣市或行政區的中文名稱（例如「桃園」「中壢」「竹北」「后里」「台北」「新竹縣」）。住的地方、交通車的起點、面試地點都不算。「新北或桃園」「台中 彰化」要填兩個；縣市跟行政區連在一起講（「新竹竹北」「台中西屯」「桃園中壢」）是同一個地方，只填行政區。英文、錯字、簡稱要轉成正式名稱（taoyuan→桃園、桃圓→桃園、北市→台北、台北縣→新北）。
- exclude_locations：不想去的地點（「大安區以外都可以」「不去中壢」「桃園除外」「不要新北」）。
- categories：只能從選項選。行業詞＋職務詞以職務為準（「物流業外送員」→外送）。「撿貨、出貨」→理貨/倉儲，「烘焙、飯店、餐廳內場」→餐飲/服務，「晶圓廠、電子廠」→製造/作業員。沒有對應類型的職務（保全、清潔、會計、美髮…）填到 unsupported_roles，不要硬塞到最像的類型。
- brand：講到的公司或品牌名稱（蝦皮、美光、全聯、LADY M…）。「飯店」「科技業」「宅配」「代招」「物流業」這種通稱不是品牌，留空。
- shifts：「晚上有空」→晚班；「白天上班」→早班；「小夜班、中班、打烊」→晚班；「夜班、大夜、通宵」→大夜班；「假日也可以上班」→假日班；「早晚班都可以」→早班跟晚班。「不排斥／不介意／不怕／可以接受 X」＝可以 X，填在 shifts（不是排除）。
- exclude_shifts：不要的班別（「不要夜班」「不用輪班」「可以不輪班嗎」「白天要顧小孩」→早班）。
- leaves：「做五休二」「休六日」「假日想休息」→週休二日；「週休一日」「週休三日」沒有對應的選項，不要填週休二日。
- exclude_leaves：不要的休假方式。
- pays：「週結」＝週領、「領現金」＝現金。exclude_pays：不要的發薪方式（「不要月領」）。
- worktype：打工、工讀、PT＝兼職；正職＝全職；沒講填空字串。
- salary_kind、salary_min：「月薪至少三萬」→ 月薪、30000；「時薪200以上」→ 時薪、200。沒講填空字串跟 0。日薪沒有這個選項，不要填。
- benefits：要找「有這個福利」的工作時才填（交通車、宿舍、員工餐、停車位…）。只是問「有沒有交通車」而且沒有要換條件時，intent 填問問題、benefits 留空。
- broaden：講了「X都可以／X不限」要拿掉的維度。「時間都可以」→班別；「全台都可以」「哪裡都可以」→地區；「什麼工作都可以」→類型。只說「都可以」但分不出是哪一項時不要填，intent 填不確定。
- handoff_reason：intent 是轉專員時才填，business＝業務詢問/廠商徵才，privacy＝個資/停止聯繫，complaint＝抱怨/客訴，employee＝在職員工問題，interview＝面試/應徵進度，human＝要找真人。
"""


def _format_slots(slots: dict) -> str:
    parts = [f"{_SLOT_NAMES[k]}：{v}" for k, v in (slots or {}).items() if k in _SLOT_NAMES and v]
    return "、".join(parts) or "（還沒有）"


def _format_history(history: list) -> str:
    lines = []
    for item in (history or [])[-4:]:
        text = str(item.get("text", "")).replace("\n", " ")
        lines.append(f"{item.get('role', '')}：{text[:150]}")
    return "\n".join(lines) or "（沒有）"


def build_prompt(message: str, slots: dict = None, history: list = None) -> str:
    return _PROMPT.format(slots=_format_slots(slots), history=_format_history(history), message=message)


def normalize_form(data) -> dict:
    """把 AI 回傳的 JSON 整理成每個欄位都有值的 dict（缺的補空值、型別不對的丟掉）。"""
    data = data if isinstance(data, dict) else {}
    form = {}
    for key in _LIST_FIELDS:
        value = data.get(key) or []
        form[key] = [str(v).strip() for v in value if str(v).strip()] if isinstance(value, list) else []
    for key in _STR_FIELDS:
        value = data.get(key)
        form[key] = str(value).strip() if isinstance(value, (str, int, float)) and value is not None else ""
    try:
        form["salary_min"] = max(0, int(data.get("salary_min") or 0))
    except (TypeError, ValueError):
        form["salary_min"] = 0
    if form["intent"] not in INTENTS:
        form["intent"] = ""
    return form


def understand_message(message: str, slots: dict = None, history: list = None, thinking_budget: int = None) -> dict:
    """呼叫 Gemini 整理需求單。失敗（沒有回應、不是 JSON）回傳 None，呼叫端照原本的流程走。"""
    raw = query_gemini_ai(
        build_prompt(message, slots, history), response_schema=UNDERSTANDING_SCHEMA,
        thinking_budget=thinking_budget,
    )
    if not raw:
        return None
    try:
        form = normalize_form(json.loads(raw))
    except (ValueError, TypeError):
        print(f"[AI需求單] 回傳不是合法 JSON：{raw[:200]}")
        return None
    return form if form["intent"] else None


# ---------------- 檢查需求單：不在選項裡、資料庫找不到的值一律丟掉 ----------------

def _valid_location(name: str, active_jobs: list) -> str:
    """AI 填的地名要程式自己也認得（標準句子交給原本流程時才一定看得懂）。"""
    name = str(name or "").strip().replace("臺", "台")
    if not name:
        return ""
    parsed = extract_current_target_location(name, "", active_jobs)
    return name if parsed and "|" not in parsed else ""


def _static_county_core(district: str) -> str:
    """全台行政區對照表（職缺上架表單用的那份）查行政區在哪個縣市，職缺資料裡
    剛好沒有這個區時也查得到。"""
    from services.job_listing_submit_service import TAIWAN_CITY_DISTRICTS
    target = str(district).replace("臺", "台")
    for county, districts in TAIWAN_CITY_DISTRICTS.items():
        for full in districts:
            core = full[len(county):] if full.startswith(county) else full
            if target in (core, core[:-1]):
                return county[:2]
    return ""


def drop_county_before_district(locations: list, message: str, active_jobs: list = None) -> list:
    """「新竹竹北」是同一個地方：AI 偶爾兩個都填，變成「新竹或竹北」把新竹市也列進來
    （第一次準確率考試）。句子裡縣市緊接著行政區時，拿掉那個縣市。"""
    if len(locations) < 2 or not message:
        return locations
    compact = re.sub(r"\s+", "", str(message)).replace("臺", "台")
    dropped = set()
    for county in locations:
        core = county[:2]
        if core + "市" not in _COUNTY_FULL_NAMES and core + "縣" not in _COUNTY_FULL_NAMES:
            continue
        for district in locations:
            if district == county:
                continue
            district_core = (resolve_county_for_location(district, active_jobs) or "")[:2] or _static_county_core(district)
            if district_core != core:
                continue
            if re.search(re.escape(county) + r"[縣市]?" + re.escape(district), compact):
                dropped.add(county)
    return [loc for loc in locations if loc not in dropped]


def validate_form(form: dict, active_jobs: list, message: str = "") -> dict:
    """回傳一份新的需求單：地名、廠商、福利都要資料庫認得，其他欄位只留選項內的值。"""
    if not form:
        return None
    clean = dict(form)
    clean["locations"] = drop_county_before_district(list(dict.fromkeys(
        v for v in (_valid_location(x, active_jobs) for x in form["locations"]) if v)), message, active_jobs)
    clean["exclude_locations"] = list(dict.fromkeys(
        v for v in (_valid_location(x, active_jobs) for x in form["exclude_locations"]) if v and v not in clean["locations"]))
    for key, allowed in (
        ("categories", CATEGORIES), ("exclude_categories", CATEGORIES), ("shifts", SHIFTS), ("exclude_shifts", SHIFTS),
        ("leaves", LEAVES), ("exclude_leaves", LEAVES), ("pays", PAYS), ("exclude_pays", PAYS),
        ("broaden", BROADEN_DIMENSIONS),
    ):
        clean[key] = list(dict.fromkeys(v for v in form[key] if v in allowed))
    # 同一個值同時要又不要：以「要」為準（「不排斥夜班」AI 偶爾兩邊都填）
    for want, drop in (("categories", "exclude_categories"), ("shifts", "exclude_shifts"),
                       ("leaves", "exclude_leaves"), ("pays", "exclude_pays")):
        clean[drop] = [v for v in clean[drop] if v not in clean[want]]
    brand = detect_brand_label(form["brand"], active_jobs) if form["brand"] else ""
    clean["brand"] = brand or ""
    clean["unsupported_roles"] = form["unsupported_roles"] if not form["brand"] or not brand else []
    benefits = []
    for b in form["benefits"]:
        benefits.extend(detect_benefit_labels(b, active_jobs))
    clean["benefits"] = list(dict.fromkeys(benefits))
    clean["worktype"] = form["worktype"] if form["worktype"] in WORKTYPES else ""
    if form["salary_kind"] in ("月薪", "時薪") and form["salary_min"] > 0:
        clean["salary_kind"], clean["salary_min"] = form["salary_kind"], form["salary_min"]
    else:
        clean["salary_kind"], clean["salary_min"] = "", 0
    clean["handoff_reason"] = form["handoff_reason"] if form["handoff_reason"] in HANDOFF_REASONS else ""
    # 讓求職者選的按鈕，下一輪一定要是程式精準命中的句子，不然又會回到 AI
    clean["clarify_options"] = [o for o in form["clarify_options"] if is_precise_hit(o, active_jobs)][:4]
    return clean


def has_conditions(form: dict) -> bool:
    return bool(form and any(form.get(k) for k in (
        "locations", "exclude_locations", "categories", "exclude_categories", "brand", "shifts", "exclude_shifts",
        "leaves", "exclude_leaves", "pays", "exclude_pays", "worktype", "salary_kind", "benefits", "broaden",
    )))


def render_canonical_text(form: dict) -> str:
    """需求單轉成程式一定看得懂的標準句子（跟按鈕送回來的句型一樣）：
    「桃園或新北 蝦皮 理貨/倉儲 兼職 早班或晚班 週休二日 日領 月薪30000以上 交通車的工作，
    不要大夜班，班別都可以」。每一種寫法都有測試確認原本的流程會解讀成同樣的條件。"""
    if not form:
        return ""
    positive = []
    if form.get("locations"):
        positive.append("或".join(form["locations"]))
    if form.get("brand"):
        positive.append(form["brand"])
    if form.get("categories"):
        positive.append("或".join(form["categories"]))
    if form.get("worktype"):
        positive.append(form["worktype"])
    for key in ("shifts", "leaves", "pays"):
        if form.get(key):
            positive.append("或".join(form[key]))
    if form.get("salary_kind") and form.get("salary_min"):
        positive.append(f"{form['salary_kind']}{form['salary_min']}以上")
    if form.get("benefits"):
        positive.append("有" + "、".join(form["benefits"]))
    clauses = []
    if positive:
        clauses.append(" ".join(positive) + "的工作")
    for key in ("exclude_locations", "exclude_categories", "exclude_shifts", "exclude_leaves", "exclude_pays"):
        clauses.extend(f"不要{v}" for v in form.get(key) or [])
    if form.get("broaden"):
        clauses.append(" ".join(f"{d}都可以" for d in form["broaden"]))
    return "，".join(clauses)


# ---------------- 精準命中：只由程式認得的詞組成的句子 ----------------

_FILLER_RE = re.compile(
    r"這家廠商|這家|的工作|工作|職缺|缺|我要|我想找|我想要|我想|想找|想要|想做|幫我找|請問|推薦|有沒有|有嗎|有嘛|"
    r"都可以|都行|也可以|也行|就好|只要|附近|一帶|看看|或是|還是|或|跟|和|及|的|有|嗎|嘛|呢|喔|啊|吧|找|看|要|想|在|那|"
    r"[、,，/\s!！?？~～.。…:：()（）「」]"
)
_EMOJI_RE = re.compile(r"[\U0001F300-\U0001FAFF☀-➿️]")
_SALARY_TOKEN_RE = re.compile(r"(月薪|時薪)\s*\d+(\.\d+)?\s*(萬|k|千)?\s*(以上|起)?", re.IGNORECASE)
_EXTRA_NEGATION_WORDS = ("以外", "除外", "不是", "不去", "免", "沒興趣", "不行")
_ADMIN_SUFFIXES = ("縣", "市", "區", "鄉", "鎮")

_token_cache = {"key": None, "tokens": []}


def _known_tokens(active_jobs: list) -> list:
    """程式認得的所有詞（地名、類型、班別、休假、發薪、全兼職、福利、維度名稱），長的先比。"""
    key = id(active_jobs), len(active_jobs or [])
    if _token_cache["key"] == key:
        return _token_cache["tokens"]
    tokens = set()
    for loc in LOCATION_CANDIDATES:
        tokens.add(loc)
    for full in _COUNTY_FULL_NAMES:
        tokens.update({full, full[:-1], full.replace("台", "臺"), full[:-1].replace("台", "臺")})
    for core in build_district_county_full_index(active_jobs or []):
        tokens.add(core)
        tokens.update(core + s for s in _ADMIN_SUFFIXES)
    for label, words in CATEGORY_KEYWORDS.items():
        tokens.add(label)
        tokens.update(words)
    for mapping in (SHIFT_SYNONYMS, PAY_METHOD_SYNONYMS, WORKTYPE_SYNONYMS):
        for label, words in mapping.items():
            tokens.add(label)
            tokens.update(words)
    for label, words in LEAVE_BUCKETS:
        tokens.add(label)
        tokens.update(words)
    tokens.update(k for k in build_benefit_keyword_index(active_jobs or []) if len(k) >= 2)
    tokens.update(BROADEN_DIMENSIONS)
    tokens.update({"其他條件", "類型", "全部", "不限"})
    ordered = sorted((t for t in tokens if t), key=len, reverse=True)
    _token_cache.update(key=key, tokens=ordered)
    return ordered


def _clause_is_known(clause: str, active_jobs: list) -> bool:
    rest = clause
    brand = detect_brand_label(clause, active_jobs) if active_jobs else ""
    if brand:
        rest = re.sub(re.escape(brand), " ", rest, flags=re.IGNORECASE)
    rest = _SALARY_TOKEN_RE.sub(" ", rest).lower()
    for token in _known_tokens(active_jobs):
        if token.lower() in rest:
            rest = rest.replace(token.lower(), " ")
    return not _FILLER_RE.sub("", rest).strip()


def is_precise_hit(text: str, active_jobs: list) -> bool:
    """整句話拿掉程式認得的詞跟虛詞之後什麼都不剩，才算精準命中（程式自己處理）。
    用「，」分開的每一段各自判斷；有否定詞時只有「不要X」（X 是單一個認得的詞）
    這種按鈕句型算，「不要夜班 日領就好」這種混在一起的交給 AI。"""
    raw = _EMOJI_RE.sub("", str(text or "")).strip()
    if not raw:
        return True
    known = {t.lower() for t in _known_tokens(active_jobs)}
    for clause in (c.strip() for c in re.split(r"[，,]", raw)):
        if not clause:
            continue
        negation_form = re.fullmatch(r"不要\s*(.+?)(的工作)?", clause)
        if negation_form:
            body = re.sub(r"(的工作|的|喔|哦|啦|了|啊|呀|吧|耶)+$", "", negation_form.group(1).strip().lower()) or negation_form.group(1).strip().lower()
            if body not in known and not (active_jobs and detect_brand_label(body, active_jobs).lower() == body):
                return False
            continue
        if any(w in clause.lower() for w in NEGATION_TRIGGERS) or any(w in clause for w in _EXTRA_NEGATION_WORDS):
            return False
        if not _clause_is_known(clause, active_jobs):
            return False
    return True


# ---------------- 紀錄 ----------------

def log_understanding(message: str, form: dict, route: str, latency: float, mode: str, canonical: str = ""):
    """寫進 Cloud Run log（Logs Explorer 搜尋「[AI需求單]」），之後拿來檢查 AI 判斷得準不準。"""
    try:
        payload = {
            "mode": mode, "route": route, "message": message[:200], "canonical": canonical,
            "latency": round(latency, 2), "form": {k: v for k, v in (form or {}).items() if v},
        }
        print(f"[AI需求單] {json.dumps(payload, ensure_ascii=False)}")
    except Exception:
        pass


def timed_understand(message: str, slots: dict, history: list, thinking_budget: int = None) -> tuple:
    start = time.monotonic()
    form = understand_message(message, slots, history, thinking_budget=thinking_budget)
    return form, time.monotonic() - start
