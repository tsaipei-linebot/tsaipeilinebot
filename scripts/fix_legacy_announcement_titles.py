#!/usr/bin/env python3
"""一次性遷移腳本：修正舊的系統更新自動公告，標題被誤植成技術性的
git 合併說明（例如「Merge pull request #174 from
tsaipei-linebot/claude/taoyuan-bind-plus-format」），真正的 PR 標題反而
被塞進內文——這是 `.github/workflows/deploy.yml` 的自動公告邏輯原本假設
PR 合併 commit 是 squash 格式，但這個 repo 實際用的是一般合併（`git
merge`）造成的 bug，2026-09-19 自動公告功能上線後、到這支腳本修正邏輯
（見同一個 PR 的 `.github/workflows/deploy.yml` 改動）上線前發出的每一則
自動公告都受影響。手動在網頁上新增的公告不受影響（標題不會剛好長這樣）。

安全性（只修正看得懂的、看不懂的列出來不亂動）：
- 只處理標題符合「Merge pull request #NNN from ...」這個技術性格式的
  公告，其他一律跳過不動。
- 內文也是空的（沒辦法從內文救回真正的標題）的話跳過、列出來，不會
  亂猜一個標題填上去，需要人工到 /announcements 刪除或手動改。
- 內文第一行當新標題，內文其餘的行（如果有）當新內文；正常情況下
  內文只有一行（PR 標題本身），修正後新內文會是空字串。

用法（在有 GCP 憑證、能連 Firestore 的環境，例如 Cloud Shell，位於 repo
根目錄執行）：

    python -m scripts.fix_legacy_announcement_titles

會先印出即將修正的清單再詢問是否要真的寫入，輸入 yes 才會執行。
"""
import re
import sys

import platform_announcements

_MERGE_TITLE_PATTERN = re.compile(r"^Merge pull request #\d+ from ")


def plan_fix(announcements: list) -> dict:
    """純函式邏輯（announcements 是已經查好的資料，不碰 Firestore 寫入），
    方便寫單元測試。回傳：
    - to_fix：標題符合技術性合併說明格式、內文有值，可以自動救回真正
      標題的公告，附帶新標題/新內文。
    - already_correct：標題不符合這個技術性格式，跳過不動。
    - no_content：標題符合技術性格式，但內文也是空的，沒辦法自動救回
      真正的標題，需要人工處理。
    """
    to_fix = []
    already_correct = []
    no_content = []
    for a in announcements:
        title = a.get("title", "") or ""
        content = a.get("content", "") or ""
        if not _MERGE_TITLE_PATTERN.match(title):
            already_correct.append({"id": a["id"], "title": title})
            continue
        lines = content.strip().splitlines()
        if not lines:
            no_content.append({"id": a["id"], "title": title})
            continue
        to_fix.append(
            {
                "id": a["id"],
                "old_title": title,
                "new_title": lines[0].strip(),
                "new_content": "\n".join(lines[1:]).strip(),
            }
        )
    return {"to_fix": to_fix, "already_correct": already_correct, "no_content": no_content}


def main():
    announcements = platform_announcements.list_announcements()
    plan = plan_fix(announcements)

    print(f"公告總共 {len(announcements)} 則。")
    print(f"標題正常、這次跳過不動：{len(plan['already_correct'])} 則")

    if plan["no_content"]:
        print(f"\n標題是技術性合併說明、但內文也是空的，沒辦法自動救回真正標題，需要人工到 /announcements 處理：{len(plan['no_content'])} 則")
        for row in plan["no_content"]:
            print(f"  - [{row['id']}] {row['title']}")

    if not plan["to_fix"]:
        print("\n沒有可以自動修正標題的公告。")
        return

    print(f"\n=== 即將修正標題的公告（共 {len(plan['to_fix'])} 則）===")
    for row in plan["to_fix"]:
        print(f"  - 舊標題：{row['old_title']}")
        print(f"    新標題：{row['new_title']}")

    answer = input(f"\n確定要修正上面 {len(plan['to_fix'])} 則公告的標題嗎？輸入 yes 才會執行：")
    if answer.strip().lower() != "yes":
        print("已取消，沒有做任何變更。")
        return

    for row in plan["to_fix"]:
        platform_announcements.update_announcement_title(row["id"], row["new_title"], row["new_content"])

    print(f"\n完成，共修正 {len(plan['to_fix'])} 則公告的標題。")


if __name__ == "__main__":
    if len(sys.argv) != 1:
        print(__doc__)
        sys.exit(1)
    main()
