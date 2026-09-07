"""管理部「門號繳費提醒」：Cloud Scheduler 每週一上午 9 點呼叫一次，把
一週內要繳費的門號整理成一則 LINE 訊息推播到管理部群組。用共用密鑰驗證
（X-Management-Asset-Reminder-Secret header），不經過同仁登入 session
——呼叫端是 Cloud Scheduler，不是瀏覽器。固定每週觸發一次，天然不會對
同一顆門號重複提醒，不需要像配送部文件到期提醒那樣另外記錄「提醒過了
沒有」。
"""
from fastapi import APIRouter, Header, HTTPException

from management import repository
from management.config import ASSET_REMINDER_SECRET, SIM_PAYMENT_REMINDER_DAYS_AHEAD
from management.line_bot import push_group_message

router = APIRouter()


def _format_message(items: list) -> str:
    lines = [f"📱 本週門號繳費提醒（共 {len(items)} 筆）"]
    for item in items:
        lines.append(f"🔔 {item['name']}（保管人：{item.get('assigned_to') or '-'}）- 繳費日 {item['due_date']}")
    return "\n".join(lines)


@router.post("/api/sim-payment-reminder-check")
def sim_payment_reminder_check(x_management_asset_reminder_secret: str = Header(None)):
    if not ASSET_REMINDER_SECRET or x_management_asset_reminder_secret != ASSET_REMINDER_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")

    items = repository.list_sim_payment_reminders(SIM_PAYMENT_REMINDER_DAYS_AHEAD)
    if not items:
        return {"status": "ok", "reminded": 0}

    sent = push_group_message(_format_message(items))
    return {"status": "ok", "reminded": len(items) if sent else 0, "sent": sent}
