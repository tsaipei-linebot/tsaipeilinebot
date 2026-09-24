import csv
import hashlib
import io
import re
import time
import zipfile
from datetime import date, timedelta

import requests
from google.cloud import firestore
from linebot.models import TextSendMessage

from config import (
    GCP_PROJECT_ID, FACTORY_OPENDATA_DATASET_ID, FACTORY_WATCH_LOOKBACK_DAYS,
    FACTORY_WATCH_LINE_TARGET_ID, SERVICE_BASE_URL,
)

# ==========================================
# Firestore 客戶端初始化（沿用 Cloud Run 服務帳戶 ADC，作法同 session_service.py）
# ==========================================
db = firestore.Client(project=GCP_PROJECT_ID, database="(default)")

FACTORY_SEEN_COLLECTION = "factory_watch_seen"

DATA_GOV_TW_DATASET_API = "https://data.gov.tw/api/v2/rest/dataset/"

# 欄位名稱在政府開放資料裡偶有變動，用關鍵字比對取代寫死欄位名稱，
# 比對順序代表優先度（例如工廠登記核准日期優先於較籠統的設立許可核准日期）。
COLUMN_KEYWORDS = {
    "reg_no": ["工廠登記編號", "登記編號"],
    "name": ["工廠名稱"],
    "address": ["工廠地址", "地址"],
    "tax_id": ["統一編號"],
    "approval_date": ["工廠登記核准日期", "登記核准日期", "核准日期", "設立許可核准日期"],
    "industry": ["行業別", "主要行業", "產業類別"],
    "products": ["主要產品"],
}

_COUNTY_PATTERN = re.compile(r"^(..?[縣市])")


# ==========================================
# 資料抓取：先解析 data.gov.tw 資料集的中繼資料找出實際 CSV 下載連結
# （下載連結本身會不定期更動，資料集 ID 才是穩定的），再下載解析
# ==========================================
def _find_first_csv_url(node) -> str:
    if isinstance(node, str):
        return node if node.lower().split("?")[0].endswith(".csv") else ""
    if isinstance(node, dict):
        for value in node.values():
            found = _find_first_csv_url(value)
            if found:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _find_first_csv_url(item)
            if found:
                return found
    return ""


def _discover_csv_url(dataset_id: str) -> str:
    resp = requests.get(f"{DATA_GOV_TW_DATASET_API}{dataset_id}", timeout=15)
    resp.raise_for_status()
    return _find_first_csv_url(resp.json())


def _decode_csv_bytes(raw_bytes: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp950"):
        try:
            return raw_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise RuntimeError("工廠登記資料 CSV 編碼解析失敗（utf-8/cp950 都無法解碼）")


# 2026-09-24 修正：data.gov.tw 資料集 6569 掛的 CSV（www.ida.gov.tw/opendata/02/
# SDD6569.csv）其實**不是工廠名錄本身，而是一份只有一列的「目錄」**：
#     序號,年份,名稱,檔案格式,下載連結
#     1,113,登記工廠名錄,ZIP,https://serv.gcis.nat.gov.tw/RDownLoad/Data/statistical/生產中工廠清冊.zip
# 真正的名錄在那個 ZIP 裡（2026-09 實測：壓縮檔 6MB，裡面一個約 29MB 的
# UTF-8 CSV，檔名像 11508.csv＝民國 115 年 8 月的資料，約每個月更新一次）。
# 原本直接把目錄當名錄讀，永遠只有 1 筆、一家新工廠都找不到——當初開發環境
# 連不到政府網站，這段從來沒實際跑過，是使用者 2026-09-24 在 Cloud Shell
# 一步一步下載檔案才查出來的。
INDEX_LINK_COLUMN_KEYWORD = "下載連結"
INDEX_YEAR_COLUMN_KEYWORD = "年份"


def pick_archive_url_from_index(text: str) -> str:
    """如果這份 CSV 是「目錄」（有「下載連結」欄位、連結是 .zip），回傳要下載
    的 ZIP 網址（有好幾列時取年份最大的那列）；不是目錄就回傳空字串。純函式。"""
    reader = csv.DictReader(io.StringIO(text))
    fieldnames = reader.fieldnames or []
    link_col = next((f for f in fieldnames if INDEX_LINK_COLUMN_KEYWORD in f), None)
    if not link_col:
        return ""
    year_col = next((f for f in fieldnames if INDEX_YEAR_COLUMN_KEYWORD in f), None)
    best_url, best_year = "", -1
    for row in reader:
        url = (row.get(link_col) or "").strip()
        if ".zip" not in url.lower().split("?")[0]:
            continue
        try:
            year = int(re.sub(r"\D", "", row.get(year_col) or "") or 0) if year_col else 0
        except ValueError:
            year = 0
        if year > best_year:
            best_url, best_year = url, year
    return best_url


def _open_csv_text_from_zip(zip_bytes: bytes):
    """ZIP 裡第一個 .csv 檔，回傳可以一列一列讀的文字串流（不一次全部解碼成
    一大段字串——名錄約 29MB、好幾萬列，全部讀進記憶體再轉成字典清單，
    Cloud Run 的記憶體會很吃緊）。"""
    archive = zipfile.ZipFile(io.BytesIO(zip_bytes))
    members = [n for n in archive.namelist() if n.lower().endswith(".csv")]
    if not members:
        raise RuntimeError("工廠名錄 ZIP 裡找不到 CSV 檔")
    with archive.open(members[0]) as probe:
        head = probe.read(4096)
    encoding = "utf-8-sig"
    try:
        head.decode("utf-8-sig")
    except UnicodeDecodeError:
        encoding = "cp950"
    return io.TextIOWrapper(archive.open(members[0]), encoding=encoding, errors="replace", newline="")


def _iter_raw_rows():
    """一列一列產生名錄資料（dict）。會自動處理「目錄 CSV → ZIP → 真正的 CSV」
    這一層轉址；萬一哪天資料集直接掛真正的 CSV，也照樣能讀。"""
    csv_url = _discover_csv_url(FACTORY_OPENDATA_DATASET_ID)
    if not csv_url:
        raise RuntimeError(
            f"找不到登記工廠名錄（dataset {FACTORY_OPENDATA_DATASET_ID}）的 CSV 下載連結，"
            "需要人工確認 data.gov.tw 資料集頁面的最新資源網址"
        )
    resp = requests.get(csv_url, timeout=60)
    resp.raise_for_status()
    text = _decode_csv_bytes(resp.content)

    archive_url = pick_archive_url_from_index(text)
    if not archive_url:
        yield from csv.DictReader(io.StringIO(text))
        return

    archive_resp = requests.get(archive_url, timeout=120)
    archive_resp.raise_for_status()
    yield from csv.DictReader(_open_csv_text_from_zip(archive_resp.content))


# ==========================================
# 欄位比對與資料正規化
# ==========================================
def _resolve_columns(fieldnames: list) -> dict:
    resolved = {}
    for key, keywords in COLUMN_KEYWORDS.items():
        for keyword in keywords:
            match = next((f for f in fieldnames if keyword in f), None)
            if match:
                resolved[key] = match
                break
    return resolved


def _parse_roc_or_gregorian_date(raw: str):
    """相容政府資料常見的民國年（7 碼，如 1130215）跟西元年（8 碼或含分隔符）日期格式，
    解析失敗回傳 None，交由呼叫端決定如何處理（不主動當成「不符合」而排除掉）。"""
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw.strip())
    try:
        if len(digits) == 8:
            year, month, day = int(digits[:4]), int(digits[4:6]), int(digits[6:8])
            return date(year, month, day)
        if len(digits) == 7:
            roc_year, month, day = int(digits[:3]), int(digits[3:5]), int(digits[5:7])
            return date(roc_year + 1911, month, day)
        parts = re.split(r"[-/]", raw.strip())
        if len(parts) == 3:
            year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
            if year < 1911:
                year += 1911
            return date(year, month, day)
    except (ValueError, IndexError):
        return None
    return None


def _normalize_record(raw_row: dict, columns: dict) -> dict:
    def get(key):
        col = columns.get(key)
        return (raw_row.get(col) or "").strip() if col else ""

    approval_raw = get("approval_date")
    return {
        "reg_no": get("reg_no"),
        "name": get("name"),
        "address": get("address"),
        "tax_id": get("tax_id"),
        "industry": get("industry"),
        "products": get("products"),
        "approval_date_raw": approval_raw,
        "approval_date": _parse_roc_or_gregorian_date(approval_raw),
    }


def _within_lookback(record: dict, lookback_days: int) -> bool:
    approval = record.get("approval_date")
    if approval is None:
        # 2026-09-24 改成「日期不明就不算新工廠」：原本日期看不懂一律當成新的，
        # 交給去重把關——但名錄其實是全台好幾萬家工廠，只要有一批沒填登記
        # 核准日期，第一次跑就會把它們全部當成「新登記」灌進來。筆數另外記在
        # run_weekly_scan() 的 summary["undated"]，數字異常時看得出來。
        return False
    return approval >= date.today() - timedelta(days=lookback_days)


# ==========================================
# 去重：以統一編號／工廠登記編號／(名稱+地址) 雜湊 當唯一鍵，
# 比對 Firestore 裡「已經推播過」的清單
# ==========================================
def _dedup_key(record: dict) -> str:
    # 2026-09-24 改成工廠登記編號優先：實際名錄裡同一家公司（同一個統一編號）
    # 常有好幾座工廠（例如「點鑫產業」跟「點鑫產業二廠」統編相同、登記編號
    # 不同），用統一編號當鍵的話第二座廠永遠被當成「看過了」。之前從來沒有
    # 成功跑過（見上面 pick_archive_url_from_index() 的說明），Firestore 裡
    # 沒有舊鍵，改順序不會造成重複通知。
    tax_id = record.get("tax_id") or ""
    reg_no = record.get("reg_no") or ""
    if reg_no:
        return f"reg:{reg_no}"
    if tax_id:
        return f"tax:{tax_id}"
    raw = f"{record.get('name', '')}|{record.get('address', '')}"
    return f"hash:{hashlib.md5(raw.encode('utf-8')).hexdigest()}"


_FIRESTORE_CHUNK = 300


def _filter_unseen(records: list) -> list:
    """一次批次讀一批（get_all），不要一筆一筆讀——第一次跑時候選可能有好幾百筆。"""
    unseen = []
    for start in range(0, len(records), _FIRESTORE_CHUNK):
        chunk = records[start:start + _FIRESTORE_CHUNK]
        refs = [db.collection(FACTORY_SEEN_COLLECTION).document(_dedup_key(r)) for r in chunk]
        seen_ids = {snap.id for snap in db.get_all(refs) if snap.exists}
        unseen.extend(r for r, ref in zip(chunk, refs) if ref.id not in seen_ids)
    return unseen


def _mark_seen(records: list):
    now = time.time()
    for start in range(0, len(records), _FIRESTORE_CHUNK):
        batch = db.batch()
        for record in records[start:start + _FIRESTORE_CHUNK]:
            ref = db.collection(FACTORY_SEEN_COLLECTION).document(_dedup_key(record))
            batch.set(ref, {"factory_name": record.get("name", ""), "first_seen_at": now})
        batch.commit()


# ==========================================
# 明細輸出：寫入 Firestore（2026-09-24 起取代原本的 Google Sheet「新登記工廠」
# 分頁，改在平台 /salesdev 的「新登記工廠」分頁顯示，原因見 salesdev/repository.py
# 開頭；舊試算表的資料用 /salesdev 的「匯入舊試算表資料」搬過來）
# ==========================================
def _record_to_document(record: dict, found_date: str) -> dict:
    return {
        "dedup_key": _dedup_key(record),
        "found_date": found_date,
        "name": record.get("name", ""),
        "tax_id": record.get("tax_id", ""),
        "address": record.get("address", ""),
        "industry": record.get("industry", ""),
        "products": record.get("products", ""),
        "approval_date_raw": record.get("approval_date_raw", ""),
        "reg_no": record.get("reg_no", ""),
    }


def write_new_records(records: list):
    from salesdev import repository

    found_date = date.today().isoformat()
    repository.upsert_factories([_record_to_document(r, found_date) for r in records], found_date)


# ==========================================
# LINE 推播摘要
# ==========================================
def _extract_county(address: str) -> str:
    if not address:
        return ""
    match = _COUNTY_PATTERN.match(address.strip())
    return match.group(1) if match else ""


def build_line_summary_message(records: list, preview_limit: int = 5) -> str:
    lines = [
        "🏭 本週新登記工廠通知",
        f"本次共發現 {len(records)} 家新登記工廠（全台，資料源：經濟部產業發展署 登記工廠名錄）",
        "",
    ]
    for i, record in enumerate(records[:preview_limit], 1):
        county = _extract_county(record.get("address", "")) or "地區未知"
        lines.append(f"{i}. {record.get('name') or '(無名稱)'}（{county}）")
    if len(records) > preview_limit:
        lines.append(f"...等共 {len(records)} 家")
    lines.append("")
    if SERVICE_BASE_URL:
        lines.append(f"完整名單請看 👉 {SERVICE_BASE_URL.rstrip('/')}/salesdev?tab=factories")
    return "\n".join(lines)


# ==========================================
# 主流程：由 Cloud Scheduler 觸發的端點呼叫
# ==========================================
def run_weekly_scan(line_bot_api) -> dict:
    summary = {
        "fetched": 0, "undated": 0, "candidates": 0, "new_count": 0,
        "saved": False, "line_pushed": False, "errors": [],
    }

    # 一列一列讀、當場篩選，只把「近期核准」的留在記憶體裡（名錄有好幾萬列）
    candidates = []
    columns = None
    try:
        for row in _iter_raw_rows():
            if columns is None:
                columns = _resolve_columns(list(row.keys()))
            summary["fetched"] += 1
            record = _normalize_record(row, columns)
            if not record.get("name"):
                continue
            if record.get("approval_date") is None:
                summary["undated"] += 1
            if _within_lookback(record, FACTORY_WATCH_LOOKBACK_DAYS):
                candidates.append(record)
    except Exception as e:
        print(f"[工廠登記監控] 資料抓取失敗: {e}")
        summary["errors"].append(f"fetch_failed: {e}")
        return summary

    summary["candidates"] = len(candidates)
    print(
        f"[工廠登記監控] 名錄 {summary['fetched']} 筆，近 {FACTORY_WATCH_LOOKBACK_DAYS} 天核准 "
        f"{summary['candidates']} 筆，沒有核准日期 {summary['undated']} 筆"
    )

    try:
        new_records = _filter_unseen(candidates)
    except Exception as e:
        print(f"[工廠登記監控] Firestore 去重比對失敗: {e}")
        summary["errors"].append(f"dedup_failed: {e}")
        return summary

    summary["new_count"] = len(new_records)
    if not new_records:
        print("[工廠登記監控] 本週沒有偵測到新登記工廠")
        return summary

    try:
        write_new_records(new_records)
        summary["saved"] = True
    except Exception as e:
        # 沒存成功就不要標記已通知，也不要推播，讓下次執行可以重試同一批資料
        print(f"[工廠登記監控] 寫入 Firestore 失敗，暫緩標記已通知: {e}")
        summary["errors"].append(f"save_failed: {e}")
        return summary

    try:
        _mark_seen(new_records)
    except Exception as e:
        print(f"[工廠登記監控] 標記已通知狀態失敗: {e}")
        summary["errors"].append(f"mark_seen_failed: {e}")

    if not FACTORY_WATCH_LINE_TARGET_ID:
        print("[工廠登記監控] 尚未設定 FACTORY_WATCH_LINE_TARGET_ID，略過 LINE 推播（資料已存進平台）")
        return summary

    if not line_bot_api:
        print("[工廠登記監控] LINE Bot API 尚未初始化，略過推播")
        return summary

    try:
        message = build_line_summary_message(new_records)
        line_bot_api.push_message(FACTORY_WATCH_LINE_TARGET_ID, TextSendMessage(text=message))
        summary["line_pushed"] = True
    except Exception as e:
        print(f"[工廠登記監控] LINE 推播失敗: {e}")
        summary["errors"].append(f"line_push_failed: {e}")

    return summary
