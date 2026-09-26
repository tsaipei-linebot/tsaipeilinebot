"""職缺 AI 文案（2026-09-26，GAS 搬家階段 3 第 1 個 PR）：從 job-portal-gas-project 搬過來並照使用者的決定調整。

照搬（`Project_Job.js`）：
- `format_smart_location()` ＝ `formatSmartLocation()`：行政區 4 個以內全列，5 個以上寫「各區門市據點（共 N 區，門市自選）」。
- `enforce_compliance_rules()` ＝ `enforceComplianceRules()`：兵役、年齡、性別、容貌身家的硬性過濾＋排版標準化，
  **規則一個字都沒改**（使用者 2026-09-26 決定過濾規則先不改），AI 寫之前、寫之後各過濾一次。
- 一次產 4 樣：對外標題、對外工作內容、精華亮點、排版工作說明；AI 失敗時用只寫事實的保底文案。

照使用者 2026-09-26 的決定調整（HANDOFF「AI 文案調教的決定」）：
- 語氣改得更親切、稱呼固定用「您」、表情符號維持原本的量；不加固定報名結尾（官網、沛沛都有報名機制）。
- 「排版工作說明」統一成送出版的 6 區塊，再加「✨【應徵與配合條件】」——**只從同仁原文抓，原文沒有就整塊不寫**。
- 客戶去識別化（台積電 → 知名半導體大廠）送出、批次都做。
- **防腦補用程式檢查**，不是只靠 prompt（`find_unsupported()`）：產出裡的數字都要在原始資料找得到；常見福利／條件
  字眼（無經驗可、週休二日、供餐…）產出有、原始資料沒有就算腦補。不過關重寫一次，再不過就用保底文案，並標記
  `check_issues` 讓核准卡片提示主管人工確認。
- 另外把 Notion 已經勾好的「休假方式」也當成原始資料交給 AI（GAS 沒給，AI 只能從內文猜）。

這個 PR 只提供「產生」，不寫 Notion、不動現在的送審流程；`/job-listings/migration` 可以拿現有職缺預覽比對。
"""
import json
import re

from services import ai_service

# ---------------------------------------------------------------- 地點

def _split(value) -> list:
    if isinstance(value, (list, tuple)):
        items = value
    else:
        items = re.split(r"[,，、/\\\s]+", str(value or ""))
    seen, result = set(), []
    for item in items:
        item = str(item or "").strip()
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def format_smart_location(cities, districts) -> str:
    city_str = "、".join(_split(cities))
    dists = _split(districts)
    if not dists:
        return city_str or "依公司指定地點"
    if len(dists) <= 4:
        dist_str = "、".join(dists)
        return f"{city_str}（{dist_str}）" if city_str else dist_str
    if city_str:
        return f"{city_str} 各區門市據點（共 {len(dists)} 區，門市自選）"
    return f"各區門市據點（共 {len(dists)} 區，門市自選）"


# ---------------------------------------------------------------- 就業服務法過濾（照搬，不改）

_NUM = "[0-9０-９一二兩三四五六七八九十百]+"
_AGE_UNIT = "(?:歲|周歲|週歲)"
_AGE_SCOPE = "(?:以內|以下|以上|左右|上下|內|前)"


def enforce_compliance_rules(text) -> str:
    if not text:
        return ""
    s = str(text)
    # 1. 思考雜訊與 Markdown
    s = re.sub(r".*(?:NO intro|Final Polish|Checked|thought|reasoning).*[\r\n]*", "", s, flags=re.I)
    s = re.sub(r"^```[a-zA-Z]*\n?", "", s, flags=re.M)
    s = re.sub(r"```$", "", s, flags=re.M)
    # 2. 兵役
    s = re.sub(r"[（(]?\s*(需|限)?\s*(役畢|免役|未役)\s*[)）]?", "", s, flags=re.I)
    s = re.sub(r"(需|限|須)\s*役畢", "", s, flags=re.I)
    # 3. 年齡
    s = re.sub(rf"({_NUM})\s*[-~～至到到約]\s*({_NUM})\s*{_AGE_UNIT}", "", s)
    s = re.sub(rf"(?:限|須|需|要|年齡|年紀|適合)\s*({_NUM})\s*[-~～至到到約]\s*({_NUM})"
               r"(?!\s*(?::|：|點|分|時|小時|公斤|kg|KG|Kg|號|樓|段|巷|弄|包|箱|件))", "", s)
    s = re.sub(rf"(?:限|須|需|要)?\s*({_NUM})\s*{_AGE_UNIT}\s*{_AGE_SCOPE}?", "", s)
    s = re.sub(rf"(?:限|須|需|要|年齡|年紀)\s*({_NUM})\s*{_AGE_SCOPE}", "", s)
    s = re.sub(r"(限|須|需)?\s*([0-9一二三四五六七八九十]+)年級生", "", s)
    s = re.sub(r"年輕(活力|有幹勁|貌美|力壯)?", "具備熱忱", s)
    s = re.sub(r"年紀(輕|小|大)", "", s)
    # 4. 性別
    s = re.sub(r"限(男|女|男性|女性|男生|女生)", "", s)
    s = re.sub(r"適合(女性|男性|男生|女生)", "歡迎各界人才", s)
    s = s.replace("男女不拘", "歡迎各界人才")
    s = s.replace("限男生搬重", "需配合搬重貨物")
    s = s.replace("限女性細心", "需具備細心度")
    s = re.sub(r"(男|女)作業員", "作業員", s)
    s = re.sub(r"(男|女)理貨員", "理貨員", s)
    # 5. 身心與容貌
    s = re.sub(r"五官端正|容貌端莊|身家清白|無前科|健全", "", s)
    # 6. 多餘符號與空格
    s = re.sub(r"[,，、]{2,}", "、", s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"^[ \t,，、/／-]+|[ \t,，、/／-]+$", "", s, flags=re.M)
    # 7. 排版標準化
    s = re.sub(r"(?:[\r\n\s]*)(?:🎯\s*)?【(?:主要)?工作內容】", "\n\n🎯【工作內容】\n", s)
    s = re.sub(r"(?:[\r\n\s]*)(?:⏰\s*)?【(?:工作)?時間與休假】", "\n\n⏰【時間與休假】\n", s)
    s = re.sub(r"(?:[\r\n\s]*)(?:💰\s*)?【薪資(?:與福利)?待遇】", "\n\n💰【薪資待遇】\n", s)
    s = re.sub(r"(?:[\r\n\s]*)(?:📍\s*)?【(?:工作)?地點(?:資訊|與交通)?】", "\n\n📍【地點資訊】\n", s)
    s = re.sub(r"([^\n])\s*(・)", r"\1\n\2", s)
    s = re.sub(r"(\r\n|\r|\n){3,}", "\n\n", s)
    return s.strip()


# 注意：GAS 對「排版工作說明」也跑同一支過濾，第 7 步會把「🎯【主要工作內容】」「⏰【工作時間與休假】」
# 這些 6 區塊標題改寫成 4 區塊的名稱——這是 GAS 原本就有的行為，照搬（使用者決定過濾規則先不改）。

# ---------------------------------------------------------------- 防腦補檢查

# 產出有、原始資料沒有就算腦補的字眼；同一組是同義詞，原始資料出現其中任何一個就算有根據
CLAIM_GROUPS = [
    ("無經驗可", "免經驗", "無經驗", "新手可", "新手"),
    ("週休二日", "周休二日", "週休2日", "周休2日"),
    ("供餐", "包餐", "供膳", "提供餐", "伙食"),
    ("交通車", "接駁車", "接駁"),
    ("宿舍", "包住", "提供住宿"),
    ("獎金",),
    ("全勤",),
    ("加班費",),
    ("固定班", "固定時段", "不輪班"),
    ("勞健保", "勞保", "健保"),
    ("團保", "團體保險"),
    ("年終",),
    ("三節",),
    ("員工旅遊",),
    ("教育訓練", "培訓"),
    ("升遷", "晉升"),
    ("調薪", "加薪"),
    ("日領", "週領", "周領", "現領"),
    ("停車",),
    ("捷運",),
    ("火車站", "車站"),
    ("公車",),
    ("冷氣", "空調"),
    ("制服",),
    ("置物櫃",),
    ("學生可", "學生"),
    ("二度就業",),
    ("中高齡",),
]

_FULLWIDTH = str.maketrans("０１２３４５６７８９", "0123456789")


def _numbers(text: str) -> set:
    found = set()
    for raw in re.findall(r"\d+(?:\.\d+)?", str(text or "").translate(_FULLWIDTH).replace(",", "")):
        try:
            value = float(raw)
        except ValueError:
            continue
        found.add(int(value) if value == int(value) else value)
    return found


_CN_DIGIT = {"零": 0, "一": 1, "二": 2, "兩": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _chinese_numbers(text: str) -> set:
    """原文常寫國字（週休二日、八點、十五公斤），轉成數字才能跟 AI 寫的阿拉伯數字比對。只處理到 99。"""
    found = set()
    for token in re.findall(r"[零一二兩三四五六七八九十]+", str(text or "")):
        if "十" in token:
            head, _, tail = token.partition("十")
            tens = _CN_DIGIT.get(head, 1) if head else 1
            ones = _CN_DIGIT.get(tail, 0) if tail else 0
            if (head and head not in _CN_DIGIT) or (tail and tail not in _CN_DIGIT):
                continue
            found.add(tens * 10 + ones)
        elif len(token) == 1:
            found.add(_CN_DIGIT[token])
    return found


def _supported(number, source_numbers: set) -> bool:
    if number in source_numbers:
        return True
    # 原文「下午 5 點」AI 寫成「17:00」：13～23 點跟 1～11 點視為同一個時間
    return isinstance(number, int) and 13 <= number <= 23 and (number - 12) in source_numbers


def find_unsupported(output_text: str, source_text: str) -> list:
    """回傳腦補清單（白話）；空清單＝通過。只看 AI 產出的文字，固定的版型文字（區塊標題、就業服務法聲明）不算。"""
    issues = []
    source_numbers = _numbers(source_text) | _chinese_numbers(source_text) | {0}  # 08:00 的 00
    extra = sorted(n for n in _numbers(output_text) if not _supported(n, source_numbers))
    if extra:
        issues.append("原始資料沒有的數字：" + "、".join(str(n) for n in extra))
    for group in CLAIM_GROUPS:
        if any(word in output_text for word in group) and not any(word in source_text for word in group):
            issues.append(f"原始資料沒有提到「{group[0]}」")
    return issues


# ---------------------------------------------------------------- 產生

LAW_NOTE = "💡 依《就業服務法》規定，本公司所有職缺皆無性別、年齡限制，歡迎所有朋友應徵！"

_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "external_title": {"type": "STRING"},
        "external_desc": {"type": "STRING"},
        "highlight": {"type": "STRING"},
        "formatted_detail": {"type": "STRING"},
    },
    "required": ["external_title", "external_desc", "highlight", "formatted_detail"],
}


def _inputs(job: dict) -> dict:
    title = job.get("external_title") or job.get("title") or "招募職缺"
    shift = job.get("shift")
    shift_text = "、".join(_split(shift)) if shift else ""
    leave = job.get("leave_type")
    leave_text = "、".join(_split(leave)) if leave else ""
    return {
        "title": enforce_compliance_rules(title),
        "location": format_smart_location(job.get("city"), job.get("district")),
        "salary": job.get("salary") or "依公司規定",
        "shift": shift_text or "依排班規定",
        "leave": leave_text,
        "original": enforce_compliance_rules(job.get("original_desc") or job.get("external_desc") or "歡迎洽詢應徵。"),
    }


def source_text(inputs: dict) -> str:
    return "\n".join(str(inputs[k]) for k in ("title", "location", "salary", "shift", "leave", "original"))


def build_prompt(inputs: dict, retry_issues: list = None) -> str:
    leave_line = f"6. 休假方式：{inputs['leave']}\n" if inputs["leave"] else ""
    retry = ""
    if retry_issues:
        retry = ("\n【上一次的輸出被退件，原因如下，這次務必修正】\n"
                 + "\n".join(f"- {i}" for i in retry_issues)
                 + "\n只能寫原始資料裡有的內容，上面這些一律刪掉。\n")
    return f"""
你是一位親切、懂勞動法規的專業人資文案夥伴。請根據下方【原始資料】，用親切、溫暖、像在跟求職者聊天的語氣，
一次輸出 4 個招募文案欄位的 JSON。稱呼求職者一律用「您」（不要用「你」）。表情符號只用在下面格式指定的位置。

【原始資料（只能使用這裡的內容，禁止假設任何沒有提供的資訊）】
1. 職缺名稱：{inputs['title']}
2. 工作地點：{inputs['location']}
3. 薪資待遇：{inputs['salary']}
4. 工作班別：{inputs['shift']}
5. 同仁寫的工作內容原文：
{inputs['original']}
{leave_line}
【最高準則】
1. 零腦補：所有內容 100% 來自上方原始資料。沒寫的福利、設備、獎金、數字、工作內容、條件一律不准寫。
   原始資料沒有的數字（時薪、時段、公斤數、天數、人數）絕對不能出現。
2. 《就業服務法》第 5 條：原始資料裡的年齡、性別、役畢等限制一律刪除。
3. 客戶去識別化：「台積電」「欣興電子」這類具體公司名稱，改寫成「知名半導體大廠」「知名電子大廠」這類說法。
4. 資訊放對區塊：上班時段、休假只能放時間區塊；地址、交通只能放地點區塊；薪資、加班費只能放薪資區塊；
   工作內容區塊只放實際工作項目與現場作業條件（搬重公斤數、服裝、置物櫃等）。
{retry}
【4 個欄位】
1. "external_title"：對外吸睛標題，繁體中文 20～38 字，格式：【🔥地區/特色】職缺名稱【✨班別/薪資】。
2. "external_desc"：條列版對外工作內容，區塊之間空一行，項目用「・」：
🎯【工作內容】
・（工作事項、搬重、服裝與置物櫃等現場規定）

⏰【時間與休假】
・（班別、完整起訖時段、休假方式——原始資料有才寫）

💰【薪資待遇】
・（時薪／月薪、加班費——原始資料有才寫）

📍【地點資訊】
・工作地點：（地點）
・周邊交通：（原始資料有才寫，沒有就整行不寫）
3. "highlight"：手機卡片的親切短句，繁體中文 30～45 字，只點出原始資料本來就有的優點；句子要完整，
   結尾用「！」或「。」，不要加報名方式或「歡迎私訊」這類結尾。
4. "formatted_detail"：完整工作說明，區塊之間空一行，項目用「・」：
📋【職缺名稱：（美化後對外職稱）】

🎯【主要工作內容】
・（實際工作職責、搬重條件、現場作業規定）

⏰【工作時間與休假】
・工作班別：{inputs['shift']}
・時段選擇：（完整起訖時間，原始資料有才寫）
・休假制度：（原始資料有才寫）

💰【薪資與福利待遇】
・薪資待遇：{inputs['salary']}

📍【工作地點與交通】
・工作地點：{inputs['location']}（有詳細地址就完整列出）
・周邊交通：（原始資料有才寫）

✨【應徵與配合條件】
・（只能從「同仁寫的工作內容原文」裡挑出應徵條件，例如體能、出勤、經驗要求；
   原文完全沒有提到任何條件時，這個區塊（含標題）整個不要出現，絕對不要自己想條件）

{LAW_NOTE}

原始資料裡沒有的項目整行不寫，不要寫「無」「未提供」「依公司規定」這類填空字。
只輸出 JSON，不要輸出 Markdown 或任何說明文字。
""".strip()


def fallback(inputs: dict) -> dict:
    """AI 失敗或兩次都沒通過檢查時用：只寫確定的事實，不做任何宣稱（照 GAS 保底文案，格式改成統一版）。"""
    title = inputs["title"] if re.match(r"^【.*】$", inputs["title"]) else f"【{inputs['title']}】"
    bullets = "・" + re.sub(r"\n+", "\n・", inputs["original"])
    leave = f"\n・休假制度：{inputs['leave']}" if inputs["leave"] else ""
    return {
        "external_title": title,
        "external_desc": (f"🎯【工作內容】\n{bullets}\n\n⏰【時間與休假】\n・工作班別：{inputs['shift']}{leave}\n\n"
                          f"💰【薪資待遇】\n・薪資待遇：{inputs['salary']}\n\n📍【地點資訊】\n・工作地點：{inputs['location']}"),
        "highlight": f"開放應徵{title}！工作地點：{inputs['location']}，班別：{inputs['shift']}，薪資：{inputs['salary']}。",
        "formatted_detail": (f"📋【職缺名稱：{inputs['title']}】\n\n🎯【主要工作內容】\n{bullets}\n\n"
                             f"⏰【工作時間與休假】\n・工作班別：{inputs['shift']}{leave}\n\n"
                             f"💰【薪資與福利待遇】\n・薪資待遇：{inputs['salary']}\n\n"
                             f"📍【工作地點與交通】\n・工作地點：{inputs['location']}\n\n{LAW_NOTE}"),
    }


def _polish(parsed: dict) -> dict:
    title = str(parsed.get("external_title") or "").strip()
    title = re.sub(r"^[\"'【]*(.*?)[\"'】]*$", r"【\1】", title).replace("【【", "【").replace("】】", "】")
    return {
        "external_title": enforce_compliance_rules(title),
        "external_desc": enforce_compliance_rules(parsed.get("external_desc") or ""),
        "highlight": enforce_compliance_rules(str(parsed.get("highlight") or "").strip()),
        "formatted_detail": enforce_compliance_rules(str(parsed.get("formatted_detail") or "").strip()),
    }


def _ask(prompt: str):
    raw = ai_service.query_gemini_ai(prompt, response_schema=_SCHEMA)
    if not raw:
        return None
    try:
        return json.loads(re.sub(r"^```json\s*|\s*```$", "", raw.strip()))
    except ValueError:
        return None


def generate(job: dict) -> dict:
    """job 的 key：title、external_title、city、district、salary、shift、leave_type、original_desc（同仁原文；舊職缺
    沒有就用 external_desc）。回傳 4 個欄位＋is_fallback＋check_issues（最後一次檢查沒過的原因）＋attempts。"""
    inputs = _inputs(job)
    src = source_text(inputs)
    issues = None
    for attempt in (1, 2):
        parsed = _ask(build_prompt(inputs, issues))
        if not parsed:
            continue
        result = _polish(parsed)
        if not all(result.values()):
            issues = ["有欄位是空的"]
            continue
        # 版型固定文字不算 AI 產出（例如就業服務法聲明）
        produced = "\n".join(result.values()).replace(LAW_NOTE, "")
        issues = find_unsupported(produced, src)
        if not issues:
            return {**result, "is_fallback": False, "check_issues": [], "attempts": attempt}
    # 保底文案也跑一次過濾，區塊標題才會跟 AI 版一樣（GAS 的過濾會把 6 區塊標題改成 4 區塊的名稱）
    safe = {k: enforce_compliance_rules(v) for k, v in fallback(inputs).items()}
    return {**safe, "is_fallback": True, "check_issues": issues or ["AI 沒有回應"], "attempts": 2}


# ---------------------------------------------------------------- 讀 Notion 現有職缺（預覽比對用）

_FIELDS = {
    "title": "職缺名稱",
    "external_title": "職缺名稱(對外)",
    "city": "縣市",
    "district": "行政區",
    "salary": "薪資",
    "shift": "班別",
    "leave_type": "休假方式",
    "external_desc": "工作內容(對外)",
    "highlight": "精華亮點",
    "formatted_detail": "排版工作說明",
    "status": "狀態",
    "review_status": "審核狀態",
    "publisher": "刊登人",
}


def _page_to_job(page: dict) -> dict:
    from services.notion_service import parse_notion_property

    props = page.get("properties") or {}
    job = {"id": page.get("id", "")}
    for key, name in _FIELDS.items():
        prop = props.get(name)
        if prop is None and name == "職缺名稱":
            prop = next((p for p in props.values() if isinstance(p, dict) and p.get("type") == "title"), None)
        job[key] = parse_notion_property(prop) if prop is not None else ""
    return job


def list_jobs() -> list:
    """Notion 職缺資料庫裡非停招的職缺（跟 GAS fetchActiveJobsFromNotion 同一個範圍），依職缺名稱排序。"""
    from config import NOTION_JOBS_DB_ID
    from services.notion_service import query_notion_database_direct

    jobs = [_page_to_job(p) for p in query_notion_database_direct(NOTION_JOBS_DB_ID)]
    jobs = [j for j in jobs if j["status"] != "停招"]
    return sorted(jobs, key=lambda j: j["title"])


def get_job(page_id: str):
    return next((j for j in list_jobs() if j["id"] == page_id), None)
