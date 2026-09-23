"""從職缺內文抓電話/分機/email（原封不動搬自 recruitment-leads-scraper）。
抓到的是刊登者（派遣公司）的聯絡方式，不是要派公司的。"""
import re

# 市話（含分機）：例如 02-1234-5678#123、(02)1234-5678 分機102、02-12345678轉56
_PHONE_EXT_RE = re.compile(
    r"(?P<phone>\(?0\d{1,2}\)?[-\s]?\d{3,4}[-\s]?\d{3,4})"
    r"(?:\s*(?:#|分機|轉)\s*(?P<ext>\d{1,5}))?"
)
_MOBILE_RE = re.compile(r"09\d{2}[-\s]?\d{3}[-\s]?\d{3}")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def extract_contacts(text: str) -> dict:
    if not text:
        return {"phone": "", "phone_ext": "", "email": ""}

    phone, ext = "", ""
    match = _PHONE_EXT_RE.search(text)
    if match:
        phone = re.sub(r"[()\s]", "", match.group("phone"))
        ext = match.group("ext") or ""
    else:
        match = _MOBILE_RE.search(text)
        if match:
            phone = match.group(0).replace(" ", "")

    email_match = _EMAIL_RE.search(text)
    email = email_match.group(0) if email_match else ""
    return {"phone": phone, "phone_ext": ext, "email": email}
