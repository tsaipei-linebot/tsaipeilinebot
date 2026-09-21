"""批次匯入報班時段用的 CSV 解析（2026-09-21 新增）。

跟 delivery/csv_import.py（批次匯入人員）同一種分工：刻意寫成不碰
Firestore 的純函式，方便直接寫單元測試；是否真的寫入資料庫交給呼叫端
（routes/rider_routes.py）決定，這裡只負責把上傳的檔案內容解析成結構化
的每列結果。跟人員匯入不同的是，報班地點是騎士接單媒合功能自己獨立的
一份地點清單（rider_shift_locations，不是廠商/人員那套），而且是
Firestore 裡動態維護的清單（不像 VENDOR_LOOKUP 是寫死在 config.py 的
常數），所以地點名稱→地點資料的對照表（locations_by_name）由呼叫端先
查好、當參數傳進來，這裡才能維持「不碰 Firestore」的純函式設計。
"""
import csv
import io
from datetime import datetime

from config import TAIPEI_TZ
from delivery.config import RIDER_DEFAULT_SEARCH_RADIUS_KM

REQUIRED_HEADERS = {"地點", "開始時間", "結束時間", "需求人數"}

# Excel 打時間常見會用空格或 T 分隔日期跟時間，兩種都接受，跟
# csv_import.py 的到職日期比照兩種格式輸入是同一個考量。
_DATETIME_INPUT_FORMATS = ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M")


def _decode(content: bytes) -> str:
    """跟 delivery/csv_import.py 的 _decode() 邏輯完全一樣（Excel/記事本在
    台灣常見存成 Big5），這裡不共用同一支函式，是因為這兩份匯入本來就是
    各自獨立的功能，重複這幾行比互相 import 增加耦合更單純。"""
    for encoding in ("utf-8-sig", "cp950"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def _parse_datetime(raw: str):
    """回傳 (Unix timestamp 或 None, 是否格式錯誤)。"""
    value = (raw or "").strip()
    if not value:
        return None, True
    for fmt in _DATETIME_INPUT_FORMATS:
        try:
            return TAIPEI_TZ.localize(datetime.strptime(value, fmt)).timestamp(), False
        except ValueError:
            continue
    return None, True


def parse_shift_posting_csv(content: bytes, locations_by_name: dict):
    """回傳 (rows, header_error)。

    locations_by_name：{地點名稱: {"lat":..., "lng":...}}，呼叫端先用
    rider_repository.list_shift_locations() 查好傳進來，只有清單裡「啟用
    中」的地點才能被匹配到（跟新增報班時段表單的搜尋式下拉選單是同一份
    資料來源，行為一致）。

    header_error 不是 None 時代表整份檔案的表頭有問題，rows 一定是空
    list；否則 rows 是每一列的解析結果：
    - 成功：{"row": 列號, "ok": True, "location_name":..., "lat":..., "lng":...,
             "start_at":..., "end_at":..., "capacity":..., "radius_km":...}
    - 失敗：{"row": 列號, "ok": False, "error": 錯誤訊息, "location_name": ...}
    完全空白的列（地點、開始時間都沒填）直接跳過，不算錯誤。
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
        location_name = (raw.get("地點") or "").strip()
        start_raw = raw.get("開始時間") or ""
        end_raw = raw.get("結束時間") or ""
        capacity_raw = (raw.get("需求人數") or "").strip()
        radius_raw = (raw.get("服務半徑") or "").strip()

        if not location_name and not start_raw.strip():
            continue

        location = locations_by_name.get(location_name)
        if not location:
            rows.append(
                {
                    "row": i,
                    "ok": False,
                    "error": f"地點「{location_name}」在報班地點清單找不到，請先到報班地點管理新增（或確認名稱有沒有打錯字）",
                    "location_name": location_name,
                }
            )
            continue

        start_at, start_invalid = _parse_datetime(start_raw)
        end_at, end_invalid = _parse_datetime(end_raw)
        if start_invalid or end_invalid:
            rows.append(
                {
                    "row": i,
                    "ok": False,
                    "error": "開始時間/結束時間格式看不懂，請用 2024-01-31 09:00 這種格式",
                    "location_name": location_name,
                }
            )
            continue
        if end_at <= start_at:
            rows.append(
                {"row": i, "ok": False, "error": "結束時間要晚於開始時間", "location_name": location_name}
            )
            continue

        try:
            capacity = int(capacity_raw)
        except ValueError:
            rows.append({"row": i, "ok": False, "error": "需求人數要填數字", "location_name": location_name})
            continue
        if capacity <= 0:
            rows.append({"row": i, "ok": False, "error": "需求人數要大於 0", "location_name": location_name})
            continue

        radius_km = RIDER_DEFAULT_SEARCH_RADIUS_KM
        if radius_raw:
            try:
                radius_km = float(radius_raw)
            except ValueError:
                rows.append({"row": i, "ok": False, "error": "服務半徑要填數字", "location_name": location_name})
                continue
            if radius_km <= 0:
                rows.append({"row": i, "ok": False, "error": "服務半徑要大於 0", "location_name": location_name})
                continue

        rows.append(
            {
                "row": i,
                "ok": True,
                "location_name": location_name,
                "lat": location["lat"],
                "lng": location["lng"],
                "start_at": start_at,
                "end_at": end_at,
                "capacity": capacity,
                "radius_km": radius_km,
            }
        )
    return rows, None
