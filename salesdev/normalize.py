"""職缺文字修復、地址拆解、歸併鍵（2026-09-24 新增）。

**為什麼要歸併**：看過試算表實際資料後發現，派遣公司會在同一個地點換
標題一直重刊（例如悅盛人力在「桃園市桃園區桃鶯路」十幾筆，標題全都不
一樣，門牌還被打碼成 XX號／0號／437號），也會有好幾家派遣公司同時在徵
同一個地點。這些背後其實是同一家缺人的工廠，審查、反查都只需要做一次。
所以用「縣市＋區＋路段」當歸併鍵，同一個鍵的職缺歸成一組。

**已知限制**：門牌常被打碼，只能判斷到「同一條路」。很長的路（例如
神岡區中山路）上可能有好幾家工廠，會被誤歸成同一組——所以畫面上一組
一定要能點開看裡面每一筆，不要直接把重複的刪掉。

全部寫成純函式，方便直接單元測試。
"""
import hashlib
import re
import unicodedata

# ---------------------------------------------------------------------------
# 文字修復
# ---------------------------------------------------------------------------

# 小雞上工的標題表情符號會變成「ð¥」這種亂碼：它的頁面沒有宣告 charset，
# requests 就照 HTTP 規格用 latin-1 解碼，中文剛好是 \uXXXX 跳脫寫法所以
# 沒事，但直接寫在 JSON 裡的表情符號（4 個 byte）就被拆成 4 個 latin-1
# 字元。新抓的資料在 scrapers/chickpt.py 已經改成指定 utf-8 解碼從源頭修好，
# 這裡是給「舊試算表匯入的資料」跟保險用的修復：把連續的 latin-1 範圍字元
# 還原成 bytes 再用 utf-8 解一次，解不開就保留原樣。
_MOJIBAKE_RUN = re.compile(r"[\u0080-\u00ff]{2,}")


def repair_mojibake(text: str) -> str:
    if not text:
        return ""

    def _fix(match):
        chunk = match.group(0)
        try:
            return chunk.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return chunk

    return _MOJIBAKE_RUN.sub(_fix, text)


def clean_text(text: str) -> str:
    """修亂碼＋全形轉半形（NFKC）＋去頭尾空白。顯示用的標題也走這支，
    只是不像 `normalize_title_for_key()` 那樣把符號拿掉。"""
    text = repair_mojibake(text or "")
    text = unicodedata.normalize("NFKC", text)
    return text.strip()


# ---------------------------------------------------------------------------
# 地址拆解
# ---------------------------------------------------------------------------

# 縣市用白名單，不用「兩個字＋縣/市」的樣式：不然「新竹縣竹北市」裡的
# 「竹北市」、「彰化縣彰化市」裡的「彰化市」會被誤認成縣市。
COUNTIES = (
    "台北市", "新北市", "桃園市", "台中市", "台南市", "高雄市", "基隆市", "新竹市",
    "嘉義市", "新竹縣", "苗栗縣", "彰化縣", "南投縣", "雲林縣", "嘉義縣", "屏東縣",
    "宜蘭縣", "花蓮縣", "台東縣", "澎湖縣", "金門縣", "連江縣",
)
_COUNTY_RE = re.compile("|".join(COUNTIES))
# 貪婪比對＋回溯：「新市區中山路」要拿到「新市區」而不是「新市」
_DISTRICT_RE = re.compile(r"^([\u4e00-\u9fff]{1,3}[區鄉鎮市])")
# 村/里要先拿掉，不然「大華村頂湖一街」會被當成一條叫「大華村頂湖一街」的路
_VILLAGE_RE = re.compile(r"^[\u4e00-\u9fff]{1,3}[村里](?=[\u4e00-\u9fff])")
# 路名只接受中文/英數（遇到括號、空白就斷開），用 search 而不是從頭比對，
# 這樣「沙崙示範場域(高發二路360號)」才抓得到「高發二路」
_ROAD_RE = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]{1,10}?(?:大道|路|街)(?:[一二三四五六七八九十0-9]{1,2}段)?")
_ARABIC_TO_CHINESE = str.maketrans("123456789", "一二三四五六七八九")


def _normalize_address_text(address: str) -> str:
    text = clean_text(address)
    text = text.replace("臺", "台")
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"^台灣", "", text)
    return text


def parse_address(address: str) -> dict:
    """拆成 {"county", "district", "road"}，拆不出來的欄位是空字串。

    處理幾種實際資料看到的髒寫法：
    - 「台灣桃園市…」「臺中市…」：去掉「台灣」、臺→台
    - 「彰化縣彰化市新北市三重區自強路5段110號」（地址被接錯、出現兩個
      縣市）：取**最後一個**縣市開始的那段，因為路名在那一段裡
    - 「新北市淡水區淡水區北新路…」（區重複）：多的那個拿掉
    - 門牌打碼（XX號、0號、**號）：本來就不用門牌，直接忽略
    - 「路5段」跟「路五段」：段數統一成國字
    """
    text = _normalize_address_text(address)
    result = {"county": "", "district": "", "road": ""}
    if not text:
        return result

    counties = list(_COUNTY_RE.finditer(text))
    if counties:
        last = counties[-1]
        result["county"] = last.group(0)
        rest = text[last.end():]
    else:
        rest = text

    district_match = _DISTRICT_RE.match(rest)
    if district_match:
        result["district"] = district_match.group(1)
        rest = rest[district_match.end():]
        # 區名重複（「淡水區淡水區北新路」）
        while rest.startswith(result["district"]):
            rest = rest[len(result["district"]):]

    rest = _VILLAGE_RE.sub("", rest)

    road_match = _ROAD_RE.search(rest)
    # 路名本身被打碼（「XX路1號」）等於沒有路名，不能拿來歸併，不然全部
    # 打碼的職缺都會被歸成同一條「XX路」
    if road_match and re.search(r"[一-鿿]", re.sub(r"(?:大道|路|街).*$", "", road_match.group(0))):
        road = road_match.group(0)
        road = re.sub(
            r"([0-9])段$", lambda m: m.group(1).translate(_ARABIC_TO_CHINESE) + "段", road
        )
        result["road"] = road
    return result


def address_label(parts: dict) -> str:
    return f"{parts.get('county', '')}{parts.get('district', '')}{parts.get('road', '')}"


# ---------------------------------------------------------------------------
# 歸併鍵
# ---------------------------------------------------------------------------

# 標題比對時拿掉的宣傳字眼，這些字出不出現都是同一個職缺
_TITLE_NOISE_WORDS = ("急徵", "急招", "急缺", "高薪", "推薦", "熱門", "限時", "限量")


def normalize_title_for_key(title: str) -> str:
    """給「沒有地址」的職缺判斷重複用：只留中文、英數，去掉表情符號、
    【】、各種標點與宣傳字眼。"""
    text = clean_text(title).lower()
    for word in _TITLE_NOISE_WORDS:
        text = text.replace(word, "")
    return "".join(ch for ch in text if ch.isalnum())


def normalize_company_for_key(company: str) -> str:
    return "".join(ch for ch in clean_text(company) if ch.isalnum())


def group_key_for(work_address: str, company_name: str, job_title: str) -> tuple:
    """回傳 (歸併鍵, 顯示名稱, 種類)。

    - 地址拆得出「縣市＋區＋路」→ 用地點歸併，不同派遣公司同一地點也會
      歸在一起（種類 "address"）
    - 拆不出來（沒有地址，或只有「台北市中山區」這種區域）→ 退回用
      「派遣公司＋整理過的標題」，只有同一家、標題實質相同才算重複
      （種類 "title"）
    """
    parts = parse_address(work_address)
    if parts["county"] and parts["district"] and parts["road"]:
        label = address_label(parts)
        return f"addr:{parts['county']}|{parts['district']}|{parts['road']}", label, "address"

    company_key = normalize_company_for_key(company_name)
    title_key = normalize_title_for_key(job_title)
    area = f"{parts['county']}{parts['district']}"
    label = f"{clean_text(company_name)}｜{clean_text(job_title)}"
    return f"title:{company_key}|{area}|{title_key}", label, "title"


def group_doc_id(group_key: str) -> str:
    """Firestore 文件 ID 不能含「/」而且太長不好看，用雜湊。"""
    return hashlib.sha1(group_key.encode("utf-8")).hexdigest()[:24]


def job_doc_id(source: str, job_id: str) -> str:
    """職缺的 Firestore 文件 ID：來源＋平台自己的職缺編號。小雞上工的編號
    是整條網址，取最後的 slug；其他不能當文件 ID 的字元換掉。"""
    job_id = (job_id or "").strip().rstrip("/")
    if "/" in job_id:
        job_id = job_id.rsplit("/", 1)[-1]
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", job_id)
    return f"{source}_{safe}"


def job_id_from_url(source: str, url: str) -> str:
    """舊試算表只有「職缺連結」沒有職缺編號，從網址取回編號，讓匯入的舊
    資料跟之後每天新抓的是同一個文件 ID（不然同一筆會變成兩筆）。"""
    url = (url or "").strip().rstrip("/")
    if not url:
        return ""
    if source == "chickpt":
        return url
    return url.rsplit("/", 1)[-1].split("?", 1)[0]
