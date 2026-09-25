"""職缺維護 LINE 官方帳號的 Webhook 入口（2026-09-25 新增）：平台當「總機」，見
services/job_portal_line_relay.py 開頭的說明。

LINE 後台 Webhook URL 設成 `https://<平台網域>/api/job-portal/line-webhook`。
- 沒設定 Channel secret／轉發網址 → 503（LINE 後台按「Verify」會看到失敗，提醒還沒設好）。
- 簽章不對 → 403，不轉發。
- 簽章對 → 馬上回 200 給 LINE，轉發在背景做（LINE 要求 Webhook 快速回應；Cloud Run 已設定 CPU 一律
  配置，背景工作不會被暫停）。
"""
from fastapi import APIRouter, BackgroundTasks, Header, Request
from fastapi.responses import JSONResponse

from services import job_portal_line_relay as relay

router = APIRouter()


@router.post("/api/job-portal/line-webhook")
async def job_portal_line_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_line_signature: str = Header(""),
):
    if not relay.is_configured():
        return JSONResponse({"status": "error", "message": "not configured"}, status_code=503)
    body = await request.body()
    if not relay.verify_signature(body, x_line_signature):
        print("[LINE_RELAY] 簽章驗證失敗，拒絕轉發")
        return JSONResponse({"status": "error", "message": "invalid signature"}, status_code=403)
    background_tasks.add_task(relay.relay, body)
    return JSONResponse({"status": "success"})
