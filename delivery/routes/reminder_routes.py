"""每天由 Cloud Scheduler 呼叫，檢查即將到期/已過期的文件，透過公司現有的
LINE 官方帳號推播提醒。跟 webhook_routes.py 的表單 webhook 一樣，用共用密鑰
驗證（X-Delivery-Reminder-Secret header）、不經過同仁登入 session——呼叫端是
Cloud Scheduler，不是瀏覽器。
"""
from fastapi import APIRouter, Header, HTTPException

from delivery import repository
from delivery.config import (
    REMINDER_DAYS_AHEAD,
    REMINDER_RESEND_INTERVAL_DAYS,
    REMINDER_TRIGGER_SECRET,
    VENDOR_MAP,
)
from delivery.line_notify import push_reminder_message

router = APIRouter()

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
    if not REMINDER_TRIGGER_SECRET or x_delivery_reminder_secret != REMINDER_TRIGGER_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")

    items = repository.list_expiring_documents(REMINDER_DAYS_AHEAD, REMINDER_RESEND_INTERVAL_DAYS)
    if not items:
        return {"status": "ok", "reminded": 0}

    sent = push_reminder_message(_format_message(items))
    if sent:
        repository.mark_documents_reminded(items)

    return {"status": "ok", "reminded": len(items) if sent else 0, "sent": sent}
