#!/usr/bin/env python3
"""一次性建立腳本：把使用者提供的材霈旗下派遣公司牌照清單，建立進
platform_companies.py 管理的公司主檔。

用法（在有 GCP 憑證、能連 Firestore 的環境，例如 Cloud Shell，位於 repo
根目錄執行）：

    python -m scripts.seed_companies

會先印出即將建立的公司清單再詢問是否要真的寫入。**已經存在的公司（簡稱
已經有資料）會直接跳過、不會覆蓋**，所以這支腳本可以放心重複執行，之後
如果同一份清單有新增公司，重跑一次只會補上新的、不會動到已經手動編輯過
的既有資料。

之後要新增/修改公司，直接在 /companies 網頁上操作即可，不用再跑腳本；
勞工保險證號等這次沒有提供的欄位，也是之後直接在 /companies 網頁上補。
"""
import sys

from platform_companies import company_exists, create_company

# 使用者直接提供的公司清單（簡稱、公司名稱、公司名稱英文、負責人、電話、
# 統一編號），保險相關欄位這次沒有提供，先留空，之後在 /companies 補上。
_COMPANIES = [
    {"short_name": "材霈", "name": "材霈有限公司", "name_en": "Tsaipei Co., Ltd.", "responsible_person": "蔡宗禮", "phone": "(02) 6637-3899", "tax_id": "29168344"},
    {"short_name": "瑋政", "name": "瑋政有限公司", "name_en": "", "responsible_person": "蔡志祥", "phone": "(02) 6637-3899", "tax_id": "68138452"},
    {"short_name": "宸暐", "name": "宸暐企業有限公司", "name_en": "", "responsible_person": "蔡志祥", "phone": "(02) 6637-3899", "tax_id": "66504925"},
    {"short_name": "宸恩", "name": "宸恩實業有限公司", "name_en": "", "responsible_person": "蔡志祥", "phone": "(02) 6637-3899", "tax_id": "91005757"},
    {"short_name": "大廷", "name": "大廷交通貨運有限公司", "name_en": "", "responsible_person": "蔡志祥", "phone": "(02)66373899", "tax_id": "89119852"},
    {"short_name": "鈞羽", "name": "鈞羽有限公司", "name_en": "", "responsible_person": "蔡志祥", "phone": "02-66373899", "tax_id": "94029297"},
    {"short_name": "祥舜", "name": "祥舜人力資源有限公司", "name_en": "SEAN SHUN HUMAN RESOURCES CO., LTD.", "responsible_person": "吳惠儒", "phone": "(02) 6637-3899", "tax_id": "94250493"},
    {"short_name": "聿見", "name": "聿見國際有限公司", "name_en": "", "responsible_person": "蔡妤安", "phone": "(02)66373899", "tax_id": "60616413"},
    {"short_name": "宸恩工廠", "name": "宸恩實業有限公司( 工廠)", "name_en": "", "responsible_person": "蔡志祥", "phone": "(02) 6637-3899", "tax_id": "91005757"},
    {"short_name": "宗舜", "name": "宗舜國際人力資源有限公司", "name_en": "", "responsible_person": "林明毅", "phone": "(02)85220530", "tax_id": "00249456"},
]


def plan_seed() -> dict:
    """純函式（不碰 Firestore），方便寫單元測試。回傳
    {"to_create": [...], "already_exist": [...]}。"""
    to_create = []
    already_exist = []
    for company in _COMPANIES:
        if company_exists(company["short_name"]):
            already_exist.append(company["short_name"])
        else:
            to_create.append(company)
    return {"to_create": to_create, "already_exist": already_exist}


def main():
    plan = plan_seed()

    if plan["already_exist"]:
        print("已經存在、這次會跳過不動的公司：")
        for name in plan["already_exist"]:
            print(f"  - {name}")

    if not plan["to_create"]:
        print("\n沒有需要新增的公司（可能都已經建立過了）。")
        return

    print("\n=== 即將新增的公司 ===")
    for company in plan["to_create"]:
        print(f"  - {company['short_name']}（{company['name']}），統編 {company['tax_id']}")

    answer = input(f"\n確定要新增上面 {len(plan['to_create'])} 家公司嗎？輸入 yes 才會執行：")
    if answer.strip().lower() != "yes":
        print("已取消，沒有做任何變更。")
        return

    for company in plan["to_create"]:
        create_company(company["short_name"], company)

    print(f"\n完成，共新增 {len(plan['to_create'])} 家公司。之後要新增/修改，直接到 /companies 網頁上操作即可。")


if __name__ == "__main__":
    if len(sys.argv) != 1:
        print(__doc__)
        sys.exit(1)
    main()
