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

**2026-09-13 新增 `tax_id`（統一編號）／`contract_year`（合約年）兩個
欄位，並新增 `create_vendor_auto()`／`vendor_name_exists()` 兩支函式**，
給合約產生器／派遣契約產生器送出成功後自動同步資料用（見
`services/vendor_sync.py`）：
- 合約產生器每次送出都會呼叫 `create_vendor_auto()` 新增一筆，同一家
  客戶簽了好幾年、甚至同一年簽了好幾份，都各自留一筆，不會互相覆蓋，
  除非同仁自己到 `/vendors` 手動刪除。
- 派遣契約產生器沒有統一編號/合約年這些資料，送出時只會先用
  `vendor_name_exists()` 查廠商管理裡有沒有同名紀錄，沒有才呼叫
  `create_vendor_auto()` 補一筆只有名稱的陽春紀錄，避免同一個客戶重複
  產生很多份派遣契約時，把廠商管理灌爆一堆重複的同名紀錄。

跟同仁在 `/vendors` 網頁手動建立廠商（`create_vendor()`，代號當文件 ID）
不同，`create_vendor_auto()` 的文件 ID 是交給 Firestore 自動配發——因為
統一編號以後會對到好幾筆不同年份的紀錄，不能拿來當唯一的文件 ID。

**2026-09-14 新增「服務部門」欄位（`service_departments`，可複選，存部門
名稱字串清單，選項來自 `platform_departments.py`）**：給「總表」功能判斷
「服務這個廠商的部門主管可以看到這個廠商的合約/契約」用（見
`services/contract_summary_service.py`）。這欄是**同仁自己到 `/vendors`
手動勾選的**，合約/契約產生器自動同步新增的廠商紀錄一律從空清單開始，
系統不會自動幫忙猜要勾哪個部門——同一個客戶名稱底下可能同時存在好幾筆
廠商紀錄（合約產生器每次送出都新建一筆），每一筆的服務部門要各自勾選，
不會互相沿用。這欄不是 `FIELDS` 裡的字串欄位（是清單），`_to_vendor()`／
`create_vendor()`／`update_vendor()` 額外處理，`create_vendor_auto()`
刻意不處理這欄（自動建立的紀錄永遠是空清單）。
"""
from platform_db import vendors_ref

FIELDS = ("code", "name", "company_id", "note", "tax_id", "contract_year")


def _normalize_service_departments(fields: dict) -> list:
    """把表單送來的服務部門清單整理乾淨：去除頭尾空白、丟掉空白值、排序
    （方便畫面顯示穩定、也方便測試斷言），不去重複勾選理論上不會發生
    （`<input type=checkbox>` 天生就不會有重複值），這裡多做一次保險。"""
    departments = fields.get("service_departments") or []
    if isinstance(departments, str):
        departments = [departments] if departments.strip() else []
    return sorted({d.strip() for d in departments if (d or "").strip()})


def _to_vendor(vendor_id: str, data: dict) -> dict:
    vendor = {"id": vendor_id}
    for field in FIELDS:
        vendor[field] = data.get(field, "") or ""
    vendor["service_departments"] = list(data.get("service_departments") or [])
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
    payload["service_departments"] = _normalize_service_departments(fields)
    vendors_ref().document(vendor_id).set(payload)


def update_vendor(vendor_id: str, fields: dict):
    payload = {field: (fields.get(field) or "").strip() for field in FIELDS}
    payload["service_departments"] = _normalize_service_departments(fields)
    vendors_ref().document(vendor_id).update(payload)


def delete_vendor(vendor_id: str):
    vendors_ref().document(vendor_id).delete()


def create_vendor_auto(fields: dict) -> str:
    """自動建立一筆廠商紀錄，文件 ID 交給 Firestore 自動配發（不像
    `create_vendor()` 那樣需要呼叫端指定代號）——合約產生器／派遣契約
    產生器送出成功後自動同步資料時呼叫這支，不是同仁在 /vendors 網頁上
    手動建立廠商的那個流程。「代號」這欄如果呼叫端沒指定，預設帶入統一
    編號（沒有的話退而求其次用廠商名稱）方便同仁在列表上辨識，之後仍可
    自己到 /vendors 修改，不影響文件本身的識別。回傳新建立的文件 ID。"""
    payload = {field: (fields.get(field) or "").strip() for field in FIELDS}
    if not payload["code"]:
        payload["code"] = payload["tax_id"] or payload["name"]
    doc_ref = vendors_ref().document()
    doc_ref.set(payload)
    return doc_ref.id


def find_vendor_id_by_name(name: str):
    """廠商管理裡有沒有已經存在同名（去除頭尾空白後完全相同）的紀錄，有的話
    回傳那一筆的文件 ID，沒有回傳 ``None``——2026-09-14 新增，派遣契約
    產生器同步資料時用這支決定「沿用既有這一筆的 ID」還是「新建一筆」，
    回傳值會直接存進契約紀錄的 `vendor_id` 欄位（見
    `services/vendor_sync.py`），不用再靠名稱比對去反查廠商資料。"""
    name = (name or "").strip()
    if not name:
        return None
    query = vendors_ref().where("name", "==", name).limit(1)
    doc = next(iter(query.stream()), None)
    return doc.id if doc else None


def vendor_name_exists(name: str) -> bool:
    """廠商管理裡有沒有已經存在同名（去除頭尾空白後完全相同）的紀錄——
    只比對名稱，不看統一編號／合約年／代號。"""
    return find_vendor_id_by_name(name) is not None


def validate_vendor_fields(fields: dict) -> str:
    """回傳空字串代表可以存；非空字串是不能存的錯誤訊息。只驗證「代號／
    廠商名稱」必填，「簽約公司」刻意不強制——廠商可能還沒確定簽約公司就
    要先建起來。"""
    if not (fields.get("code") or "").strip():
        return "代號不能空白。"
    if not (fields.get("name") or "").strip():
        return "廠商名稱不能空白。"
    return ""
