"""合約產生器（/client-contracts）／派遣契約產生器（/dispatch-contracts）
送出成功後，自動把客戶資訊同步一筆到廠商管理（/vendors，
`platform_vendors.py`）——2026-09-13 新增，讓廠商管理逐漸累積成兩個
產生器共用的客戶名單，派遣契約產生器之後可以直接從這份名單挑客戶，
不用每次自己手動打名字。

兩邊的同步規則刻意不一樣：
- **合約產生器每次送出都新增一筆，不檢查重複**——同一家客戶簽了好幾
  年，甚至同一年簽了好幾份不同性質的合約，使用者要的就是「每份合約都
  留下自己的紀錄」，之後要清理由同仁自己到 /vendors 手動刪除。
- **派遣契約產生器只有廠商管理裡還沒有同名紀錄時才新增**——同一個客戶
  通常會重複產生很多份派遣契約（同一客戶陸續派不同的人過去），如果
  每次都新增，廠商管理會被灌爆一堆重複的同名紀錄，派遣契約產生器本身
  也沒有統一編號/合約年這些資料可以拿來精準判斷「是不是同一家」，只能
  比對名稱。

**2026-09-14 新增：兩支函式都回傳這次同步用到的廠商文件 ID**（不管是
新建的還是沿用既有的），呼叫端（`client_contract_routes.py`／
`dispatch_contract_routes.py`）會把這個 ID 存進合約/契約紀錄自己的
`vendor_id` 欄位，之後「服務部門主管看不看得到這筆紀錄」「廠商管理能不能
預覽連動的合約/契約」都直接照這個 ID 查，不用再靠名稱比對去猜——這是
「廠商管理增加服務部門」這次改動新增的內部關聯欄位，同仁在畫面上看不到，
也不用自己填。
"""
import platform_vendors


def sync_vendor_from_client_contract(*, name: str, tax_id: str, contract_year, company_id: str) -> str:
    """合約產生器送出成功後呼叫。contract_year 可以是 int 或字串，這裡統一
    轉成字串存進廠商管理（跟 platform_vendors.FIELDS 其他欄位一樣是文字
    欄位）。回傳新建立的廠商文件 ID。"""
    return platform_vendors.create_vendor_auto(
        {
            "name": name,
            "tax_id": tax_id,
            "contract_year": str(contract_year) if contract_year else "",
            "company_id": company_id,
        }
    )


def sync_vendor_from_dispatch_contract(client_name: str) -> str:
    """派遣契約產生器送出成功後呼叫（客戶名稱是手動輸入、沒有選擇對應
    合約的備案路徑才會走到這裡，見 dispatch_contract_routes.py）。找不到
    同名紀錄才新增，只有名稱，其餘欄位（統一編號、合約年、簽約公司）留空
    ——派遣契約產生器本來就沒有收這些資料，同仁之後可以自己到 /vendors
    補齊。回傳這次用到的廠商文件 ID（沿用既有的，或新建的）；客戶名稱
    空白時回傳空字串，呼叫端不會有東西可以存。"""
    client_name = (client_name or "").strip()
    if not client_name:
        return ""
    existing_id = platform_vendors.find_vendor_id_by_name(client_name)
    if existing_id:
        return existing_id
    return platform_vendors.create_vendor_auto({"name": client_name})
