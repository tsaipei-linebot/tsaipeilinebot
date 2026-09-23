import re
from linebot.models import QuickReplyButton, MessageAction
from services.session_service import get_user_slots
from services.notion_service import clean_text_for_search

def _tokenize_search_terms(text: str) -> list:
    """將自然語言拆成可用於本地候選職缺/FAQ 篩選的詞彙[cite: 1]

    地區、班別、廠商三類關鍵字改成直接引用模組層級的單一清單來源
    （LOCATION_CANDIDATES / SHIFT_SYNONYMS / KNOWN_BRANDS，定義在本檔案下方，
    Python 只在函式「呼叫時」才查找全域名稱，所以先在這裡引用、稍後才定義沒有問題），
    避免跟 extract_current_target_location、extract_shift_preference、detect_brand_label
    各自維護一份導致覆蓋範圍兜不起來。休假制度、薪資、工作類別不在這次「地區/班別/廠商」
    集中化範圍內，維持原本各自的關鍵字。用 dict.fromkeys 去重，避免清單合併後出現重複詞
    導致同一個詞被算兩次分數。
    """
    normalized = clean_text_for_search(text)
    candidates = list(dict.fromkeys([
        *LOCATION_CANDIDATES,
        *[syn for syns in SHIFT_SYNONYMS.values() for syn in syns],
        "早上",  # SHIFT_SYNONYMS 只收「早上班」，這裡額外保留原本就有涵蓋的單獨「早上」寫法
        "週休", "周休", "見紅休", "休六日", "四休二", "4休2", "做四休二", "作四休二", "做二休二", "四班二輪", "排休", "輪休",
        "高時薪", "高薪", "時薪高", "日領", "週領", "短期",
        *[syn for syns in KNOWN_BRANDS.values() for syn in syns],
        "外送", "司機", "配送", "送貨", "門市", "店員", "店到店", "智取店",
        "製造", "製造業", "科技", "科技廠", "作業員", "技術員", "產線", "組裝", "包裝", "機台", "半導體", "工廠", "電子廠",
        "理貨", "揀貨", "倉管", "倉儲", "物流", "餐飲", "服飾", "服務",
    ]))
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
    # 否定詞只管到同一個子句：「不要蝦皮了 高雄有什麼」裡的「高雄」不該被
    # 前一個子句的「不要」波及（實測會把高雄當成被排除的地區清掉）。
    window = _CLAUSE_BREAK_RE.split(window)[-1]
    return any(trigger in window for trigger in NEGATION_TRIGGERS)


# 「了」當子句結尾（「不要蝦皮了」），但「除了」本身就是否定詞，不能被切開。
_CLAUSE_BREAK_RE = re.compile(r'[，,。！!？?\s、；;|]|(?<!除)了')
_CLAUSE_PUNCT_RE = re.compile(r'[，,。！!？?\s、；;]+')

LOCATION_CANDIDATES = [
    "板橋", "新莊", "三重", "中和", "永和", "土城", "蘆洲", "樹林", "汐止", "林口", "泰山", "五股", "三峽", "鶯歌",
    "桃園", "中壢", "龜山", "蘆竹", "大園", "八德", "平鎮", "楊梅", "龍潭",
    "台北", "臺北", "新北", "台中", "臺中", "台南", "臺南", "高雄", "新竹", "彰化", "嘉義", "苗栗", "宜蘭", "屏東", "基隆",
    # 原本沒收錄的縣市：實測「花蓮有週休二日的工作嗎」抓不到地區，直接回
    # 「有的！」推薦台北/新北的職缺。
    "花蓮", "台東", "臺東", "南投", "雲林", "澎湖", "金門", "馬祖", "連江",
]

# LOCATION_CANDIDATES 裡屬於「縣市層級」（不是行政區層級）的詞——用來讓
# extract_current_target_location()／detect_negated_location() 判斷優先順序：
# 縣市層級的詞（例如「新竹」）精準度不如行政區層級的詞（例如「竹北」），一句話
# 同時出現兩者時（例如「新竹縣 竹北沒缺嗎」），不能讓縣市層級的詞搶先命中、
# 蓋掉更精確的行政區層級辨識，見 HANDOFF.md 竹北案例。
_LOCATION_COUNTY_LEVEL_NAMES = {
    "台北", "臺北", "新北", "台中", "臺中", "台南", "臺南", "高雄",
    "新竹", "彰化", "嘉義", "苗栗", "宜蘭", "屏東", "基隆",
    "花蓮", "台東", "臺東", "南投", "雲林", "澎湖", "金門", "馬祖", "連江",
}

# 行政區/城市關鍵字 -> 所屬縣市，只給「同縣市鄰近地區退讓建議」這個功能用
# （見 find_county_level_alternative_jobs）。這份對照表刻意手動維護、只收錄
# LOCATION_CANDIDATES 裡的詞，不做任何地理相鄰（隔壁行政區）的推論——「同一
# 個縣市」是可以直接查表確認的事實，「兩個行政區地理相鄰」牽涉到完整的台灣
# 行政區地圖資料，這裡不處理。
LOCATION_TO_COUNTY = {
    "板橋": "新北市", "新莊": "新北市", "三重": "新北市", "中和": "新北市", "永和": "新北市",
    "土城": "新北市", "蘆洲": "新北市", "樹林": "新北市", "汐止": "新北市", "林口": "新北市",
    "泰山": "新北市", "五股": "新北市", "三峽": "新北市", "鶯歌": "新北市", "新北": "新北市",
    "桃園": "桃園市", "桃園區": "桃園市", "中壢": "桃園市", "龜山": "桃園市", "蘆竹": "桃園市", "大園": "桃園市",
    "八德": "桃園市", "平鎮": "桃園市", "楊梅": "桃園市", "龍潭": "桃園市",
    "台北": "台北市", "臺北": "台北市",
    "台中": "台中市", "臺中": "台中市",
    "台南": "台南市", "臺南": "台南市",
    "高雄": "高雄市",
    "新竹": "新竹市",
    "彰化": "彰化縣",
    "嘉義": "嘉義市",
    "苗栗": "苗栗縣",
    "宜蘭": "宜蘭縣",
    "屏東": "屏東縣",
    "基隆": "基隆市",
    "花蓮": "花蓮縣",
    "台東": "台東縣", "臺東": "台東縣",
    "南投": "南投縣",
    "雲林": "雲林縣",
    "澎湖": "澎湖縣",
    "金門": "金門縣",
    "馬祖": "連江縣", "連江": "連江縣",
}


# ==========================================
# 行政區名稱動態辨識：從 Notion 職缺資料本身「長出」地名清單，不再只靠上面
# LOCATION_CANDIDATES 這份手動維護、只涵蓋新北/桃園的清單。
#
# 背景（見 HANDOFF.md 竹北／佳里案例）：LOCATION_CANDIDATES 除了新北市、
# 桃園市之外，其他縣市完全沒有收錄任何行政區名稱，只要求職者問的行政區
# 沒被手動收錄，程式要嘛誤配對到剛好也含有同一個縣市字樣的無關行政區
# （竹北案例），要嘛整個掉到 AI 決策、賭 AI 判斷得準不準（佳里案例，AI
# 即使看到正確資料還是判斷錯誤）。與其手動列一份涵蓋全台灣的地名清單
# （既費工又一定會漏），改成每次都直接掃描目前有效職缺的「行政區」欄位，
# 自動長出「目前系統裡真的有出現過的地名」——同仁在 Notion 開新職缺、
# 填了新的行政區，系統下一次讀取職缺資料時就自動認得，不需要再改程式碼。
#
# 這整套跟職缺資料共用同一份 30 秒快取（active_jobs 本身就是
# fetch_jobs_data() 快取好的結果），純粹是對已經在記憶體裡的資料做字串
# 解析，不會多打一次 Notion API。
# ==========================================

# 台灣 22 個縣市的正式全名（含「市/縣」），用來把「台北市大安區」這種同仁
# 慣用的完整寫法，切成「縣市」跟「行政區」兩段。這份清單本身是全台灣的
# 縣市層級行政區劃，數量固定且早就穩定不變，跟「行政區」（可能有數百個、
# 職缺資料庫實際會用到哪些完全無法預先窮舉）是不同等級的兩件事，才適合
# 直接寫死維護。故意把「新竹市」排在「新竹縣」之前、「嘉義市」排在
# 「嘉義縣」之前——這兩組縣市剛好共用同一個核心地名，之後把核心字還原成
# 完整縣市名稱時，沿用 LOCATION_TO_COUNTY 既有「新竹→新竹市」「嘉義→嘉義市」
# 的簡化慣例（不特別區分市/縣），維持行為一致。
_COUNTY_FULL_NAMES = [
    "台北市", "新北市", "桃園市", "台中市", "台南市", "高雄市",
    "基隆市", "新竹市", "嘉義市",
    "新竹縣", "苗栗縣", "彰化縣", "南投縣", "雲林縣", "嘉義縣",
    "屏東縣", "宜蘭縣", "花蓮縣", "台東縣", "澎湖縣", "金門縣", "連江縣",
]
_COUNTY_CORE_TO_FULL = {}
for _full in _COUNTY_FULL_NAMES:
    _COUNTY_CORE_TO_FULL.setdefault(_full[:-1], _full)


def _strip_admin_suffix(s: str) -> str:
    """去掉行政區劃「市/縣/區/鄉/鎮」這類單位字尾，取得地名核心字。刻意只在
    去掉字尾後還剩下至少 2 個字時才去掉——避免「東區」「西區」這種本身只有
    2 個字的地名被去成單一個字（「東」「西」），變成極短、極容易在任何文字
    裡誤判命中的危險子字串。"""
    if len(s) >= 3 and s[-1] in ("市", "縣", "區", "鄉", "鎮"):
        return s[:-1]
    return s


def _split_district_token(token: str, fallback_county_core: str = "") -> tuple:
    """把「台北市大安區」這種同仁慣用的完整寫法，拆成 (縣市核心字, 行政區核心字)，
    例如 ("台北", "大安")。如果這個 token 本身沒有帶縣市前綴（同仁少數情況下
    可能只寫「大安區」），改用呼叫端傳進來的 fallback_county_core（通常來自
    這筆職缺自己的「縣市」欄位，且只在該欄位只填了單一縣市時才有意義）。"""
    token = token.strip()
    if not token:
        return "", ""
    for full in _COUNTY_FULL_NAMES:
        for variant in {full, full.replace("台", "臺")}:
            if token.startswith(variant):
                county_core = variant[:-1].replace("臺", "台")
                rest = token[len(variant):].strip()
                return county_core, _strip_admin_suffix(rest)
    return fallback_county_core, _strip_admin_suffix(token)


def build_district_county_index(active_jobs: list) -> dict:
    """掃描目前有效職缺的「行政區」（原始欄位，不是清理過的 _location_search_text）
    欄位，建立「行政區核心字 -> 這個核心字目前對應到哪些縣市」的對照表，例如
    {"佳里": {"台南"}, "東區": {"台中", "台南"}}。這份索引有兩個用途：
    1. 讓 extract_current_target_location() 能辨識出 LOCATION_CANDIDATES
       沒收錄、但職缺資料庫裡真實存在的行政區名稱（例如「佳里」「竹北」）。
    2. 標記出「同一個行政區名稱同時存在於多個縣市」的情況（例如「東區」台中、
       台南都有），呼叫端據此決定要不要保守跳過、不猜——寧可讓使用者的話
       落到既有的 AI 決策保底流程，也不要自己猜錯縣市答非所問。"""
    index = {}
    for job in active_jobs:
        district_field = str(job.get("行政區") or "").strip()
        if not district_field:
            continue
        county_field = str(job.get("縣市") or "").strip()
        county_tokens = [c.strip() for c in re.split(r'[,，、\s]+', county_field) if c.strip()]
        # 「縣市」欄位只填了單一縣市時，才能安全地當作 fallback（欄位裡列了
        # 好幾個縣市的情況下，沒辦法知道哪個行政區 token 對應哪一個縣市，
        # 不猜、留給行政區 token 自己帶的縣市前綴去判斷）。
        fallback_county_core = _strip_admin_suffix(county_tokens[0]) if len(county_tokens) == 1 else ""

        for token in re.split(r'[,，、\s]+', district_field):
            county_core, district_core = _split_district_token(token, fallback_county_core)
            if not district_core or not county_core:
                continue
            index.setdefault(district_core, set()).add(county_core)
    return index


def resolve_county_for_location(location: str, active_jobs: list = None) -> str:
    """查詢一個地名目前對應的縣市全名（例如「桃園市」），給同縣市退讓建議
    這幾個功能共用。優先查 LOCATION_TO_COUNTY 這份手動維護、已驗證過的既有
    對照表（新北/桃園的行政區，跟 台北/台中/台南 等縣市層級名稱），查不到
    時才退一步用 build_district_county_index() 從目前的職缺資料動態解析——
    只有在這個地名「明確只對應到一個縣市」時才回傳，同名跨縣市的情況
    （例如「東區」）保守回傳空字串，不猜。"""
    county = LOCATION_TO_COUNTY.get(location, "")
    if county:
        return county
    # 「台北市中山區」「嘉義縣」這種本身就帶完整縣市名稱的地點。
    for full in _COUNTY_FULL_NAMES:
        if location.startswith(full) or location.startswith(full.replace("台", "臺")):
            return full
    if not active_jobs:
        return ""
    counties = build_district_county_index(active_jobs).get(location, set())
    if len(counties) != 1:
        return ""
    return _COUNTY_CORE_TO_FULL.get(next(iter(counties)), "")


def find_county_level_alternative_jobs(category_matched_jobs: list, target_location: str, active_jobs: list = None) -> list:
    """求職者問的行政區完全沒有精準符合的職缺時，退一步找「同縣市」還有沒有
    符合類別/廠商條件的職缺——比照真人派遣專員自然會推薦鄰近地區類似工作的
    習慣。刻意做成確定性比對（只查 resolve_county_for_location() 這份人工
    維護＋職缺資料動態解析的對照表、比對結構化的 _location_search_text 欄位），
    不是交給 AI 自己判斷/推論，避免重蹈這幾天才修好的「AI 自行推論地區涵蓋
    範圍」覆轍。呼叫端仍必須誠實告知使用者「原本問的地區沒有，這是同縣市的
    其他地方」，不能包裝成原本地區也有符合的職缺。"""
    county = resolve_county_for_location(target_location, active_jobs)
    if not county:
        return []
    county_clean = clean_text_for_search(county)
    return [j for j in category_matched_jobs if county_clean in j.get("_location_search_text", "")]


def find_same_county_district_labels(same_county_jobs: list, target_location: str, active_jobs: list = None) -> list:
    """從已經確認「同縣市」的候選職缺裡，列出實際同縣市的行政區名稱清單，
    給 handlers/message_handler.py 的退讓建議回覆文字直接列出來用（例如
    「不過桃園市的蘆竹、龜山有相關職缺」），不要只講「同縣市還有相關職缺」
    這種空泛說法。

    使用者確認過這個情境刻意不設數量上限（同縣市地區數量通常不多），但這
    只影響這裡組出來的「回覆文字」，跟 services/flex_service.py 的
    format_clean_location()（卡片顯示用、職缺涵蓋 5 個以上行政區時會改用
    概括描述以免卡片爆版）完全是兩份獨立邏輯、互不影響，不要因為這裡不設
    上限就跟著放寬卡片那邊的顯示上限。

    刻意讀「行政區」這個原始欄位（不是 _location_search_text）：後者是
    clean_text_for_search() 處理過的比對專用字串，逗號等分隔符號會被直接
    刪除、行政區名稱會黏在一起，沒辦法拆回一個一個地名。"""
    county = resolve_county_for_location(target_location, active_jobs)
    if not county:
        return []

    labels = []
    seen = set()
    for job in same_county_jobs:
        district_field = str(job.get("行政區") or "")
        for token in re.split(r'[,，、\s]+', district_field):
            token = token.strip()
            if not token:
                continue
            # 這個行政區 token 是不是屬於目標縣市：沿用同一份 LOCATION_TO_COUNTY
            # 對照表，檢查 token 裡有沒有出現屬於這個縣市的地名關鍵字（例如
            # 「桃園市八德區」裡的「八德」對應到「桃園市」）。
            if not any(name in token and c == county for name, c in LOCATION_TO_COUNTY.items()):
                continue
            # 顯示用的地區名稱去掉重複的縣市前綴（例如「桃園市八德區」只顯示
            # 「八德區」），跟卡片顯示（format_clean_location）用的是同一種
            # 去重前綴邏輯，維持兩邊呈現風格一致。
            label = token
            for variant in {county, county.replace("台", "臺"), county.replace("臺", "台")}:
                if variant and label.startswith(variant):
                    label = label[len(variant):].strip() or label
                    break
            if label and label not in seen:
                seen.add(label)
                labels.append(label)
    return labels


def extract_current_target_location(raw_msg: str, history_text: str = "", active_jobs: list = None) -> str:
    """從使用者最新訊息擷取鎖定地區（避免被對話歷史中的範例字詞干擾，並跳過被否定的地名）[cite: 1]

    分三輪、精準度由高到低檢查，刻意不是單純「查完 LOCATION_CANDIDATES 全部
    查不到才查動態索引」：LOCATION_CANDIDATES 裡混雜了「行政區層級」（板橋、
    八德…）跟「縣市層級」（新竹、台南…）兩種詞，如果整份清單一起查、縣市
    層級的詞排在後面但一樣會被查到，會導致「新竹縣 竹北沒缺嗎」這種訊息被
    清單裡的「新竹」搶先命中，動態索引裡更精確的「竹北」根本沒機會被檢查到
    （見 HANDOFF.md 竹北案例）。所以先查兩份「行政區層級」的來源（手動維護的
    LOCATION_CANDIDATES 子集，之後才是動態解析的 build_district_county_index()），
    只有兩者都沒查到時，才退回「縣市層級」的詞。同一個行政區名稱同時存在於
    多個縣市時（例如「東區」台中、台南都有），動態索引保守跳過不猜，讓這句話
    落到既有的 AI 決策保底流程，不要冒著答非所問的風險猜一個。"""
    for loc in LOCATION_CANDIDATES:
        if loc in _LOCATION_COUNTY_LEVEL_NAMES:
            continue
        if loc in raw_msg and not _keyword_is_negated(raw_msg, loc):
            # 「桃園區」是桃園市底下的一個區，不是整個桃園市。
            if loc == "桃園" and "桃園區" in raw_msg:
                return "桃園區"
            return loc.replace("臺", "台")

    if active_jobs:
        district_index = build_district_county_index(active_jobs)
        # 依地名長度由長到短檢查，避免短地名先命中、蓋掉更精確的地名。
        for district_core in sorted(district_index.keys(), key=len, reverse=True):
            if district_core not in raw_msg or _keyword_is_negated(raw_msg, district_core):
                continue
            counties = district_index[district_core]
            if len(counties) == 1:
                return district_core
            # 同一個區名在好幾個縣市都有（例如台北市、基隆市都有中山區）時，
            # 原本一律跳過，「台北市中山區」就退回成「整個台北」。這句話本身
            # 有講是哪個縣市時，組成「台北市中山區」精準比對。
            qualified = _qualify_ambiguous_district(raw_msg, district_core, counties)
            if qualified:
                return qualified

    for loc in LOCATION_CANDIDATES:
        if loc not in _LOCATION_COUNTY_LEVEL_NAMES:
            continue
        if loc in raw_msg and not _keyword_is_negated(raw_msg, loc):
            # 新竹、嘉義的縣跟市是兩個不同的縣市，求職者有講清楚時要分開，
            # 原本「嘉義縣」會連嘉義市的職缺一起列出。
            if loc in ("新竹", "嘉義"):
                for full in (f"{loc}縣", f"{loc}市"):
                    if full in raw_msg:
                        return full
            return loc.replace("臺", "台")

    return ""


def _qualify_ambiguous_district(raw_msg: str, district_core: str, counties: set) -> str:
    mentioned = [c for c in counties if c in raw_msg or c.replace("台", "臺") in raw_msg]
    if len(mentioned) != 1:
        return ""
    county_full = _COUNTY_CORE_TO_FULL.get(mentioned[0], "")
    if not county_full:
        return ""
    if district_core[-1] in ("區", "鄉", "鎮", "市"):
        return f"{county_full}{district_core}"
    for suffix in ("區", "鄉", "鎮", "市"):
        if f"{district_core}{suffix}" in raw_msg:
            return f"{county_full}{district_core}{suffix}"
    return f"{county_full}{district_core}區"


def detect_negated_location(raw_msg: str, active_jobs: list = None) -> str:
    """偵測使用者是否明確表示排除某個地區（例如「不要新莊了」），回傳被排除的地名，
    沒有則回傳空字串。優先順序跟 extract_current_target_location() 一致，理由同上。"""
    for loc in LOCATION_CANDIDATES:
        if loc in _LOCATION_COUNTY_LEVEL_NAMES:
            continue
        if loc in raw_msg and _keyword_is_negated(raw_msg, loc):
            return loc.replace("臺", "台")

    if active_jobs:
        district_index = build_district_county_index(active_jobs)
        for district_core in sorted(district_index.keys(), key=len, reverse=True):
            if len(district_index[district_core]) != 1:
                continue
            if district_core in raw_msg and _keyword_is_negated(raw_msg, district_core):
                return district_core

    for loc in LOCATION_CANDIDATES:
        if loc not in _LOCATION_COUNTY_LEVEL_NAMES:
            continue
        if loc in raw_msg and _keyword_is_negated(raw_msg, loc):
            return loc.replace("臺", "台")

    return ""


# ==========================================
# 福利/配備關鍵字直達攔截：跟行政區動態解析（見上方 build_district_county_index）
# 同一種精神——與其把「公司車」這類福利關鍵字寫死在程式碼裡，不如直接從
# Notion 職缺資料庫的「福利」欄位（同仁自行維護）動態長出關鍵字清單。
#
# 背景：有些求職者問的不是地區/類別/廠商，而是「這份工作有沒有某項福利/
# 配備」（例如「有公司車嗎」「我要公司車的工作」），這種問法通常很直接對應
# 到某幾筆有勾選該福利的職缺，適合做成確定性攔截，不用交給 AI 自己從候選
# 職缺的自由文字裡猜（這個 session 已經踩過好幾次「AI 即使看到正確資料還是
# 判斷錯誤」的坑）。跟這份資料共用職缺資料本來就有的 30 秒快取，不會多打
# 一次 Notion API。
# ==========================================

def build_benefit_keyword_index(active_jobs: list) -> dict:
    """掃描目前有效職缺的「福利」欄位，建立「福利關鍵字 -> 有這項福利的職缺
    清單」的對照表，例如 {"公司車": [job1, job3]}。同仁在 Notion 幫職缺勾選/
    填上福利關鍵字，系統下一次讀取職缺資料就自動認得，不需要改程式碼。"""
    index = {}
    for job in active_jobs:
        for token in _job_benefit_tokens(job):
            index.setdefault(token, []).append(job)
    return index


def _job_benefit_tokens(job: dict) -> list:
    return [t.strip() for t in re.split(r'[,，、\s]+', str(job.get("福利") or "")) if t.strip()]


def find_benefit_matched_jobs(raw_msg: str, active_jobs: list) -> tuple:
    """從使用者訊息裡找出有沒有命中目前職缺資料庫「福利」欄位收錄的關鍵字，
    命中就回傳 (關鍵字, 有這項福利的職缺清單)；沒有 active_jobs 或完全沒
    命中則回傳 ("", [])。刻意依關鍵字長度由長到短檢查，避免短關鍵字先命中
    蓋掉更精確的關鍵字（跟 extract_current_target_location() 處理行政區的
    方式一致）。

    安全性檢查發現兩個防呆漏掉的地方，這裡補上：
    1. 排除被否定的關鍵字（例如「不要有公司車的工作」，"公司車" 雖然出現在
       訊息裡，但使用者明確表示不要，不該當成正向意圖直接攔截推薦）。
    2. 排除長度小於 2 的關鍵字——跟 _strip_admin_suffix() 保留至少 2 個字
       的防呆原則一致，避免同仁不小心在「福利」欄位填了單一個字（例如
       「餐」），變成極短、極容易在任何無關句子裡誤判命中的危險子字串
       （例如求職者問「想找餐飲的工作」，這句話跟福利完全無關）。"""
    if not active_jobs:
        return "", []
    benefit_index = build_benefit_keyword_index(active_jobs)
    for keyword in sorted(benefit_index.keys(), key=len, reverse=True):
        if len(keyword) < 2:
            continue
        if keyword in raw_msg and not _keyword_is_negated(raw_msg, keyword):
            return keyword, benefit_index[keyword]
    return "", []


# ==========================================
# 領薪方式關鍵字直達攔截：跟福利關鍵字直達攔截（見上方 find_benefit_matched_jobs）
# 同樣的精神——求職者問到發薪方式時，一律直接比對結構化欄位、完全不交給
# AI 判斷。但刻意不是動態掃描 Notion 資料庫「長」出關鍵字清單：領薪方式
# （日領/週領/月領/年薪/現金/匯款…）是求職者最在意、最不能出錯的資訊，
# 就算系統裡剛好目前沒有任何職缺勾選某個發薪方式（例如目前完全沒有日領
# 職缺），求職者問「有沒有日領的工作」時，也必須明確誠實告知「目前沒
# 有」，不能因為關鍵字清單裡沒收錄這個詞、就讓這句話落到 AI 決策保底
# 流程——AI 判斷發薪方式時曾經把職缺行銷文案（「精華亮點」）裡的「薪資
# 當日結算」（意思是薪資用當天紀錄去計算，不代表當天真的撥款）誤判成
# 「日領」，即使當時候選職缺清單裡已經老實列出「領薪方式:週領,匯款,
# 月領,現金」這個正確的結構化欄位資訊給 AI 看，AI 還是判斷錯（見
# HANDOFF.md 日領誤判案例）。所以這裡手動維護一份固定的發薪方式同義詞
# 清單（跟 SHIFT_SYNONYMS 班別同義詞是同一種寫法），求職者問到任何一種
# 發薪方式時，一律改成直接查表比對結構化的「領薪方式」欄位，完全不看
# 任何自由文字欄位，杜絕 AI 自行從行銷文案延伸推論的風險。
# ==========================================
PAY_METHOD_SYNONYMS = {
    "日領": ["日領", "當日領", "當天領", "日結"],
    "週領": ["週領", "周領"],
    "雙週領": ["雙週領", "雙周領"],
    "月領": ["月領"],
    "年薪": ["年薪", "年領"],
    "現金": ["現金"],
    "匯款": ["匯款", "轉帳"],
    # Notion「領薪方式」欄位真實存在的選項，原本沒收錄，問到會落到 AI。
    "街口": ["街口"],
    "預支": ["預支", "借支"],
}


def detect_pay_method_labels(text: str, negated: bool = False) -> list:
    """「雙週領」裡面包含「週領」：被較長關鍵字涵蓋的不重複算。"""
    return _find_label_mentions(text, PAY_METHOD_SYNONYMS.items(), negated)


def detect_pay_method_label(text: str) -> str:
    """從文字中判斷求職者指定的發薪方式，跳過被否定的詞（例如「不要日領的」）。"""
    labels = detect_pay_method_labels(text)
    return labels[0] if labels else ""


def _job_pay_method_tokens(job: dict) -> set:
    return {clean_text_for_search(t) for t in re.split(r'[,，、\s]+', str(job.get("領薪方式") or "")) if t.strip()}


def find_pay_method_matched_jobs(raw_msg: str, active_jobs: list) -> tuple:
    """依 detect_pay_method_label() 判斷出的發薪方式，直接比對職缺結構化的
    「領薪方式」欄位（同仁在 Notion 勾選的官方資料），完全不看任何自由文字
    欄位。回傳 (發薪方式標籤, 符合的職缺清單)；沒有命中發薪方式關鍵字則
    回傳 ("", [])——代表這句話跟發薪方式無關，交由既有流程判斷。命中關鍵字
    但完全沒有職缺符合（結構化欄位裡沒有任何職缺勾選這個發薪方式）時，
    呼叫端要老實回覆「目前沒有」，不能落到 AI 決策保底流程重蹈覆轍。"""
    if not active_jobs:
        return "", []
    label = detect_pay_method_label(raw_msg)
    if not label:
        return "", []
    # 逐一比對欄位裡的每個選項，不用子字串：避免「週領」命中「雙週領」。
    return label, filter_jobs_by_pay_label(active_jobs, label)


# 班別同義詞清單：獨立成模組常數，讓 extract_shift_preference 跟 _tokenize_search_terms
# 共用同一份來源，避免兩處各自維護、覆蓋範圍不一致。
SHIFT_SYNONYMS = {
    "早班": ["早班", "早上班", "白班", "日班", "常日班", "正常班", "全日班"],
    "晚班": ["晚班", "小夜", "中班", "下午班", "打烊班", "打烊"],
    "大夜班": ["大夜", "夜班", "大夜班", "深夜班", "通宵"],
    "假日班": ["假日班", "假日", "週末班", "周休兼職", "假日兼職"],
    "兼職/工讀": ["兼職", "打工", "工讀", "pt", "短期工讀", "學生工讀", "兼差"],
    "輪班": ["輪班", "四班二輪", "二班二輪", "輪三班", "三班輪", "早晚輪班"],
    "彈性排班": ["彈性排班", "自由排班", "排班彈性", "時段彈性", "不限時段"]
}

# 休假制度分類。順序有意義：extract_leave_preference() 一次只回傳第一個
# 命中的分類（給職缺欄位的單一值分類用），「四三輪休」要排在「排休」
# （含「輪休」）前面。做四休二跟做二休二是不同的班表（使用者 2026-09-23
# 決定分開）。刻意不收「休假日」：「你們休假日也要上班嗎」是在問問題，
# 不是要找週休二日的職缺。
LEAVE_BUCKETS = [
    ("週休二日", ["週休", "周休", "見紅休", "固定休六日", "休六日", "休雙休"]),
    ("做四休二", ["四休二", "4休2", "作四休二", "做四休二", "做4休2", "4天休2天", "四天休二天", "四天休兩天"]),
    ("做二休二", ["做二休二", "作二休二", "二休二", "2休2", "做2休2", "四班二輪", "做兩休兩", "兩天休兩天", "2天休2天"]),
    ("做三休三", ["做三休三", "作三休三", "三休三", "3休3", "做3休3"]),
    ("四三輪休", ["四三輪休"]),
    ("休日一", ["固定休日一", "休日一"]),
    ("自由報班", ["自由報班"]),
    ("排休", ["排休", "輪休", "排班休", "月休八天", "月休8天"]),
]


def _span_is_negated(clean: str, start: int) -> bool:
    window = _CLAUSE_BREAK_RE.split(clean[max(0, start - 6):start])[-1]
    return any(trigger in window for trigger in NEGATION_TRIGGERS)


def _find_label_mentions(text: str, label_keywords, negated: bool = False) -> list:
    """回傳這段文字提到的所有標籤（依出現順序）。一段文字被較長的關鍵字
    涵蓋時只算較長的那個：「雙週領」不會同時算成週領、「假日班」不會同時
    算成早班（「日班」）、「四三輪休」不會同時算成排休（「輪休」）。
    否定詞只看每個關鍵字自己所在的子句：「不要夜班，日領的就好」的日領
    沒有被否定。negated=True 時反過來只回傳被否定的標籤。"""
    # 先依標點切成子句再各自清理：clean_text_for_search 會把逗號吃掉，
    # 「不要夜班，日領的就好」直接清理會讓日領前面 6 個字碰到「不要」。
    clean = "|".join(clean_text_for_search(part) for part in _CLAUSE_PUNCT_RE.split(str(text or "")))
    hits = []
    for label, keywords in label_keywords:
        for keyword in keywords:
            kw = clean_text_for_search(keyword).lower()
            if not kw:
                continue
            start = clean.find(kw)
            while start != -1:
                hits.append((start, start + len(kw), label))
                start = clean.find(kw, start + 1)
    kept = [
        h for h in hits
        if not any(o[0] <= h[0] and h[1] <= o[1] and (o[1] - o[0]) > (h[1] - h[0]) for o in hits)
    ]
    labels = []
    for start, _, label in sorted(kept):
        if _span_is_negated(clean, start) == negated and label not in labels:
            labels.append(label)
    return labels


def extract_shift_labels(text: str, negated: bool = False) -> list:
    return _find_label_mentions(text, SHIFT_SYNONYMS.items(), negated)


def extract_shift_preference(text: str) -> str:
    """從文字中判斷求職者偏好的時段/班別（支援同義詞與工時縮寫）[cite: 1]"""
    labels = extract_shift_labels(text)
    return labels[0] if labels else ""


def job_shift_labels(job: dict) -> set:
    labels = set()
    for token in re.split(r'[,，、\s()（）]+', str(job.get("班別") or "")):
        if token.strip():
            labels.update(extract_shift_labels(token))
    if "兼職" in str(job.get("全/兼職") or ""):
        labels.add("兼職/工讀")
    return labels


def extract_leave_preference(text: str) -> str:
    """從文字中判斷求職者偏好的休假制度（支援多種休假模式）[cite: 1]"""
    clean = clean_text_for_search(text)
    for label, keywords in LEAVE_BUCKETS:
        if any(clean_text_for_search(k) in clean for k in keywords):
            return label
    return ""


def extract_leave_labels(text: str, negated: bool = False) -> list:
    return _find_label_mentions(text, LEAVE_BUCKETS, negated)


def _classify_all_leave_labels(text: str) -> set:
    """一筆職缺的「休假方式」欄位可能同時填了兩種制度（例如同仁實際填過
    「做二休二,排休」，代表這筆職缺依班別不同分別適用這兩種休假制度）。
    extract_leave_preference() 一次只會依優先順序判斷出「第一個命中」的
    一種分類，直接對整串欄位值呼叫一次，「排休」就會因為「週休/四休二」
    的關鍵字檢查排在前面且先命中「做二休二」而完全被忽略，導致求職者問
    「有排休的工作嗎」時，這筆其實也真的有排休的職缺永遠比對不到。這裡
    改成先用逗號/頓號/空白拆開欄位裡的每一段分別判斷，再加上對整串欄位值
    判斷一次當保險（涵蓋「做二休二」這種本身就帶有頓號可能被拆壞的最小
    單位寫法），回傳「這筆職缺實際涵蓋的所有休假制度分類」。"""
    labels = set()
    for token in re.split(r'[,，、\s]+', str(text or "")):
        token = token.strip()
        if not token:
            continue
        label = extract_leave_preference(token)
        if label:
            labels.add(label)
    whole_label = extract_leave_preference(str(text or ""))
    if whole_label:
        labels.add(whole_label)
    return labels


def find_leave_matched_jobs(raw_msg: str, active_jobs: list) -> tuple:
    """依 extract_leave_preference() 判斷出的休假制度，直接比對職缺結構化的
    「休假方式」欄位，完全不看任何自由文字欄位。跟 find_pay_method_matched_jobs()
    是同一種寫法：刻意重複使用 extract_leave_preference() 這同一套分類邏輯，
    同時套用在求職者的話跟職缺自己的「休假方式」欄位值上——確保兩邊都歸類到
    同一個標準用語才算符合，不需要另外維護一份對照表。回傳 (休假制度標籤,
    符合的職缺清單)；沒有命中休假關鍵字則回傳 ("", [])。"""
    if not active_jobs:
        return "", []
    label = extract_leave_preference(raw_msg)
    if not label:
        return "", []
    return label, filter_jobs_by_leave_label(active_jobs, label)


# 下面「依標籤篩選」的函式，給跨輪記住的條件用：條件是上一輪講的，這一句
# 話裡已經沒有那個詞，不能再從訊息重新判斷。標籤可以是「日領|週領」這種
# 用「|」分隔的多個值（求職者講「日領或週領都可以」），符合其中一個就算。
def filter_jobs_by_leave_label(jobs: list, label: str) -> list:
    wanted = set(label.split("|"))
    return [j for j in jobs if wanted & _classify_all_leave_labels(j.get("休假方式") or "")]


def filter_jobs_by_pay_label(jobs: list, label: str) -> list:
    wanted = {clean_text_for_search(part) for part in label.split("|")}
    return [j for j in jobs if wanted & _job_pay_method_tokens(j)]


def filter_jobs_by_benefit_label(jobs: list, label: str) -> list:
    wanted = set(label.split("|"))
    return [j for j in jobs if wanted & set(_job_benefit_tokens(j))]


def filter_jobs_by_shift_label(jobs: list, label: str) -> list:
    wanted = set(label.split("|"))
    return [j for j in jobs if wanted & job_shift_labels(j)]


def detect_benefit_labels(raw_msg: str, active_jobs: list, negated: bool = False) -> list:
    """福利關鍵字清單從職缺資料動態長出來（見 build_benefit_keyword_index）。"""
    keywords = [(k, [k]) for k in build_benefit_keyword_index(active_jobs or []) if len(k) >= 2]
    return _find_label_mentions(raw_msg, keywords, negated)


# 「都可以」要清掉哪一項（使用者 2026-09-23 決定：只清句子裡提到的那一項，
# 沒講清楚是哪一項時只清類型/廠商、保留地區）。例如「班別都可以啦」只清
# 班別，不能像原本一樣連地區、類型、廠商一起清掉。「條件都不限」「不限
# 條件」這種講法清掉休假/發薪/福利/班別。
_SCOPED_BROADEN_DIMENSION_WORDS = {
    "location": ["地區", "地點", "區域", "哪裡"],
    "category": ["類型", "類別", "職種", "工作內容", "什麼工作", "什麼職缺"],
    "brand": ["廠商", "公司", "品牌"],
    "shift": ["班別", "時段", "早晚班", "上班時間"],
    "leave": ["休假方式", "休假制度", "休假"],
    "pay": ["發薪方式", "領薪方式", "發薪", "領薪"],
    "benefit": ["福利"],
    "secondary_all": ["其他條件", "條件"],
}
_BROADEN_SUFFIXES = ["都可以", "都行", "都好", "不限", "都不限", "隨便", "沒差", "都沒差", "無所謂", "都無所謂", "都ok"]


def detect_scoped_broaden_dimensions(clean_input: str) -> set:
    dims = set()
    for dim, words in _SCOPED_BROADEN_DIMENSION_WORDS.items():
        for word in words:
            if f"不限{word}" in clean_input or any(f"{word}{suffix}" in clean_input for suffix in _BROADEN_SUFFIXES):
                dims.add(dim)
                break
    if "secondary_all" in dims:
        dims.discard("secondary_all")
        dims.update({"leave", "pay", "benefit", "shift"})
    return dims


# 「不一定要週休」「日領沒有就算了」「不需要交通車」這種「我不一定要這個
# 條件」的說法。原本否定詞清單沒收，條件反而又被記一次、一字不差地重問。
# 這類說法依「不確定就讓求職者選」的原則（使用者 2026-09-23），先問要不要
# 拿掉這個條件，不自己猜。
_RELAX_PREFIXES = ["不一定要", "不一定", "不用", "不需要", "不必"]
_RELAX_SUFFIXES = [
    "不一定", "沒有也沒關係", "沒有也沒差", "沒有也可以", "沒有也行", "沒有就算了", "就算了",
    "不用也行", "不用也可以", "不需要", "不用",
]


def detect_relax_dimensions(clean_input: str, benefit_keywords=()) -> set:
    words = {
        "leave": ["休假方式", "休假"] + [k for _, kws in LEAVE_BUCKETS for k in kws],
        "pay": ["發薪方式", "領薪方式", "發薪", "領薪"] + [k for kws in PAY_METHOD_SYNONYMS.values() for k in kws],
        "benefit": ["福利"] + list(benefit_keywords),
        "shift": ["班別"] + [k for kws in SHIFT_SYNONYMS.values() for k in kws],
    }
    dims = set()
    for dim, dim_words in words.items():
        for word in dim_words:
            w = clean_text_for_search(word).lower()
            if not w:
                continue
            if (
                any(f"{p}{w}" in clean_input for p in _RELAX_PREFIXES)
                or any(f"{w}{s}" in clean_input for s in _RELAX_SUFFIXES)
                or f"沒有{w}也" in clean_input
            ):
                dims.add(dim)
                break
    return dims


# 同一句話可能是「在找工作」也可能是「在問規定」（「可以預支薪水嗎」「交通車
# 有哪些站點」「週領是禮拜幾發」）。原本只要講到就記成篩選條件、直接回職缺
# 卡片。依使用者 2026-09-23 定的原則：確定是在找工作才直接篩；分不出來就
# 回傳 "question"，由呼叫端給按鈕讓求職者自己選。INFO_INTENT_PREFIX 是
# 「了解規定」那顆按鈕送回來的固定開頭，看到就當成單純問問題、不記條件。
INFO_INTENT_PREFIX = "想了解"
_JOB_WORDS = ["工作", "職缺", "缺人", "有缺", "徵人", "在徵", "應徵"]
_QUESTION_MARKERS = [
    "怎麼", "如何", "哪些", "哪裡", "哪幾", "幾點", "幾號", "幾天", "禮拜幾", "星期幾",
    "是什麼", "什麼時候", "多少", "規定", "流程", "站點", "還是", "多久", "為什麼",
]
_DEMAND_WORDS = ["有沒有", "我要", "想要", "只要", "要找", "想找", "就好"]


def classify_condition_utterance(raw_msg: str) -> str:
    text = raw_msg.strip()
    if text.startswith(INFO_INTENT_PREFIX):
        return "info"
    clean = clean_text_for_search(text)
    if any(w in clean for w in _JOB_WORDS):
        return "demand"
    if any(m in clean for m in _QUESTION_MARKERS):
        return "question"
    if re.search(r"有.{0,10}[嗎呢]", text) or any(w in clean for w in _DEMAND_WORDS):
        return "demand"
    if text.endswith(("嗎", "?", "？")):
        return "question"
    return "demand"


def text_mentions_pay_label(text: str, label: str) -> bool:
    clean = clean_text_for_search(text)
    return any(clean_text_for_search(syn) in clean for syn in PAY_METHOD_SYNONYMS.get(label, [label]))

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
    # 「服飾」「專櫃」是門市（服飾店、百貨專櫃），不是餐飲：原本放在餐飲/
    # 服務，問「服飾店」會推餐廳內場。
    "門市": ["門市", "店員", "門市人員", "蝦皮門市", "智取店", "店到店", "櫃檯", "專櫃", "服飾"],
    "製造/作業員": ["製造", "製造業", "作業員", "技術員", "產線", "組裝", "機台", "半導體", "工廠", "科技廠", "電子廠", "品管", "包裝員"],
    "理貨/倉儲": ["理貨", "揀貨", "倉管", "包裝", "倉儲", "倉庫", "物流", "堆高機", "貼標"],
    # 刻意不收單獨的「服務」：「有交通車接送服務嗎」會被誤判成要找餐飲類，
    # 把原本鎖定的作業員/理貨類別換掉。
    "餐飲/服務": ["餐飲", "服務員", "服務生", "服務業", "服務類", "餐廳", "廚房", "內場", "外場", "洗碗", "助手"],
}


def detect_category_label(clean_input: str) -> str:
    """從文字中判斷求職者偏好的工作類別（完整支援製造業與多元工種，並跳過被否定的類別）[cite: 1]

    同一個類別可能有多個同義關鍵字（例如「外送」類別底下同時有「外送」跟
    「司機」），要逐一檢查每個關鍵字、只要其中任一個有出現且沒被否定就算
    命中；不能只挑第一個出現的關鍵字來判斷，否則「不要外送，我想要司機的
    工作」這種句子會因為第一個匹配到的「外送」被否定，就整個類別判斷成
    沒命中，白白漏掉後面「司機」這個明確的正向訊號（詳見 detect_negated_category()
    的對稱寫法，這裡原本沒有跟它一致）。"""
    for label, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw in clean_input and not _keyword_is_negated(clean_input, kw):
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
        "門市": ["門市", "店員", "門市人員", "店到店", "智取店", "櫃檯", "專櫃", "服飾"],
        # 不收「設備」「包裝」：「設備人員」（蝦皮內勤的維修外勤）跟「電商物流
        # 理貨包裝員」會被誤判成作業員，蝦皮類型反問因此多出一個點下去只看到
        # 設備人員的「蝦皮製造/作業員」選項。
        "製造/作業員": ["製造", "作業員", "技術員", "產線", "組裝", "機台", "半導體", "工廠", "科技", "電子", "品檢", "品保", "品管", "檢驗"],
        "理貨/倉儲": ["理貨", "揀貨", "倉管", "包裝", "倉儲", "倉庫", "物流", "堆高機", "進貨", "出貨", "搬運"],
        # 不收單獨的「服務」：「行業別＝服務業」的門市職缺會被誤判成餐飲。
        "餐飲/服務": ["餐飲", "服務員", "服務生", "服務人員", "廚房", "內場", "外場", "洗碗", "助手"]
    }
    return mapping.get(category_label, [])

def _vendor_core_name(vendor_name: str) -> str:
    """從系統廠商名稱裡取出核心可辨識名稱，去掉同仁為了內部辨識加註的後綴
    （例如「錢都(代招)」→「錢都」、「美光(台中)-台南廠」→「美光」）。同仁常會在
    正式名稱後面用括號、連字號、加號加註分店/職務/代招等備註方便管理，
    但求職者不會把這些內部備註打進訊息裡，只比對完整名稱會永遠對不上。"""
    core = re.split(r'[（(_\-+]', vendor_name)[0].strip()
    return core or vendor_name


# 常見知名廠商白名單：獨立成模組常數，讓 detect_brand_label 跟其他需要判斷
# 「這句話有沒有提到已知品牌」的地方（例如統一意圖判斷）共用同一份清單，
# 避免各處各自維護、覆蓋範圍不一致。
KNOWN_BRANDS = {
    "蝦皮": ["蝦皮", "spx"],
    "momo": ["momo", "富邦", "富昇"],
    # 「coupung」是 Notion 上真實存在的錯字廠商名稱，先收錄讓它比對得到。
    "Coupang": ["coupang", "酷澎", "coupung"],
    # 系統廠商名稱是「PChome理貨」，沒有括號可切出「PChome」，求職者打
    # 「PChome」永遠比對不到。
    "PChome": ["pchome", "網家"],
    # 「Uber」實測回報：Notion 上真實的系統廠商名稱是「UBER DRIECT」（同仁
    # 打字時把 DIRECT 打成 DRIECT）、「Uber(COSTCO)」、「uber 站所小幫手」
    # 這幾種各自不同的完整寫法，求職者只會打最簡短的「Uber」，
    # detect_brand_label() 原本只有「完整廠商名稱比對」或「核心名稱比對」
    # （用括號/底線/連字號切開來取前段）這兩種比對方式，「UBER DRIECT」
    # 沒有括號可以切、核心名稱等於完整字串，永遠對不上單純的「Uber」，
    # 導致外送分支的廠商窄化形同沒生效、混進蝦皮的外送職缺。這裡補上
    # 白名單，求職者打「Uber」就能直接命中所有 Uber 系列的職缺。
    "Uber": ["uber"],
    "美光": ["美光", "micron"],
    "欣興": ["欣興"],
    "台積電": ["台積電", "tsmc"],
    "宏達電": ["宏達電", "htc"]
}


def has_recognizable_category_or_brand_keyword(clean_input: str) -> bool:
    """統一意圖判斷來源：檢查文字是否包含任何已知工作類別（CATEGORY_KEYWORDS）
    或廠商（KNOWN_BRANDS）關鍵字。取代原本在 message_handler.py 另外維護、
    覆蓋範圍不完整的手動白名單（例如漏掉「理貨」「餐飲」）。"""
    all_category_keywords = {kw for keywords in CATEGORY_KEYWORDS.values() for kw in keywords}
    all_brand_keywords = {kw for keywords in KNOWN_BRANDS.values() for kw in keywords}
    return any(k in clean_input for k in all_category_keywords) or any(k in clean_input for k in all_brand_keywords)


def detect_brand_label(text: str, active_jobs: list = None) -> str:
    """動態從訊息辨識求職者詢問之特定廠商或品牌（嚴格排除行業別與疑問詞）[cite: 1]"""
    normalized = clean_text_for_search(text)

    # 0. 先認知名品牌家族。實測：求職者點「蝦皮外送」「蝦皮門市」按鈕，原本
    #    會先比對到系統廠商名稱「蝦皮外送(支援)」「蝦皮門市」，廠商被鎖成
    #    「蝦皮外送」「蝦皮門市」這種帶類別字的名稱，下一句換問「那理貨呢」
    #    就篩不到任何職缺。而且比對結果取決於 Notion 職缺的排列順序。廠商
    #    一律記品牌本身（蝦皮），類別交給類別槽位管。
    for brand_key, synonyms in KNOWN_BRANDS.items():
        if any(syn in normalized and not _keyword_is_negated(normalized, syn) for syn in synonyms):
            return brand_key

    # 1. 優先精準比對 Notion 資料庫中現有的所有系統廠商名稱（含核心名稱比對，
    #    避免同仁加註的內部後綴導致完整名稱永遠比對不到）[cite: 1]
    #    無論哪種比對方式命中，一律回傳「核心名稱」而不是那一筆職缺的完整廠商名稱：
    #    像「美光(桃園)」「美光(台中)」「美光(台南)」這種同一品牌、不同地區各自登記
    #    一筆的情況，如果回傳的是命中的那一筆完整名稱（例如「美光(桃園)」），brand
    #    槽位會被鎖在特定地區的寫法，而後續 build_ai_job_candidates／_score_job_for_ai
    #    的品牌篩選/評分都是拿 brand 去對已清理過括號的 _search_text 做字串比對，
    #    帶括號的完整名稱幾乎永遠比對不到，導致品牌保底機制形同虛設。回傳核心名稱
    #    才能讓同一品牌旗下所有地區的職缺都能被正確篩選/加分到。
    #    同時命中好幾個廠商名稱時，取比對到的字串最長的那個（最精確），不取
    #    Notion 列表裡剛好排在前面的那個。
    if active_jobs:
        best_name, best_len = "", 0
        for j in active_jobs:
            v_name = str(j.get("系統廠商名稱") or "").strip()
            if v_name and len(v_name) >= 2:
                v_core_name = _vendor_core_name(v_name)
                v_clean = clean_text_for_search(v_name)
                v_core_clean = clean_text_for_search(v_core_name)
                for candidate in (v_clean, v_core_clean):
                    if candidate and len(candidate) >= 2 and candidate in normalized and len(candidate) > best_len:
                        best_name, best_len = v_core_name, len(candidate)
        if best_name:
            return best_name

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

def _job_extended_category_text(job: dict) -> str:
    # 跟 _job_extended_search_text 不同：這裡刻意不放「工作內容(對外)」這種
    # 行銷用自由文字欄位。自由文字裡常會出現「各區門市據點」這種泛用說法
    # （意思是「全台多處工作地點」），不代表這個職缺的職務類別真的是「門市」。
    # 也刻意不放「職缺名稱」（內部名稱）——這是同仁自己取的行政/部門命名
    # 慣例，可能帶到「門市」「智取店」「店到店」這類字眼，只是因為這個職缺
    # 是「支援門市營運的內勤職位」（例如職缺名稱「蝦皮內勤(北北基宜)門市
    # 裝潢工程外勤專員」，職務類別其實是「設備人員」），不代表職務本身真的
    # 是門市類別。職務類別的寬鬆比對只能信任真正結構化、對外一致的欄位
    # （職缺名稱(對外)／職務類別／行業別），跟地區比對只信任「行政區」欄位、
    # 不信任自由文字地址的原則一致。
    fields = [
        job.get("職缺名稱(對外)", ""),
        job.get("職務類別", ""),
        job.get("行業別", ""),
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
    if brand_label in KNOWN_BRANDS:
        return any(k in text for k in KNOWN_BRANDS[brand_label])
    return clean_text_for_search(brand_label) in text


def job_matches_brand(job: dict, brand_label: str) -> bool:
    """只比對職缺自己的廠商/職缺名稱欄位，不比對含地區跟行銷文案的
    _search_text。實測：廠商「新興(代招)」在新北五股，但 _search_text 比對
    會把地址在高雄市新興區的其他廠商職缺也算成「新興」。"""
    if not brand_label:
        return True
    fields = [job.get("系統廠商名稱"), job.get("職缺名稱"), job.get("職缺名稱(對外)"), job.get("_internal_title")]
    brand_text = " ".join(str(f) for f in fields if f)
    if not brand_text.strip():
        brand_text = job.get("_search_text", "")
    return _brand_matches_text(brand_text, brand_label)


def detect_negated_brand(text: str) -> str:
    """「不要蝦皮了」這種明確排除知名品牌的說法，回傳被排除的品牌。"""
    normalized = clean_text_for_search(text)
    for brand_key, synonyms in KNOWN_BRANDS.items():
        if any(syn in normalized and _keyword_is_negated(normalized, syn) for syn in synonyms):
            return brand_key
    return ""

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
    # 職務類別的嚴格比對絕對不能用含「職缺名稱」（內部名稱）的 primary_text——
    # 那是同仁自己取的行政/部門命名慣例，可能帶到「門市」「智取店」「店到店」
    # 這類字眼，只是因為這個職缺是「支援門市營運的內勤職位」（例如職缺名稱
    # 「蝦皮內勤(北北基宜)門市裝潢工程外勤專員」，職務類別其實是「設備
    # 人員」），不代表職務本身真的是門市類別。廠商比對不受影響，職缺名稱
    # 通常確實會帶到真正的廠商名稱，繼續信任 primary_text。
    # 「職務類別」有填時只看這個欄位：對外職缺名稱是招募文案，實測「作業員」
    # 職缺的對外名稱寫「電商物流理貨包裝」，會被當成理貨類別嚴格命中。
    # 沒填職務類別的職缺才退回看對外名稱。
    primary_category_text = category or public_title
    extended_text = _job_extended_search_text(job)
    extended_category_text = _job_extended_category_text(job)

    if category_label == "門市":
        if _job_has_delivery_conflict(job):
            return False

        primary_category_match = _category_matches_text(primary_category_text, "門市")
        primary_brand_match = True if not brand_label else _brand_matches_text(primary_text, brand_label)

        if primary_category_match and primary_brand_match:
            return True

        if not allow_relaxed:
            return False

        # 廠商仍可用完整的 extended_text（含系統廠商名稱等欄位）寬鬆比對，
        # 但職務類別絕對不能用含「工作內容(對外)」自由文字的 extended_text，
        # 否則「設備人員」職缺只因為工作說明裡寫到「各區門市據點」就會被
        # 誤判成門市類別（該欄位只是在講到職地點遍布全台，不是職務類別）。
        relaxed_category_match = _category_matches_text(extended_category_text, "門市")
        relaxed_brand_match = True if not brand_label else _brand_matches_text(extended_text, brand_label)
        return relaxed_category_match and relaxed_brand_match

    if _category_matches_text(primary_category_text, category_label):
        return True

    return allow_relaxed and _category_matches_text(extended_category_text, category_label)

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


# 目前唯一有各自專屬「精準工種直達攔截」分支的類別（見 handlers/message_handler.py），
# 只有這幾種才適合拿來當「蝦皮職缺類型反問」的按鈕選項——求職者點下按鈕、把
# 這個類別名稱送回來時，一定要能命中對應的直達攔截分支，不能落回同一個反問，
# 否則會卡在無限循環。像「設備人員」這種目前還沒有專屬分支的類別，刻意不
# 單獨列成按鈕，統一併進「全部類型都看看」保底選項，不會完全看不到，只是
# 不能精準篩選。「餐飲/服務」原本也是這種情況，實測發現「類別+福利/發薪/
# 休假方式」合併問時會混進不相關廠商的職缺，補上專屬候選池分支後，現在
# 也適合列進來。
DIRECT_INTERCEPT_ROUTABLE_CATEGORIES = ["外送", "門市", "理貨/倉儲", "製造/作業員", "餐飲/服務"]


def distinct_routable_categories_for_jobs(jobs: list) -> list:
    """找出這批職缺實際涵蓋 DIRECT_INTERCEPT_ROUTABLE_CATEGORIES 裡的哪幾種
    類別（依該清單順序回傳，刻意用嚴格比對 allow_relaxed=False，只信任
    結構化的「職務類別」／「職缺名稱(對外)」欄位，不看自由文字，避免誤判
    ——見 job_matches_category_filter() 的欄位信任原則說明）。供「蝦皮職缺
    類型反問」判斷要不要問、要問哪幾個選項使用：同一時間蝦皮如果只有一種
    類型在招，不需要多問；有兩種以上才需要讓求職者選。"""
    return [
        label for label in DIRECT_INTERCEPT_ROUTABLE_CATEGORIES
        if any(job_matches_category_filter(j, label, allow_relaxed=False) for j in jobs)
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
    # 一定要拿 _location_search_text（只含縣市/行政區這兩個結構化欄位）來比對，
    # 不能用 search_text（含工作內容/排版說明/精華亮點等自由文字）——自由文字裡
    # 剛好提到某個地名（例如地址是「八德路」，不是桃園市八德區）會被誤判成
    # 這個職缺真的位於該地區（詳見 _location_search_text 的欄位說明）。
    location_text = job.get("_location_search_text", "")
    if current_location:
        loc = clean_text_for_search(current_location)
        if loc and loc in location_text:
            score += 40

    # 2. 廠商權重加分[cite: 1]
    # brand_slot 一定要先經過 clean_text_for_search() 正規化再比對 search_text
    # （search_text 已經是正規化過的文字，「台」一律轉成「臺」）。像「台積電」
    # 這種 KNOWN_BRANDS 的 key 本身帶半形「台」字，先前只做 .lower() 沒有做
    # 台/臺正規化，會導致 search_text 裡永遠比對不到、這 80 分加分形同虛設
    # （sibling 函式 _brand_matches_text 有正確做這一步，這裡漏掉了）。
    vendor_clean = clean_text_for_search(job.get("系統廠商名稱", ""))
    brand_slot = slots.get("brand", "")
    if brand_slot and clean_text_for_search(brand_slot) in search_text:
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
    # 用跟直達篩選同一套班別分類（_job_shift_labels）判斷，原本用單字比對，
    # 「假日班」的「日」會被當成早班加分。班別可能是「早班|晚班」多個值。
    shift_slot = slots.get("shift", "")
    if shift_slot and shift_slot != "不限":
        if set(shift_slot.split("|")) & job_shift_labels(job):
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

def build_ai_job_candidates(active_jobs: list, query_text: str, current_location: str = "", slots: dict = None, limit: int = 70) -> list:
    """在送 Gemini 前建立候選集合（全部改成加減分排序，不再做地區/品牌/休假制度的硬篩選）[cite: 1]

    原本地區、品牌、週休制度都是先 hard filter 縮小 target_pool，篩不到才逐層放寬。
    這個做法會讓 AI 在「指定條件完全沒有職缺」時，候選清單裡可能只剩下極少數（甚至零筆）
    職缺，資訊不足以判斷該怎麼退讓推薦。現在全部職缺一律先送進 _score_job_for_ai 依地區、
    品牌、班別、休假、薪資等條件加減分，再取分數最高的前 limit 筆，讓 AI 在更完整的候選
    資訊下自行判斷是否要退讓推薦（例如同品牌但不同地區、同地區但休假制度不同）。
    """
    if not active_jobs:
        return []

    slots = slots or {}
    # 記住的班別/休假/發薪/福利條件先篩一次再排序：原本只當成加減分，AI
    # 拿到的候選清單還是混著不符合的職缺，會推薦不是日領的職缺給講過要日領
    # 的求職者。某一項篩完是空的就不篩那一項，留給 AI 判斷怎麼退讓推薦。
    for key, label_filter in [
        ("shift", filter_jobs_by_shift_label), ("leave", filter_jobs_by_leave_label),
        ("pay", filter_jobs_by_pay_label), ("benefit", filter_jobs_by_benefit_label),
    ]:
        if slots.get(key):
            narrowed = label_filter(active_jobs, slots[key])
            if narrowed:
                active_jobs = narrowed
    scored = [(_score_job_for_ai(job, query_text, current_location, slots), idx, job) for idx, job in enumerate(active_jobs)]
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


def find_high_confidence_faq_match(faq_list: list, query_text: str, min_question_length: int = 4) -> dict:
    """在送 AI 前先判斷這句話是不是已經完整、明確地命中某一筆 FAQ 的問題本文
    （雙向包含比對：求職者問句包住整個 FAQ 問題，或 FAQ 問題包住整個求職者問句）。

    命中的話代表這題有明確、已審核過的官方答案，上層應該直接回傳 Notion 原文，
    不要再送給 AI 改寫：一來 AI 意譯規章/福利類文字可能產生合規風險（用詞跑掉、
    語意跑掉），二來省下一次 Gemini 呼叫。只做雙向完整包含比對（而非任意關鍵字
    命中），並要求命中的問題本文長度至少 min_question_length 個字，避免像「薪水」
    這種短詞被子字串比對誤判成明確命中。命中多筆時取問題本文最長（比對特徵最完整）
    的那一筆。"""
    if not faq_list or not query_text:
        return None

    query_clean = clean_text_for_search(query_text)
    if not query_clean:
        return None

    best_match, best_len = None, 0
    for faq in faq_list:
        q_clean = clean_text_for_search(faq.get("question", ""))
        if not q_clean or len(q_clean) < min_question_length:
            continue
        if q_clean in query_clean or query_clean in q_clean:
            if len(q_clean) > best_len:
                best_match, best_len = faq, len(q_clean)

    return best_match

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
