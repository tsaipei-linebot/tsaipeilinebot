"""職缺維護 LINE 官方帳號的「總機」（2026-09-25，GAS 搬家階段 2 方案 B）。

這個官方帳號同時負責薪資補款、職缺維護、專案合約、「綁定＋姓名＋PIN」登記，原本 LINE Webhook 直接打
GAS。使用者 2026-09-25 選方案 B：**Webhook 改指向平台，平台當總機**，之後每搬一個功能就改成平台自己
處理那一種訊息、其餘繼續轉給 GAS，全部搬完（階段 4）就不再轉、GAS 關掉。

這一版只做「全部原封不動轉給 GAS」，行為跟直接打 GAS 一模一樣：
- 先用 Channel secret 驗 `X-Line-Signature`（LINE 官方的驗證方式；GAS 讀不到 HTTP 標頭才用網址密鑰）。
- 轉發時 body 一個 byte 都不改，打 `JOB_PORTAL_LINE_RELAY_TARGET_URL`（就是原本設在 LINE 後台的 GAS
  網址，含 `?webhook_secret=`），GAS 那邊的驗證照舊。GAS 用事件裡的 replyToken 回覆，轉發很快，不會過期。
- 轉發紀錄只存「時間、事件種類、GAS 回應」，**不存訊息內容**（最近 30 筆，給 `/finance/migration`
  切換後檢查用）。
"""
import base64
import hashlib
import hmac
import json
from datetime import datetime, timezone

import requests

from config import JOB_PORTAL_LINE_CHANNEL_SECRET, JOB_PORTAL_LINE_RELAY_TARGET_URL
from platform_db import get_db

LOG_COLLECTION = "job_portal_line_relay"
LOG_DOC = "recent"
LOG_LIMIT = 30
FORWARD_TIMEOUT = 25


def is_configured() -> bool:
    return bool(JOB_PORTAL_LINE_CHANNEL_SECRET and JOB_PORTAL_LINE_RELAY_TARGET_URL)


def verify_signature(body: bytes, signature: str) -> bool:
    if not JOB_PORTAL_LINE_CHANNEL_SECRET or not signature:
        return False
    digest = hmac.new(JOB_PORTAL_LINE_CHANNEL_SECRET.encode("utf-8"), body, hashlib.sha256).digest()
    return hmac.compare_digest(base64.b64encode(digest).decode("utf-8"), signature)


def describe_events(body: bytes) -> list:
    """回傳事件種類清單（例如 ["message", "postback:review_salary"]），不含任何訊息內容。"""
    try:
        events = json.loads(body.decode("utf-8")).get("events") or []
    except Exception:
        return ["(無法解析)"]
    kinds = []
    for event in events:
        kind = str(event.get("type") or "?")
        if kind == "postback":
            data = str((event.get("postback") or {}).get("data") or "")
            action = next((p.split("=", 1)[1] for p in data.split("&") if p.startswith("action=")), "")
            if action:
                kind = f"postback:{action}"
        kinds.append(kind)
    return kinds


def forward_to_gas(body: bytes) -> tuple:
    """原封不動轉給 GAS，回傳 (ok, 說明)。不拋例外。"""
    try:
        resp = requests.post(
            JOB_PORTAL_LINE_RELAY_TARGET_URL,
            data=body,
            headers={"Content-Type": "application/json; charset=utf-8"},
            timeout=FORWARD_TIMEOUT,
        )
    except requests.RequestException as e:
        return False, f"連不上 GAS（{e.__class__.__name__}）"
    try:
        result = resp.json()
    except ValueError:
        result = {}
    status = result.get("status")
    if resp.status_code == 200 and status == "success":
        return True, "GAS 已處理"
    if status == "error":
        return False, f"GAS 回覆錯誤：{result.get('message') or '沒有說明'}"
    return False, f"GAS 回應 HTTP {resp.status_code}"


def _log_ref():
    return get_db().collection(LOG_COLLECTION).document(LOG_DOC)


def record(kinds: list, ok: bool, note: str) -> None:
    """記一筆轉發紀錄（只保留最近 LOG_LIMIT 筆）。記錄失敗不影響轉發。"""
    try:
        ref = _log_ref()
        snapshot = ref.get()
        entries = (snapshot.to_dict() or {}).get("entries", []) if snapshot.exists else []
        entry = {"at": datetime.now(timezone.utc), "events": kinds, "ok": ok, "note": note}
        ref.set({"entries": ([entry] + list(entries))[:LOG_LIMIT]})
    except Exception as e:
        print(f"[LINE_RELAY] 記錄轉發結果失敗：{e}")


def recent() -> list:
    try:
        snapshot = _log_ref().get()
        return (snapshot.to_dict() or {}).get("entries", []) if snapshot.exists else []
    except Exception:
        return []


def relay(body: bytes) -> None:
    """背景工作：轉發＋記錄。"""
    kinds = describe_events(body)
    ok, note = forward_to_gas(body)
    print(f"[LINE_RELAY] events={kinds} ok={ok} note={note}")
    record(kinds, ok, note)
