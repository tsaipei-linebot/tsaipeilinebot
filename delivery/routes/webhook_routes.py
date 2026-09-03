"""接收 Google 表單（Apps Script onFormSubmit 觸發器）送來的應徵回覆。

這支端點刻意不經過 login_required：呼叫端是 Google 的伺服器（Apps Script
UrlFetchApp），不是瀏覽器登入 session，改用共用密鑰驗證（跟 main.py 的
/internal/load-test-message 是同一種做法）。沒有設定 DELIVERY_FORM_WEBHOOK_SECRET
時一律回傳 403，等同這個 webhook 不存在。
"""
from fastapi import APIRouter, Header, HTTPException, Request

from delivery import repository
from delivery.config import FORM_WEBHOOK_SECRET
from delivery.form_webhook import extract_answer

router = APIRouter()


@router.post("/api/form-submission")
async def form_submission(request: Request, x_delivery_form_secret: str = Header(None)):
    if not FORM_WEBHOOK_SECRET or x_delivery_form_secret != FORM_WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")

    body = await request.json()
    answers = body.get("answers") or {}

    name = extract_answer(answers, "姓名")
    phone = extract_answer(answers, "電話")

    if not name:
        raise HTTPException(status_code=400, detail="表單回覆裡找不到姓名欄位")

    applicant_id = repository.create_applicant(name, phone, answers)
    return {"status": "ok", "applicant_id": applicant_id}
