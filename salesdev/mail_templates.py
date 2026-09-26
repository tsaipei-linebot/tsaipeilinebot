"""業務開發介紹信的範本（2026-09-26 新增，先給台灣就業通分頁用）。

使用者決定（見 HANDOFF.md「台灣就業通：寄信」）：
- 方案 A：按「開啟 Gmail」打開 gary@tsaipei.com 的 Gmail 撰寫畫面，收件人、主旨、內文
  都填好，使用者自己看過、**自己夾 PDF**、按寄出。平台不直接寄信。
- 每封信只有一個收件人。
- 「內文範本」跟「簡介版本」分開管理，寄信時各選一個；內文裡放 `{簡介}` 的地方換成
  選的簡介。其他可以用的變數：`{公司名稱}`、`{聯絡人}`、`{職缺名稱}`、`{地區}`。
- 簡介可以設定「要附加的檔案名稱」，寄信畫面會提醒記得夾。

Firestore `salesdev_mail_templates`：`kind`＝"body"（內文，有主旨）或 "intro"（簡介）。
第一次打開範本頁、裡面什麼都沒有時，會先放一份「暫用」的內文跟簡介，使用者確認好
正式內容再改。
"""
import time
from urllib.parse import quote

from salesdev import repository

COLLECTION = "salesdev_mail_templates"
KIND_BODY = "body"
KIND_INTRO = "intro"
KINDS = (KIND_BODY, KIND_INTRO)

PLACEHOLDERS = ("{公司名稱}", "{聯絡人}", "{職缺名稱}", "{地區}", "{簡介}")
# 資料沒有的時候填什麼（避免信裡出現「 您好」這種空白）
FALLBACKS = {"聯絡人": "人資負責人", "職缺名稱": "產線人員", "地區": "貴司所在地區"}

SEED_BODY = {
    "name": "暫用範本（請改成正式內文）",
    "subject": "{公司名稱} 產線人力支援｜材霈有限公司",
    "content": (
        "{聯絡人} 您好：\n\n"
        "我們在台灣就業通看到貴司正在招募「{職缺名稱}」，冒昧來信。\n\n"
        "{簡介}\n\n"
        "如果貴司近期有產線人力的需求，歡迎直接回信，或告訴我們方便聯絡的時間，我們會盡快與您聯繫。\n\n"
        "材霈有限公司 胡少凱\n\n"
        "（如不需要此類資訊，請回覆告知，我們將不再寄送。）"
    ),
}
SEED_INTRO = {
    "name": "暫用簡介（請改成正式簡介）",
    "content": "材霈有限公司提供產線作業員、技術員的人力派遣與招募服務。（這段是暫用文字，請換成正式簡介）",
    "attachment_name": "材霈公司簡介.pdf",
}

GMAIL_COMPOSE_URL = "https://mail.google.com/mail/"


def ref():
    return repository.get_db().collection(COLLECTION)


def _now():
    return repository.now_str()


def list_templates(kind: str = None, active_only: bool = False) -> list:
    items = [{"id": s.id, **(s.to_dict() or {})} for s in ref().stream()]
    if kind:
        items = [t for t in items if t.get("kind") == kind]
    if active_only:
        items = [t for t in items if t.get("active", True)]
    items.sort(key=lambda t: (t.get("kind", ""), not t.get("active", True), t.get("created_at", 0)))
    return items


def ensure_seeded(username: str = "") -> bool:
    """範本一個都沒有時，先放一份暫用的內文跟簡介。回傳有沒有新增。"""
    if list_templates():
        return False
    now = time.time()
    for kind, data in ((KIND_BODY, SEED_BODY), (KIND_INTRO, SEED_INTRO)):
        ref().document(f"{kind}_{int(now * 1000)}").set(
            {**data, "kind": kind, "active": True, "created_at": now, "updated_by": username or "系統", "updated_at": _now()}
        )
    return True


def get_template(template_id: str):
    if not template_id:
        return None
    snapshot = ref().document(template_id).get()
    if not snapshot.exists:
        return None
    return {"id": snapshot.id, **(snapshot.to_dict() or {})}


def save_template(template_id: str, kind: str, name: str, subject: str, content: str, attachment_name: str, username: str) -> str:
    """新增（template_id 空白）或修改範本。成功回傳範本 ID，失敗丟 ValueError（訊息給使用者看）。"""
    name, content = (name or "").strip(), (content or "").replace("\r\n", "\n").strip()
    if kind not in KINDS:
        raise ValueError("範本種類不正確。")
    if not name or not content:
        raise ValueError("名稱跟內容都要填。")
    if kind == KIND_BODY and not (subject or "").strip():
        raise ValueError("內文範本要填主旨。")
    data = {
        "kind": kind,
        "name": name,
        "content": content,
        "updated_by": username,
        "updated_at": _now(),
    }
    if kind == KIND_BODY:
        data["subject"] = subject.strip()
    else:
        data["attachment_name"] = (attachment_name or "").strip()
    if template_id:
        if not get_template(template_id):
            raise ValueError("找不到這個範本。")
        ref().document(template_id).set(data, merge=True)
        return template_id
    new_id = f"{kind}_{int(time.time() * 1000)}"
    ref().document(new_id).set({**data, "active": True, "created_at": time.time()})
    return new_id


def set_active(template_id: str, active: bool, username: str) -> bool:
    if not get_template(template_id):
        return False
    ref().document(template_id).set({"active": bool(active), "updated_by": username, "updated_at": _now()}, merge=True)
    return True


def company_values(company: dict, contact_names: list = None) -> dict:
    """公司資料 → 範本變數（沒有資料就用 FALLBACKS）。純函式。"""
    names = contact_names if contact_names is not None else company.get("contact_names") or []
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


def render(body: dict, intro: dict, company: dict) -> dict:
    """→ {"subject", "content"}。簡介先代入公司資料，再放進內文的 {簡介}。純函式。"""
    values = company_values(company)
    intro_text = fill((intro or {}).get("content", ""), values)
    values = {**values, "簡介": intro_text}
    return {
        "subject": fill((body or {}).get("subject", ""), values),
        "content": fill((body or {}).get("content", ""), values),
    }


def gmail_compose_url(account: str, to: str, subject: str, content: str) -> str:
    """Gmail 網頁版的撰寫信件網址（不用設定瀏覽器的 mailto）。`authuser` 指定用哪個
    Google 帳號開，避免瀏覽器同時登入好幾個帳號時開到別的信箱。純函式。"""
    query = "&".join(
        f"{key}={quote(value, safe='')}"
        for key, value in (("view", "cm"), ("fs", "1"), ("authuser", account), ("to", to), ("su", subject), ("body", content))
    )
    return f"{GMAIL_COMPOSE_URL}?{query}"
