"""台灣就業通（2026-09-26 新增）：勞動部公開的職缺 API ＋ 職缺頁上的聯絡資訊。

做法照使用者自己寫、實際跑成功的 Python 腳本（taiwanjobs_leads.py，2026-09-26
上傳到對話）：
1. 打官方職缺 API（政府資料開放平臺「台灣就業通網站職缺清單」，dataset 44062），
   一次最多 1000 筆，可以帶郵遞區號 `zipno`。API 本身**沒有**聯絡人/Email 欄位。
2. 用職缺名稱比對關鍵字，留下產線類職缺。
3. 逐一打開職缺頁（API 的 URL_QUERY），從頁面文字抓 Email、聯絡人、電話、地址。
   使用者的腳本實跑：176 間有 111 間抓到 Email（70 個公司網域、41 個 Gmail 之類）。

跟使用者腳本不同的地方：
- 關鍵字只比對**職缺名稱**（腳本連工作內容一起比，「電子」「包裝」「品管」在飯店、
  餐廳、門市的工作內容也常出現，實跑名單大多不是工廠）。
- 地區改成全台工業區的郵遞區號（`DEFAULT_ZIPCODES`），腳本是內湖、南港＋全國最新。
- 同一間公司的每一筆職缺都打開看，不是只看第一筆；每一頁的 Email 全部記下來。
- 每次請求至少間隔 1.5 秒（平台共用的 HttpClient），腳本是 0.3～0.6 秒。
- **不關閉 HTTPS 憑證檢查**（腳本用了 verify=False）。如果 Cloud Run 上驗證不過，
  會在畫面上的執行結果顯示錯誤，不會默默改成不驗證。
- 使用者 2026-09-26 決定：Email **全部列出、不分類型、不過濾**，派遣公司也不排除
  （只加標記、畫面可以切換隱藏）。
"""
import html as html_lib
import re
from urllib.parse import parse_qs, urlparse

from salesdev.normalize import clean_text
from salesdev.scrapers.base import HttpClient

SOURCE = "taiwanjobs"
API_URL = "https://free.taiwanjobs.gov.tw/webservice_taipei/Webservice.ashx"
API_MAX_COUNT = 1000

# 使用者腳本裡的關鍵字（只比對職缺名稱）
DEFAULT_KEYWORDS = [
    "作業員", "技術員", "包裝", "SMT", "組裝", "品檢", "品管",
    "操作員", "產線", "機台", "無塵室", "沖壓", "射出", "製造", "加工", "倉管",
    "電子", "廠務", "生管",
]

# 全台主要工業區所在的郵遞區號（管理員可以在畫面上改）。114 內湖、115 南港是使用者
# 腳本原本就有的。
DEFAULT_ZIPCODES = [
    "114", "115",
    # 新北
    "220", "221", "235", "236", "237", "238", "239", "241", "242", "243", "244", "247", "248", "249",
    # 桃園
    "320", "324", "325", "326", "327", "328", "330", "333", "334", "335", "337", "338",
    # 新竹、苗栗
    "300", "302", "303", "304", "305", "306", "307", "310", "350", "351", "360",
    # 台中
    "407", "411", "412", "414", "420", "421", "427", "428", "429", "432", "433", "434", "435", "436", "437",
    # 彰化、南投、雲林、嘉義
    "500", "503", "505", "506", "507", "508", "509", "510", "514", "515", "520", "540",
    "632", "638", "640", "612", "613", "621",
    # 台南
    "709", "710", "711", "717", "730", "736", "741", "744", "745",
    # 高雄、屏東
    "806", "811", "812", "814", "815", "820", "821", "825", "831", "833", "900",
]

_EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
_IGNORED_EMAIL_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".js", ".css")
# 網站本身（客服信箱、頁尾）的信箱，不是求才公司的
_IGNORED_EMAIL_DOMAINS = ("taiwanjobs.gov.tw", "mol.gov.tw", "wda.gov.tw")
# 名字不能包含冒號：原本（使用者腳本）寫 [^\s]，遇到「聯絡人員：無」會把「：無」當成名字
_CONTACT_NAME_RE = re.compile(r"聯絡人員\s*[:：]?\s*([^\s:：]{2,15})")
_CONTACT_NAME_STOPWORDS = ("電子信箱", "電話", "行動電話", "無", "不拘")
_PHONE_RE = re.compile(r"(?:電話|TEL)\s*[:：]?\s*([0-9\-–#\s]{7,25})")
_ADDRESS_RE = re.compile(r"(?:應徵地址|工作地點)\s*[:：]?\s*([^\s,，。]{6,35})")
MAX_EMAILS_PER_PAGE = 5


# ---------------------------------------------------------------------------
# 職缺 API（XML）
# ---------------------------------------------------------------------------

def clean_xml_field(block: str, tag_prefix: str) -> str:
    """API 的標籤名稱後面接全形中文說明（例如 `<COMPNAME（公司名稱）>`），用前綴比對；
    剝掉 CDATA 跟 HTML 跳脫字元。照使用者腳本的寫法。純函式。"""
    match = re.search(rf"<{tag_prefix}[^>]*>(.*?)</{tag_prefix}[^>]*>", block, re.DOTALL | re.IGNORECASE)
    if not match:
        return ""
    value = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", match.group(1).strip(), flags=re.DOTALL)
    return html_lib.unescape(value).strip()


def parse_api_records(xml_text: str) -> list:
    """API 回傳的 XML → 職缺 dict 清單（沒有公司名稱或職缺網址的略過）。純函式。"""
    text = (xml_text or "").replace("\r", "")
    records = re.findall(r"<Data[^>]*>(.*?)</Data>", text, re.DOTALL | re.IGNORECASE)
    if not records:
        records = re.findall(
            r"<(?:Table|Job|record)[^>]*>(.*?)</(?:Table|Job|record)>", text, re.DOTALL | re.IGNORECASE
        )
    jobs = []
    for record in records:
        company = clean_text(clean_xml_field(record, "COMPNAME"))
        url = clean_xml_field(record, "URL_QUERY")
        if not company or not url:
            continue
        if url.startswith("http://"):
            url = "https://" + url[len("http://"):]
        employer_id, hire_id = ids_from_url(url)
        jobs.append(
            {
                "company_name": company,
                "job_title": clean_text(clean_xml_field(record, "OCCU_DESC")),
                "job_url": url,
                "employer_id": employer_id,
                "hire_id": hire_id,
                "headcount": clean_xml_field(record, "JOB_PERSON") or clean_xml_field(record, "WORKER"),
                "salary_low": clean_xml_field(record, "NT_L"),
                "salary_high": clean_xml_field(record, "NT_U"),
                "city": clean_text(clean_xml_field(record, "CITYNAME")),
            }
        )
    return jobs


def ids_from_url(url: str) -> tuple:
    """職缺網址 `JobDetail.aspx?EMPLOYER_ID=2386205&HIRE_ID=10405549` → ("2386205", "10405549")。"""
    query = parse_qs(urlparse(url or "").query)
    lowered = {k.lower(): v for k, v in query.items()}
    return (lowered.get("employer_id") or [""])[0], (lowered.get("hire_id") or [""])[0]


def title_matches(title: str, keywords: list) -> str:
    """職缺名稱比對到的第一個關鍵字（不分大小寫）；沒有比對到回傳空字串。純函式。"""
    lowered = (title or "").lower()
    for keyword in keywords:
        if keyword and keyword.lower() in lowered:
            return keyword
    return ""


def fetch_api_jobs(client: HttpClient, zipno: str = "", count: int = API_MAX_COUNT) -> list:
    params = {"count": count}
    if zipno:
        params["zipno"] = zipno
    response = client.get(API_URL, params=params)
    response.encoding = "utf-8"
    return parse_api_records(response.text)


# ---------------------------------------------------------------------------
# 職缺頁（HTML）
# ---------------------------------------------------------------------------

def page_text(html: str) -> str:
    text = re.sub(r"<script[^>]*>.*?</script>", " ", html or "", flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_lib.unescape(text.replace("&nbsp;", " "))
    return re.sub(r"\s+", " ", text)


def parse_contact(html: str) -> dict:
    """職缺頁 → {"emails": [...], "contact_name", "contact_phone", "job_address"}。
    Email 全部記下來（去重、保留順序、最多 5 個），只排除圖片檔名跟台灣就業通/勞動部
    自己的信箱。純函式。"""
    text = page_text(html)
    emails = []
    for email in _EMAIL_RE.findall(text):
        email = email.strip().rstrip(".")
        lowered = email.lower()
        if lowered.endswith(_IGNORED_EMAIL_SUFFIXES) or any(d in lowered for d in _IGNORED_EMAIL_DOMAINS):
            continue
        if lowered not in [e.lower() for e in emails]:
            emails.append(email)
    result = {"emails": emails[:MAX_EMAILS_PER_PAGE], "contact_name": "", "contact_phone": "", "job_address": ""}

    match = _CONTACT_NAME_RE.search(text)
    if match and match.group(1).strip() not in _CONTACT_NAME_STOPWORDS:
        result["contact_name"] = match.group(1).strip()
    match = _PHONE_RE.search(text)
    if match and not match.group(1).strip().startswith("0800"):
        result["contact_phone"] = match.group(1).strip()
    match = _ADDRESS_RE.search(text)
    if match:
        result["job_address"] = match.group(1).strip()
    return result


def fetch_contact(client: HttpClient, job_url: str) -> dict:
    response = client.get(job_url)
    if not response.encoding or response.encoding.lower() == "iso-8859-1":
        # 伺服器沒宣告 charset 時 requests 會當成 latin-1，中文會變亂碼（小雞上工踩過同一個雷）
        response.encoding = "utf-8"
    return parse_contact(response.text)
