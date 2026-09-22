"""內部系統入口頁（/portal）＋ 職缺維護系統免登入銜接。

/portal 現在改成登入後才看得到（見 login_routes.py），只顯示這個帳號有
權限的部門卡片；職缺維護系統是完全獨立在 Netlify 的系統，不受這個平台的
權限管理，登入 /portal 的每個人都看得到那張卡片——差別只在有沒有幫他
對應到職缺系統的自動登入身分（見 job_portal_sso.py），比對得到就直接
免登入進去，比對不到就照舊導去手動輸入姓名/PIN 的畫面，不會擋人。

**公告管理**（2026-09-18 新增，見 platform_announcements.py 開頭的說明）：
`/announcements` 系列路由，全平台管理員可以自行發佈全公司公告，顯示在
/portal 最上方，任何登入的帳號都看得到同一份，不像卡片本身要依模組權限
篩選。
"""
import hmac
from datetime import datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse

import job_portal_sso
import platform_accounts
import platform_announcements
from dispatch_sites import list_sites
from platform_announcements import ANNOUNCEMENT_DEFAULT_DAYS
from platform_templating import templates
from services.dispatch_service import has_dispatch_access

router = APIRouter()

# 各部門模組在 /portal 卡片上顯示的圖示/說明文字。之後每加一個新部門，
# platform_accounts.MODULES 多一筆之外，這裡也要補一筆對應的顯示內容，
# 不然新模組雖然有權限但卡片會找不到說明文字（見 portal_home() 的
# fallback：找不到就用空字串，不會噴錯，只是畫面比較陽春）。
#
# help_href（2026-09-18 新增）：這個模組有沒有寫好「使用說明」頁面。
# 卡片上會多顯示一個「使用說明」按鈕，連去該模組自己的說明頁（不是共用
# 一頁，因為每個模組的操作內容差很多）；說明頁本身也是掛在該模組自己的
# 子系統底下、走該模組自己的 login_required，跟這裡卡片顯示不顯示是
# 同一組權限判斷，不會有「按鈕沒有但網址還是進得去」的落差。沒有
# help_href 的模組（還沒寫說明頁）卡片上就不會顯示這個按鈕——之後每寫好
# 一個模組的說明頁，這裡補上對應的 URL 即可。
_MODULE_CARD_INFO = {
    "delivery": {
        "description": "廠商人員管理、應徵名單、補款假別、車輛與意外事件回報",
        "help_href": "/delivery/help",
    },
    "management": {
        "description": "公告事項、會議記錄、規章/SOP 文件庫、業績報表、客戶拜訪、員工名冊、資產設備",
        "help_href": "/management/help",
    },
    "hr": {
        "description": "意外通報、體檢報告、員工關懷、公司證照、教育訓練彙整",
        "help_href": "/hr/help",
    },
    "salesdev": {
        "description": "派遣客戶開發名單、新登記工廠監控彙整",
        # 其他模組都是掛在自己的子系統底下，卡片連去各自的 /{code}/login；
        # 這個模組直接掛在根 app 上（見 salesdev_routes.py），沒有獨立的
        # 登入頁，卡片直接連過去即可。
        "href": "/salesdev",
        "help_href": "/salesdev/help",
    },
    "job_listings": {
        "description": "新增/維護職缺，送審後同步 Notion 職缺資料庫、官網與招募機器人",
        # 跟 salesdev 一樣直接掛在根 app（見 job_listing_routes.py），
        # 沒有獨立的登入頁。
        "href": "/job-listings",
        "help_href": "/job-listings/help",
    },
    "project_contracts": {
        "description": "提報新的專案合作廠商資訊，上傳合約檔案後自動寄送人資與財務單位",
        # 跟 job_listings 一樣直接掛在根 app（見 project_contract_routes.py），
        # 沒有獨立的登入頁。
        "href": "/project-contracts",
        "help_href": "/project-contracts/help",
    },
    "chicken_points": {
        "description": "同仁自費購買小雞點數，線上填單、手指簽名送出，會計登入查看紀錄",
        # 跟 job_listings 一樣直接掛在根 app（見 chicken_points_routes.py），
        # 沒有獨立的登入頁。
        "href": "/chicken-points",
        "help_href": "/chicken-points/help",
    },
    "dispatch_contracts": {
        "description": "填入客戶的班別/薪資/工作條件，自動套版產生派遣契約 Word 檔並存檔",
        # 跟 job_listings 一樣直接掛在根 app（見 dispatch_contract_routes.py），
        # 沒有獨立的登入頁。
        "href": "/dispatch-contracts",
        "help_href": "/dispatch-contracts/help",
    },
    "client_contracts": {
        "description": "填入客戶公司資料、合約期間與費率，自動套版產生企業服務合約 Word 檔並存檔",
        # 跟 job_listings 一樣直接掛在根 app（見 client_contract_routes.py），
        # 沒有獨立的登入頁。
        "href": "/client-contracts",
        "help_href": "/client-contracts/help",
    },
}


def _require_login(request: Request):
    if not platform_accounts.current_account(request):
        return RedirectResponse(url="/login?next=/portal", status_code=303)
    return None


def _with_display_date(announcement: dict) -> dict:
    return {
        **announcement,
        "created_at_display": datetime.fromtimestamp(announcement.get("created_at", 0)).strftime("%Y-%m-%d"),
        "expires_at_display": datetime.fromtimestamp(announcement.get("expires_at", 0)).strftime("%Y-%m-%d"),
    }


@router.get("/portal")
def portal_home(request: Request, redirect=Depends(_require_login)):
    if redirect:
        return redirect
    account = platform_accounts.current_account(request)
    cards = []
    for module in platform_accounts.MODULES:
        if not platform_accounts.has_module_access(account, module["code"]):
            continue
        info = _MODULE_CARD_INFO.get(module["code"], {})
        cards.append(
            {
                "name": module["name"],
                "description": info.get("description", ""),
                "href": info.get("href", f"/{module['code']}/login"),
                "help_href": info.get("help_href", ""),
            }
        )
    # 多所派遣媒合（2026-09-21 新增桃園所，2026-09-22 重構成多所共用＋
    # 新增高雄所）：不掛進 platform_accounts.MODULES，能不能看到照「部門」
    # 判斷（見 services/dispatch_service.py／dispatch_sites.py 開頭說明），
    # 不是模組權限勾選，所以這裡另外判斷、另外加卡片，跟上面那個迴圈
    # 分開。依所別清單的順序，帳號的部門符合哪個所就加那張卡片——一個
    # 帳號通常只會對應到一個所，但迴圈寫法天生就支援全平台管理員這種
    # 「每個所都看得到」的例外情況。
    for site in list_sites():
        if has_dispatch_access(account, site["code"]):
            cards.append(
                {
                    "name": f"{site['name']}專區",
                    "description": f"{site['name']}派遣人員/地點管理、需求時段媒合",
                    "href": f"/dispatch/{site['code']}",
                    "help_href": "",
                }
            )
    return templates.TemplateResponse(
        request,
        "portal_home.html",
        {
            "user": account,
            "cards": cards,
            "job_listing_url": job_portal_sso.JOB_LISTING_BASE_URL,
            "announcements": [_with_display_date(a) for a in platform_announcements.list_active_announcements()],
        },
    )


@router.get("/announcements")
def announcements_page(request: Request, redirect=Depends(platform_accounts.require_platform_admin)):
    """全公司公告管理，限全平台管理員（2026-09-18 新增，見
    platform_announcements.py 開頭的說明：這是全公司層級的公告，不是任何
    單一部門模組的功能）。"""
    if redirect:
        return redirect
    return templates.TemplateResponse(
        request,
        "announcements.html",
        {
            "user": platform_accounts.current_account(request),
            "announcements": [_with_display_date(a) for a in platform_announcements.list_announcements()],
            "default_days": ANNOUNCEMENT_DEFAULT_DAYS,
            "error": "",
        },
    )


@router.post("/announcements/new")
def create_announcement_submit(
    request: Request,
    title: str = Form(...),
    content: str = Form(""),
    days: int = Form(ANNOUNCEMENT_DEFAULT_DAYS),
    redirect=Depends(platform_accounts.require_platform_admin),
):
    if redirect:
        return redirect
    title = title.strip()
    if title:
        account = platform_accounts.current_account(request)
        platform_announcements.create_announcement(
            title, content.strip(), created_by=account["username"], days=days if days > 0 else ANNOUNCEMENT_DEFAULT_DAYS
        )
    return RedirectResponse(url="/announcements", status_code=303)


@router.post("/announcements/{announcement_id}/active")
def toggle_announcement_active(
    announcement_id: str,
    request: Request,
    active: str = Form(...),
    redirect=Depends(platform_accounts.require_platform_admin),
):
    if redirect:
        return redirect
    platform_announcements.set_announcement_active(announcement_id, active == "1")
    return RedirectResponse(url="/announcements", status_code=303)


@router.post("/announcements/{announcement_id}/delete")
def delete_announcement_submit(
    announcement_id: str, request: Request, redirect=Depends(platform_accounts.require_platform_admin)
):
    if redirect:
        return redirect
    platform_announcements.delete_announcement(announcement_id)
    return RedirectResponse(url="/announcements", status_code=303)


@router.get("/portal/job-system-login")
def job_system_login(request: Request, redirect=Depends(_require_login)):
    if redirect:
        return redirect
    account = platform_accounts.current_account(request)
    identity = job_portal_sso.find_identity_by_name(account["name"])
    if not identity:
        # 比對不到這個人的職缺系統身分，直接導去原本網址，同仁照舊手動
        # 輸入姓名/PIN，不受影響、也不會看到任何錯誤訊息。
        return RedirectResponse(url=job_portal_sso.JOB_LISTING_BASE_URL, status_code=303)
    token = job_portal_sso.mint_sso_token(identity["name"], identity["pin"])
    return RedirectResponse(url=f"{job_portal_sso.JOB_LISTING_BASE_URL}?sso={token}", status_code=303)


@router.get("/api/job-system-sso/exchange")
def job_system_sso_exchange(token: str = ""):
    """職缺系統的 index.html 用背景 fetch 呼叫這支端點，把一次性代碼換回
    真正的姓名/PIN。刻意不要求我們平台自己的登入 session——這支是給另一個
    網域的頁面呼叫的，它本來就沒有、也不需要有我們的 session cookie，
    安全性完全靠代碼本身的簽章 + 45 秒有效期，不是靠登入狀態把關。"""
    identity = job_portal_sso.verify_sso_token(token)
    if not identity:
        return JSONResponse({"error": "invalid_or_expired_token"}, status_code=404)
    return JSONResponse(
        identity,
        headers={
            "Access-Control-Allow-Origin": job_portal_sso.ALLOWED_EXCHANGE_ORIGIN,
            "Cache-Control": "no-store",
        },
    )


@router.post("/internal/sync-job-system-identities")
def sync_job_system_identities(request: Request):
    """Cloud Scheduler 定期呼叫，把職缺系統組織表的姓名/PIN 同步進
    Firestore。安全機制比照 main.py 的 LOAD_TEST_SECRET：帶對
    X-Job-Sheet-Sync-Secret header 才受理，沒設定密鑰的話這支端點一律
    回傳 403，等同不存在。"""
    secret = request.headers.get("X-Job-Sheet-Sync-Secret", "")
    if not job_portal_sso.SYNC_TRIGGER_SECRET or not secret or not hmac.compare_digest(
        secret, job_portal_sso.SYNC_TRIGGER_SECRET
    ):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    count = job_portal_sso.sync_identities_from_sheet()
    return {"synced": count}


@router.post("/internal/announcements/auto-publish")
async def auto_publish_announcement(request: Request):
    """系統更新自動公告（2026-09-19 新增，見 platform_announcements.py
    開頭的說明）：`.github/workflows/deploy.yml` 部署成功後呼叫，用這次
    合併的 commit 訊息自動建立一則公告，不用每次都手動打字發佈。安全
    機制比照 /internal/sync-job-system-identities：帶對
    X-Auto-Announce-Secret header 才受理，沒設定密鑰的話這支端點一律
    回傳 403，等同不存在——GitHub Actions 呼叫這支端點沒有登入 session
    可以用，只能靠共用密鑰驗證。少凱業務開發專區的異動要不要發公告是
    在 CI 那邊（判斷這次改了哪些檔案）先擋掉，不會呼叫到這支端點，這裡
    不重複做這個判斷。"""
    secret = request.headers.get("X-Auto-Announce-Secret", "")
    if not platform_announcements.AUTO_ANNOUNCE_SECRET or not secret or not hmac.compare_digest(
        secret, platform_announcements.AUTO_ANNOUNCE_SECRET
    ):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    form = await request.form()
    title = (form.get("title") or "").strip()
    content = (form.get("content") or "").strip()
    if not title:
        return JSONResponse({"error": "missing_title"}, status_code=400)
    announcement_id = platform_announcements.create_announcement(title, content, created_by="system")
    return {"id": announcement_id}
