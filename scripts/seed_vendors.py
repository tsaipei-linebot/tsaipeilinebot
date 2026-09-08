#!/usr/bin/env python3
"""一次性建立腳本：把配送部系統既有的 4 個廠商名稱，先建進
platform_vendors.py 管理的廠商主檔，方便接下來直接在 /vendors 網頁上設定
每個廠商的簽約公司。

**這支腳本只是拿既有廠商名稱當起點，跟配送部系統 `delivery/config.py` 的
`VENDORS` 清單完全沒有連動**——之後不管哪邊的廠商清單怎麼改，都不會影響
另一邊，見 platform_vendors.py 開頭的說明。

用法（在有 GCP 憑證、能連 Firestore 的環境，例如 Cloud Shell，位於 repo
根目錄執行）：

    python -m scripts.seed_vendors

會先印出即將建立的廠商清單再詢問是否要真的寫入。**已經存在的廠商（代號
已經有資料）會直接跳過、不會覆蓋**，可以放心重複執行。「簽約公司」這次
沒有資料，先留空，之後直接在 /vendors 網頁上補。
"""
import sys

from platform_vendors import create_vendor, vendor_exists

# 沿用配送部系統既有的廠商代號/名稱（delivery/config.py 的 VENDORS），
# 純粹當作起點方便同仁辨識，不代表兩份資料有任何連動。
_VENDORS = [
    {"code": "shopee", "name": "蝦皮"},
    {"code": "ud", "name": "UD"},
    {"code": "uc", "name": "UC"},
    {"code": "sf", "name": "順豐"},
]


def plan_seed() -> dict:
    """純函式（不碰 Firestore），方便寫單元測試。回傳
    {"to_create": [...], "already_exist": [...]}。"""
    to_create = []
    already_exist = []
    for vendor in _VENDORS:
        if vendor_exists(vendor["code"]):
            already_exist.append(vendor["code"])
        else:
            to_create.append(vendor)
    return {"to_create": to_create, "already_exist": already_exist}


def main():
    plan = plan_seed()

    if plan["already_exist"]:
        print("已經存在、這次會跳過不動的廠商：")
        for code in plan["already_exist"]:
            print(f"  - {code}")

    if not plan["to_create"]:
        print("\n沒有需要新增的廠商（可能都已經建立過了）。")
        return

    print("\n=== 即將新增的廠商 ===")
    for vendor in plan["to_create"]:
        print(f"  - {vendor['code']}（{vendor['name']}），簽約公司先留空")

    answer = input(f"\n確定要新增上面 {len(plan['to_create'])} 個廠商嗎？輸入 yes 才會執行：")
    if answer.strip().lower() != "yes":
        print("已取消，沒有做任何變更。")
        return

    for vendor in plan["to_create"]:
        create_vendor(vendor["code"], vendor)

    print(f"\n完成，共新增 {len(plan['to_create'])} 個廠商。之後到 /vendors 網頁上補「簽約公司」即可。")


if __name__ == "__main__":
    if len(sys.argv) != 1:
        print(__doc__)
        sys.exit(1)
    main()
