"""每天由 Cloud Scheduler 呼叫，檢查即將到期/已過期的證明，透過公司現有的
LINE 官方帳號推播提醒（2026-09-24 起只有週一會真的推播）。跟 webhook_routes.py 的表單 webhook 一樣，用共用密鑰
驗證（X-Delivery-Reminder-Secret header）、不經過同仁登入 session——呼叫端是
Cloud Scheduler，不是瀏覽器。
"""
import hmac
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Header, HTTPException

from delivery import repository
from delivery.config import (
    REMINDER_DAYS_AHEAD,
    REMINDER_TRIGGER_SECRET,
    REMINDER_WEEKDAY,
    VENDOR_MAP,
)
from delivery.line_notify import push_reminder_message

router = APIRouter()

# Cloud Run 的主機是 UTC 時區，「今天星期幾」要用台灣時間算
_TAIPEI = timezone(timedelta(hours=8))


def _taipei_today():
    return datetime.now(_TAIPEI).date()


_MAX_ITEMS_IN_MESSAGE = 20


def _format_message(items: list) -> str:
    lines = [f"📋 配送部系統－文件到期提醒（共 {len(items)} 筆）"]
    for item in items[:_MAX_ITEMS_IN_MESSAGE]:
        vendor_name = VENDOR_MAP.get(item["vendor"], item["vendor"])
        tag = "⚠️ 已過期" if item["expired"] else "🔔 即將到期"
        lines.append(
            f"{tag}｜{item['personnel_name']}（{vendor_name}）- {item['doc_name']}，到期日 {item['expiry_date']}"
        )
    remaining = len(items) - _MAX_ITEMS_IN_MESSAGE
    if remaining > 0:
        lines.append(f"...還有 {remaining} 筆，請登入系統查看")
    return "\n".join(lines)


@router.post("/api/expiry-reminder-check")
def expiry_reminder_check(x_delivery_reminder_secret: str = Header(None)):
    if not REMINDER_TRIGGER_SECRET or not x_delivery_reminder_secret or not hmac.compare_digest(
        x_delivery_reminder_secret, REMINDER_TRIGGER_SECRET
    ):
        raise HTTPException(status_code=403, detail="Forbidden")

    # 2026-09-24：排程維持每天早上 9 點打過來（使用者決定不改 Cloud
    # Scheduler），但只有週一才真的推播——使用者要的是「到期前一個月開始，
    # 每週一提醒一次，直到更新日期」。
    today = _taipei_today()
    if today.weekday() != REMINDER_WEEKDAY:
        return {"status": "ok", "reminded": 0, "skipped": "not_reminder_day"}

    items = repository.list_expiring_documents(REMINDER_DAYS_AHEAD, today=today)
    if not items:
        return {"status": "ok", "reminded": 0}

    sent = push_reminder_message(_format_message(items))
    return {"status": "ok", "reminded": len(items) if sent else 0, "sent": sent}


def _format_leave_quota_message(alerts: list) -> str:
    lines = [f"📋 配送部系統－假別額度提醒（累積達法定上限 90% 以上，共 {len(alerts)} 筆）"]
    for alert in alerts[:_MAX_ITEMS_IN_MESSAGE]:
        vendor_name = VENDOR_MAP.get(alert["vendor"], alert["vendor"])
        lines.append(
            f"⚠️ {alert['personnel_name']}（{vendor_name}）- {alert['leave_type_name']}，"
            f"已用 {alert['days_used']}/{alert['quota_days']} 天（{alert['percent_used']}%）"
        )
    remaining = len(alerts) - _MAX_ITEMS_IN_MESSAGE
    if remaining > 0:
        lines.append(f"...還有 {remaining} 筆，請登入系統查看")
    return "\n".join(lines)


@router.post("/api/leave-quota-reminder-check")
def leave_quota_reminder_check(x_delivery_reminder_secret: str = Header(None)):
    """跟到期文件提醒一樣是 Cloud Scheduler 打的排程端點（共用同一組
    DELIVERY_REMINDER_SECRET）。跟到期提醒不同的是這裡沒有「已提醒過」的
    排除邏輯：只要累積使用還在90%以上，每次排程執行都會再推播一次（使用者
    要求「達到90%後每次都要提醒」，見 repository.list_leave_quota_alerts()
    的說明）。"""
    if not REMINDER_TRIGGER_SECRET or not x_delivery_reminder_secret or not hmac.compare_digest(
        x_delivery_reminder_secret, REMINDER_TRIGGER_SECRET
    ):
        raise HTTPException(status_code=403, detail="Forbidden")

    alerts = repository.list_leave_quota_alerts()
    if not alerts:
        return {"status": "ok", "reminded": 0}

    sent = push_reminder_message(_format_leave_quota_message(alerts))
    return {"status": "ok", "reminded": len(alerts) if sent else 0, "sent": sent}
