"""批次匯入人員用的 CSV 解析。

刻意寫成不碰 Firestore 的純函式（parse_personnel_csv），方便直接寫單元測試。
是否寫入資料庫、是否跳過重複身分證字號，交給呼叫端（routes/import_routes.py）
決定，這裡只負責把上傳的檔案內容解析成結構化的每列結果。
"""
import csv
import io
from datetime import datetime

from delivery.config import VENDOR_LOOKUP

REQUIRED_HEADERS = {"廠商", "姓名"}

# 到職日期是選填欄位，主要給「整批匯入舊資料」這種當下就已經知道實際
# 報到日的情境用（跟手動新增表單、應徵名單「錄取」那兩個當下通常還不
# 確定報到日、所以刻意不收這個欄位的設計不衝突——見 HANDOFF.md）。
# Excel 打日期常見會存成 - 或 / 分隔，這裡兩種都接受。
_HIRE_DATE_INPUT_FORMATS = ("%Y-%m-%d", "%Y/%m/%d")


def _normalize_hire_date(raw: str):
    """回傳 (正規化後的 YYYY-MM-DD 字串或空字串, 是否格式錯誤)。空白值算
    合法（選填），格式看不懂才算錯誤。"""
    value = (raw or "").strip()
    if not value:
        return "", False
    for fmt in _HIRE_DATE_INPUT_FORMATS:
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d"), False
        except ValueError:
            continue
    return "", True


def _decode(content: bytes) -> str:
    """Excel/記事本在台灣常見存成 Big5(cp950)，這裡先試 UTF-8（含 BOM），
    解碼失敗（表示不是合法 UTF-8）再退回 cp950；兩者都失敗就用 UTF-8
    容錯模式，至少不會整個匯入功能直接掛掉。"""
    for encoding in ("utf-8-sig", "cp950"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def parse_personnel_csv(content: bytes):
    """回傳 (rows, header_error)。

    header_error 不是 None 時代表整份檔案的表頭有問題（例如缺欄位），rows
    一定是空 list；否則 rows 是每一列的解析結果，每個元素是：
    - 成功：{"row": 列號, "ok": True, "vendor": 廠商代號, "name": ..., "id_number": ..., "phone": ..., "hire_date": "" 或 "YYYY-MM-DD"}
    - 失敗：{"row": 列號, "ok": False, "error": 錯誤訊息, "name": ...}
    完全空白的列（廠商、姓名都沒填）直接跳過，不算錯誤，方便匯出的檔案留有
    空行也不會被擋下來。
    """
    text = _decode(content)
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return [], "檔案是空的或無法辨識表頭"

    headers = {h.strip() for h in reader.fieldnames if h}
    missing = REQUIRED_HEADERS - headers
    if missing:
        return [], f"缺少必要欄位：{'、'.join(sorted(missing))}"

    rows = []
    for i, raw in enumerate(reader, start=2):  # 第 1 列是表頭，資料從第 2 列開始
        vendor_raw = (raw.get("廠商") or "").strip()
        name = (raw.get("姓名") or "").strip()
        id_number = (raw.get("身分證字號") or "").strip()
        phone = (raw.get("電話") or "").strip()
        hire_date_raw = raw.get("到職日期") or ""

        if not vendor_raw and not name:
            continue

        vendor_code = VENDOR_LOOKUP.get(vendor_raw.lower())
        if not vendor_code:
            rows.append({"row": i, "ok": False, "error": f"廠商「{vendor_raw}」無法辨識", "name": name})
            continue
        if not name:
            rows.append({"row": i, "ok": False, "error": "姓名為空", "name": name})
            continue

        hire_date, hire_date_invalid = _normalize_hire_date(hire_date_raw)
        if hire_date_invalid:
            rows.append(
                {"row": i, "ok": False, "error": f"到職日期「{hire_date_raw.strip()}」格式看不懂，請用 2024-01-31 或 2024/01/31 這種格式", "name": name}
            )
            continue

        rows.append(
            {
                "row": i,
                "ok": True,
                "vendor": vendor_code,
                "name": name,
                "id_number": id_number,
                "phone": phone,
                "hire_date": hire_date,
            }
        )
    return rows, None
