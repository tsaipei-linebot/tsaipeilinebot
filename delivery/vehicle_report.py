"""解析 LINE 群組傳來的「車輛回報」訊息（領車/還車），並串接車輛管理的
Firestore 存取，回傳要回覆到 LINE 群組的文字。

只有 parse_vehicle_report() 是純函式（不碰 Firestore），方便寫單元測試；
handle_vehicle_report() 才會真的查/寫資料庫，交給 handlers/message_handler.py
呼叫。

訊息格式（同仁會照公司內部的範本貼過來，可能連同說明文字一起貼，這裡只挑
「欄位名：值」這種格式的行來抓資料，其餘文字忽略）：

    廠商：UD
    姓名：李睿哲
    開始日期：2026-8-26      <- 領車：開始日期有填、結束日期空白
    結束日期：
    車號：ERV-2360
    服務門市：臺北市...       <- 領車用「服務門市」、還車用「還車地點」，
                               但實際判斷事件類型是看哪個日期欄位有填，
                               不是看這一行的欄位名稱。
"""
import re

from delivery.config import VENDOR_LOOKUP

_FIELD_PATTERNS = {
    "vendor": re.compile(r"廠商[：:]\s*(.*)"),
    "personnel_name": re.compile(r"姓名[：:]\s*(.*)"),
    "start_date": re.compile(r"開始日期[：:]\s*(.*)"),
    "end_date": re.compile(r"結束日期[：:]\s*(.*)"),
    "vehicle_no": re.compile(r"車號[：:]\s*(.*)"),
    "location": re.compile(r"(?:服務門市|還車地點)[：:]\s*(.*)"),
}

_DATE_PATTERN = re.compile(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})$")

PARSE_ERROR_MESSAGES = {
    "missing_fields": "❌ 回報格式有誤：廠商、姓名、車號、開始或結束日期（擇一）、地點都要填，請照範本重新回覆。",
    "invalid_vendor": "❌ 廠商看不懂，請填蝦皮／UD／UC／順豐其中一個。",
    "ambiguous_dates": "❌ 開始日期跟結束日期不能同時填：領車只填開始日期，還車只填結束日期。",
    "invalid_date": "❌ 日期格式看不懂，請用「2026-8-25」這種年-月-日的格式重新回覆。",
}

# 群組裡任何訊息都會被轉發進來解析（見 delivery-gas-project 的
# Project5_Vehicle.js），所以完全沒有任何一個回報欄位關鍵字（廠商/姓名/
# 車號/日期/地點）的訊息一律視為同仁在群組裡的普通聊天，不當成回報格式錯誤
# 處理——不然像「早安」「謝謝」這種訊息也會被回覆一堆錯誤說明，很擾民。
NOT_A_REPORT = "not_a_report"

EVENT_ERROR_MESSAGES = {
    "vehicle_not_found": "❌ 系統裡查不到這台車，請先請管理員到網頁「車輛管理」新增這台車再回報。",
    "vendor_mismatch": "❌ 這台車登記的廠商跟回報的不一樣，請確認車號或廠商有沒有打錯。",
    "not_available": "❌ 這台車目前使用中或待維修，沒辦法再次派車。",
    "not_in_use": "❌ 系統裡這台車目前不是使用中狀態，沒有領用中的紀錄可以還車。",
}


def _normalize_date(value: str) -> str:
    """把「2026-8-25」這種沒補零的日期轉成 YYYY-MM-DD；轉不出來回傳空字串。"""
    value = (value or "").strip()
    m = _DATE_PATTERN.match(value)
    if not m:
        return ""
    year, month, day = m.groups()
    return f"{year}-{int(month):02d}-{int(day):02d}"


def parse_vehicle_report(text: str) -> dict:
    """解析回報訊息。回傳 dict：
    - ok=True 時附上 event_type/vendor/personnel_name/vehicle_no/event_date/location。
    - ok=False 時附上 error 代碼（對應 PARSE_ERROR_MESSAGES 的 key）。
    """
    fields = {}
    for line in (text or "").splitlines():
        line = line.strip()
        for key, pattern in _FIELD_PATTERNS.items():
            m = pattern.match(line)
            if m:
                fields[key] = m.group(1).strip()

    if not fields:
        return {"ok": False, "error": NOT_A_REPORT}

    vendor_raw = fields.get("vendor", "")
    personnel_name = fields.get("personnel_name", "")
    vehicle_no = fields.get("vehicle_no", "")
    location = fields.get("location", "")
    start_raw = fields.get("start_date", "")
    end_raw = fields.get("end_date", "")
    start_date = _normalize_date(start_raw)
    end_date = _normalize_date(end_raw)

    if start_date and end_date:
        return {"ok": False, "error": "ambiguous_dates"}
    if (start_raw and not start_date) or (end_raw and not end_date):
        return {"ok": False, "error": "invalid_date"}

    if not vendor_raw or not personnel_name or not vehicle_no or not location or (not start_date and not end_date):
        return {"ok": False, "error": "missing_fields"}

    vendor = VENDOR_LOOKUP.get(vendor_raw.lower(), "")
    if not vendor:
        return {"ok": False, "error": "invalid_vendor"}

    event_type = "checkout" if start_date else "return"
    return {
        "ok": True,
        "event_type": event_type,
        "vendor": vendor,
        "personnel_name": personnel_name,
        "vehicle_no": vehicle_no,
        "event_date": start_date or end_date,
        "location": location,
    }


def handle_vehicle_report(text: str) -> str:
    """解析 + 寫入資料庫，回傳要回覆到 LINE 群組的文字；回傳空字串代表這則
    訊息看起來不是在嘗試回報（例如同仁的日常聊天），呼叫端應該保持沉默、
    不要回覆任何東西。延後 import delivery.repository，避免這個模組被載入
    時就需要 Firestore 憑證。"""
    parsed = parse_vehicle_report(text)
    if not parsed["ok"]:
        if parsed["error"] == NOT_A_REPORT:
            return ""
        return PARSE_ERROR_MESSAGES.get(parsed["error"], "❌ 格式有誤，請確認後重新回報。")

    from delivery import repository

    ok, error = repository.record_vehicle_event(
        vehicle_no=parsed["vehicle_no"],
        vendor=parsed["vendor"],
        personnel_name=parsed["personnel_name"],
        event_type=parsed["event_type"],
        event_date=parsed["event_date"],
        location=parsed["location"],
        source="line",
    )
    if not ok:
        return EVENT_ERROR_MESSAGES.get(error, "❌ 這筆回報無法處理，請確認車輛狀態。")

    action_name = "領車" if parsed["event_type"] == "checkout" else "還車"
    return (
        f"✅ 已登記{action_name}：車號 {parsed['vehicle_no']}，{parsed['personnel_name']}，"
        f"{parsed['event_date']}，{parsed['location']}"
    )
