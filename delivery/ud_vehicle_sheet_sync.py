"""UD 廠商車輛管理事件（領車/還車）自動同步到材霈自己維護的「三輪車」
Google Sheet 分頁（2026-09-22 新增，跟使用者討論確認後的設計）。

**只有 vendor == "ud" 的領車/還車事件才會觸發**，唯一呼叫端見
delivery/repository.py 的 record_vehicle_event()（透過內部的
_sync_ud_vehicle_sheet() 包一層）。

**用分頁的 gid（不是分頁名稱）定位分頁**：分頁名稱之後如果被手動改掉、
或全形/半形括號打法不一致（這個系統之前在部門名稱比對就踩過這個雷，見
platform_accounts.normalize_department() 的說明），程式碼靠名稱字串比對
就會找錯分頁；gid 是 Google Sheets 內部的分頁 ID，只要不整個刪掉重建
這個分頁就不會變，比名稱穩定。見 delivery/config.py 的 UD_VEHICLE_SHEET_GID
說明。

**每次領車/還車都是新增一列，不會回頭覆蓋舊資料**（這是使用者明確要求
的設計，這份表本質上是一份事件紀錄/流水帳，不是「每台車一列」的主檔）：
- 用「車號」欄位所在的整欄往下掃，掃到第一個空白列，新的一列就寫在那裡。
- 這個分頁裡實際上還堆疊了其他好幾張不相干的表格（例如另一張記錄 Uber
  車輛 uuid／文件審核狀態的表），所以「掃到空白列」同時也是用來避免
  誤寫到下面其他表格的天然邊界——正常情況下同一張表的資料列之間不會夾著
  空白列，下一張表跟這張表之間才會有空白列分隔。
- 表頭欄位也不是寫死欄位字母（例如「一定是 B 欄」），而是每次都先讀一次
  表頭列、用欄位名稱去對應實際欄位字母，比較不怕之後使用者自己在表格裡
  手動插入/搬動欄位順序。

**同步失敗（分頁權限沒開、找不到分頁、網路錯誤…）只會印出 log、回傳
False，不會拋例外**——這是附加功能，不該讓領車/還車這個主要操作跟著
失敗（比照 delivery/group_notify.py 的既有作法）。
"""
from delivery.config import UD_VEHICLE_SHEET_GID, UD_VEHICLE_SHEET_ID

# 表頭欄位名稱，跟使用者確認過的試算表欄位完全一致（見 HANDOFF.md「UD
# 車輛領還車自動同步到材霈試算表」章節）。程式不假設這些欄位一定照這個
# 順序排列，只是拿來對照表頭列裡「有出現哪些欄位」。
HEADER_NAMES = ["地區", "車號", "外送員", "手機號碼", "站所", "目前使用狀況", "停車地點", "給車", "還車", "備註"]

# 掃描表頭/資料列時最多讀幾列，避免萬一表格結構跟預期完全不同時無限
# 往下掃（這份試算表其他表格加起來也才幾百列）。
_MAX_SCAN_ROWS = 1000


def _get_sheets_service():
    from google.auth import default as google_auth_default
    from googleapiclient.discovery import build

    credentials, _ = google_auth_default(scopes=["https://www.googleapis.com/auth/spreadsheets"])
    return build("sheets", "v4", credentials=credentials, cache_discovery=False)


def _col_index_to_letter(index: int) -> str:
    """0-indexed 欄位編號轉成 Google Sheets 的欄位字母（0→A、25→Z、26→AA…）。"""
    index += 1
    letters = ""
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _find_tab_title(service) -> str:
    meta = service.spreadsheets().get(spreadsheetId=UD_VEHICLE_SHEET_ID).execute()
    for sheet in meta.get("sheets", []):
        props = sheet.get("properties", {})
        if props.get("sheetId") == UD_VEHICLE_SHEET_GID:
            return props.get("title", "")
    return ""


def _locate_next_row(rows: list) -> tuple:
    """在 `rows`（values().get() 回傳的二維陣列，第 0 筆對應試算表第 1
    列）裡找到表頭列跟緊接著的第一個空白列。回傳 (header_col, next_row)：
    - header_col：{欄位名稱: 0-indexed 欄位編號}，只包含實際在表頭列裡
      找到的欄位；找不到表頭列（沒有同時出現「車號」「外送員」的那一列）
      時是 None。
    - next_row：這張表下一筆資料應該寫入的「試算表實際列號」（1-indexed）；
      找不到表頭列時是 None。"""
    for idx, row in enumerate(rows):
        if "車號" not in row or "外送員" not in row:
            continue
        header_col = {name: row.index(name) for name in HEADER_NAMES if name in row}
        vehicle_col = header_col.get("車號")
        if vehicle_col is None:
            continue
        data_idx = idx + 1
        while data_idx < len(rows):
            data_row = rows[data_idx]
            cell = data_row[vehicle_col] if vehicle_col < len(data_row) else ""
            if not (cell or "").strip():
                break
            data_idx += 1
        return header_col, data_idx + 1
    return None, None


def sync_vehicle_event(
    *,
    service_area_name: str,
    vehicle_no: str,
    personnel_name: str,
    phone: str,
    site: str,
    status_name: str,
    location: str,
    event_type: str,
    event_date: str,
    note: str,
) -> bool:
    """把一筆 UD 車輛領車/還車事件，寫成一筆新的列到材霈的「三輪車」
    試算表。回傳是否真的寫入成功；失敗一律回傳 False、不拋例外，見本檔
    開頭的說明。"""
    if not UD_VEHICLE_SHEET_ID:
        return False

    values = {
        "地區": service_area_name,
        "車號": vehicle_no,
        "外送員": personnel_name,
        "手機號碼": phone,
        "站所": site,
        "目前使用狀況": status_name,
        "停車地點": location,
        "給車": event_date if event_type == "checkout" else "",
        "還車": event_date if event_type == "return" else "",
        "備註": note,
    }

    try:
        service = _get_sheets_service()
        title = _find_tab_title(service)
        if not title:
            print(f"[UD車輛同步] 找不到 gid={UD_VEHICLE_SHEET_GID} 對應的分頁。")
            return False

        result = service.spreadsheets().values().get(
            spreadsheetId=UD_VEHICLE_SHEET_ID, range=f"'{title}'!A1:P{_MAX_SCAN_ROWS}"
        ).execute()
        header_col, next_row = _locate_next_row(result.get("values", []))
        if header_col is None:
            print(f"[UD車輛同步] 在分頁「{title}」裡找不到表頭列（需要同時有「車號」「外送員」欄位）。")
            return False

        data = []
        for name, value in values.items():
            col_index = header_col.get(name)
            if col_index is None:
                continue
            col_letter = _col_index_to_letter(col_index)
            data.append({"range": f"'{title}'!{col_letter}{next_row}", "values": [[value]]})

        service.spreadsheets().values().batchUpdate(
            spreadsheetId=UD_VEHICLE_SHEET_ID,
            body={"valueInputOption": "USER_ENTERED", "data": data},
        ).execute()
        return True
    except Exception as e:
        print(f"[UD車輛同步] 寫入材霈試算表失敗：{e}")
        return False
