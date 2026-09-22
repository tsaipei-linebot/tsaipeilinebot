"""多所派遣 LINE 官方帳號的訊息處理邏輯（2026-09-22 重構自
`taoyuan_dispatch_bot.py`）：解析人員傳來的固定指令（綁定/需求列表/
報名/我的報名），串接 `services/dispatch_service.py` 做真正的 Firestore
讀寫，回傳要回覆的文字。`dispatch_webhook_routes.py` 收到文字訊息事件時
直接呼叫 `handle_message(site, ...)`——指令格式、流程所有所完全共用
同一套，只有 `site` 這個參數決定讀寫哪個所的資料。

只有 `parse_command()` 是純函式（不碰 Firestore），方便寫單元測試；
`handle_message()` 才會真的讀寫資料庫——跟 `hr/incident_report.py` 的
`parse_incident_report()`/`handle_incident_report()` 拆法一樣。

完全沒有自動對話/AI 邏輯，指令看不懂就回覆固定的操作說明，不會嘗試用
AI 猜使用者的意圖（避免猜錯造成誤解，也避免混進招募機器人那套對話
邏輯——見 `dispatch_line.py` 開頭說明）。
"""
import re
from datetime import datetime

from config import TAIPEI_TZ
from services import dispatch_service as service

_BIND_PATTERN = re.compile(r"^綁定[+＋]([^+＋\s]+)[+＋]([^+＋\s]+)$")
_BIND_PREFIX = "綁定"
_LIST_KEYWORDS = {"需求列表", "需求", "查看需求", "查詢需求"}
_REGISTER_PATTERN = re.compile(r"^報名[\s　]+(\S+)$")
_MY_REGISTRATIONS_KEYWORDS = {"我的報名", "報名紀錄", "查詢報名", "查詢報名狀態"}

CMD_BIND = "bind"
CMD_BIND_INVALID = "bind_invalid"
CMD_LIST_POSTINGS = "list_postings"
CMD_REGISTER = "register"
CMD_MY_REGISTRATIONS = "my_registrations"
CMD_HELP = "help"

_BIND_INVALID_TEXT = "綁定格式不對，請用「綁定+姓名+電話」，中間用「+」隔開，例如「綁定+王小明+0912345678」。"

_NOT_BOUND_TEXT = "請先完成身分綁定：傳送「綁定+姓名+電話」，例如「綁定+王小明+0912345678」。"

_REGISTRATION_STATUS_LABELS = {
    service.REGISTRATION_STATUS_PENDING: "審核中",
    service.REGISTRATION_STATUS_APPROVED: "✅ 已核准",
    service.REGISTRATION_STATUS_REJECTED: "❌ 已駁回（額滿或不符資格）",
}


def _help_text(site: str) -> str:
    from dispatch_sites import get_site

    site_config = get_site(site)
    site_name = site_config["name"] if site_config else "派遣"
    return (
        f"{site_name}派遣小幫手，可以用的指令：\n"
        "「綁定+姓名+電話」：第一次使用要先綁定身分，例如「綁定+王小明+0912345678」\n"
        "「需求列表」：查看目前開放報名、符合您人員資格的需求\n"
        "「報名 代碼」：報名需求列表裡的某一筆，例如「報名 A1B2C3」\n"
        "「我的報名」：查詢自己報名紀錄的審核狀態"
    )


def parse_command(text: str) -> dict:
    """回傳 {"type": ..., 對應參數...}，看不懂的指令回傳 {"type": "help"}。
    純文字解析，不牽涉所別。"""
    text = (text or "").strip()
    bind_match = _BIND_PATTERN.match(text)
    if bind_match:
        return {"type": CMD_BIND, "name": bind_match.group(1), "phone": bind_match.group(2)}
    if text.startswith(_BIND_PREFIX):
        return {"type": CMD_BIND_INVALID}
    if text in _LIST_KEYWORDS:
        return {"type": CMD_LIST_POSTINGS}
    register_match = _REGISTER_PATTERN.match(text)
    if register_match:
        return {"type": CMD_REGISTER, "short_code": register_match.group(1)}
    if text in _MY_REGISTRATIONS_KEYWORDS:
        return {"type": CMD_MY_REGISTRATIONS}
    return {"type": CMD_HELP}


def _format_posting_line(posting: dict) -> str:
    start_display = (
        datetime.fromtimestamp(posting["start_time"], TAIPEI_TZ).strftime("%m/%d %H:%M")
        if posting.get("start_time")
        else "-"
    )
    end_display = (
        datetime.fromtimestamp(posting["end_time"], TAIPEI_TZ).strftime("%H:%M") if posting.get("end_time") else "-"
    )
    quals = "、".join(service.QUALIFICATION_MAP.get(q, q) for q in posting.get("required_qualifications") or [])
    return (
        f"【{posting['short_code']}】{posting['location_name']}\n"
        f"時段：{start_display}-{end_display}　需求人數：{posting['headcount']}\n"
        f"需要資格：{quals or '不限'}"
    )


def _format_registration_line(registration: dict, posting: dict) -> str:
    status_label = _REGISTRATION_STATUS_LABELS.get(registration["status"], registration["status"])
    location = posting["location_name"] if posting else "（需求已被刪除）"
    start_display = (
        datetime.fromtimestamp(posting["start_time"], TAIPEI_TZ).strftime("%m/%d %H:%M")
        if posting and posting.get("start_time")
        else "-"
    )
    return f"{location}　{start_display}　{status_label}"


def handle_message(site: str, line_user_id: str, text: str) -> str:
    """真正讀寫 Firestore 的入口——webhook 收到文字訊息時呼叫這個函式，
    回傳的字串直接用 reply_message() 回覆。`site` 決定讀寫哪個所的資料，
    由 `dispatch_webhook_routes.py` 依收到訊息的 Channel 帶入。"""
    command = parse_command(text)
    cmd_type = command["type"]

    if cmd_type == CMD_BIND:
        personnel = service.find_personnel_by_name_and_phone(site, command["name"], command["phone"])
        if not personnel:
            return "查無符合的人員資料，請確認姓名、電話是否跟公司登記的一致，或聯繫管理人員確認。"
        if not personnel.get("active", True):
            return "這筆人員資料目前是停用狀態，請聯繫管理人員確認。"
        service.bind_line_user(site, line_user_id, personnel["id"], personnel["name"])
        return f"綁定成功！{personnel['name']} 您好，可以傳「需求列表」查看目前開放報名的需求。"

    if cmd_type == CMD_BIND_INVALID:
        return _BIND_INVALID_TEXT

    personnel = service.get_bound_personnel(site, line_user_id)
    if not personnel:
        return _NOT_BOUND_TEXT

    if cmd_type == CMD_LIST_POSTINGS:
        postings = service.list_open_postings_for_personnel(site, personnel["id"])
        if not postings:
            return "目前沒有符合您人員資格、還沒報名過的開放需求。"
        lines = [_format_posting_line(p) for p in postings]
        return "目前開放報名的需求：\n\n" + "\n\n".join(lines) + "\n\n回覆「報名 代碼」即可報名。"

    if cmd_type == CMD_REGISTER:
        posting = service.find_posting_by_short_code(site, command["short_code"])
        if not posting:
            return "找不到這個代碼對應的需求，請確認代碼是否正確，或傳「需求列表」重新查看。"
        _ok, message = service.register_for_posting(
            site, posting["id"], personnel["id"], personnel["name"], line_user_id
        )
        return message

    if cmd_type == CMD_MY_REGISTRATIONS:
        registrations = service.list_registrations_by_personnel(site, personnel["id"])
        if not registrations:
            return "您目前沒有任何報名紀錄。"
        lines = [
            _format_registration_line(r, service.get_posting(site, r["posting_id"])) for r in registrations
        ]
        return "您的報名紀錄：\n\n" + "\n\n".join(lines)

    return _help_text(site)
