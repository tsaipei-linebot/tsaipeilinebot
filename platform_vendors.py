"""合作廠商主檔（跟 platform_companies.py 的公司主檔一樣，是跨部門模組
共用的基礎資料）。

**這是全新、獨立的一份資料，用途是記錄「這個廠商的派遣員工要用哪家公司
簽約加保」，跟配送部系統 `delivery/config.py` 裡寫死的 `VENDORS`／
`VENDOR_MAP` 清單完全是兩回事，這次刻意不去動那份清單**——那份清單牽涉
配送部好幾個既有功能（報到文件規則、意外事件回報的廠商白名單、CSV 匯入
比對…），這些邏輯在 Python 模組載入的當下就會執行（例如
`VENDOR_MAP = {v["code"]: v["name"] for v in VENDORS}` 這種寫法），如果
改成在載入當下才去讀 Firestore，Firestore 一旦連線失敗就會讓整個 Cloud
Run 服務（不只配送部，其他子系統也一起）啟動失敗，風險太高，不值得為了
這次的需求冒險去動它。之後如果真的要把兩份廠商資料合併成一份，需要另外
評估、找一個安全的時機做，不是這次的範圍。

欄位裡的 `company_id` 對應 `platform_companies.py` 那份公司主檔的文件
ID（也就是公司的「簡稱」），允許空白——不是每個廠商當下都已經確定簽約
公司，先讓廠商本身可以建起來，保險相關欄位跟公司欄位晚點再補都可以。
"""
from platform_db import vendors_ref

FIELDS = ("code", "name", "company_id", "note")


def _to_vendor(vendor_id: str, data: dict) -> dict:
    vendor = {"id": vendor_id}
    for field in FIELDS:
        vendor[field] = data.get(field, "") or ""
    return vendor


def list_vendors() -> list:
    result = [_to_vendor(s.id, s.to_dict() or {}) for s in vendors_ref().stream()]
    result.sort(key=lambda v: v["name"] or v["code"])
    return result


def get_vendor(vendor_id: str):
    snapshot = vendors_ref().document(vendor_id).get()
    if not snapshot.exists:
        return None
    return _to_vendor(vendor_id, snapshot.to_dict() or {})


def vendor_exists(vendor_id: str) -> bool:
    return vendors_ref().document(vendor_id).get().exists


def create_vendor(vendor_id: str, fields: dict):
    payload = {field: (fields.get(field) or "").strip() for field in FIELDS}
    vendors_ref().document(vendor_id).set(payload)


def update_vendor(vendor_id: str, fields: dict):
    payload = {field: (fields.get(field) or "").strip() for field in FIELDS}
    vendors_ref().document(vendor_id).update(payload)


def delete_vendor(vendor_id: str):
    vendors_ref().document(vendor_id).delete()


def validate_vendor_fields(fields: dict) -> str:
    """回傳空字串代表可以存；非空字串是不能存的錯誤訊息。只驗證「代號／
    廠商名稱」必填，「簽約公司」刻意不強制——廠商可能還沒確定簽約公司就
    要先建起來。"""
    if not (fields.get("code") or "").strip():
        return "代號不能空白。"
    if not (fields.get("name") or "").strip():
        return "廠商名稱不能空白。"
    return ""
