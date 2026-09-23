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
    detect_category_labels, detect_negated_location, extract_leave_labels, detect_pay_method_labels,
    extract_worktype_labels, mask_salary_phrases, clean_text_for_search,
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
        "exclude_worktype": {"type": "STRING", "enum": [""] + WORKTYPES},
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
_STR_FIELDS = ["intent", "brand", "worktype", "exclude_worktype", "salary_kind", "handoff_reason", "clarify_question"]

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

最重要的三條：
A. 這句話講到的每一個條件都要填，想要的跟不要的都要：「不要新北要桃園」＝exclude_locations 新北＋locations 桃園；
   「不要產線想做倉庫」＝exclude_categories 製造/作業員＋categories 理貨/倉儲；「早班 不要假日班」＝shifts 早班＋exclude_shifts 假日班；
   「週結 不用輪班」＝pays 週領＋exclude_shifts 輪班；「全職早班」＝worktype 全職＋shifts 早班；「早班不要了 晚上的比較好」＝shifts 晚班。
   有講廠商又講職務時兩個都要填：「美光的作業員」＝brand 美光＋categories 製造/作業員；「家樂福理貨」＝brand 家樂福＋categories 理貨/倉儲。
   否定詞放在後面也是不要：「夜班 不要」「夜班，不行」「白天不行」＝exclude（不是要）。「白天要顧小孩／白天要上課」＝exclude_shifts 早班，不要填 shifts，也不要排除其他班別。
B. 沒講的不要自己加、不要自己猜：「晚上」只是晚班，沒講夜班/大夜/半夜/通宵就不要加大夜班；「錢每天拿」只是日領，沒講現金就不要加現金；
   「越快上班越好」沒有講全職兼職；只講廠商（「momo理貨」「uber外送」「7-11店員」）不要自己加班別、福利、薪資、全兼職；以前的經歷（「我之前在美光做過作業員」「以前做餐廳外場」）不是現在的條件，不要填成廠商、類型，也不要排除。
C. 只填這句話有講到、要改變的維度，沒講到的維度留空。講到的維度填「改完之後的完整值」：
   記住桃園、說「新竹也可以」→ locations ["桃園","新竹"]；說「那新竹呢」→ ["新竹"]。
   「X也可以／X也行／X也沒關係／X也OK」＝在目前記住的值再加上 X：記住早班說「夜班也可以」→ shifts ["早班","大夜班"]；
   記住週休二日說「排休也沒關係」→ leaves ["週休二日","排休"]；記住理貨/倉儲說「作業員也可以」→ categories ["理貨/倉儲","製造/作業員"]；
   記住週領說「月領也可以」→ pays ["週領","月領"]；記住兼職說「正職也行」→ broaden ["全職/兼職"]（全兼職只能填一個，兩個都可以就是放寬）。
   「X不要了／X可以不要／不一定要X」而 X 是目前記住的條件＝拿掉 X：記住桃園|新竹說「新竹不要了」→ locations ["桃園"]；
   記住日領說「日領可以不要」「不一定要日領」→ broaden ["發薪方式"]（這是放寬，不是排除）。
   「沒有X的話Y也行」＝X跟Y都可以（「沒有日領的話週領也行」→ pays ["日領","週領"]）；「沒有X也沒關係」＝X不一定要（記住交通車時 → broaden ["福利"]），不是要X。

欄位規則：
- intent：
  找工作＝講了想要／不要的工作條件，或要看職缺。先講近況再找工作也算（「我們公司倒閉了，需要找新工作」「我要離職了，桃園有理貨嗎」）。
  「X有Y嗎」「有Y的嗎」問的是有沒有這種職缺也是找工作，要把條件填進去（「有小夜班嗎」「有日領的嗎」「盧洲有門市嗎」「新北板橋區有門市嗎」「台中西屯有門市嗎」「桃園有缺嗎」）。
  「我們工廠缺人可以幫忙找嗎」「我們公司要找人」＝廠商徵才（轉專員 business）；「這個月薪水還沒入帳」＝在職員工（轉專員 employee）。
  只說「我住X」「我人在X」沒有其他條件＝閒聊，不改地區。「倉庫在觀音還是大園」「從八德出發有交通車嗎」是問地點或交通＝問問題。
  問問題＝在問規定、福利、面試、薪資怎麼算、公司制度、工作本身的細節（冷氣、要不要站、要不要搬重物、離車站多遠、要不要輪班、交通車從哪裡出發），沒有要改條件（「有冷氣嗎」「薪水會扣勞健保嗎」「面試要穿什麼」「高雄餐飲假日要上班嗎」「門市要輪班嗎」）。問句裡出現的詞不能當成條件。
  轉專員＝廠商要徵人或談合作、在職員工的薪資／出勤／請假／離職手續問題、抱怨或罵人（包含罵機器人）、明確要求真人或專員聯絡、要刪除個資或停止聯繫。單純講近況（公司倒閉、要離職、不喜歡跟人說話）不算。
  閒聊＝打招呼、道謝、講自己或家人的事但沒有條件（「我媽住高雄」「我昨天上大夜班好累」）。
  不確定＝真的分不出是要還是不要、或分不出是指哪一項條件時才用（例如沒有上下文只說「都可以」「沒差」「好」）；填 clarify_question（一句簡短的確認問題）跟 clarify_options（2～4 個選項，每個都要是這種固定句型：「晚班的工作」「不要晚班」「桃園的工作」「班別都可以」）。
- 回答沛沛上一句的選擇題時，照選項的意思填：
  「要幫您找「晚班」或「大夜班」的工作嗎？」→「都可以」＝shifts ["晚班","大夜班"]；「晚班就好」＝shifts ["晚班"]。
  「要把「班別：大夜班」這個條件拿掉嗎？」→「好／可以／拿掉吧」＝broaden ["班別"]。
  「您是想了解「X」的規定，還是想找有「X」的職缺？」→「找職缺／找工作啦」＝找工作並填 X；「想先了解一下」＝問問題。
  「…這幾種職缺，請問您想看哪一種？」→「第二個」＝列出來的第二種；「隨便」＝broaden ["類型"]；「不要」＝不確定。
  「您說的「都可以」是指哪一項條件都可以呢？」「想調整地區、班別還是類型？」→「班別啦」＝broaden ["班別"]。
  「想換到哪個地區呢？」→「隨便啦」＝broaden ["地區"]。
  沛沛上一句只問某一項（地區、類型、班別）時，回「都可以／隨便／沒差／我都可以」就是放寬那一項，不要填不確定：
  問「請問您想在哪個地區工作呢」→「我都可以」＝broaden ["地區"]；問「想看哪一種呢」→「隨便」＝broaden ["類型"]；問「要幫您找「晚班」的工作嗎？」→「好」＝shifts ["晚班"]。
- locations：想去上班的地點，填縣市或行政區的中文名稱（例如「桃園」「中壢」「竹北」「后里」「台北」「新竹縣」）。住的地方、人在哪裡、交通車的起點、面試地點都不算。
  「新北或桃園」「台中 彰化」要填兩個；縣市跟行政區連在一起講（「新竹竹北」「台中西屯」「新竹東區」）是同一個地方，填連在一起的寫法（「台中西屯」）。
  記住台北、說「大安區可以嗎」＝縮小到大安區 → ["大安區"]，不要再填台北。
  「新莊以外的新北都可以」＝locations ["新北"]＋exclude_locations ["新莊"]。英文、錯字、簡稱要轉成正式名稱（taoyuan→桃園、桃圓→桃園、盧洲→蘆洲、北市→台北、中市→台中、台北縣→新北、竹科→新竹、中科→台中）。
- exclude_locations：不想去的地點（「大安區以外都可以」「不去中壢」「桃園除外」「不要新北」）。
- categories：只能從選項選。每個類型包含的職務（程式認得的詞）：
{category_words}
  上面沒列到的職務照意思判斷（隨車助手→外送、品檢員→製造/作業員、撿貨出貨→理貨/倉儲、烘焙飯店→餐飲/服務、便利商店店員→門市、收銀員→門市）。
  行業詞＋職務詞只算職務（「物流業外送員」→外送、「電子廠倉管」→理貨/倉儲、「科技廠行政」→客服/行政），不要把行業也填成類型。
  真的沒有對應類型的職務（保全、警衛、清潔、會計、美髮、護理、老師、工程師…）才填 unsupported_roles，不要硬塞；「保全或倉管」＝unsupported_roles ["保全"]＋categories ["理貨/倉儲"]。
- brand：講到的公司或品牌名稱（蝦皮、美光、全聯、全家、Uber、LADY M…）。「飯店」「科技業」「宅配」「代招」「物流業」「便利商店」這種通稱不是品牌，留空。「全家便利商店」＝brand 全家＋categories 門市。
- shifts：「晚上有空／晚上的班／小夜／中班／打烊」→晚班；「白天上班／只能白天」→早班；「只有六日有空／只有假日可以」→假日班（不是週休二日）；「夜班、大夜、半夜、通宵」→大夜班；「假日也可以上班」→假日班；「早晚班都可以」→早班跟晚班。
  「不排斥／不介意／不怕／可以接受 X」＝可以 X，填在 shifts（不是排除）。「我老公晚上會在家顧小孩」＝晚上可以上班 → 晚班。
- exclude_shifts：不要的班別（「不要夜班」「不用輪班」「可以不輪班嗎」「白天要顧小孩」→早班）。
- leaves：「做五休二」「休六日」「假日想休息」「固定休假日」→週休二日（做五休二不是做四休二）；「週休一日」「一週休一天」「週休三日」沒有對應的選項，不要填週休二日。
  說「假日也可以上班」時，目前記住的週休二日要拿掉（broaden ["休假方式"]）。
- exclude_leaves：不要的休假方式（「排休不要」→排休）。
- pays：「週結」＝週領、「領現金」＝現金、「一個月領一次」＝月領、「做一天領一天」＝日領。exclude_pays：不要的發薪方式（「不要月領」）。
- worktype：打工、工讀、PT＝兼職；正職＝全職；沒講填空字串。exclude_worktype：不要的（「不要兼職」「兼職的也不要」→兼職）。
- salary_kind、salary_min：只有講了月薪或時薪而且有數字才填（「月薪至少三萬」→ 月薪、30000；「時薪200以上」→ 時薪、200）。
  日薪、一天賺多少、「薪水高一點」都不要填，也不要自己換算成時薪或月薪。
- benefits：要找「有這個福利」的工作時才填（交通車、宿舍、員工餐、停車位…）。只是問「有沒有交通車」而且沒有要換條件時，intent 填問問題、benefits 留空。
- broaden：講了「X都可以／X不限／X無所謂」要拿掉的維度，X 是維度名稱（地區、類型、班別、時間、休假方式、發薪方式、領薪方式、薪水、廠商、公司）。
  「時間都可以」→班別；「領薪方式都可以」→發薪方式；「其他公司也可以」→廠商；「全台都可以」「哪裡都可以」→地區；「什麼工作都可以」→類型。
  「桃園都可以」是地區換成整個桃園（locations ["桃園"]），不是放寬地區。只說「都可以」但分不出是哪一項時不要填，intent 填不確定。
- handoff_reason：intent 是轉專員時才填，business＝業務詢問/廠商徵才，privacy＝個資/停止聯繫，complaint＝抱怨/客訴/罵人，employee＝在職員工問題（薪水、請假、離職手續），interview＝面試/應徵進度，human＝要找真人。
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


_INDUSTRY_WORDS = {"物流", "工廠", "科技廠", "電子廠", "製造業", "服務業", "服務類", "半導體", "餐飲", "製造"}


def _category_words() -> str:
    """類型包含的職務詞（行業詞拿掉，不然 AI 會把「物流公司的司機」「電子廠清潔」也填成理貨、作業員）。"""
    return "\n".join(
        f"  ・{label}：{'、'.join(w for w in words if w not in _INDUSTRY_WORDS)}" for label, words in CATEGORY_KEYWORDS.items())


def build_prompt(message: str, slots: dict = None, history: list = None) -> str:
    return _PROMPT.format(slots=_format_slots(slots), history=_format_history(history), message=message,
                          category_words=_category_words())


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


def _is_county_name(loc: str) -> bool:
    core = str(loc).replace("臺", "台")[:2]
    return loc in (core, core + "市", core + "縣") and (core + "市" in _COUNTY_FULL_NAMES or core + "縣" in _COUNTY_FULL_NAMES)


def _county_core_of(loc: str, active_jobs: list = None) -> str:
    parsed = extract_current_target_location(loc, "", active_jobs) or loc
    return (resolve_county_for_location(parsed, active_jobs) or "")[:2] or _static_county_core(parsed)


def drop_county_before_district(locations: list, message: str, active_jobs: list = None) -> list:
    """整理 AI 填的地區（準確率考試抓到的幾種情況）：
    - 「新竹竹北」「台中西屯」是同一個地方，AI 常把縣市跟區各填一個，變成「新竹或竹北」把整個
      新竹都列進來：句子裡縣市緊接著區時，合成一個（「東區」這種好幾個縣市都有的區，合成
      「新竹東區」才知道是哪一個）。
    - 記住台北、這句說「大安區可以嗎」，AI 會把記住的台北也填回去：這句話沒講到的縣市、而且
      它底下的區也在清單裡，就拿掉那個縣市（「桃園或中壢」這種兩個都講了的保留）。"""
    locs = [str(x).strip().replace("臺", "台") for x in locations or [] if str(x).strip()]
    if len(locs) < 2 or not message:
        return locs
    compact = re.sub(r"\s+", "", str(message)).replace("臺", "台")
    counties = [x for x in locs if _is_county_name(x)]
    used, merged = set(), {}
    for loc in locs:
        if loc in counties:
            continue
        for county in counties:
            if re.search(re.escape(county[:2]) + r"[縣市]?" + re.escape(loc), compact):
                used.add(county)
                combo = county[:2] + loc
                ambiguous = _is_ambiguous_district(loc)
                if active_jobs is not None:
                    merged[loc] = combo if ambiguous and _valid_location(combo, active_jobs) else loc
                else:
                    merged[loc] = combo if ambiguous else loc
    out = []
    for loc in locs:
        if loc in counties:
            if loc in used:
                continue
            if loc[:2] not in compact and any(_county_core_of(d, active_jobs) == loc[:2] for d in locs if d not in counties):
                continue
            out.append(loc)
        else:
            out.append(merged.get(loc, loc))
    return list(dict.fromkeys(out))


def _is_ambiguous_district(district: str) -> bool:
    from services.job_listing_submit_service import TAIWAN_CITY_DISTRICTS
    target = str(district).replace("臺", "台")
    found = set()
    for county, districts in TAIWAN_CITY_DISTRICTS.items():
        for full in districts:
            core = full[len(county):] if full.startswith(county) else full
            if target in (core, core[:-1]):
                found.add(county)
    return len(found) > 1


# ---------------- 交叉檢查：AI 填的每個值都要有根據，程式認得的詞不能被漏掉 ----------------
# 使用者 2026-09-23 定的原則「把邊界定義好」：AI 負責聽懂語意，但
#   - AI 加的值，句子（或沛沛上一句、目前記住的條件）裡要看得到根據，不然丟掉
#     （第二次考試：「momo理貨」AI 自己加了早班、員工餐、全職、月薪三萬；「之前做業務」加成不要外送）
#   - 句子裡明明有程式認得的詞（作業員、理貨、桃園、週休二日），AI 漏填的由程式補上
#     （「美光的作業員」只填了美光；「正職 週休二日 月薪32000以上」漏了週休二日）
#   - 否定詞緊貼著的（「白天不行」「夜班 不要」「不要台北」）以程式判斷的方向為準

_EXTRA_EVIDENCE = {
    "早班": ["早", "白天", "上午", "日班", "白班", "day"],
    "晚班": ["晚", "小夜", "中班", "打烊", "下午", "evening"],
    "大夜班": ["夜班", "大夜", "半夜", "通宵", "night", "深夜"],
    "假日班": ["假日", "六日", "週末", "周末", "星期六", "禮拜六", "weekend"],
    "輪班": ["輪"],
    "彈性排班": ["彈性", "自己排", "自由排"],
    "週休二日": ["六日", "週末", "周末", "假日", "休二日", "五休二", "雙休", "見紅休", "固定休", "休假日"],
    "日領": ["每天", "天天", "一天", "日結", "當天", "日領", "現領"],
    "週領": ["週", "周", "禮拜", "星期", "week"],
    "雙週領": ["雙週", "兩週", "兩周", "雙周"],
    "月領": ["月領", "一個月", "每月", "月結", "個月領"],
    "現金": ["現金", "現領"],
    "匯款": ["匯款", "轉帳", "入帳"],
    "全職": ["full"],
    "兼職": ["打工", "工讀", "兼差", "part", "pt"],
}
_NEGATION_CUES = ("不要", "不想", "不能", "不做", "不去", "除了", "以外", "除外", "排除", "不考慮", "不接受", "不用",
                  "不行", "不方便", "沒辦法", "沒興趣", "不喜歡", "太遠", "免", "ng", "no", "❌", "不可以", "拒絕", "討厭")
_BROADEN_CUES = ("都可以", "都行", "都好", "都ok", "不限", "隨便", "沒差", "無所謂", "哪裡", "全台", "全省", "什麼",
                 "其他", "也可以", "也行", "也沒關係", "不一定", "可以不要", "不需要", "拿掉", "都看", "any", "whatever",
                 "皆可", "沒關係")
_NEG_AFTER_RE = r"^[\s，,、的是]{0,2}(不要|不行|不可以|不能|不方便|沒空|沒辦法|要顧|要上課|要接送|要照顧|ng|NG|免|沒興趣|不考慮|不想|不做)"
_NEG_BEFORE_RE = r"(不要|不想|不能|不做|不上|沒辦法|不方便|除了|排除|不接受|不用|免)\s?$"
_PAST_CUES = ("以前", "之前", "做過", "現在在做", "目前在做", "原本", "曾經", "上一份")
_ORIGIN_CUES = ("住", "人在", "從", "出發", "家在", "戶籍", "面試", "報到", "老家")
_LOCATION_ALIASES = {"北市": "台北", "中市": "台中", "南市": "台南", "高市": "高雄", "北縣": "新北", "竹縣": "新竹縣",
                     "竹市": "新竹市", "桃市": "桃園", "竹科": "新竹", "中科": "台中", "南科": "台南", "雙北": "台北"}


def _label_words(label: str, mapping) -> list:
    words = [label]
    if isinstance(mapping, dict):
        words += list(mapping.get(label) or [])
    else:
        words += next((w for name, w in mapping if name == label), [])
    return [w.lower() for w in words + _EXTRA_EVIDENCE.get(label, []) if w]


def _has_evidence(label: str, mapping, text: str) -> bool:
    return any(w in text for w in _label_words(label, mapping))


def _negated_labels(text: str, mapping) -> set:
    """否定詞緊貼著的班別：「白天不行」「夜班，不行」「不要夜班」「白天要顧小孩」。"""
    hits = set()
    items = mapping.items() if isinstance(mapping, dict) else mapping
    for label, words in items:
        for w in sorted({label, *words}, key=len, reverse=True):
            for m in re.finditer(re.escape(w.lower()), text):
                after, before = text[m.end():m.end() + 6], text[max(0, m.start() - 4):m.start()]
                if re.match(_NEG_AFTER_RE, after) or re.search(_NEG_BEFORE_RE, before):
                    hits.add(label)
    return hits


def cross_check_form(form: dict, message: str, last_bot: str = "", slots: dict = None, active_jobs: list = None) -> dict:
    """需求單交叉檢查（見上方說明）。不需要職缺資料也能跑（準確率考試會直接用這個）。"""
    if not form:
        return form
    f = dict(form)
    message = str(message or "").replace("斑", "班")  # 「日斑」「夜斑」常見錯字
    msg = message.lower()
    slots = slots or {}
    context = " ".join([msg, str(last_bot or "").lower(), " ".join(str(v).lower() for v in slots.values())])
    short_reply = len(clean_text_for_search(msg)) <= 6 and bool(re.search(r"[?？]", str(last_bot or "")))

    # 地區簡稱（中市、竹科）
    for key in ("locations", "exclude_locations"):
        f[key] = [_LOCATION_ALIASES.get(str(v).strip(), v) for v in f.get(key) or []]

    # 1. 沒有根據的值丟掉
    for key, mapping in (("shifts", SHIFT_SYNONYMS), ("exclude_shifts", SHIFT_SYNONYMS), ("leaves", LEAVE_BUCKETS),
                         ("exclude_leaves", LEAVE_BUCKETS), ("pays", PAY_METHOD_SYNONYMS), ("exclude_pays", PAY_METHOD_SYNONYMS)):
        where = context
        if key.startswith("exclude_"):
            # 排除的值要這句話自己講到；這句完全沒講到這一類的詞（只回「不要」）才看沛沛上一句
            names = mapping if isinstance(mapping, dict) else dict(mapping)
            if any(_has_evidence(label, mapping, msg) for label in names):
                where = msg
        f[key] = [v for v in f.get(key) or [] if _has_evidence(v, mapping, where)]
    no_weekend = bool(re.search(r"週休[一1三3]日|周休[一1三3]日|休[一1]天|一週休一|休三天", msg))
    for key in ("worktype", "exclude_worktype"):
        if f.get(key) and not _has_evidence(f[key], WORKTYPE_SYNONYMS, context):
            f[key] = ""
    if not any(c in msg for c in _NEGATION_CUES):
        for key in ("exclude_locations", "exclude_categories", "exclude_shifts", "exclude_leaves", "exclude_pays"):
            f[key] = []
        f["exclude_worktype"] = ""
    if f.get("broaden") and not (any(c in msg for c in _BROADEN_CUES) or short_reply):
        f["broaden"] = []
    if f.get("salary_kind"):
        has_amount = bool(re.search(r"\d|萬|千|[一二兩三四五六七八九十]萬", msg))
        kind_ok = (f["salary_kind"] == "時薪" and re.search(r"時薪|時新|小時|hourly|/h", msg)) or \
                  (f["salary_kind"] == "月薪" and re.search(r"月|萬|k\b|k以上|千", msg))
        if not has_amount or not kind_ok or re.search(r"日薪|一天|每天", msg):
            f["salary_kind"], f["salary_min"] = "", 0
    dropped_benefits = [b for b in f.get("benefits") or []
                        if re.search(r"(沒有|不用|不需要|不一定要|不必)\s*" + re.escape(str(b).lower()), msg)]
    f["benefits"] = [b for b in f.get("benefits") or [] if b not in dropped_benefits and str(b).lower()[:2] in context]
    if dropped_benefits and slots.get("benefit") and "福利" not in f.get("broaden", []):
        f["broaden"] = list(f.get("broaden") or []) + ["福利"]

    # 2. 否定詞緊貼著的班別以程式判斷為準
    for label in _negated_labels(msg, SHIFT_SYNONYMS):
        f["shifts"] = [v for v in f["shifts"] if v != label]
        if label not in f["exclude_shifts"]:
            f["exclude_shifts"] = f["exclude_shifts"] + [label]

    # 3. 地區：否定方向跟漏填。否定要緊貼著地名才算（「不要新北要桃園」的桃園是要的；
    #    程式原本的否定判斷會把整句都當成否定，這裡不用它當依據）
    def _loc_negated(v):
        v = re.escape(str(v))
        return bool(re.search(r"(不要|不去|除了|不考慮|排除)\s*" + v, message)
                    or re.search(v + r".{0,4}(以外|除外|不要|不去|太遠|不行|不考慮)", message))
    candidates = [str(v) for v in f["locations"] + f["exclude_locations"]]
    negated_loc = detect_negated_location(message, active_jobs)
    if negated_loc:
        candidates.append(negated_loc)
    negated = list(dict.fromkeys(v for v in candidates if _loc_negated(v)))
    f["locations"] = [v for v in f["locations"] if str(v) not in negated]
    f["exclude_locations"] = list(dict.fromkeys(
        [v for v in f["exclude_locations"] if str(v) in negated or str(v) not in message] + negated))
    if (not f["locations"] and f.get("intent") == "找工作" and "地區" not in f.get("broaden", [])
            and not any(c in message for c in _ORIGIN_CUES)):
        found = extract_current_target_location(message, "", active_jobs)
        # 被排除的地方不能又補成想去的（「不去中壢」「桃園除外」「林口太遠」）
        excluded = [str(v) for v in f["exclude_locations"]]
        found_parts = [p for p in (found or "").split("|")
                       if p and not any(p in e or e in p for e in excluded)
                       and not re.search(re.escape(p) + r".{0,4}(以外|除外|不要|不去|太遠|不行|不考慮)", message)
                       and not re.search(r"(不要|不去|除了|不考慮|排除)\s*" + re.escape(p), message)]
        if found_parts:
            f["locations"] = found_parts

    # 4. 類型、休假、發薪、全兼職：句子裡有程式認得的詞、AI 卻沒填的補上
    wanted_part = message
    if any(c in message for c in _PAST_CUES):
        # 「我以前在餐廳做外場，現在想找工廠」：只看「現在想…」後面那段
        cut = max((message.rfind(w) for w in ("現在想", "現在要", "想找", "想做", "想轉", "想換")), default=-1)
        wanted_part = message[cut:] if cut != -1 else ""
    if wanted_part != message:
        # 以前做過的類型（詞只出現在「以前…」那段）不是現在要的
        def _cat_in(label, text):
            return any(w in text for w in [label] + list(CATEGORY_KEYWORDS.get(label, [])))
        f["categories"] = [c for c in f["categories"] if _cat_in(c, wanted_part) or not _cat_in(c, message)]
        f["exclude_categories"] = [c for c in f["exclude_categories"] if _cat_in(c, wanted_part) or not _cat_in(c, message)]
        if f.get("brand") and f["brand"] in message and f["brand"] not in wanted_part:
            f["brand"] = ""
    if f.get("intent") == "找工作" and wanted_part:
        message = wanted_part
        if not f["categories"]:
            text = message
            if f.get("brand"):
                text = re.sub(re.escape(f["brand"]), " ", text, flags=re.IGNORECASE)
            found = [c for c in detect_category_labels(clean_text_for_search(text)) if c not in f["exclude_categories"]]
            if 1 <= len(found) <= 2:
                f["categories"] = found
        if not f["leaves"]:
            f["leaves"] = [v for v in extract_leave_labels(message) if v not in f["exclude_leaves"]]
        if not f["pays"]:
            f["pays"] = [v for v in detect_pay_method_labels(mask_salary_phrases(message)) if v not in f["exclude_pays"]]
        if not f["worktype"] and not f["exclude_worktype"]:
            found = extract_worktype_labels(message)
            if len(found) == 1:
                f["worktype"] = found[0]

    if no_weekend:
        f["leaves"] = [v for v in f["leaves"] if v != "週休二日"]

    # 5. 同一個維度又給了值又說放寬：以給的值為準
    dim_of = {"地區": "locations", "類型": "categories", "班別": "shifts", "休假方式": "leaves", "發薪方式": "pays",
              "全職/兼職": "worktype", "廠商": "brand", "福利": "benefits"}
    slot_of = {"locations": "location", "categories": "category", "shifts": "shift", "leaves": "leave",
               "pays": "pay", "worktype": "worktype", "brand": "brand", "benefits": "benefit"}
    kept = []
    for d in f.get("broaden") or []:
        key = dim_of.get(d, "")
        value = f.get(key)
        remembered = set(str(slots.get(slot_of.get(key, ""), "") or "").split("|")) - {""}
        given = set(value) if isinstance(value, list) else ({value} if value else set())
        if given and given <= remembered:
            # AI 只是把原本記住的值再寫一次，放寬才是這句話的意思（「班別都可以」）
            f[key] = [] if isinstance(value, list) else ""
            kept.append(d)
        elif not given:
            kept.append(d)
    f["broaden"] = kept
    return f



def validate_form(form: dict, active_jobs: list, message: str = "", last_bot: str = "", slots: dict = None) -> dict:
    """回傳一份新的需求單：地名、廠商、福利都要資料庫認得，其他欄位只留選項內的值，
    再做交叉檢查（cross_check_form）。"""
    if not form:
        return None
    clean = dict(form)
    for key, allowed in (
        ("categories", CATEGORIES), ("exclude_categories", CATEGORIES), ("shifts", SHIFTS), ("exclude_shifts", SHIFTS),
        ("leaves", LEAVES), ("exclude_leaves", LEAVES), ("pays", PAYS), ("exclude_pays", PAYS),
    ):
        clean[key] = list(dict.fromkeys(v for v in clean.get(key) or [] if v in allowed))
    if message:
        clean = cross_check_form(clean, message, last_bot, slots, active_jobs)
    form = clean
    clean["locations"] = list(dict.fromkeys(
        v for v in (_valid_location(x, active_jobs) for x in drop_county_before_district(form["locations"], message, active_jobs)) if v))
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
    clean["exclude_worktype"] = form["exclude_worktype"] if form["exclude_worktype"] in WORKTYPES and form["exclude_worktype"] != clean["worktype"] else ""
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
        "leaves", "exclude_leaves", "pays", "exclude_pays", "worktype", "exclude_worktype", "salary_kind", "benefits", "broaden",
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
    if form.get("exclude_worktype"):
        clauses.append(f"不要{form['exclude_worktype']}")
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
        # 「門市要輪班嗎」「倉庫需要搬重物嗎」是在問工作內容，不是在講條件
        if re.search(r"(要|需要|會|得)[^，,]*[嗎嘛]", clause):
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
