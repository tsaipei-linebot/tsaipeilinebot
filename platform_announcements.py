"""/portal 入口頁的全公司公告（2026-09-18 新增）。

原本先做在配送部系統主頁（見 delivery/repository.py 舊版的「公告管理」
那節），使用者確認要放在全公司都會看到的 /portal 入口頁，不是配送部
專屬——改放這裡（跟 platform_accounts.py／platform_departments.py 同一層
級，不屬於任何單一部門模組），並且拿掉配送部主頁那個版本，避免同時維護
兩套公告功能。

**發佈公告只開放全平台管理員**（`platform_accounts.require_platform_admin`，
目前就是老闆本人那組帳號）——/portal 沒有「配送部管理員」那種單一模組的
管理權限概念，公告內容是全公司層級的事，交給老闆本人的帳號管理最合理。

**公告顯示不分權限**：任何登入的帳號在 /portal 都看得到同一份公告清單，
不像卡片本身要依「這個帳號開了哪些模組」篩選——公告是公司層級的訊息，
不因為某個帳號沒有某個模組的權限就該看不到。

到期時間（`expires_at`）是「建立時間 + N 天」，`list_active_announcements()`
每次查詢時都重新比對目前時間，超過期限的自動不顯示，不需要另外寫排程去
清資料或改狀態；`active` 欄位是給管理員「不用等到期，想馬上下架」用的
手動開關。

**內容原則（不是程式邏輯，是給發佈公告的人參考）**：「少凱業務開發專區」
（salesdev 模組）的任何事項/功能異動，不列入這裡的公告——那個卡片是
少凱個人的業務開發專區，跟其他人無關，不用全公司廣播。

**系統更新自動公告（2026-09-19 新增）**：使用者要求系統只要有功能更新
就自動加入公告，不用每次都手動打字發佈（少凱業務開發專區的異動維持
上面那條「不列入」的原則）。做法是 `.github/workflows/deploy.yml` 部署
成功後多一個步驟：比對這次 push 到 main 改了哪些檔案，如果全部都是
`salesdev` 相關檔案（含 `HANDOFF.md`，因為每次改動都會更新這份文件，
不能因為它也改了就誤判成「不是純 salesdev 改動」），就跳過不發公告；
否則就用這次合併的 commit 訊息（就是 PR 標題＋內文，已經是給人看的
中文說明）呼叫 `POST /internal/announcements/auto-publish` 這個端點
（見下面 `AUTO_ANNOUNCE_SECRET` 的說明）自動建立一則公告。這個端點
不需要登入，用共用密鑰驗證，做法比照 `job_portal_sso.py` 的
`SYNC_TRIGGER_SECRET`（GitHub Actions 呼叫、不是瀏覽器呼叫，沒有登入
session 可以用）。
"""
import os
import time

from platform_db import announcements_ref

ANNOUNCEMENT_DEFAULT_DAYS = 7

# GitHub Actions 部署成功後呼叫 /internal/announcements/auto-publish 自動
# 建立公告時要帶對的共用密鑰，沒設定的話這支端點一律回傳 403，等同不存在
# （跟 job_portal_sso.py 的 SYNC_TRIGGER_SECRET 同一套做法）。
AUTO_ANNOUNCE_SECRET = os.getenv("AUTO_ANNOUNCE_SECRET", "")


def list_active_announcements() -> list:
    """/portal 首頁顯示用：只回傳「還在啟用中，而且還沒過期」的公告，
    新到舊排序。"""
    now = time.time()
    result = []
    for snapshot in announcements_ref().stream():
        data = snapshot.to_dict() or {}
        data["id"] = snapshot.id
        data.setdefault("active", True)
        if not data["active"]:
            continue
        if data.get("expires_at", 0) <= now:
            continue
        result.append(data)
    result.sort(key=lambda a: a.get("created_at", 0), reverse=True)
    return result


def list_announcements() -> list:
    """公告管理頁用：回傳全部公告（含已停用、已過期的），新到舊排序，
    讓管理員可以回顧之前發過什麼公告。"""
    now = time.time()
    result = []
    for snapshot in announcements_ref().stream():
        data = snapshot.to_dict() or {}
        data["id"] = snapshot.id
        data.setdefault("active", True)
        data["expired"] = data.get("expires_at", 0) <= now
        result.append(data)
    result.sort(key=lambda a: a.get("created_at", 0), reverse=True)
    return result


def get_announcement(announcement_id: str):
    if not announcement_id:
        return None
    snapshot = announcements_ref().document(announcement_id).get()
    if not snapshot.exists:
        return None
    data = snapshot.to_dict() or {}
    data["id"] = snapshot.id
    data.setdefault("active", True)
    return data


def create_announcement(title: str, content: str, created_by: str = "", days: int = ANNOUNCEMENT_DEFAULT_DAYS) -> str:
    now = time.time()
    doc_ref = announcements_ref().document()
    doc_ref.set(
        {
            "title": title,
            "content": content,
            "active": True,
            "created_by": created_by,
            "created_at": now,
            "expires_at": now + days * 86400,
        }
    )
    return doc_ref.id


def update_announcement_title(announcement_id: str, title: str, content: str) -> bool:
    """改標題/內文，目前只有 `scripts/fix_legacy_announcement_titles.py`
    這支一次性遷移腳本在用（修正 2026-09-19～2026-09-21 之間，自動公告
    邏輯誤判合併 commit 格式、把技術性的「Merge pull request #NNN
    from...」說明當成標題的舊公告）——公告管理頁本身目前沒有編輯功能，
    只有新增/停用/刪除（見 portal_routes.py）。"""
    ref = announcements_ref().document(announcement_id)
    if not ref.get().exists:
        return False
    ref.update({"title": title, "content": content})
    return True


def set_announcement_active(announcement_id: str, active: bool) -> bool:
    ref = announcements_ref().document(announcement_id)
    if not ref.get().exists:
        return False
    ref.update({"active": active})
    return True


def delete_announcement(announcement_id: str) -> bool:
    ref = announcements_ref().document(announcement_id)
    if not ref.get().exists:
        return False
    ref.delete()
    return True
