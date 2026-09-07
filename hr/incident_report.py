"""解析 LINE 群組傳來的「意外事件回報」訊息，並串接人資意外通報的 Firestore
存取，回傳要回覆到 LINE 群組的文字。

沿用管理部現有的 LINE 官方帳號（webhook 直接打進
management/routes/line_webhook_routes.py，不經過 delivery-gas-project 轉發），
只回覆同一個群組的登記確認訊息，不轉發到第二個群組——比配送部
（見 delivery/incident_report.py）那套單純。

表單 11 個欄位跟配送部「意外事件回報」相同，唯一差異是「廠商名稱」這裡
改成自由文字（不限定蝦皮/UD/UC/順豐），同仁填什麼就存什麼，不做白名單驗證。

只有 parse_incident_report() 是純函式（不碰 Firestore），方便寫單元測試；
handle_incident_report() 才會真的寫資料庫。

訊息格式（同仁照公司內部的範本貼過來，編號可以是「1.」「1、」等寫法，這裡
不要求編號完全對齊，只看欄位名稱本身；★風險等級那行是系統事後才填的，
回報時完全忽略）：

    意外事件回傳格式             <- 啟動關鍵字，必須單獨一行、一字不差
    1.廠商名稱：XX工廠
    2.身分類別：雇傭
    3.人員名稱：林子椉
    4.發生時間：9/4 11:00        <- 月/日 時:分，沒有年份，用系統目前年份補上
    5.發生地點：金山南路一段126號
    6.執行勤務中/上下班途中：執行勤務中
    7.是否報警：有
    8.受傷情形：無
    9.是否聯繫家屬：無
    10.是否牽扯他人：有
    11.意外事件經過：行進其間與汽車後照鏡擦撞

    ★風險等級：(此欄不用填寫)     <- 忽略，風險等級是管理員事後在網頁上評估設定的
"""
import re
from datetime import date

from hr.config import DUTY_STATUSES, IDENTITY_TYPES, YES_NO_VALUES

_MAX_ITEMS_IN_WEEKLY_REMINDER = 20

_TRIGGER_LINE = "意外事件回傳格式"

# 編號前綴（「1.」「1、」「1．」都可以，也容忍完全沒有編號）當作可有可無的
# 雜訊，真正判斷欄位靠後面的欄位名稱本身，比照配送部那套一開始就設計得
# 寬鬆一點。
_NUM_PREFIX = r"^\d*[.．、]?\s*"

_FIELD_PATTERNS = {
    "vendor": re.compile(_NUM_PREFIX + r"廠商名稱[：:]\s*(.*)"),
    "identity_type": re.compile(_NUM_PREFIX + r"身分類別[：:]\s*(.*)"),
    "personnel_name": re.compile(_NUM_PREFIX + r"人員名稱[：:]\s*(.*)"),
    "occurred_at": re.compile(_NUM_PREFIX + r"發生時間[：:]\s*(.*)"),
    "location": re.compile(_NUM_PREFIX + r"發生地點[：:]\s*(.*)"),
    "duty_status": re.compile(_NUM_PREFIX + r"執行勤務中/上下班途中[：:]\s*(.*)"),
    "police_called": re.compile(_NUM_PREFIX + r"是否報警[：:]\s*(.*)"),
    "injury": re.compile(_NUM_PREFIX + r"受傷情形[：:]\s*(.*)"),
    "family_contacted": re.compile(_NUM_PREFIX + r"是否聯繫家屬[：:]\s*(.*)"),
    "third_party_involved": re.compile(_NUM_PREFIX + r"是否牽扯他人[：:]\s*(.*)"),
    "description": re.compile(_NUM_PREFIX + r"意外事件經過[：:]\s*(.*)"),
}

_DATETIME_PATTERN = re.compile(r"^(\d{1,2})[/-](\d{1,2})\s+(\d{1,2}):(\d{2})$")

_YES_NO_FIELD_NAMES = {
    "police_called": "是否報警",
    "family_contacted": "是否聯繫家屬",
    "third_party_involved": "是否牽扯他人",
}

PARSE_ERROR_MESSAGES = {
    "missing_fields": "❌ 回報格式有誤：11 個欄位都要填，請照範本重新回覆。",
    "invalid_identity_type": "❌ 身分類別請填「雇傭」或「承攬」。",
    "invalid_duty_status": "❌ 第 6 項請填「執行勤務中」或「上下班途中」。",
    "invalid_datetime": "❌ 發生時間格式看不懂，請用「9/4 11:00」這種「月/日 時:分」的格式重新回覆。",
}
for _field, _label in _YES_NO_FIELD_NAMES.items():
    PARSE_ERROR_MESSAGES[f"invalid_{_field}"] = f"❌ 「{_label}」請填「有」或「無」。"

# 沒有 _TRIGGER_LINE 那行的訊息（例如同仁在群組裡的日常聊天）一律視為不是
# 在回報意外事件，不回覆、不解析。
NOT_A_REPORT = "not_a_report"


def _normalize_datetime(value: str, today: date = None) -> str:
    """把「9/4 11:00」轉成「YYYY-MM-DD HH:MM」，年份用系統目前年份補上；
    轉不出來回傳空字串。"""
    value = (value or "").strip()
    m = _DATETIME_PATTERN.match(value)
    if not m:
        return ""
    month, day, hour, minute = m.groups()
    year = (today or date.today()).year
    try:
        return f"{year}-{int(month):02d}-{int(day):02d} {int(hour):02d}:{minute}"
    except ValueError:
        return ""


def parse_incident_report(text: str) -> dict:
    """解析意外事件回報訊息。回傳 dict：
    - ok=True 時附上 11 個欄位（見 _FIELD_PATTERNS 的 key）。
    - ok=False 時附上 error 代碼（對應 PARSE_ERROR_MESSAGES 的 key）。
    """
    fields = {}
    has_trigger_line = False
    for line in (text or "").splitlines():
        line = line.strip()
        if line == _TRIGGER_LINE:
            has_trigger_line = True
            continue
        for key, pattern in _FIELD_PATTERNS.items():
            m = pattern.match(line)
            if m:
                fields[key] = m.group(1).strip()

    if not has_trigger_line:
        return {"ok": False, "error": NOT_A_REPORT}

    if any(not fields.get(key) for key in _FIELD_PATTERNS):
        return {"ok": False, "error": "missing_fields"}

    if fields["identity_type"] not in IDENTITY_TYPES:
        return {"ok": False, "error": "invalid_identity_type"}

    if fields["duty_status"] not in DUTY_STATUSES:
        return {"ok": False, "error": "invalid_duty_status"}

    for key in _YES_NO_FIELD_NAMES:
        if fields[key] not in YES_NO_VALUES:
            return {"ok": False, "error": f"invalid_{key}"}

    occurred_at = _normalize_datetime(fields["occurred_at"])
    if not occurred_at:
        return {"ok": False, "error": "invalid_datetime"}

    return {
        "ok": True,
        "vendor": fields["vendor"],
        "identity_type": fields["identity_type"],
        "personnel_name": fields["personnel_name"],
        "occurred_at": occurred_at,
        "location": fields["location"],
        "duty_status": fields["duty_status"],
        "police_called": fields["police_called"],
        "injury": fields["injury"],
        "family_contacted": fields["family_contacted"],
        "third_party_involved": fields["third_party_involved"],
        "description": fields["description"],
    }


def handle_incident_report(text: str) -> tuple:
    """解析 + 寫入資料庫，回傳 (ok, reply)：
    - reply 是要回覆到 LINE 群組的文字；空字串代表這則訊息看起來不是在
      嘗試回報，呼叫端應該保持沉默、不要回覆任何東西。
    - ok 代表這筆資料是不是真的成功寫入系統。格式錯誤（ok=False 但 reply
      非空，同仁會看到 ❌ 提示）跟「根本不是在回報」（ok=False 且 reply 是
      空字串）都算失敗，差別只在要不要回覆。

    延後 import hr.repository，避免這個模組被載入時就需要 Firestore 憑證。"""
    parsed = parse_incident_report(text)
    if not parsed["ok"]:
        if parsed["error"] == NOT_A_REPORT:
            return False, ""
        return False, PARSE_ERROR_MESSAGES.get(parsed["error"], "❌ 格式有誤，請確認後重新回報。")

    from hr import repository

    _incident_id, created = repository.create_incident_event(parsed)
    action = "已登記" if created else "已更新"

    return True, (
        f"✅ {action}意外事件回報：{parsed['personnel_name']}（{parsed['identity_type']}），"
        f"{parsed['occurred_at']}，{parsed['location']}。已寫入系統，後續由管理員評估風險等級並追蹤結案。"
    )


def format_weekly_reminder(items: list) -> str:
    """組成每週未結案意外事件提醒訊息；沒有未結案案件時回傳空字串，呼叫端
    看到空字串就不要推播，避免每週固定洗版。"""
    if not items:
        return ""
    lines = [f"📋 人資專區－意外事件未結案提醒（共 {len(items)} 筆）"]
    for item in items[:_MAX_ITEMS_IN_WEEKLY_REMINDER]:
        lines.append(
            f"⚠️ {item.get('personnel_name')}（{item.get('vendor')}）- {item.get('occurred_at')}，{item.get('location')}"
        )
    remaining = len(items) - _MAX_ITEMS_IN_WEEKLY_REMINDER
    if remaining > 0:
        lines.append(f"...還有 {remaining} 筆，請登入系統查看")
    return "\n".join(lines)
