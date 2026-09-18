#!/usr/bin/env python3
"""一次性遷移腳本：把配送部系統車輛管理原本寫死在 delivery/config.py 的
7 個服務區域，改建成 Firestore 裡的動態服務區域（見
delivery/repository.py「車輛服務區域管理」那節），刻意用跟舊代碼完全
相同的文件 ID（"taipei"／"new_taipei"…），讓既有車輛主檔 service_area
欄位存的舊代碼不需要搬移，直接就能對應到新建的服務區域文件。

用法（在有 GCP 憑證、能連 Firestore 的環境，例如 Cloud Shell，位於 repo
根目錄執行）：

    python -m scripts.seed_vehicle_service_areas

會先印出即將建立的服務區域清單再詢問是否要真的寫入。**已經存在的服務
區域（ID 已經有資料）會直接跳過、不會覆蓋**，可以放心重複執行——例如
主管已經先手動改過名稱，重跑這支腳本也不會把改過的名稱洗回舊的。

之後要新增/停用/刪除服務區域，直接到「車輛管理」→「服務區域管理」網頁
上操作即可，不用再跑腳本。
"""
import sys

from delivery.repository import create_vehicle_service_area, get_vehicle_service_area

# 沿用配送部系統原本 delivery/config.py 的 SERVICE_AREAS 清單內容，id
# 對應舊代碼，純粹當作遷移起點。
_SERVICE_AREAS = [
    {"id": "taipei", "name": "台北"},
    {"id": "new_taipei", "name": "新北"},
    {"id": "taoyuan", "name": "桃園"},
    {"id": "hsinchu", "name": "新竹"},
    {"id": "taichung", "name": "台中"},
    {"id": "tainan", "name": "台南"},
    {"id": "kaohsiung", "name": "高雄"},
]


def plan_seed() -> dict:
    """純函式邏輯（get_vehicle_service_area 本身會連 Firestore），方便寫
    單元測試。回傳 {"to_create": [...], "already_exist": [...]}。"""
    to_create = []
    already_exist = []
    for area in _SERVICE_AREAS:
        if get_vehicle_service_area(area["id"]):
            already_exist.append(area["id"])
        else:
            to_create.append(area)
    return {"to_create": to_create, "already_exist": already_exist}


def main():
    plan = plan_seed()

    if plan["already_exist"]:
        print("已經存在、這次會跳過不動的服務區域：")
        for area_id in plan["already_exist"]:
            print(f"  - {area_id}")

    if not plan["to_create"]:
        print("\n沒有需要新增的服務區域（可能都已經建立過了）。")
        return

    print("\n=== 即將新增的服務區域 ===")
    for area in plan["to_create"]:
        print(f"  - {area['id']}（{area['name']}）")

    answer = input(f"\n確定要新增上面 {len(plan['to_create'])} 個服務區域嗎？輸入 yes 才會執行：")
    if answer.strip().lower() != "yes":
        print("已取消，沒有做任何變更。")
        return

    for area in plan["to_create"]:
        create_vehicle_service_area(area["name"], area_id=area["id"])

    print(
        f"\n完成，共新增 {len(plan['to_create'])} 個服務區域。之後要新增/停用/刪除，"
        "直接到「車輛管理」→「服務區域管理」網頁上操作即可。"
    )


if __name__ == "__main__":
    if len(sys.argv) != 1:
        print(__doc__)
        sys.exit(1)
    main()
