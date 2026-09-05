"""內部系統入口頁（/portal）＋ 職缺維護系統免登入銜接。

/portal 現在改成登入後才看得到（見 login_routes.py），只顯示這個帳號有
權限的部門卡片；職缺維護系統是完全獨立在 Netlify 的系統，不受這個平台的
權限管理，登入 /portal 的每個人都看得到那張卡片——差別只在有沒有幫他
對應到職缺系統的自動登入身分（見 job_portal_sso.py），比對得到就直接
免登入進去，比對不到就照舊導去手動輸入姓名/PIN 的畫面，不會擋人。
"""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse

import job_portal_sso
import platform_accounts
from platform_templating import templates

router = APIRouter()

# 各部門模組在 /portal 卡片上顯示的圖示/說明文字。之後每加一個新部門，
# platform_accounts.MODULES 多一筆之外，這裡也要補一筆對應的顯示內容，
# 不然新模組雖然有權限但卡片會找不到說明文字（見 portal_home() 的
# fallback：找不到就用空字串，不會噴錯，只是畫面比較陽春）。
_MODULE_CARD_INFO = {
    "delivery": {
        "description": "廠商人員管理、應徵名單、補款假別、車輛與意外事件回報",
    },
    "management": {
        "description": "公告事項、會議記錄、規章/SOP 文件庫、業績報表、客戶拜訪、員工名冊、資產設備",
    },
}


def _require_login(request: Request):
    if not platform_accounts.current_account(request):
        return RedirectResponse(url="/login?next=/portal", status_code=303)
    return None


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
                "href": f"/{module['code']}/login",
            }
        )
    return templates.TemplateResponse(
        request,
        "portal_home.html",
        {
            "user": account,
            "cards": cards,
            "job_listing_url": job_portal_sso.JOB_LISTING_BASE_URL,
        },
    )


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
    if not job_portal_sso.SYNC_TRIGGER_SECRET or secret != job_portal_sso.SYNC_TRIGGER_SECRET:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    count = job_portal_sso.sync_identities_from_sheet()
    return {"synced": count}
