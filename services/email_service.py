"""透過 SMTP 寄信（2026-09-23 新增）。

**為什麼要有這支**：薪資補款的通知信原本是職缺維護系統那支 Apps Script
用 `GmailApp.sendEmail()` 直接寄的，而 Apps Script 的寄信額度是**每個
Google 帳號每天 100 個收件人**（一般 Gmail 帳號）。一封補款通知信要同時
寄給財會＋審核主管＋申請人，一封就吃掉 3～5 個額度，所以每天大約只能
核准 20～25 筆就會撞到上限，撞到之後那天**所有**核准的通知信都寄不出去
（2026-09-23 實際發生，詳見 HANDOFF.md）。

同一個信箱改走 SMTP 寄，上限是完全不同的一條額度（一般 Gmail 帳號約
每天 500 個收件人、Google Workspace 約 2000），所以光是把寄信這一步從
Apps Script 搬到這裡，額度就拉高好幾倍，而且之後要換成正規的寄信服務
（SendGrid／SES 之類）只要改這支的內容跟環境變數，呼叫端完全不用動。

**刻意寫成「純 SMTP 設定」而不是綁定某一家服務**：SMTP 主機/埠號/帳號/
密碼全部走環境變數，所以不管公司信箱是 Google Workspace、Microsoft 365
還是別家主機，都是改設定值就能用，不用改程式。

任何失敗都回傳 (False, 白話錯誤訊息)，不拋例外——寄信是附加動作，不該
讓呼叫端的主要流程（例如薪資補款核准）跟著失敗。
"""
import smtplib
import ssl
from email.message import EmailMessage

from config import (
    MAIL_FROM_ADDRESS,
    MAIL_FROM_NAME,
    SMTP_HOST,
    SMTP_PASSWORD,
    SMTP_PORT,
    SMTP_USERNAME,
)

_TIMEOUT_SECONDS = 30

# 465 是 SSL（連線一開始就加密），其餘（最常見是 587）走 STARTTLS
# （先明文連線再升級成加密）——兩種都是標準做法，差別只在哪個埠號用哪種。
_SSL_PORT = 465


def is_configured() -> bool:
    return bool(SMTP_HOST and SMTP_USERNAME and SMTP_PASSWORD)


def _sender_address() -> str:
    """沒有另外指定寄件者信箱時，就用登入 SMTP 的那個帳號——大多數郵件
    服務本來就只允許用登入帳號本人的地址寄信，指定別的會被退。"""
    return MAIL_FROM_ADDRESS or SMTP_USERNAME


def _build_message(to_addresses: list, subject: str, html_body: str, attachments: list, inline_images: list):
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = f"{MAIL_FROM_NAME} <{_sender_address()}>" if MAIL_FROM_NAME else _sender_address()
    message["To"] = ", ".join(to_addresses)

    # 先放純文字備援（收件人的信箱如果不顯示 HTML 時看到的內容），再把
    # HTML 設成主要內容——順序反過來的話 HTML 會被當成備援。
    message.set_content("這封信需要支援 HTML 的信箱才能正常顯示。")
    message.add_alternative(html_body, subtype="html")

    # 內嵌圖片要掛在「HTML 那一份」底下才會跟 <img src="cid:xxx"> 對得起來，
    # 掛在整封信的最外層會變成一般附件、HTML 裡的圖就破圖。
    if inline_images:
        html_part = message.get_payload()[-1]
        for image in inline_images:
            maintype, _, subtype = (image.get("mime_type") or "image/jpeg").partition("/")
            html_part.add_related(
                image["content"],
                maintype=maintype,
                subtype=subtype or "jpeg",
                cid=f"<{image['content_id']}>",
                filename=image.get("filename") or f"{image['content_id']}.jpg",
            )

    for attachment in attachments or []:
        maintype, _, subtype = (attachment.get("mime_type") or "application/octet-stream").partition("/")
        message.add_attachment(
            attachment["content"],
            maintype=maintype,
            subtype=subtype or "octet-stream",
            filename=attachment.get("filename") or "attachment",
        )
    return message


def send_email(to_addresses: list, subject: str, html_body: str, attachments: list = None, inline_images: list = None):
    """寄一封 HTML 信，回傳 (是否成功, 訊息)。

    - `to_addresses`：收件人 email 的 list（呼叫端負責去重/驗證格式）。
    - `attachments`／`inline_images`：list of dict，內容是**已經解碼好的
      bytes**（`content`），不是 base64 字串——解碼是呼叫端的責任，這支
      只管寄。`inline_images` 的每一筆要有 `content_id`，對應 HTML 裡的
      `<img src="cid:那個 content_id">`。

    失敗一律回傳 (False, 白話說明)，不拋例外，見檔案開頭的說明。"""
    if not is_configured():
        return False, "系統還沒有設定寄信用的 SMTP 帳號密碼，請聯絡系統管理員設定後再試一次。"
    if not to_addresses:
        return False, "沒有任何收件人，這封信沒有寄出。"

    message = _build_message(to_addresses, subject, html_body, attachments, inline_images)

    try:
        if SMTP_PORT == _SSL_PORT:
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=_TIMEOUT_SECONDS, context=ssl.create_default_context()) as server:
                server.login(SMTP_USERNAME, SMTP_PASSWORD)
                server.send_message(message)
        else:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=_TIMEOUT_SECONDS) as server:
                server.starttls(context=ssl.create_default_context())
                server.login(SMTP_USERNAME, SMTP_PASSWORD)
                server.send_message(message)
    except smtplib.SMTPAuthenticationError:
        return False, "寄信帳號登入失敗，請確認 SMTP 帳號密碼是否正確（用 Gmail 的話要用「應用程式密碼」，不是平常登入的密碼）。"
    except smtplib.SMTPRecipientsRefused:
        return False, "收件人信箱被郵件主機拒絕，請確認信箱位址是否正確。"
    except smtplib.SMTPDataError as e:
        # 寄信額度用完最常出現在這裡（例如 Gmail 的 "Daily user sending
        # quota exceeded"），原文是英文，翻成白話比較好處理。
        detail = str(e)
        if "quota" in detail.lower() or "limit" in detail.lower():
            return False, "今天的寄信額度已經用完，請明天再試；如果每天都遇到，請聯絡系統管理員調整寄信方式。"
        return False, f"郵件主機拒絕這封信：{detail}"
    except Exception as e:
        return False, f"寄信失敗，請稍後再試：{e}"

    return True, ""
