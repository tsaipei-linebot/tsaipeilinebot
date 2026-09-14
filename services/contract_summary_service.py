"""總表功能（/contract-summary，2026-09-13 新增 Phase 1，2026-09-14 大改）：
把「合約產生器」（`services/client_contract_service.py`）跟「派遣契約
產生器」（`services/dispatch_contract_service.py`）各自累積的紀錄，整理
成主管盤點客戶用的總表。這兩份總表資料形狀差很多（合約按年份分好幾筆、
契約只看最新一份），刻意不合併成一張表，各自用獨立的函式處理，另外還有
一個把兩者合併呈現的「合併視圖」。

**權限模型（2026-09-14 改版，取代原本送出人鏈規則）**：這個功能完全看
廠商管理（`platform_vendors.py`）那筆紀錄勾選的「服務部門」決定看不看
得到——**不是**沿用兩個產生器本身「送出者本人／送出者的主管／全平台
管理員」那套規則（那套規則只管兩個產生器自己的首頁列表、下載、刪除，
跟這裡完全分開，見 `can_view_via_vendor_department()` 的說明）。全平台
管理員永遠看得到全部；主管職級（`platform_accounts.is_manager_rank()`）
的帳號，如果自己的部門有被勾選在某筆廠商紀錄的服務部門裡，就看得到
連到那筆廠商紀錄的合約/契約；「專員」角色或部門沒有服務任何廠商的帳號，
完全看不到這個功能（連 `/contract-summary` 頁面本身都進不去，見
`contract_summary_routes.py` 的 `_require_access()`）。

**合約／契約怎麼連到廠商管理的哪一筆**：合約產生器送出時一定新建一筆
廠商紀錄、一對一連過去；派遣契約產生器如果同仁有選「對應的合約」，直接
沿用那份合約連到的廠商，否則走名稱比對的備案路徑（找到既有的就沿用、
沒有就新建）——這個連結（`vendor_id` 欄位）是送出當下就存好的，這裡
純粹讀取，不重新判斷，詳見 `services/client_contract_service.py`／
`services/dispatch_contract_service.py` 開頭的說明。**這次上線之前的
歷史紀錄沒有這個連結（`vendor_id` 是空字串），一律看不到，不會回填**。

**合約產生器總表**：使用者勾選要看的年份（依合約起始日期年份分類，跟
`platform_vendors.py` 的 `contract_year` 欄位算法一致），每份合約都是
獨立一列，不去重（跟廠商管理連動同樣的「每份合約都留紀錄」精神）。

**派遣契約總表／合併視圖的「最新版本」判斷（2026-09-14 改版）**：不是
單純「每個客戶名稱看最新一份」，而是「每個客戶名稱＋送出人帳號」各自
留最新一筆（`_dispatch_group_key()`）——同一個客戶如果同時被不同人／
不同團隊各自負責、各自送出自己的派遣契約，彼此不會互相蓋掉。如果契約
有透過「選擇對應的合約」連到具體某一份合約，分組鍵改用「那份合約的 id
＋送出人」，比純比對客戶名稱字串更精準。
"""
from datetime import date

import platform_accounts
import platform_vendors


def _contract_year(record: dict):
    """從紀錄存的 contract_start_date（"YYYY-MM-DD" 字串）取出年份，跟
    `client_contract_routes.py` 的 `_contract_year()` 是同樣的算法，這裡
    回傳 int（或抓不到年份時回傳 None），方便拿來跟年份篩選條件比對。"""
    value = record.get("contract_start_date") or ""
    if len(value) >= 4 and value[:4].isdigit():
        return int(value[:4])
    return None


def build_vendor_lookup() -> dict:
    """回傳 {vendor_id: vendor_dict} 的對照表，一次抓所有廠商，總表用來
    查每筆合約/契約連到的服務部門，避免對每一筆紀錄各自查一次 Firestore。"""
    return {v["id"]: v for v in platform_vendors.list_vendors()}


def _account_can_claim_department(viewer_account: dict, vendor) -> bool:
    if not vendor:
        return False
    if not platform_accounts.is_manager_rank(viewer_account.get("rank", "")):
        return False
    department = viewer_account.get("department") or ""
    if not department:
        return False
    return department in (vendor.get("service_departments") or [])


def can_view_via_vendor_department(viewer_account: dict, vendor_id: str, vendor_lookup: dict) -> bool:
    """這個帳號能不能透過「服務部門」規則看到連到 `vendor_id` 這筆廠商
    紀錄的合約/契約——全平台管理員永遠可以；其他帳號要「主管職級」＋
    「自己的部門有被勾在那筆廠商紀錄的服務部門裡」才行。沒有連到任何
    廠商（`vendor_id` 空字串，例如上線前的舊紀錄）一律看不到。給總表
    這種一次要判斷「一整批」紀錄的情境用，`vendor_lookup` 要先用
    `build_vendor_lookup()` 準備好，避免每筆紀錄各自查一次 Firestore。"""
    if viewer_account.get("is_platform_admin"):
        return True
    if not vendor_id:
        return False
    return _account_can_claim_department(viewer_account, vendor_lookup.get(vendor_id))


def can_view_via_vendor_department_single(viewer_account: dict, vendor_id: str) -> bool:
    """跟 `can_view_via_vendor_department()` 判斷邏輯完全一樣，差別是這支
    只查這一筆廠商紀錄（`platform_vendors.get_vendor()`），不用先建整份
    `vendor_lookup`——給合約/契約產生器本身的「預覽」「下載」路由用（見
    `client_contract_routes.py`／`dispatch_contract_routes.py`），那兩個
    地方一次只需要判斷一筆紀錄，額外多開放給「服務部門主管」（跟原本
    送出人鏈的規則是「兩者符合一個即可」，不是取代——見
    `can_view_submission()` 開頭關於這個差異的說明）。"""
    if viewer_account.get("is_platform_admin"):
        return True
    if not vendor_id:
        return False
    return _account_can_claim_department(viewer_account, platform_vendors.get_vendor(vendor_id))


def viewer_has_any_department_access(viewer_account: dict, vendor_lookup: dict) -> bool:
    """能不能打開 `/contract-summary` 這個頁面：全平台管理員可以；其他
    帳號要「主管職級」＋「自己的部門有被勾在任何一筆廠商紀錄的服務部門
    裡」才行——不看有沒有開通合約產生器／派遣契約產生器這兩個模組。"""
    if viewer_account.get("is_platform_admin"):
        return True
    if not platform_accounts.is_manager_rank(viewer_account.get("rank", "")):
        return False
    department = viewer_account.get("department") or ""
    if not department:
        return False
    return any(department in (v.get("service_departments") or []) for v in vendor_lookup.values())


def visible_client_contract_records(records: list, viewer_account: dict, vendor_lookup: dict) -> list:
    return [r for r in records if can_view_via_vendor_department(viewer_account, r.get("vendor_id", ""), vendor_lookup)]


def visible_dispatch_contract_records(records: list, viewer_account: dict, vendor_lookup: dict) -> list:
    return [r for r in records if can_view_via_vendor_department(viewer_account, r.get("vendor_id", ""), vendor_lookup)]


def available_client_contract_years(records: list, today: date = None) -> list:
    """總表年份勾選清單：一定包含「今年」跟「明年」（就算明年還沒有任何
    合約紀錄也先給選項——年底整理隔年總覽正是這個功能最主要的使用情境），
    再補上紀錄裡實際出現過的其他年份，新到舊排序。`records` 應該是已經
    套過權限過濾的清單（`visible_client_contract_records()`）。"""
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
    「年份新到舊、同年份內客戶名稱」排序，方便同一年度的客戶排在一起看。
    `records` 應該是已經套過權限過濾的清單。"""
    year_set = set(selected_years)
    rows = []
    for record in records:
        year = _contract_year(record)
        if year is None or year not in year_set:
            continue
        rows.append({
            "id": record.get("id", ""),
            "vendor_id": record.get("vendor_id", ""),
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


def _dispatch_group_key(record: dict):
    """派遣契約「最新版本」的分組依據：優先用「連到的合約 id＋送出人」，
    沒有連到具體合約（手動輸入客戶名稱的備案路徑）才退而求其次用「客戶
    名稱文字＋送出人」。同一組裡只留最新一筆（`records` 假設已經是依
    送出時間新到舊排序），不同組（不同人／不同合約）各自保留，不會
    互相蓋掉——2026-09-14 改版，之前是純粹以客戶名稱分組。"""
    linked_id = record.get("linked_client_contract_id") or ""
    submitted_by = record.get("submitted_by", "")
    if linked_id:
        return ("contract", linked_id, submitted_by)
    return ("name", (record.get("client_name") or "").strip(), submitted_by)


def _blank_shift_columns(count: int) -> list:
    return [{"title": "", "hours": "", "wage": "", "bonus": "", "overtime": ""} for _ in range(count)]


def _shift_columns(record: dict, count: int) -> list:
    """把一筆派遣契約的班別壓平成固定數量的欄位組，不足的補空白——合併
    視圖用，讓每個客戶維持「一列」，不會因為班別數不同就整張表歪掉。"""
    shifts = record.get("shifts") or []
    columns = []
    for i in range(count):
        if i < len(shifts):
            s = shifts[i]
            columns.append({
                "title": s.get("title", ""), "hours": s.get("hours", ""),
                "wage": s.get("wage", ""), "bonus": s.get("bonus", ""), "overtime": s.get("overtime", ""),
            })
        else:
            columns.append({"title": "", "hours": "", "wage": "", "bonus": "", "overtime": ""})
    return columns


def build_dispatch_contract_summary_rows(records: list) -> list:
    """`records` 應該是已經套過權限過濾、且依送出時間新到舊排序的清單
    （`visible_dispatch_contract_records()`）——每組（見
    `_dispatch_group_key()`）只取遇到的第一筆，也就是最新一筆，同一份
    契約裡的每個班別各自變成一列。最後依客戶名稱＋送出人排序方便查找。"""
    seen_groups = set()
    rows = []
    for record in records:
        client_name = (record.get("client_name") or "").strip()
        if not client_name:
            continue
        group_key = _dispatch_group_key(record)
        if group_key in seen_groups:
            continue
        seen_groups.add(group_key)
        created_at = record.get("created_at")
        updated_year = created_at.year if created_at else None
        pay_cycle = record.get("pay_cycle", "")
        submitted_by = record.get("submitted_by", "")
        for shift in record.get("shifts") or []:
            rows.append({
                "client_name": client_name,
                "updated_year": updated_year,
                "title": shift.get("title", ""),
                "hours": shift.get("hours", ""),
                "wage": shift.get("wage", ""),
                "bonus": shift.get("bonus", ""),
                "overtime": shift.get("overtime", ""),
                "pay_cycle": pay_cycle,
                "submitted_by": submitted_by,
            })
    rows.sort(key=lambda r: (r["client_name"], r["submitted_by"]))
    return rows


def build_merged_summary_rows(client_records: list, dispatch_records: list, selected_years: list):
    """合併視圖：一列＝合約產生器總表的一份合約，右側接上這份合約連動的
    派遣契約資訊（用 `_dispatch_group_key()` 分組取最新）。班別固定切成
    「班別1／班別2／…」幾組欄位，組數＝所有配對到的契約裡班別數最多的
    那一筆需要幾組（至少留一組，避免完全沒有契約資料時欄位標題消失）。
    只有透過「選擇對應的合約」連過去的契約（`linked_client_contract_id`
    有值）才會配對進來——手動輸入客戶名稱、沒有明確連結的契約不會出現
    在這裡，避免用名稱亂猜配對，那些改用「派遣契約總表」看。`client_
    records`／`dispatch_records` 都應該是已經套過權限過濾的清單。

    回傳 (rows, max_shifts) 這個 tuple，`max_shifts` 給樣板組表頭用。"""
    client_rows = build_client_contract_summary_rows(client_records, selected_years)

    dispatch_by_contract = {}
    seen_groups = set()
    for record in dispatch_records:
        linked_id = record.get("linked_client_contract_id") or ""
        if not linked_id:
            continue
        group_key = _dispatch_group_key(record)
        if group_key in seen_groups:
            continue
        seen_groups.add(group_key)
        dispatch_by_contract.setdefault(linked_id, []).append(record)

    max_shifts = max(
        (len(d.get("shifts") or []) for matches in dispatch_by_contract.values() for d in matches),
        default=0,
    )
    max_shifts = max(max_shifts, 1)

    rows = []
    for crow in client_rows:
        matches = dispatch_by_contract.get(crow["id"], [])
        if not matches:
            rows.append({
                **crow,
                "dispatch_pay_cycle": "",
                "dispatch_submitted_by": "",
                "shift_columns": _blank_shift_columns(max_shifts),
            })
        else:
            for d in matches:
                rows.append({
                    **crow,
                    "dispatch_pay_cycle": d.get("pay_cycle", ""),
                    "dispatch_submitted_by": d.get("submitted_by", ""),
                    "shift_columns": _shift_columns(d, max_shifts),
                })
    return rows, max_shifts
