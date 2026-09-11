"""小雞點數自費申請（/chicken-points）：同仁自費購買小雞點數時填寫、簽名
送出的內部申請單。跟職缺維護、專案合約維護不同，這個功能**完全是材霈
平台自己的東西**，不轉送給外部職缺維護 GAS 系統，也沒有審核流程——同仁
填好資料、在畫面上用手指/滑鼠簽名，送出後系統把表單內容跟簽名合成一張
圖直接存進材霈平台自己的 Firestore，會計自己登入這裡查看清單即可。

是否看得到這張卡片、能不能進來，由 `/accounts` 的權限設定決定（模組代碼
`chicken_points`）。**跟其他模組不同的地方**：這裡的「專員」/「主管」
角色真的有差別——「專員」登入後只看得到自己送出過的申請紀錄；「主管」
（例如會計）看得到全部同仁的申請紀錄，方便對帳，這是目前平台裡第一個
真的用到這個角色區分的模組。

**申請部門 2026-09-11 改成自動帶入，不用同仁自己選**：直接沿用帳號
資料裡本來就有、但一直沒有功能在用的 `department` 欄位（`/accounts`
編輯帳號畫面「部門」那一欄，自由填寫）——使用者要求「跟建立帳號內的
部門相同就好了，不用讓人員選了」。因為是帳號本身就有的資料，不是同仁
在這個表單裡自己填的，所以這裡不再收 `department` 這個表單欄位，直接
從登入的帳號資料讀。如果帳號沒有設定部門（`department` 是空字串），
擋下表單並提示要先請平台管理員到 `/accounts` 幫忙補上部門，不能送出——
避免存進一筆部門是空白的紀錄，讓會計對帳時看不出是哪個部門申請的。
"""
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

import platform_accounts
from platform_templating import templates
from services.chicken_points_service import (
    POINT_RATE,
    list_all_requests,
    list_requests_by_username,
    save_request,
)

router = APIRouter()

MODULE_CODE = "chicken_points"

# 合成圖是瀏覽器端 <canvas> 直接輸出的 base64 PNG（見
# templates/chicken_points_form.html 的 composeSignedImage()），正常情況
# 下（幾行文字＋一段簽名線條）頂多幾十 KB。這裡抓一個遠高於正常情況、但
# 足以擋下異常巨大檔案的上限，避免超過 Firestore 單一文件 1MB 的限制。
_MAX_SIGNED_IMAGE_BASE64_CHARS = 700_000

_MISSING_DEPARTMENT_MESSAGE = (
    "您的帳號還沒有設定部門，沒辦法送出申請（申請部門會直接沿用帳號資料，不用自己選）。"
    "請聯絡平台管理員，到「帳號管理」幫您的帳號補上部門後再回來申請。"
)


def _require_access(request: Request):
    account = platform_accounts.current_account(request)
    if not account:
        return RedirectResponse(url=f"/login?next=/{MODULE_CODE.replace('_', '-')}", status_code=303)
    if not platform_accounts.has_module_access(account, MODULE_CODE):
        return RedirectResponse(url="/portal", status_code=303)
    return None


@router.get("/chicken-points")
def chicken_points_home(request: Request, submitted: str = "", redirect=Depends(_require_access)):
    if redirect:
        return redirect
    account = platform_accounts.current_account(request)
    is_admin_view = platform_accounts.module_role(account, MODULE_CODE) == platform_accounts.ROLE_ADMIN
    records = list_all_requests() if is_admin_view else list_requests_by_username(account["username"])
    return templates.TemplateResponse(
        request,
        "chicken_points_home.html",
        {"user": account, "records": records, "is_admin_view": is_admin_view, "submitted": submitted},
    )


@router.get("/chicken-points/new")
def chicken_points_new_form(request: Request, redirect=Depends(_require_access)):
    if redirect:
        return redirect
    account = platform_accounts.current_account(request)
    context = {
        "user": account,
        "error": "" if account.get("department") else _MISSING_DEPARTMENT_MESSAGE,
        "form": {},
        "point_rate": POINT_RATE,
    }
    return templates.TemplateResponse(request, "chicken_points_form.html", context)


@router.post("/chicken-points/new")
async def chicken_points_submit(
    request: Request,
    purchase_month: str = Form(""),
    points: str = Form(""),
    signed_image: str = Form(""),
    redirect=Depends(_require_access),
):
    if redirect:
        return redirect
    account = platform_accounts.current_account(request)
    department = account.get("department", "")
    form_values = {"purchase_month": purchase_month, "points": points}

    error = ""
    points_int = 0
    if not department:
        error = _MISSING_DEPARTMENT_MESSAGE
    elif not purchase_month:
        error = "請填寫購買月份。"
    else:
        try:
            points_int = int(points)
            if points_int <= 0:
                raise ValueError
        except (TypeError, ValueError):
            error = "請輸入正確的點數（正整數）。"

    if not error and not signed_image:
        error = "請先簽名再送出。"
    if not error and len(signed_image) > _MAX_SIGNED_IMAGE_BASE64_CHARS:
        error = "簽名圖檔異常過大，請重新整理頁面再試一次。"

    if error:
        context = {"user": account, "error": error, "form": form_values, "point_rate": POINT_RATE}
        return templates.TemplateResponse(request, "chicken_points_form.html", context, status_code=400)

    save_request(
        applicant_username=account["username"],
        applicant_name=account["name"],
        department=department,
        purchase_month=purchase_month,
        points=points_int,
        signed_image_base64=signed_image,
    )
    return RedirectResponse(url="/chicken-points?submitted=1", status_code=303)
