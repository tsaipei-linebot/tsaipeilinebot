"""業務開發介紹信的「內文範本」（2026-09-26 新增，先給台灣就業通分頁用）。

使用者決定（見 HANDOFF.md「台灣就業通：寄信」）：
- 方案 A：按「開啟 Gmail」打開 gary@tsaipei.com 的 Gmail 撰寫畫面，收件人、主旨、內文
  都填好，使用者自己看過、**自己夾 PDF**、按寄出。平台不直接寄信。
- 每封信只有一個收件人。
- 公司簡介是 PDF，使用者自己在 Gmail 夾，平台**不管理簡介文字**（原本規劃的「簡介版本」
  2026-09-26 使用者決定拿掉）。每個內文範本可以設定「要夾的附件」，寄信畫面提醒記得夾。
- 內文範本放在「信件範本」專區（/salesdev/templates），可以新增、編輯、刪除。刪掉的範本，
  以前用它寄過的紀錄還在（紀錄裡存的是範本名稱，不是參照）。
- 內文可以用的變數：`{公司名稱}`、`{聯絡人}`、`{職缺名稱}`、`{地區}`。
- 2026-09-26 同一天加上「做法二」：每個範本可以**上傳 PDF**（存 GCS，`attachment_blob`），連結 Gmail
  之後，寄信頁的「建立草稿（含附件）」會在 Gmail 草稿匣建好信、自動夾這個 PDF（見 gmail_drafts.py）。

Firestore `salesdev_mail_templates`。第一次打開、裡面什麼都沒有時，先放一份「暫用」
範本，使用者確認好正式內容再改。
"""
import time
from urllib.parse import quote

from salesdev import repository

COLLECTION = "salesdev_mail_templates"
KIND_BODY = "body"

PLACEHOLDERS = ("{公司名稱}", "{聯絡人}", "{職缺名稱}", "{地區}")
# 資料沒有的時候填什麼（避免信裡出現「 您好」這種空白）
FALLBACKS = {"聯絡人": "人資負責人", "職缺名稱": "產線人員", "地區": "貴司所在地區"}

SEED_BODY = {
    "name": "暫用範本（請改成正式內文）",
    "subject": "{公司名稱} 產線人力支援｜材霈有限公司",
    "content": (
        "{聯絡人} 您好：\n\n"
        "我們在台灣就業通看到貴司正在招募「{職缺名稱}」，冒昧來信。\n\n"
        "材霈有限公司提供產線作業員、技術員的人力派遣與招募服務，公司簡介請見附件。\n\n"
        "如果貴司近期有產線人力的需求，歡迎直接回信，或告訴我們方便聯絡的時間，我們會盡快與您聯繫。\n\n"
        "材霈有限公司 胡少凱\n\n"
        "（如不需要此類資訊，請回覆告知，我們將不再寄送。）"
    ),
    "attachment_name": "材霈公司簡介.pdf",
}

GMAIL_COMPOSE_URL = "https://mail.google.com/mail/"


def ref():
    return repository.get_db().collection(COLLECTION)


def list_templates() -> list:
    items = [{"id": s.id, **(s.to_dict() or {})} for s in ref().stream()]
    items = [t for t in items if t.get("kind", KIND_BODY) == KIND_BODY]
    items.sort(key=lambda t: t.get("created_at", 0))
    return items


def ensure_seeded(username: str = "") -> bool:
    """範本一個都沒有時，先放一份暫用的。回傳有沒有新增。"""
    if list_templates():
        return False
    now = time.time()
    ref().document(f"body_{int(now * 1000)}").set(
        {**SEED_BODY, "kind": KIND_BODY, "created_at": now, "updated_by": username or "系統", "updated_at": repository.now_str()}
    )
    return True


def get_template(template_id: str):
    if not template_id:
        return None
    snapshot = ref().document(template_id).get()
    if not snapshot.exists:
        return None
    template = {"id": snapshot.id, **(snapshot.to_dict() or {})}
    return template if template.get("kind", KIND_BODY) == KIND_BODY else None


def save_template(template_id: str, name: str, subject: str, content: str, attachment_name: str, username: str) -> str:
    """新增（template_id 空白）或修改範本。成功回傳範本 ID，失敗丟 ValueError（訊息給使用者看）。"""
    name = (name or "").strip()
    subject = (subject or "").strip()
    content = (content or "").replace("\r\n", "\n").strip()
    if not name or not subject or not content:
        raise ValueError("名稱、主旨、內文都要填。")
    data = {
        "kind": KIND_BODY,
        "name": name,
        "subject": subject,
        "content": content,
        "attachment_name": (attachment_name or "").strip(),
        "updated_by": username,
        "updated_at": repository.now_str(),
    }
    if template_id:
        if not get_template(template_id):
            raise ValueError("找不到這個範本，可能已經被刪掉了。")
        ref().document(template_id).set(data, merge=True)
        return template_id
    new_id = f"body_{int(time.time() * 1000)}"
    ref().document(new_id).set({**data, "created_at": time.time()})
    return new_id


def delete_template(template_id: str) -> bool:
    template = get_template(template_id)
    if not template:
        return False
    ref().document(template_id).delete()
    if template.get("attachment_blob"):
        from salesdev import gmail_drafts

        gmail_drafts.delete_attachment(template["attachment_blob"])
    return True


def set_attachment(template_id: str, blob_path: str, filename: str, size: int, username: str):
    """範本上傳了新的 PDF（blob_path 空白＝移除附件）。舊的檔案刪掉。"""
    template = get_template(template_id) or {}
    old_blob = template.get("attachment_blob", "")
    data = {
        "attachment_blob": blob_path,
        "attachment_size": size if blob_path else 0,
        "updated_by": username,
        "updated_at": repository.now_str(),
    }
    if blob_path:
        data["attachment_name"] = filename
    ref().document(template_id).set(data, merge=True)
    if old_blob and old_blob != blob_path:
        from salesdev import gmail_drafts

        gmail_drafts.delete_attachment(old_blob)


def company_values(company: dict) -> dict:
    """公司資料 → 範本變數（沒有資料就用 FALLBACKS）。純函式。"""
    names = company.get("contact_names") or []
    titles = company.get("latest_job_titles") or []
    areas = company.get("areas") or []
    return {
        "公司名稱": company.get("company_name", ""),
        "聯絡人": names[0] if names else FALLBACKS["聯絡人"],
        "職缺名稱": titles[0] if titles else FALLBACKS["職缺名稱"],
        "地區": areas[0] if areas else FALLBACKS["地區"],
    }


def fill(text: str, values: dict) -> str:
    """把 {公司名稱} 這類變數換掉；不認得的 {…} 原樣保留。純函式。"""
    for key, value in values.items():
        text = text.replace("{" + key + "}", value or "")
    return text


def render(body: dict, company: dict) -> dict:
    """→ {"subject", "content"}。純函式。"""
    values = company_values(company)
    return {
        "subject": fill((body or {}).get("subject", ""), values),
        "content": fill((body or {}).get("content", ""), values),
    }


def gmail_compose_url(account: str, to: str, subject: str, content: str) -> str:
    """Gmail 網頁版的撰寫信件網址（不用設定瀏覽器的 mailto）。`authuser` 指定用哪個
    Google 帳號開，避免瀏覽器同時登入好幾個帳號時開到別的信箱。純函式（寄信頁的
    JavaScript 用同樣的組法，這支給測試跟之後要在後端組網址時用）。"""
    query = "&".join(
        f"{key}={quote(value, safe='')}"
        for key, value in (("view", "cm"), ("fs", "1"), ("authuser", account), ("to", to), ("su", subject), ("body", content))
    )
    return f"{GMAIL_COMPOSE_URL}?{query}"
