"""人資專區的兩個排程提醒端點，用共用密鑰驗證，不經過同仁登入 session
（呼叫端是 Cloud Scheduler，不是瀏覽器）：

- 意外通報未結案週提醒：直接用管理部那組 LINE 帳號（見 management/line_bot.py）
  推播回意外通報那個群組——沿用同一組帳號的 Token，不需要另外申請 LINE
  官方帳號。
- 公司證照到期提醒：推播對象沿用管理部「門號繳費提醒」現有的群組設定，
  不用另外指定推播對象。
"""
from fastapi import APIRouter, Header, HTTPException

from hr import repository
from hr.config import (
    HR_INCIDENT_GROUP_ID,
    HR_INCIDENT_REMINDER_SECRET,
    HR_LICENSE_REMINDER_SECRET,
    LICENSE_REMINDER_DAYS_AHEAD,
    LICENSE_REMINDER_RESEND_INTERVAL_DAYS,
)
from hr.incident_report import format_weekly_reminder
from management.line_bot import push_group_message, push_message

router = APIRouter()


@router.post("/api/incident-weekly-reminder-check")
def incident_weekly_reminder_check(x_hr_incident_reminder_secret: str = Header(None)):
    if not HR_INCIDENT_REMINDER_SECRET or x_hr_incident_reminder_secret != HR_INCIDENT_REMINDER_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")

    items = repository.list_open_incident_events()
    text = format_weekly_reminder(items)
    if not text:
        return {"status": "ok", "reminded": 0}

    sent = push_message(HR_INCIDENT_GROUP_ID, text)
    return {"status": "ok", "reminded": len(items) if sent else 0, "sent": sent}


def _format_license_message(items: list) -> str:
    lines = [f"📜 公司證照到期提醒（共 {len(items)} 筆）"]
    for item in items:
        flag = "已過期" if item.get("expired") else "即將到期"
        lines.append(f"⚠️ {item['name']}（{flag}）- 到期日 {item['expiry_date']}")
    return "\n".join(lines)


@router.post("/api/license-reminder-check")
def license_reminder_check(x_hr_license_reminder_secret: str = Header(None)):
    if not HR_LICENSE_REMINDER_SECRET or x_hr_license_reminder_secret != HR_LICENSE_REMINDER_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")

    items = repository.list_expiring_licenses(LICENSE_REMINDER_DAYS_AHEAD, LICENSE_REMINDER_RESEND_INTERVAL_DAYS)
    if not items:
        return {"status": "ok", "reminded": 0}

    sent = push_group_message(_format_license_message(items))
    if sent:
        repository.mark_licenses_reminded([item["id"] for item in items])
    return {"status": "ok", "reminded": len(items) if sent else 0, "sent": sent}
