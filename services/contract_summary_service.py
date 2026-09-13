"""總表功能（/contract-summary，2026-09-13 新增，Phase 2）：把「合約產生器」
（`services/client_contract_service.py`）跟「派遣契約產生器」（`services/
dispatch_contract_service.py`）各自累積的紀錄，整理成主管年底盤點客戶用的
總表。這兩份總表資料形狀差很多（合約按年份分好幾筆、契約只看最新一份），
刻意不合併成一張表，各自用獨立的函式處理。

**權限沿用既有規則，這裡不另外定義**：`contract_summary_routes.py` 用
`platform_accounts.module_role()` 判斷這個帳號在 `client_contracts`／
`dispatch_contracts` 這兩個模組是不是「主管」角色（`ROLE_ADMIN`，全平台
管理員也算），只要其中一個是，就能看到對應那一半的總表；「專員」角色
（`ROLE_STAFF`）完全看不到這個功能。實際「看得到哪些紀錄」則是直接
呼叫兩個產生器既有的 `list_visible_submissions()`（自己送出的、自己是
送出者的主管、或全平台管理員），這支功能不重新實作可見範圍邏輯。

**合約產生器總表**：使用者勾選要看的年份（依合約起始日期年份分類，跟
`platform_vendors.py` 的 `contract_year` 欄位算法一致），每份合約都是
獨立一列，不去重（跟廠商管理連動同樣的「每份合約都留紀錄」精神）。
四種合約版本（時薪一口價／實支實付／白領代招／台籍代招）報價欄位形狀
都不一樣，這裡整理成一欄「報價方式」文字說明，不用四組互相稀疏的
獨立欄位塞滿表格。

**派遣契約總表**：不分年份，每個客戶只看最新一份送出的紀錄（`created_
at` 最新），同一份契約裡的每個班別各自變成一列（不是塞進同一格）——
2026-09-13 使用者確認派遣契約「用最新版本」判斷即可，不像合約有明確的
「合約年」概念可以勾選。"""
from datetime import date


def _contract_year(record: dict):
    """從紀錄存的 contract_start_date（"YYYY-MM-DD" 字串）取出年份，跟
    `client_contract_routes.py` 的 `_contract_year()` 是同樣的算法，這裡
    回傳 int（或抓不到年份時回傳 None），方便拿來跟年份篩選條件比對。"""
    value = record.get("contract_start_date") or ""
    if len(value) >= 4 and value[:4].isdigit():
        return int(value[:4])
    return None


def available_client_contract_years(records: list, today: date = None) -> list:
    """總表年份勾選清單：一定包含「今年」跟「明年」（就算明年還沒有任何
    合約紀錄也先給選項——年底整理隔年總覽正是這個功能最主要的使用情境），
    再補上紀錄裡實際出現過的其他年份，新到舊排序。"""
    today = today or date.today()
    years = {today.year, today.year + 1}
    for record in records:
        year = _contract_year(record)
        if year:
            years.add(year)
    return sorted(years, reverse=True)


def default_selected_years(today: date = None) -> list:
    """沒有勾選任何年份時（例如第一次進頁面）預設看的年份——今年＋明年，
    對應「年底盤點隔年客戶總覽」的主要使用情境。"""
    today = today or date.today()
    return [today.year, today.year + 1]


def parse_selected_years(raw_years: list, available_years: list) -> list:
    """把表單/網址參數送來的年份字串整理成合法的年份清單：不合法（非數字）
    或不在 `available_years` 裡的值直接忽略；整理完是空清單的話（第一次
    進頁面沒有勾選、或勾選的值全部不合法）改用 `default_selected_years()`
    的預設年份（限縮在 `available_years` 範圍內）。"""
    selected = set()
    for value in raw_years or []:
        try:
            year = int(value)
        except (TypeError, ValueError):
            continue
        if year in available_years:
            selected.add(year)
    if selected:
        return sorted(selected, reverse=True)
    return sorted((year for year in default_selected_years() if year in available_years), reverse=True)


def _pricing_summary(record: dict) -> str:
    """四種合約版本的報價欄位形狀都不一樣，這裡整理成一句話，總表用一欄
    顯示就好，不用四組互相稀疏的獨立欄位——跟
    `services/client_contract_service.py` 的 `CONTRACT_VERSIONS` 版本
    說明對照著看。"""
    version = record.get("contract_version")
    if version == "hourly_flat_rate":
        return f"時薪 {record.get('hourly_wage', '')}／管理費 {record.get('management_fee', '')}"
    if version == "actual_paid":
        return f"服務費：{record.get('service_fee', '')}"
    if version == "white_collar_referral":
        return f"服務費 {record.get('fee_amount', '')}／收費上限 {record.get('service_months', '')} 個月"
    if version == "taiwanese_referral":
        return (
            f"服務費 {record.get('referral_fee_percentage', '')}／"
            f"收費上限 {record.get('referral_service_months', '')} 個月"
        )
    return ""


def build_client_contract_summary_rows(records: list, selected_years: list) -> list:
    """把合約產生器的紀錄篩成勾選年份內的、整理成總表要顯示的欄位。依
    「年份新到舊、同年份內客戶名稱」排序，方便同一年度的客戶排在一起看。"""
    year_set = set(selected_years)
    rows = []
    for record in records:
        year = _contract_year(record)
        if year is None or year not in year_set:
            continue
        rows.append({
            "client_name": record.get("party_a_name", ""),
            "tax_id": record.get("party_a_tax_id", ""),
            "year": year,
            "party_b_name": record.get("party_b_name", ""),
            "contract_version": record.get("contract_version", ""),
            "pricing_summary": _pricing_summary(record),
            "remit_day": record.get("remit_day", ""),
            "submitted_by": record.get("submitted_by", ""),
        })
    rows.sort(key=lambda r: (-r["year"], r["client_name"]))
    return rows


def build_dispatch_contract_summary_rows(records: list) -> list:
    """`records` 要是依送出時間新到舊排序的清單（`list_visible_submissions()`
    本來就是這樣排的）——每個客戶只取遇到的第一筆，也就是最新一筆，同一份
    契約裡的每個班別各自變成一列。最後依客戶名稱排序方便查找（Python
    `sort()` 是穩定排序，同一個客戶的多個班別列會維持原本的相對順序）。"""
    seen_clients = set()
    rows = []
    for record in records:
        name = (record.get("client_name") or "").strip()
        if not name or name in seen_clients:
            continue
        seen_clients.add(name)
        created_at = record.get("created_at")
        updated_year = created_at.year if created_at else None
        pay_cycle = record.get("pay_cycle", "")
        for shift in record.get("shifts") or []:
            rows.append({
                "client_name": name,
                "updated_year": updated_year,
                "title": shift.get("title", ""),
                "hours": shift.get("hours", ""),
                "wage": shift.get("wage", ""),
                "bonus": shift.get("bonus", ""),
                "overtime": shift.get("overtime", ""),
                "pay_cycle": pay_cycle,
            })
    rows.sort(key=lambda r: r["client_name"])
    return rows
