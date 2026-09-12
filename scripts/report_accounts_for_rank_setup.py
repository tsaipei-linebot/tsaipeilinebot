#!/usr/bin/env python3
"""唯讀輔助腳本：這次新增「職級」欄位（2026-09-12），既有帳號都還沒有
職級資料，需要你自己到 /accounts 網頁一個個帳號補上正確的部門／職級。

**這支腳本不會寫入任何資料**，純粹是幫你快速看一份清單：列出每個帳號
目前開放了哪些模組、以前在哪些模組裡是「主管」角色（改版前的舊資料），
方便你決定這個人的職級應該設多高——舊資料裡是「主管」的人，通常代表他
的職級至少要到副主任（含）以上，改版後才不會失去原本的管理權限。

**這件事很重要，麻煩儘快處理**：部署改版程式碼之後，因為職級欄位一開始
是空的，`is_manager_rank("")` 一律當作沒有管理權限，代表原本在配送部／
管理部／人資／小雞點數自費申請這四個模組裡是「主管」的帳號，會暫時失去
管理權限，直到你到 /accounts 幫他把職級設到副主任（含）以上為止。

用法（在有 GCP 憑證、能連 Firestore 的環境，例如 Cloud Shell，位於 repo
根目錄執行）：

    python -m scripts.report_accounts_for_rank_setup
"""
import platform_accounts
from platform_db import users_ref

# 這四個模組是目前唯一真的用到「主管」角色的模組，其餘模組本來就是
# 「開放就能用」，沒有角色差異，不用特別看。
_ADMIN_AWARE_MODULES = ["delivery", "management", "hr", "chicken_points"]


def build_report() -> list:
    """回傳每個帳號的報告資料，純函式（不印東西）方便寫測試。**這裡刻意
    直接讀 Firestore 的原始文件，不是透過 `platform_accounts.list_
    accounts()`**——後者的 `modules` 已經被正規化成單純的「開放了哪些
    模組」清單，原本存的舊角色值（"admin"/"staff"）在正規化過程中被
    捨棄了（角色現在只看職級），這支報告腳本的目的正是要找回「改版前
    誰是主管」這個已經不再存在於正規化資料裡的資訊，所以只能讀原始
    文件裡 `modules` 欄位本來的字典格式。"""
    accounts = platform_accounts.list_accounts()
    raw_modules_by_username = {}
    for snapshot in users_ref().stream():
        data = snapshot.to_dict() or {}
        raw_modules_by_username[snapshot.id] = data.get("modules") or {}

    report = []
    for a in accounts:
        raw_modules = raw_modules_by_username.get(a["username"], {})
        was_admin_of = []
        if isinstance(raw_modules, dict):
            was_admin_of = [
                platform_accounts.MODULE_MAP.get(code, code)
                for code in _ADMIN_AWARE_MODULES
                if raw_modules.get(code) == platform_accounts.ROLE_ADMIN
            ]
        report.append(
            {
                "username": a["username"],
                "name": a["name"],
                "department": a["department"] or "（尚未設定）",
                "rank": platform_accounts.RANK_MAP.get(a["rank"], "（尚未設定）") if a["rank"] else "（尚未設定）",
                "open_modules": [platform_accounts.MODULE_MAP.get(c, c) for c in a["modules"]],
                "was_admin_of": was_admin_of,
                "is_platform_admin": a["is_platform_admin"],
            }
        )
    return report


def main():
    report = build_report()
    if not report:
        print("目前還沒有任何帳號。")
        return

    print(f"共 {len(report)} 個帳號，列出目前開放的模組跟職級狀態：\n")
    pending_count = 0
    for r in report:
        print(f"帳號：{r['username']}（{r['name']}）")
        print(f"  部門：{r['department']}　職級：{r['rank']}")
        print(f"  已開放模組：{'、'.join(r['open_modules']) if r['open_modules'] else '（無）'}")
        if r["is_platform_admin"]:
            print("  ⚠ 全平台管理員，不受職級影響，不用特別設定職級。")
        elif r["was_admin_of"]:
            print(f"  ⚠ 改版前是「{'、'.join(r['was_admin_of'])}」的主管，記得幫他設定職級到副主任（含）以上，才不會失去管理權限。")
            pending_count += 1
        print()

    print(f"以上僅供參考，共 {pending_count} 個帳號改版前有主管權限、還沒設定職級，建議優先處理這些。")
    print("實際部門/職級請直接到 /accounts 逐一設定，這支腳本不會寫入任何資料。")
    print("提醒：只要幫某個帳號在 /accounts 存檔過一次，這支腳本就看不到他改版前的舊主管紀錄了（資料格式會被更新掉），建議儘快、一次把清單跑完再開始逐一設定。")


if __name__ == "__main__":
    main()
