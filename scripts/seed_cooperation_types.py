#!/usr/bin/env python3
"""一次性遷移腳本：把配送部系統原本寫死在 delivery/config.py 的 3 個
「合作方式」（二輪承攬／二輪雇傭／三輪雇傭），改建成 Firestore 裡的動態
合作方式（見 delivery/repository.py「合作方式管理」那節），刻意用跟舊
代碼完全相同的文件 ID（"two_wheel_contract"／"two_wheel_employed"／
"three_wheel_employed"），讓既有人員/應徵者主檔 cooperation_type 欄位存
的舊代碼、以及 DOC_TYPES 保險文件規則、應徵名單試駕規則（都是照這幾個
字串值本身判斷）不需要搬移，直接就能對應到新建的合作方式文件。

這 3 筆種子資料的適用廠商固定是蝦皮／蝦皮三輪速配倉（vendors 欄位），
維持這兩個廠商原本共用同一組合作方式的行為；其他廠商要用合作方式的話，
直接到「合作方式管理」網頁上新增（可以獨立設定，也可以勾選跟蝦皮共用）。

用法（在有 GCP 憑證、能連 Firestore 的環境，例如 Cloud Shell，位於 repo
根目錄執行）：

    python -m scripts.seed_cooperation_types

會先印出即將建立的合作方式清單再詢問是否要真的寫入。**已經存在的合作
方式（ID 已經有資料）會直接跳過、不會覆蓋**，可以放心重複執行——例如
主管已經先手動改過名稱或適用廠商，重跑這支腳本也不會把改過的內容洗回
舊的。

之後要新增/停用/刪除合作方式、或調整適用廠商，直接到「合作方式管理」
網頁上操作即可，不用再跑腳本。
"""
import sys

from delivery.repository import create_cooperation_type, get_cooperation_type

# 沿用配送部系統原本 delivery/config.py 的 COOPERATION_TYPES 清單內容，id
# 對應舊代碼，純粹當作遷移起點。
_COOPERATION_TYPES = [
    {"id": "two_wheel_contract", "name": "二輪承攬", "vendors": ["shopee", "shopee_speed_warehouse"]},
    {"id": "two_wheel_employed", "name": "二輪雇傭", "vendors": ["shopee", "shopee_speed_warehouse"]},
    {"id": "three_wheel_employed", "name": "三輪雇傭", "vendors": ["shopee", "shopee_speed_warehouse"]},
]


def plan_seed() -> dict:
    """純函式邏輯（get_cooperation_type 本身會連 Firestore），方便寫單元
    測試。回傳 {"to_create": [...], "already_exist": [...]}。"""
    to_create = []
    already_exist = []
    for coop in _COOPERATION_TYPES:
        if get_cooperation_type(coop["id"]):
            already_exist.append(coop["id"])
        else:
            to_create.append(coop)
    return {"to_create": to_create, "already_exist": already_exist}


def main():
    plan = plan_seed()

    if plan["already_exist"]:
        print("已經存在、這次會跳過不動的合作方式：")
        for type_id in plan["already_exist"]:
            print(f"  - {type_id}")

    if not plan["to_create"]:
        print("\n沒有需要新增的合作方式（可能都已經建立過了）。")
        return

    print("\n=== 即將新增的合作方式 ===")
    for coop in plan["to_create"]:
        print(f"  - {coop['id']}（{coop['name']}，適用廠商：{'、'.join(coop['vendors'])}）")

    answer = input(f"\n確定要新增上面 {len(plan['to_create'])} 個合作方式嗎？輸入 yes 才會執行：")
    if answer.strip().lower() != "yes":
        print("已取消，沒有做任何變更。")
        return

    for coop in plan["to_create"]:
        create_cooperation_type(coop["name"], coop["vendors"], type_id=coop["id"])

    print(
        f"\n完成，共新增 {len(plan['to_create'])} 個合作方式。之後要新增/停用/刪除、或調整適用廠商，"
        "直接到「合作方式管理」網頁上操作即可。"
    )


if __name__ == "__main__":
    if len(sys.argv) != 1:
        print(__doc__)
        sys.exit(1)
    main()
