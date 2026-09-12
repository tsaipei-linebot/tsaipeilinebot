#!/usr/bin/env python3
"""一次性建立腳本：把使用者提供的正式部門清單，建立進
platform_departments.py 管理的部門主檔。

用法（在有 GCP 憑證、能連 Firestore 的環境，例如 Cloud Shell，位於 repo
根目錄執行）：

    python -m scripts.seed_departments

會先印出即將建立的部門清單再詢問是否要真的寫入。**已經存在同名部門的
會直接跳過、不會重複建立**，所以這支腳本可以放心重複執行。

之後要新增/修改/調整部門順序，直接在 /departments 網頁上操作即可，不用
再跑腳本。
"""
import sys

from platform_departments import create_department, department_name_exists

# 2026-09-12 使用者提供的正式部門清單，依這個順序建立（決定 /departments、
# /accounts 帳號權限管理分組畫面的預設顯示順序）。
_DEPARTMENTS = [
    "台北所(派遣組)",
    "台北所(國際組)",
    "新北所(派遣組)",
    "新北所(配送組)",
    "桃園所",
    "台中所",
    "高雄所",
    "管理部",
    "財務部",
]


def plan_seed() -> dict:
    """純函式（不碰 Firestore 以外的東西，department_name_exists 本身會
    連 Firestore），方便寫單元測試。回傳 {"to_create": [...], "already_
    exist": [...]}。"""
    to_create = []
    already_exist = []
    for name in _DEPARTMENTS:
        if department_name_exists(name):
            already_exist.append(name)
        else:
            to_create.append(name)
    return {"to_create": to_create, "already_exist": already_exist}


def main():
    plan = plan_seed()

    if plan["already_exist"]:
        print("已經存在、這次會跳過不動的部門：")
        for name in plan["already_exist"]:
            print(f"  - {name}")

    if not plan["to_create"]:
        print("\n沒有需要新增的部門（可能都已經建立過了）。")
        return

    print("\n=== 即將依序新增的部門 ===")
    for name in plan["to_create"]:
        print(f"  - {name}")

    answer = input(f"\n確定要新增上面 {len(plan['to_create'])} 個部門嗎？輸入 yes 才會執行：")
    if answer.strip().lower() != "yes":
        print("已取消，沒有做任何變更。")
        return

    for name in plan["to_create"]:
        create_department(name)

    print(f"\n完成，共新增 {len(plan['to_create'])} 個部門。之後要新增/修改/調整順序，直接到 /departments 網頁上操作即可。")


if __name__ == "__main__":
    if len(sys.argv) != 1:
        print(__doc__)
        sys.exit(1)
    main()
