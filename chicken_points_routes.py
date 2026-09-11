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
"""
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

import platform_accounts
from platform_templating import templates
from services.chicken_points_service import (
    DEPARTMENT_OPTIONS,
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
    return templates.TemplateResponse(
        request,
        "chicken_points_form.html",
        {
            "user": platform_accounts.current_account(request),
            "error": "",
            "form": {},
            "department_options": DEPARTMENT_OPTIONS,
            "point_rate": POINT_RATE,
        },
    )


@router.post("/chicken-points/new")
async def chicken_points_submit(
    request: Request,
    department: str = Form(""),
    purchase_month: str = Form(""),
    points: str = Form(""),
    signed_image: str = Form(""),
    redirect=Depends(_require_access),
):
    if redirect:
        return redirect
    account = platform_accounts.current_account(request)
    form_values = {"department": department, "purchase_month": purchase_month, "points": points}

    error = ""
    points_int = 0
    if department not in DEPARTMENT_OPTIONS:
        error = "請選擇申請部門。"
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
        context = {
            "user": account,
            "error": error,
            "form": form_values,
            "department_options": DEPARTMENT_OPTIONS,
            "point_rate": POINT_RATE,
        }
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
