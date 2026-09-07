#!/usr/bin/env python3
"""一次性遷移腳本：把既有帳號的舊版 `role`（"admin"/"staff"，只代表配送部
系統的角色）轉成新版的多模組權限欄位 `modules: {"delivery": role}`，並且
把指定的一組帳號（通常是老闆本人）標記成 is_platform_admin=True，其他帳號
一律是 False。轉完之後移除舊的 `role` 欄位。

只需要在正式環境跑「一次」（新增管理部模組、導入多模組權限架構那次部署）。
之後新帳號一律透過 delivery.seed_admin 建立，不會再有舊版 role 欄位，也就
不需要再跑這支腳本。

用法（在有 GCP 憑證的環境，例如 Cloud Shell，位於 repo 根目錄執行）：

    python -m scripts.migrate_users_to_modules <要設為全平台管理員的帳號>

例如你（老闆）在配送部系統的帳號是 boss：

    python -m scripts.migrate_users_to_modules boss

會先印出即將變更的內容再詢問是否要真的寫入，避免手滑跑錯帳號。
"""
import sys

from platform_db import users_ref


def _plan_migration(platform_admin_username: str):
    """回傳 (username, old_role, new_modules, is_platform_admin) 的清單，
    純函式方便測試，不直接碰 Firestore 寫入。"""
    plan = []
    for snapshot in users_ref().stream():
        data = snapshot.to_dict() or {}
        if "modules" in data:
            # 已經是新格式（例如重複執行這支腳本），跳過。
            continue
        old_role = data.get("role", "staff")
        plan.append(
            {
                "username": snapshot.id,
                "old_role": old_role,
                "modules": {"delivery": old_role},
                "is_platform_admin": snapshot.id == platform_admin_username,
            }
        )
    return plan


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)

    platform_admin_username = sys.argv[1]
    plan = _plan_migration(platform_admin_username)

    if not plan:
        print("沒有需要遷移的帳號（可能已經全部是新格式了）。")
        return

    print("即將進行以下變更：")
    for item in plan:
        print(
            f"  - {item['username']}：role={item['old_role']} -> "
            f"modules={item['modules']}, is_platform_admin={item['is_platform_admin']}"
        )

    if not any(item["is_platform_admin"] for item in plan):
        print(f"\n⚠️  警告：沒有任何一個帳號的使用者名稱是「{platform_admin_username}」，"
              "沒有人會被設成全平台管理員，請確認帳號名稱是否打對。")

    answer = input("\n確定要寫入嗎？輸入 yes 才會執行：")
    if answer.strip().lower() != "yes":
        print("已取消，沒有做任何變更。")
        return

    from google.cloud import firestore

    for item in plan:
        users_ref().document(item["username"]).update(
            {
                "modules": item["modules"],
                "is_platform_admin": item["is_platform_admin"],
                "role": firestore.DELETE_FIELD,
            }
        )

    print(f"\n完成，共遷移 {len(plan)} 組帳號。")


if __name__ == "__main__":
    main()
