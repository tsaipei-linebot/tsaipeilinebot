#!/usr/bin/env python3
"""一次性匯入腳本：把「職缺維護表單」背後那份 Google Sheet 的「員工主管
組織表」分頁（跟 services/salary_repayment_service.py 讀的是同一份試算表、
同一個分頁），拿來批次幫既有帳號填好 platform_accounts 的
`manager_usernames` 欄位。

背景：/accounts 現在可以直接在網頁上手動指定每個帳號的「所屬主管」（見
platform_accounts.py 的說明），這樣之後 /me 這類「主管能看部屬資料」的
功能，比對的是系統自己的帳號，不用再依賴外部試算表的文字姓名比對。這支
腳本只是「省去第一次要一筆一筆手動勾選」的力氣，帶入既有試算表裡已經有
的主管關係當作起點，之後要調整都可以直接在 /accounts 網頁上改，不用再跑
這支腳本。

比對邏輯：拿試算表「員工姓名」欄位去對系統帳號的姓名（platform_accounts
的 name 欄位），姓名完全相同才算數；同名同姓或姓名對不上的情況，這支腳本
都會印出來讓你自己確認、不會自動亂猜。

用法（在有 GCP 憑證、能連 Firestore 也能連 Google Sheets API 的環境，例如
Cloud Shell，位於 repo 根目錄執行）：

    python -m scripts.import_account_managers

會先印出即將變更的內容（哪些帳號會被設定哪些主管、哪些姓名對不上）再詢問
是否要真的寫入，避免匯入到錯誤的資料。只會更新 manager_usernames 這個
欄位，不會動到密碼、模組權限、部門等其他資料。
"""
import sys

from config import SALARY_REPAYMENT_ORG_SHEET_NAME, SALARY_REPAYMENT_SHEET_ID
from platform_accounts import list_accounts, set_manager_usernames
from services.salary_repayment_service import parse_name_list, rows_to_dicts


def _fetch_org_rows() -> list:
    from google.auth import default as google_auth_default
    from googleapiclient.discovery import build

    credentials, _ = google_auth_default(scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
    service = build("sheets", "v4", credentials=credentials, cache_discovery=False)
    result = service.spreadsheets().values().get(
        spreadsheetId=SALARY_REPAYMENT_SHEET_ID,
        range=f"'{SALARY_REPAYMENT_ORG_SHEET_NAME}'!A1:Z2000",
    ).execute()
    return rows_to_dicts(result.get("values", []))


def plan_import(org_rows: list, accounts: list) -> dict:
    """純函式（不碰 Firestore／Sheets API），方便寫單元測試。回傳：
    - updates：[{username, name, manager_usernames, manager_names}, ...]，
      每個現有帳號比對出來、準備要寫入的主管帳號清單。
    - unmatched_employee_names：試算表裡有、但系統帳號找不到同名帳號的
      員工姓名（這些人根本沒有登入帳號，不需要處理，僅供你參考）。
    - unmatched_manager_names：試算表裡某個員工的主管姓名，在系統帳號裡
      找不到同名帳號（該主管可能還沒建帳號，這個主管關係會被跳過）。
    - duplicate_names：系統帳號裡姓名重複的人（同名同姓），這種情況比對
      會不準，這支腳本會直接跳過這些人、全部留給你手動在 /accounts 設定，
      不會亂猜是哪一個帳號。
    """
    accounts_by_name = {}
    duplicate_names = set()
    for a in accounts:
        if a["name"] in accounts_by_name:
            duplicate_names.add(a["name"])
        else:
            accounts_by_name[a["name"]] = a

    updates = []
    unmatched_employee_names = []
    unmatched_manager_names = set()

    for row in org_rows:
        employee_name = (row.get("員工姓名") or "").strip()
        if not employee_name:
            continue
        if employee_name in duplicate_names:
            continue
        account = accounts_by_name.get(employee_name)
        if not account:
            unmatched_employee_names.append(employee_name)
            continue

        manager_usernames = []
        manager_names = []
        for manager_name in parse_name_list(row.get("主管姓名")):
            if manager_name == employee_name:
                # 試算表裡「最高層級」的人常把自己填成自己的主管，代表沒有
                # 上層主管，這裡不需要參照到自己。
                continue
            if manager_name in duplicate_names:
                unmatched_manager_names.add(f"{manager_name}（同名同姓，無法判斷是哪一個帳號）")
                continue
            manager_account = accounts_by_name.get(manager_name)
            if manager_account:
                manager_usernames.append(manager_account["username"])
                manager_names.append(manager_name)
            else:
                unmatched_manager_names.add(manager_name)

        updates.append(
            {
                "username": account["username"],
                "name": employee_name,
                "manager_usernames": manager_usernames,
                "manager_names": manager_names,
            }
        )

    return {
        "updates": updates,
        "unmatched_employee_names": unmatched_employee_names,
        "unmatched_manager_names": sorted(unmatched_manager_names),
        "duplicate_names": sorted(duplicate_names),
    }


def main():
    org_rows = _fetch_org_rows()
    accounts = list_accounts()
    plan = plan_import(org_rows, accounts)

    print("=== 即將設定的主管關係 ===")
    for item in plan["updates"]:
        managers = "、".join(item["manager_names"]) if item["manager_names"] else "(無)"
        print(f"  - {item['username']}（{item['name']}）主管 -> {managers}")

    if plan["duplicate_names"]:
        print("\n⚠️  以下姓名在系統帳號裡有重複，這幾個人已整批跳過，需要你自己到 /accounts 手動設定：")
        for name in plan["duplicate_names"]:
            print(f"  - {name}")

    if plan["unmatched_manager_names"]:
        print("\n⚠️  以下主管姓名在系統帳號裡找不到對應帳號（可能還沒建帳號），這幾段主管關係不會被設定：")
        for name in plan["unmatched_manager_names"]:
            print(f"  - {name}")

    if plan["unmatched_employee_names"]:
        print(f"\n（另外試算表裡有 {len(plan['unmatched_employee_names'])} 位沒有對應到系統帳號，代表這些人根本沒有登入帳號，不需要處理。）")

    if not plan["updates"]:
        print("\n沒有任何帳號可以比對成功，不會做任何變更。")
        return

    answer = input(f"\n確定要把上面 {len(plan['updates'])} 組帳號的主管關係寫入嗎？輸入 yes 才會執行：")
    if answer.strip().lower() != "yes":
        print("已取消，沒有做任何變更。")
        return

    for item in plan["updates"]:
        set_manager_usernames(item["username"], item["manager_usernames"])

    print(f"\n完成，共更新 {len(plan['updates'])} 組帳號的主管關係。之後要調整，直接到 /accounts 編輯帳號即可，不用再跑這支腳本。")


if __name__ == "__main__":
    if len(sys.argv) != 1:
        print(__doc__)
        sys.exit(1)
    main()
