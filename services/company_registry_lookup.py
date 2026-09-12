"""合約產生器（/client-contracts）用：專員輸入甲方公司名稱或統一編號時，
依序查兩個免費的外部公開服務，查得到就回傳公司名稱/代表人/地址/統一編號，
表單就能自動帶入這幾個欄位；兩邊都查不到（或連線失敗、逾時、回傳格式看不懂）
一律回傳 ``None``，讓表單頁面照樣可以手動輸入——這兩個都是外部免費服務，
不是材霈付費訂閱的正式 API，這裡對任何失敗都要容錯，不能讓查詢失敗擋住
合約產生流程（2026-09-12 使用者明確同意這個失敗容錯做法，見 HANDOFF.md）。

兩個資料來源：
1. 經濟部商工行政資料開放平臺（``data.gcis.nat.gov.tw``）——政府官方資料，
   用統一編號精準查詢，公開的示範查詢端點不需要另外申請 API 金鑰。
2. g0v 社群維護的台灣公司資料庫（``company.g0v.ronny.tw``）——公司名稱或
   統一編號都能查，免金鑰、免申請，但是個人維運的公益專案，沒有正式服務
   保證，資料每月更新一次不是即時。

**這支檔案在開發沙盒環境裡沒辦法直接連線測試**（沙盒的出口網路政策不開放
任意外部網域，只允許少數幾個固定的服務），這跟 LibreOffice PDF 轉檔在
沙盒失敗是同一種狀況——不代表正式環境（Cloud Run）連不出去，只是沒辦法
在這裡先驗證兩邊 API 實際的回傳格式，上線後要麻煩使用者實際用一個已知的
客戶統編/名稱測試一次，確認查得到資料；查不到的話這個函式一律安全退回
``None``，不會讓合約產生器本身掛掉，只是甲方欄位需要同仁手動填而已。
"""
import re

import requests

_REQUEST_TIMEOUT_SECONDS = 8

# 「公司登記基本資料-應用一」的公開示範查詢端點，見開發指引
# https://data.gcis.nat.gov.tw/od/rule ——不需要另外申請金鑰即可用
# $filter 查詢，只支援統一編號精準比對，不支援名稱模糊搜尋。
_GCIS_API_URL = "https://data.gcis.nat.gov.tw/od/data/api/5F64D864-61CB-4D0D-8AD9-492047CC1EA6"
_G0V_SEARCH_URL = "https://company.g0v.ronny.tw/api/search"

_TAX_ID_PATTERN = re.compile(r"^\d{8}$")

# 兩邊資料來源背後都是同一份政府商工登記資料集，欄位名稱理論上一致，這裡
# 多列幾個常見的候選欄位名稱，任一個對得上就用，避免對方回傳格式有些微
# 差異就整個查詢失效。
_NAME_KEYS = ("Company_Name", "company_name", "name")
_REPRESENTATIVE_KEYS = ("Responsible_Name", "Representative_Name", "representative_name")
_ADDRESS_KEYS = ("Company_Location", "Company_Address", "company_location", "address")
_TAX_ID_KEYS = ("Business_Accounting_NO", "Business_Accounting_No", "tax_id")


def _first_present(record: dict, keys) -> str:
    for key in keys:
        value = record.get(key)
        if value:
            return str(value).strip()
    return ""


def _normalize_record(record: dict) -> dict:
    return {
        "name": _first_present(record, _NAME_KEYS),
        "representative": _first_present(record, _REPRESENTATIVE_KEYS),
        "address": _first_present(record, _ADDRESS_KEYS),
        "tax_id": _first_present(record, _TAX_ID_KEYS),
    }


def _extract_first_record(data):
    """兩邊 API 都可能直接回傳一個 list，或包在 {"data": [...]} 這種外殼裡，
    這裡都接受，格式看不懂就當作查不到。"""
    if isinstance(data, list):
        records = data
    elif isinstance(data, dict):
        records = data.get("data") or data.get("records") or data.get("result")
        if not isinstance(records, list):
            return None
    else:
        return None
    return records[0] if records and isinstance(records[0], dict) else None


def _lookup_gcis_by_tax_id(tax_id: str):
    try:
        response = requests.get(
            _GCIS_API_URL,
            params={"$format": "json", "$filter": f"Business_Accounting_NO eq {tax_id}"},
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as err:
        print(f"[合約產生器 甲方查詢] 經濟部商工開放平台查詢失敗：{err}")
        return None
    record = _extract_first_record(data)
    if not record:
        return None
    normalized = _normalize_record(record)
    return normalized if normalized["name"] else None


def _lookup_g0v(query: str):
    try:
        response = requests.get(_G0V_SEARCH_URL, params={"q": query}, timeout=_REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as err:
        print(f"[合約產生器 甲方查詢] g0v 公司資料庫查詢失敗：{err}")
        return None
    record = _extract_first_record(data)
    if not record:
        return None
    normalized = _normalize_record(record)
    return normalized if normalized["name"] else None


def lookup_company(query: str):
    """query 可以是公司名稱或統一編號（8 碼數字），依序查經濟部商工開放
    平台（統編精準查詢，只在 query 是 8 碼數字時嘗試）、g0v 公司資料庫
    （名稱或統編皆可查），都查不到回傳 ``None``。回傳格式：
    ``{"name", "representative", "address", "tax_id"}``，任一個查不到的
    欄位是空字串，不是缺 key。"""
    query = (query or "").strip()
    if not query:
        return None
    if _TAX_ID_PATTERN.match(query):
        result = _lookup_gcis_by_tax_id(query)
        if result:
            return result
    return _lookup_g0v(query)
