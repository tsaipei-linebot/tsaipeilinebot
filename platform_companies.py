"""材霈旗下派遣公司牌照主檔（跟 platform_accounts.py 的帳號資料一樣，是
跨部門模組共用的基礎資料，不屬於任何單一部門）。

**這裡的「公司」跟配送部系統既有的「廠商」（蝦皮、UD、UC、順豐…）是完全
不同的概念，不要混在一起**：廠商是配送人員實際去哪裡上班，公司是材霈自己
的派遣牌照、用哪張牌照幫這個人加保——同一個人很可能同時「在蝦皮上班」但
「用某一張材霈自己的牌照加保」。

之後加保/退保、面試安排等功能，都會需要參照到某一家公司（例如用哪組
「勞工保險證號」產生加保檔案），這份主檔是那些功能的地基，這次先只做
公司資料本身的建立/查詢/編輯，還沒有任何功能真的引用它。

**注意跟同仁在職紀錄不一樣，公司主檔本身不受「不可覆蓋、事件式紀錄」
那條規則限制**——公司的電話、負責人這類資料改了就直接更新即可，沒有
五年保留法規問題；真正受那條規則限制的是「誰、什麼時候、用哪家公司加保」
這類同仁在職異動紀錄，那部分之後要另外設計，不是這裡的公司主檔本身。
"""
from platform_db import companies_ref

# 目前只先存基本資料欄位；勞工保險證號／勞退提繳單位編號這幾欄之後同仁
# 自己在 /companies 網頁上補（見 HANDOFF.md「未來規劃討論」），這裡先留
# 空欄位，不代表不需要。
FIELDS = (
    "short_name", "name", "name_en", "responsible_person", "phone", "address",
    "tax_id", "labor_insurance_no", "labor_insurance_check_code",
    "pension_unit_no", "note",
)


def _to_company(company_id: str, data: dict) -> dict:
    company = {"id": company_id}
    for field in FIELDS:
        company[field] = data.get(field, "") or ""
    return company


def list_companies() -> list:
    result = [_to_company(s.id, s.to_dict() or {}) for s in companies_ref().stream()]
    result.sort(key=lambda c: c["short_name"] or c["name"])
    return result


def get_company(company_id: str):
    snapshot = companies_ref().document(company_id).get()
    if not snapshot.exists:
        return None
    return _to_company(company_id, snapshot.to_dict() or {})


def company_exists(company_id: str) -> bool:
    return companies_ref().document(company_id).get().exists


def create_company(company_id: str, fields: dict):
    payload = {field: (fields.get(field) or "").strip() for field in FIELDS}
    companies_ref().document(company_id).set(payload)


def update_company(company_id: str, fields: dict):
    payload = {field: (fields.get(field) or "").strip() for field in FIELDS}
    companies_ref().document(company_id).update(payload)


def delete_company(company_id: str):
    companies_ref().document(company_id).delete()


def validate_company_fields(fields: dict) -> str:
    """回傳空字串代表可以存；非空字串是不能存的錯誤訊息，直接顯示在畫面上。
    只驗證「簡稱／公司名稱／統一編號」這幾個識別用的必要欄位，其餘（電話、
    負責人、保險相關欄位）刻意不強制要求——保險欄位常常是之後才補得上，
    不應該卡住公司本身先建起來。"""
    if not (fields.get("short_name") or "").strip():
        return "簡稱不能空白。"
    if not (fields.get("name") or "").strip():
        return "公司名稱不能空白。"
    if not (fields.get("tax_id") or "").strip():
        return "統一編號不能空白。"
    return ""
