"""職缺維護系統（GAS）委託材霈平台寄信的端點（2026-09-23 新增）。

薪資補款核准後的通知信原本是 GAS 自己用 `GmailApp.sendEmail()` 寄，撞到
Apps Script「每天 100 個收件人」的額度上限（見 services/email_service.py
開頭的完整說明）。改成 GAS 照舊組好信件內容跟附件，最後一步改呼叫這裡，
由平台用 SMTP 寄出——**核准流程、寫試算表、產 PDF 全部維持在 GAS 不變**，
只搬「寄出去」這一個動作，萬一有問題把 GAS 那一行改回去就回到原狀。

附件跟內嵌圖片都用 base64 傳（GAS 那邊 `Utilities.base64Encode()`），
在這裡解碼成 bytes 再交給 email_service。內嵌圖片的 `content_id` 對應
信件 HTML 裡的 `<img src="cid:那個 id">`，補款佐證照片就是靠這個顯示在
信件內文裡，不是只當附件。

密鑰比對用 `hmac.compare_digest`（固定時間比對），跟 main.py 那幾支
內部端點同一種寫法。
"""
import base64
import hmac

from fastapi import APIRouter, Header, HTTPException, Request

from config import JOB_PORTAL_MAIL_WEBHOOK_SECRET
from services import email_service

router = APIRouter()

# 一封信的附件加起來的上限。GAS 那邊一封補款通知信最多是「佐證照片 +
# PDF 存查單」兩個附件，正常情況遠低於這個數字；設這個上限純粹是避免
# 異常的請求把記憶體吃爆。
_MAX_TOTAL_ATTACHMENT_BYTES = 20 * 1024 * 1024


def _decode_parts(raw_parts: list, require_content_id: bool = False) -> list:
    """把請求裡的 base64 附件/內嵌圖片解碼成 email_service 要的格式。
    解不開的單筆直接跳過（寧可少一個附件也要把信寄出去），不讓整封信失敗。"""
    parts = []
    total_bytes = 0
    for raw in raw_parts or []:
        if not isinstance(raw, dict):
            continue
        content_id = (raw.get("content_id") or "").strip()
        if require_content_id and not content_id:
            continue
        try:
            content = base64.b64decode(raw.get("base64") or "", validate=True)
        except Exception:
            continue
        if not content:
            continue
        total_bytes += len(content)
        if total_bytes > _MAX_TOTAL_ATTACHMENT_BYTES:
            break
        part = {
            "content": content,
            "filename": (raw.get("filename") or "").strip(),
            "mime_type": (raw.get("mime_type") or "").strip(),
        }
        if require_content_id:
            part["content_id"] = content_id
        parts.append(part)
    return parts


def _normalize_recipients(raw) -> list:
    """收件人允許傳 list 或逗號分隔的字串（GAS 那邊本來就是用逗號串起來
    的字串），統一整理成去重、去空白的 list。"""
    if isinstance(raw, str):
        candidates = raw.split(",")
    elif isinstance(raw, list):
        candidates = raw
    else:
        candidates = []
    seen = []
    for candidate in candidates:
        address = str(candidate or "").strip()
        if address and address not in seen:
            seen.append(address)
    return seen


@router.post("/api/job-portal/send-mail")
async def send_job_portal_mail(request: Request, x_job_portal_mail_secret: str = Header(None)):
    if (
        not JOB_PORTAL_MAIL_WEBHOOK_SECRET
        or not x_job_portal_mail_secret
        or not hmac.compare_digest(x_job_portal_mail_secret, JOB_PORTAL_MAIL_WEBHOOK_SECRET)
    ):
        raise HTTPException(status_code=403, detail="forbidden")

    body = await request.json()
    to_addresses = _normalize_recipients(body.get("to"))
    subject = (body.get("subject") or "").strip()
    html_body = body.get("html") or ""

    if not to_addresses:
        return {"status": "error", "message": "沒有任何收件人，這封信沒有寄出。"}
    if not subject or not html_body:
        return {"status": "error", "message": "信件主旨或內容是空的，這封信沒有寄出。"}

    ok, message = email_service.send_email(
        to_addresses,
        subject,
        html_body,
        attachments=_decode_parts(body.get("attachments")),
        inline_images=_decode_parts(body.get("inline_images"), require_content_id=True),
    )
    if ok:
        return {"status": "success", "recipients": ",".join(to_addresses)}
    # 失敗原因照實回給 GAS，GAS 會把它接到主管的 LINE 訊息跟「補寄信」的
    # 結果訊息裡（訊息本身已經是白話，見 email_service.py）。
    print(f"[薪資補款寄信] 失敗（收件人：{','.join(to_addresses)}）：{message}")
    return {"status": "error", "message": message}
