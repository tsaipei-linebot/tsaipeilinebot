"""接收 Google 表單（Apps Script onFormSubmit 觸發器）送來的應徵回覆。

這支端點刻意不經過 login_required：呼叫端是 Google 的伺服器（Apps Script
UrlFetchApp），不是瀏覽器登入 session，改用共用密鑰驗證（跟 main.py 的
/internal/load-test-message 是同一種做法）。沒有設定 DELIVERY_FORM_WEBHOOK_SECRET
時一律回傳 403，等同這個 webhook 不存在。
"""
import hmac

from fastapi import APIRouter, Header, HTTPException, Request

from delivery import repository, rider_repository
from delivery.config import (
    FORM_WEBHOOK_SECRET,
    INCIDENT_REPORT_WEBHOOK_SECRET,
    RIDER_WEBHOOK_SECRET,
    VEHICLE_REPORT_WEBHOOK_SECRET,
    VENDOR_MAP,
)
from delivery.form_webhook import extract_answer
from delivery.incident_report import format_weekly_reminder, handle_incident_report
from delivery.rider_events import handle_rider_event
from delivery.vehicle_report import handle_vehicle_report

router = APIRouter()


async def _parse_json_body(request: Request) -> dict:
    """呼叫端（GAS）送過來的資料格式不對時，統一回傳乾淨的 400 而不是
    讓 JSON 解析失敗一路噴成 500——方便之後從錯誤紀錄分辨「呼叫端資料
    有問題」跟「我們這邊程式真的壞了」。"""
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(status_code=400, detail="收到的內容不是合法的 JSON 格式")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="收到的 JSON 格式不是預期的物件結構")
    return body


@router.post("/api/form-submission")
async def form_submission(request: Request, x_delivery_form_secret: str = Header(None)):
    if not FORM_WEBHOOK_SECRET or not x_delivery_form_secret or not hmac.compare_digest(
        x_delivery_form_secret, FORM_WEBHOOK_SECRET
    ):
        raise HTTPException(status_code=403, detail="Forbidden")

    body = await _parse_json_body(request)
    answers = body.get("answers") or {}
    if not isinstance(answers, dict):
        raise HTTPException(status_code=400, detail="answers 欄位格式不正確")

    # 廠商（跟蝦皮的合作方式，如果有）不是表單題目，是每個表單自己的 Apps
    # Script 觸發器寫死帶過來的（見 HANDOFF.md），不合法的值一律當空字串。
    vendor = body.get("vendor") or ""
    if vendor not in VENDOR_MAP:
        vendor = ""
    cooperation_type = body.get("cooperation_type") or ""
    if not repository.get_cooperation_type(cooperation_type):
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
    if not VEHICLE_REPORT_WEBHOOK_SECRET or not x_delivery_vehicle_secret or not hmac.compare_digest(
        x_delivery_vehicle_secret, VEHICLE_REPORT_WEBHOOK_SECRET
    ):
        raise HTTPException(status_code=403, detail="Forbidden")

    body = await _parse_json_body(request)
    text = body.get("text") or ""
    reply = handle_vehicle_report(text)
    return {"reply": reply}


@router.post("/api/incident-report")
async def incident_report_webhook(request: Request, x_delivery_incident_secret: str = Header(None)):
    """跟 vehicle_report_webhook 同一個 GAS 專案、同一個 LINE 群組轉發過來，
    但走獨立的密鑰/端點，解析成意外事件回報寫入資料庫。GAS 那邊收到回覆後
    除了貼回原群組，只有 ok=true（真的成功寫入系統的回報，不是格式錯誤
    的嘗試）才會另外把同仁原始貼的完整文字推播到第二個群組（見
    delivery-gas-project 的 Project6_Incident.js），這裡不需要知道第二個
    群組是誰。"""
    if not INCIDENT_REPORT_WEBHOOK_SECRET or not x_delivery_incident_secret or not hmac.compare_digest(
        x_delivery_incident_secret, INCIDENT_REPORT_WEBHOOK_SECRET
    ):
        raise HTTPException(status_code=403, detail="Forbidden")

    body = await _parse_json_body(request)
    text = body.get("text") or ""
    ok, reply = handle_incident_report(text)
    return {"reply": reply, "ok": ok}


@router.get("/api/incident-weekly-reminder-text")
def incident_weekly_reminder_text(x_delivery_incident_secret: str = Header(None)):
    """每週一由 GAS 的時間驅動觸發器呼叫，取得未結案意外事件的提醒文字。
    這裡只負責「組訊息內容」，實際推播到 LINE 群組是 GAS 那邊用它自己手上
    的 CHANNEL1 Token 做，Python 這邊不需要、也不會拿到那個 Token。"""
    if not INCIDENT_REPORT_WEBHOOK_SECRET or not x_delivery_incident_secret or not hmac.compare_digest(
        x_delivery_incident_secret, INCIDENT_REPORT_WEBHOOK_SECRET
    ):
        raise HTTPException(status_code=403, detail="Forbidden")

    items = repository.list_open_incident_events()
    return {"text": format_weekly_reminder(items)}


@router.post("/api/rider-events")
async def rider_events_webhook(request: Request, x_delivery_rider_secret: str = Header(None)):
    """外送員接單媒合（2026-09-19 新增）：接收 delivery-gas-project 轉發的
    騎士 1 對 1 私訊事件（文字／位置訊息／Postback），回傳一份 LINE 訊息
    物件的 JSON 陣列。跟車輛/意外事件回報同一種做法——GAS 那邊收到回應後
    自己用它手上的 CHANNEL1 Token 呼叫 LINE Reply API 轉發出去，這裡完全
    不摸 LINE API、也不需要另外持有一份 Token（見 delivery/config.py 開頭
    RIDER_WEBHOOK_SECRET 旁的說明）。"""
    if not RIDER_WEBHOOK_SECRET or not x_delivery_rider_secret or not hmac.compare_digest(
        x_delivery_rider_secret, RIDER_WEBHOOK_SECRET
    ):
        raise HTTPException(status_code=403, detail="Forbidden")

    body = await _parse_json_body(request)
    messages = handle_rider_event(body)
    return {"messages": messages}


@router.post("/api/rider-binding-sync")
async def rider_binding_sync_webhook(request: Request, x_delivery_rider_secret: str = Header(None)):
    """外送員接單媒合（2026-09-19 新增）：騎士在 GAS 那邊完成/更新「綁定+
    工號+姓名」私訊後同步呼叫，把 LINE UserId↔工號/姓名 寫進這裡的
    delivery_rider_bindings，即時接單/報班媒合才知道誰是已登記的合作騎士。
    共用同一把 RIDER_WEBHOOK_SECRET，不需要另外設定密鑰。"""
    if not RIDER_WEBHOOK_SECRET or not x_delivery_rider_secret or not hmac.compare_digest(
        x_delivery_rider_secret, RIDER_WEBHOOK_SECRET
    ):
        raise HTTPException(status_code=403, detail="Forbidden")

    body = await _parse_json_body(request)
    user_id = (body.get("userId") or "").strip()
    if not user_id:
        raise HTTPException(status_code=400, detail="userId 欄位不可為空")
    rider_repository.upsert_rider_binding(user_id, body.get("employeeId") or "", body.get("name") or "")
    return {"status": "ok"}


@router.post("/api/personnel-employee-no-sync")
async def personnel_employee_no_sync_webhook(request: Request, x_delivery_rider_secret: str = Header(None)):
    """外送員接單媒合（2026-09-21 新增）：一次性把工號搬移到人員名冊，只給
    delivery-gas-project 的 syncPersonnelEmployeeNo() 呼叫，共用同一把
    RIDER_WEBHOOK_SECRET（不是每個人都需要另外設定一把新密鑰）。實際的
    比對/寫入邏輯在 repository.match_shopee_personnel_employee_no()，這裡
    只負責密鑰驗證跟把結果原樣回傳，讓 GAS 那邊能把「同名同姓/查無此人」
    的清單印出來給管理員人工核對。"""
    if not RIDER_WEBHOOK_SECRET or not x_delivery_rider_secret or not hmac.compare_digest(
        x_delivery_rider_secret, RIDER_WEBHOOK_SECRET
    ):
        raise HTTPException(status_code=403, detail="Forbidden")

    body = await _parse_json_body(request)
    rows = body.get("rows") or []
    if not isinstance(rows, list):
        raise HTTPException(status_code=400, detail="rows 欄位必須是陣列")
    result = repository.match_shopee_personnel_employee_no(rows)
    return result
