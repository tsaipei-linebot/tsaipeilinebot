import csv
import hashlib
import io
import re
import time
from datetime import date, timedelta

import requests
from google.cloud import firestore
from linebot.models import TextSendMessage

from config import (
    GCP_PROJECT_ID, FACTORY_OPENDATA_DATASET_ID, FACTORY_WATCH_LOOKBACK_DAYS,
    FACTORY_WATCH_SHEET_ID, FACTORY_WATCH_SHEET_NAME, FACTORY_WATCH_LINE_TARGET_ID,
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
    "industry": ["行業別", "主要行業"],
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


def _fetch_raw_rows() -> list:
    csv_url = _discover_csv_url(FACTORY_OPENDATA_DATASET_ID)
    if not csv_url:
        raise RuntimeError(
            f"找不到登記工廠名錄（dataset {FACTORY_OPENDATA_DATASET_ID}）的 CSV 下載連結，"
            "需要人工確認 data.gov.tw 資料集頁面的最新資源網址"
        )
    resp = requests.get(csv_url, timeout=60)
    resp.raise_for_status()
    text = _decode_csv_bytes(resp.content)
    return list(csv.DictReader(io.StringIO(text)))


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
        # 日期格式無法辨識時不主動排除，交給 Firestore 去重機制把關，避免漏掉真正的新工廠
        return True
    return approval >= date.today() - timedelta(days=lookback_days)


# ==========================================
# 去重：以統一編號／工廠登記編號／(名稱+地址) 雜湊 當唯一鍵，
# 比對 Firestore 裡「已經推播過」的清單
# ==========================================
def _dedup_key(record: dict) -> str:
    tax_id = record.get("tax_id") or ""
    reg_no = record.get("reg_no") or ""
    if tax_id:
        return f"tax:{tax_id}"
    if reg_no:
        return f"reg:{reg_no}"
    raw = f"{record.get('name', '')}|{record.get('address', '')}"
    return f"hash:{hashlib.md5(raw.encode('utf-8')).hexdigest()}"


def _filter_unseen(records: list) -> list:
    unseen = []
    for record in records:
        ref = db.collection(FACTORY_SEEN_COLLECTION).document(_dedup_key(record))
        if not ref.get().exists:
            unseen.append(record)
    return unseen


def _mark_seen(records: list):
    now = time.time()
    for record in records:
        ref = db.collection(FACTORY_SEEN_COLLECTION).document(_dedup_key(record))
        ref.set({"factory_name": record.get("name", ""), "first_seen_at": now})


# ==========================================
# 明細輸出：寫入 Google Sheet（用 Cloud Run 服務帳戶 ADC，Sheet 需先分享給該服務帳戶）
# ==========================================
def _get_sheets_service():
    from google.auth import default as google_auth_default
    from googleapiclient.discovery import build

    credentials, _ = google_auth_default(scopes=["https://www.googleapis.com/auth/spreadsheets"])
    return build("sheets", "v4", credentials=credentials, cache_discovery=False)


def _record_to_sheet_row(record: dict, found_date: str) -> list:
    return [
        found_date,
        record.get("name", ""),
        record.get("tax_id", ""),
        record.get("address", ""),
        record.get("industry", ""),
        record.get("products", ""),
        record.get("approval_date_raw", ""),
        record.get("reg_no", ""),
    ]


def write_new_records_to_sheet(records: list):
    if not FACTORY_WATCH_SHEET_ID:
        raise RuntimeError("尚未設定 FACTORY_WATCH_SHEET_ID，無法寫入明細")

    service = _get_sheets_service()
    found_date = date.today().isoformat()
    body = {"values": [_record_to_sheet_row(r, found_date) for r in records]}
    service.spreadsheets().values().append(
        spreadsheetId=FACTORY_WATCH_SHEET_ID,
        range=f"{FACTORY_WATCH_SHEET_NAME}!A:H",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body=body,
    ).execute()


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
    if FACTORY_WATCH_SHEET_ID:
        lines.append(f"完整名單請看 👉 https://docs.google.com/spreadsheets/d/{FACTORY_WATCH_SHEET_ID}/edit")
    return "\n".join(lines)


# ==========================================
# 主流程：由 Cloud Scheduler 觸發的端點呼叫
# ==========================================
def run_weekly_scan(line_bot_api) -> dict:
    summary = {"fetched": 0, "candidates": 0, "new_count": 0, "sheet_updated": False, "line_pushed": False, "errors": []}

    try:
        raw_rows = _fetch_raw_rows()
        summary["fetched"] = len(raw_rows)
    except Exception as e:
        print(f"[工廠登記監控] 資料抓取失敗: {e}")
        summary["errors"].append(f"fetch_failed: {e}")
        return summary

    if not raw_rows:
        return summary

    columns = _resolve_columns(list(raw_rows[0].keys()))
    normalized = [_normalize_record(row, columns) for row in raw_rows]
    candidates = [r for r in normalized if r.get("name") and _within_lookback(r, FACTORY_WATCH_LOOKBACK_DAYS)]
    summary["candidates"] = len(candidates)

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
        write_new_records_to_sheet(new_records)
        summary["sheet_updated"] = True
    except Exception as e:
        # Sheet 沒寫成功就不要標記已通知，也不要推播，讓下次執行可以重試同一批資料
        print(f"[工廠登記監控] 寫入 Google Sheet 失敗，暫緩標記已通知: {e}")
        summary["errors"].append(f"sheet_write_failed: {e}")
        return summary

    try:
        _mark_seen(new_records)
    except Exception as e:
        print(f"[工廠登記監控] 標記已通知狀態失敗: {e}")
        summary["errors"].append(f"mark_seen_failed: {e}")

    if not FACTORY_WATCH_LINE_TARGET_ID:
        print("[工廠登記監控] 尚未設定 FACTORY_WATCH_LINE_TARGET_ID，略過 LINE 推播（Sheet 已更新）")
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
