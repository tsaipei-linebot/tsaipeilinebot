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
"""
import platform_vendors


def sync_vendor_from_client_contract(*, name: str, tax_id: str, contract_year, company_id: str):
    """合約產生器送出成功後呼叫。contract_year 可以是 int 或字串，這裡統一
    轉成字串存進廠商管理（跟 platform_vendors.FIELDS 其他欄位一樣是文字
    欄位）。"""
    platform_vendors.create_vendor_auto(
        {
            "name": name,
            "tax_id": tax_id,
            "contract_year": str(contract_year) if contract_year else "",
            "company_id": company_id,
        }
    )


def sync_vendor_from_dispatch_contract(client_name: str):
    """派遣契約產生器送出成功後呼叫。找不到同名紀錄才新增，只有名稱，其餘
    欄位（統一編號、合約年、簽約公司）留空——派遣契約產生器本來就沒有
    收這些資料，同仁之後可以自己到 /vendors 補齊。"""
    client_name = (client_name or "").strip()
    if not client_name or platform_vendors.vendor_name_exists(client_name):
        return
    platform_vendors.create_vendor_auto({"name": client_name})
