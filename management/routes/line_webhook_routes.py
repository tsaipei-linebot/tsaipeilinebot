"""管理部專屬 LINE 官方帳號的 Webhook。刻意只做兩件事：
1. 收到指定的查詢指令（見 _ID_QUERY_PATTERN）時，回覆這個聊天室的 ID
   （群組回 Group ID、多人聊天室回 Room ID、一對一聊天回使用者 User ID），
   方便設定 MANAGEMENT_LINE_GROUP_ID 環境變數，不用像配送部那樣去 Cloud
   Logging 撈。
2. 訊息來自人資專區的意外通報群組（HR_INCIDENT_GROUP_ID）時，改交給
   hr.incident_report 解析、寫入，並把結果直接回覆到同一個群組——沿用
   這組帳號的 webhook（本來就直接打進這支服務，不像配送部那組要先經過
   delivery-gas-project 轉發），不需要另外申請 LINE 帳號、也不需要動
   delivery-gas-project（見 hr/config.py 的說明）。只回覆同一個群組，
   不轉發到第二個群組，比配送部那套單純。

其餘訊息一律不回應——這個群組之後可能拿來給同仁討論事情，機器人不能對
每一則訊息都跳出來回覆，只在真的打查詢指令或意外通報格式時才出聲。完全
沒有自動對話/AI 邏輯，避免跟招募機器人（沛沛）的求職者對話混在一起（見
management/line_bot.py 的說明）。
"""
import re

from fastapi import APIRouter, Header, HTTPException, Request
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from starlette.concurrency import run_in_threadpool

from management.line_bot import handler, line_bot_api

router = APIRouter()

_ID_QUERY_PATTERN = re.compile(r"^[#＃]?(群組\s*id|groupid|group\s*id)$", re.IGNORECASE)


@router.post("/line/callback")
async def line_callback(request: Request, x_line_signature: str = Header(None)):
    if not handler:
        raise HTTPException(status_code=503, detail="LINE channel not configured")
    if not x_line_signature:
        raise HTTPException(status_code=400, detail="Missing X-Line-Signature header")

    body = await request.body()
    try:
        await run_in_threadpool(handler.handle, body.decode("utf-8"), x_line_signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")
    return "OK"


if handler:

    @handler.add(MessageEvent, message=TextMessage)
    def _reply_chat_id_on_command(event):
        text = (event.message.text or "").strip()

        source = event.source
        # 延後 import，避免 management 模組載入時就需要 hr 模組（跟
        # hr/incident_report.py 延後 import hr.repository 是同一種考量）。
        from hr.config import HR_INCIDENT_GROUP_ID

        if source.type == "group" and HR_INCIDENT_GROUP_ID and source.group_id == HR_INCIDENT_GROUP_ID:
            from hr.incident_report import handle_incident_report

            _ok, reply = handle_incident_report(text)
            if reply:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
            return

        if not _ID_QUERY_PATTERN.match(text):
            return

        if source.type == "group":
            reply = f"這個群組的 Group ID：\n{source.group_id}"
        elif source.type == "room":
            reply = f"這個聊天室的 Room ID：\n{source.room_id}"
        else:
            reply = f"你的個人 User ID：\n{source.user_id}"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
