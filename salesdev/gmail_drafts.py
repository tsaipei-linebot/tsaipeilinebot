"""業務開發寄信「做法二」（2026-09-26 新增）：平台在 gary@tsaipei.com 的 Gmail 建好一封
**草稿**（收件人、主旨、內文都填好、公司簡介 PDF 已經夾好），使用者打開草稿看過再按寄出。

為什麼要這樣做：原本的做法（開 Gmail 撰寫畫面的網址）只能帶純文字，**沒辦法帶附件**，
使用者要每封自己夾 PDF；使用者決定改用草稿，夾附件比較好。平台**仍然不會自己寄信**。

**授權方式：OAuth（使用者自己按一次「連結 Gmail」）**，不用 Google Workspace 的「全網域
委派」——委派等於讓平台能代表公司任何一個人收發信，權限太大。這裡只要求
`gmail.compose`（建立草稿／寄信的權限，**不能讀信**），而且平台程式只會建草稿。
OAuth 用戶端要建在 Google Cloud 專案 tsaipei-505807，同意畫面設成「內部」（只有公司
Workspace 帳號能授權，不用送 Google 審核），用戶端 ID／密鑰放 Cloud Run 環境變數
`SALESDEV_GMAIL_OAUTH_CLIENT_ID`／`SALESDEV_GMAIL_OAUTH_CLIENT_SECRET`（步驟見 HANDOFF.md）。

授權後拿到的 refresh token 存 Firestore `salesdev_settings/gmail_oauth`（只有平台服務帳戶
讀得到）。要取消：Google 帳號 →「安全性」→「具有帳戶存取權的第三方應用程式」移除，
或平台信件範本頁按「中斷連結」。

不另外裝 google-auth-oauthlib：授權碼換 token、refresh 都是標準的 OAuth HTTP 請求，
直接用 requests 打；草稿用 Gmail API 的上傳端點（`uploadType=media`，整封信的 MIME
直接送，附件上限約 35MB，比只能送 5MB 的一般端點大）。
"""
import os
import secrets
import uuid
from email.message import EmailMessage
from email.utils import formataddr
from urllib.parse import urlencode

import requests

from config import GCP_PROJECT_ID, SALESDEV_GMAIL_ACCOUNT, SALESDEV_GMAIL_OAUTH_CLIENT_ID, SALESDEV_GMAIL_OAUTH_CLIENT_SECRET
from salesdev import repository

SCOPE = "https://www.googleapis.com/auth/gmail.compose"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
PROFILE_URL = "https://gmail.googleapis.com/gmail/v1/users/me/profile"
DRAFT_UPLOAD_URL = "https://gmail.googleapis.com/upload/gmail/v1/users/me/drafts?uploadType=media"
SETTINGS_DOC = "gmail_oauth"
REQUEST_TIMEOUT = 30

MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024  # Gmail 整封信上限 25MB，留一點給內文跟編碼膨脹
SENDER_NAME = "材霈有限公司"


class GmailNotConnected(RuntimeError):
    """還沒連結 Gmail，或授權被取消了（要重新按「連結 Gmail」）。"""


def is_configured() -> bool:
    return bool(SALESDEV_GMAIL_OAUTH_CLIENT_ID and SALESDEV_GMAIL_OAUTH_CLIENT_SECRET)


def _settings_ref():
    return repository.get_db().collection(repository.SETTINGS_COLLECTION).document(SETTINGS_DOC)


def connection_status() -> dict:
    """給畫面用：{"configured", "connected", "email", "connected_at"}（不含 token）。"""
    snapshot = _settings_ref().get()
    data = (snapshot.to_dict() or {}) if snapshot.exists else {}
    return {
        "configured": is_configured(),
        "connected": bool(data.get("refresh_token")),
        "email": data.get("email", ""),
        "connected_at": data.get("connected_at", ""),
        "connected_by": data.get("connected_by", ""),
    }


# ---------------------------------------------------------------------------
# OAuth
# ---------------------------------------------------------------------------

def new_state() -> str:
    return secrets.token_urlsafe(24)


def authorization_url(redirect_uri: str, state: str) -> str:
    params = {
        "client_id": SALESDEV_GMAIL_OAUTH_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        # 每次都要求同意，才一定拿得到 refresh token（Google 只有第一次同意才給）
        "prompt": "consent",
        "login_hint": SALESDEV_GMAIL_ACCOUNT,
        "hd": SALESDEV_GMAIL_ACCOUNT.split("@")[-1],
        "state": state,
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def _post_token(data: dict) -> dict:
    response = requests.post(
        TOKEN_URL,
        data={**data, "client_id": SALESDEV_GMAIL_OAUTH_CLIENT_ID, "client_secret": SALESDEV_GMAIL_OAUTH_CLIENT_SECRET},
        timeout=REQUEST_TIMEOUT,
    )
    payload = response.json() if response.content else {}
    if response.status_code != 200:
        raise RuntimeError(f"Google 回覆錯誤：{payload.get('error_description') or payload.get('error') or response.status_code}")
    return payload


def complete_authorization(code: str, redirect_uri: str, username: str) -> str:
    """授權碼 → refresh token，確認是 SALESDEV_GMAIL_ACCOUNT 這個帳號再存起來。
    回傳連結的 Email；帳號不對或拿不到 refresh token 丟 RuntimeError。"""
    tokens = _post_token({"code": code, "redirect_uri": redirect_uri, "grant_type": "authorization_code"})
    refresh_token = tokens.get("refresh_token")
    access_token = tokens.get("access_token")
    if not refresh_token or not access_token:
        raise RuntimeError("Google 沒有給長期授權（refresh token），請再按一次「連結 Gmail」。")
    profile = requests.get(PROFILE_URL, headers={"Authorization": f"Bearer {access_token}"}, timeout=REQUEST_TIMEOUT)
    email = (profile.json() or {}).get("emailAddress", "") if profile.status_code == 200 else ""
    if email.lower() != SALESDEV_GMAIL_ACCOUNT.lower():
        _revoke(refresh_token)
        raise RuntimeError(f"登入的是 {email or '別的帳號'}，請用 {SALESDEV_GMAIL_ACCOUNT} 登入後再試一次。")
    _settings_ref().set(
        {"refresh_token": refresh_token, "email": email, "connected_at": repository.now_str(), "connected_by": username}
    )
    return email


def _revoke(token: str):
    try:
        requests.post(REVOKE_URL, params={"token": token}, timeout=REQUEST_TIMEOUT)
    except requests.RequestException:
        pass


def disconnect():
    snapshot = _settings_ref().get()
    data = (snapshot.to_dict() or {}) if snapshot.exists else {}
    if data.get("refresh_token"):
        _revoke(data["refresh_token"])
    _settings_ref().set({"refresh_token": "", "email": "", "disconnected_at": repository.now_str()})


def _access_token() -> str:
    snapshot = _settings_ref().get()
    data = (snapshot.to_dict() or {}) if snapshot.exists else {}
    if not is_configured() or not data.get("refresh_token"):
        raise GmailNotConnected("還沒連結 Gmail，請先到「信件範本」按「連結 Gmail」。")
    try:
        tokens = _post_token({"refresh_token": data["refresh_token"], "grant_type": "refresh_token"})
    except RuntimeError as exc:
        if "invalid_grant" in str(exc) or "revoked" in str(exc).lower() or "expired" in str(exc).lower():
            raise GmailNotConnected("Gmail 授權已經失效（可能被取消了），請到「信件範本」重新按「連結 Gmail」。") from exc
        raise
    return tokens["access_token"]


# ---------------------------------------------------------------------------
# 草稿
# ---------------------------------------------------------------------------

def build_message(to: str, subject: str, content: str, attachment: tuple = None) -> bytes:
    """組 MIME 信件。attachment＝(檔名, bytes)。純函式。"""
    message = EmailMessage()
    message["To"] = to
    message["From"] = formataddr((SENDER_NAME, SALESDEV_GMAIL_ACCOUNT))
    message["Subject"] = subject
    message.set_content(content)
    if attachment:
        filename, data = attachment
        message.add_attachment(data, maintype="application", subtype="pdf", filename=filename)
    return message.as_bytes()


def draft_open_url(message_id: str) -> str:
    """打開這封草稿的 Gmail 網址（authuser 指定帳號，避免開到別的 Google 帳號）。"""
    base = f"https://mail.google.com/mail/u/?authuser={SALESDEV_GMAIL_ACCOUNT}"
    return f"{base}#drafts?compose={message_id}" if message_id else f"{base}#drafts"


def create_draft(to: str, subject: str, content: str, attachment: tuple = None) -> dict:
    """在 gary@tsaipei.com 建草稿。回傳 {"draft_id", "message_id", "url"}。"""
    response = requests.post(
        DRAFT_UPLOAD_URL,
        headers={"Authorization": f"Bearer {_access_token()}", "Content-Type": "message/rfc822"},
        data=build_message(to, subject, content, attachment),
        timeout=60,
    )
    payload = response.json() if response.content else {}
    if response.status_code not in (200, 201):
        error = (payload.get("error") or {}).get("message") or response.status_code
        raise RuntimeError(f"Gmail 建立草稿失敗：{error}")
    message_id = (payload.get("message") or {}).get("id", "")
    return {"draft_id": payload.get("id", ""), "message_id": message_id, "url": draft_open_url(message_id)}


# ---------------------------------------------------------------------------
# 附件（範本的公司簡介 PDF）存 GCS：跟配送部/人資共用 DELIVERY_GCS_BUCKET，前綴 salesdev/
# ---------------------------------------------------------------------------

_storage_client = None


def _bucket():
    global _storage_client
    bucket_name = os.getenv("DELIVERY_GCS_BUCKET", "")
    if not bucket_name:
        raise RuntimeError("尚未設定 DELIVERY_GCS_BUCKET 環境變數，沒辦法存附件。")
    if _storage_client is None:
        from google.cloud import storage

        _storage_client = storage.Client(project=GCP_PROJECT_ID)
    return _storage_client.bucket(bucket_name)


def upload_attachment(template_id: str, content: bytes) -> str:
    blob_path = f"salesdev/mail_templates/{template_id}/{uuid.uuid4().hex}.pdf"
    _bucket().blob(blob_path).upload_from_string(content, content_type="application/pdf")
    return blob_path


def download_attachment(blob_path: str):
    if not blob_path or not blob_path.startswith("salesdev/"):
        return None
    blob = _bucket().blob(blob_path)
    if not blob.exists():
        return None
    return blob.download_as_bytes()


def delete_attachment(blob_path: str):
    if not blob_path or not blob_path.startswith("salesdev/"):
        return
    try:
        _bucket().blob(blob_path).delete()
    except Exception:
        pass


def looks_like_pdf(content: bytes) -> bool:
    return bool(content) and content[:5] == b"%PDF-"
