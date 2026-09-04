"""接收 Google 表單（Apps Script onFormSubmit 觸發器）送來的應徵回覆。

這支端點刻意不經過 login_required：呼叫端是 Google 的伺服器（Apps Script
UrlFetchApp），不是瀏覽器登入 session，改用共用密鑰驗證（跟 main.py 的
/internal/load-test-message 是同一種做法）。沒有設定 DELIVERY_FORM_WEBHOOK_SECRET
時一律回傳 403，等同這個 webhook 不存在。
"""
from fastapi import APIRouter, Header, HTTPException, Request

from delivery import repository
from delivery.config import (
    COOPERATION_TYPE_MAP,
    FORM_WEBHOOK_SECRET,
    INCIDENT_REPORT_WEBHOOK_SECRET,
    VEHICLE_REPORT_WEBHOOK_SECRET,
    VENDOR_MAP,
)
from delivery.form_webhook import extract_answer
from delivery.incident_report import format_weekly_reminder, handle_incident_report
from delivery.vehicle_report import handle_vehicle_report

router = APIRouter()


@router.post("/api/form-submission")
async def form_submission(request: Request, x_delivery_form_secret: str = Header(None)):
    if not FORM_WEBHOOK_SECRET or x_delivery_form_secret != FORM_WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")

    body = await request.json()
    answers = body.get("answers") or {}

    # 廠商（跟蝦皮的合作方式，如果有）不是表單題目，是每個表單自己的 Apps
    # Script 觸發器寫死帶過來的（見 HANDOFF.md），不合法的值一律當空字串。
    vendor = body.get("vendor") or ""
    if vendor not in VENDOR_MAP:
        vendor = ""
    cooperation_type = body.get("cooperation_type") or ""
    if cooperation_type not in COOPERATION_TYPE_MAP:
        cooperation_type = ""

    name = extract_answer(answers, "姓名")
    phone = extract_answer(answers, "電話")

    if not name:
        raise HTTPException(status_code=400, detail="表單回覆裡找不到姓名欄位")

    applicant_id = repository.upsert_applicant(name, phone, answers, vendor=vendor, cooperation_type=cooperation_type)
    return {"status": "ok", "applicant_id": applicant_id}


@router.post("/api/vehicle-report")
async def vehicle_report_webhook(request: Request, x_delivery_vehicle_secret: str = Header(None)):
    """接收另一個獨立 LINE 官方帳號（跟這支招募機器人是不同 Channel）的
    Google Apps Script 專案（delivery-gas-project）轉發過來的群組訊息，解析
    成領車/還車回報。這支端點本身不判斷訊息來源是哪個群組——那個防呆是
    GAS 那邊做的（只有它設定的那個群組會被轉發過來），這裡只認密鑰。"""
    if not VEHICLE_REPORT_WEBHOOK_SECRET or x_delivery_vehicle_secret != VEHICLE_REPORT_WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")

    body = await request.json()
    text = body.get("text") or ""
    reply = handle_vehicle_report(text)
    return {"reply": reply}


@router.post("/api/incident-report")
async def incident_report_webhook(request: Request, x_delivery_incident_secret: str = Header(None)):
    """跟 vehicle_report_webhook 同一個 GAS 專案、同一個 LINE 群組轉發過來，
    但走獨立的密鑰/端點，解析成意外事件回報寫入資料庫。GAS 那邊收到回覆後
    除了貼回原群組，還會另外推播同一則訊息到第二個群組（見
    delivery-gas-project 的 Project6_Incident.js），這裡不需要知道第二個
    群組是誰。"""
    if not INCIDENT_REPORT_WEBHOOK_SECRET or x_delivery_incident_secret != INCIDENT_REPORT_WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")

    body = await request.json()
    text = body.get("text") or ""
    reply = handle_incident_report(text)
    return {"reply": reply}


@router.get("/api/incident-weekly-reminder-text")
def incident_weekly_reminder_text(x_delivery_incident_secret: str = Header(None)):
    """每週一由 GAS 的時間驅動觸發器呼叫，取得未結案意外事件的提醒文字。
    這裡只負責「組訊息內容」，實際推播到 LINE 群組是 GAS 那邊用它自己手上
    的 CHANNEL1 Token 做，Python 這邊不需要、也不會拿到那個 Token。"""
    if not INCIDENT_REPORT_WEBHOOK_SECRET or x_delivery_incident_secret != INCIDENT_REPORT_WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")

    items = repository.list_open_incident_events()
    return {"text": format_weekly_reminder(items)}
