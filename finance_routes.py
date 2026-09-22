"""財務部專區（/finance，2026-09-22 新增）：財務同仁查看所有「已核准」
的薪資補款紀錄（不像 /me 只看得到自己送出或轄下同仁送出的），並可依日期
區間下載存查用 PDF——打包成一個 ZIP 檔案（一筆補款單一份 PDF，跟核准信
附件同一份排版），由 job-portal-gas-project 的 `EXPORT_SALARY_PDFS`
端點現場產生，材霈平台這邊不重新刻一份排版邏輯。

**權限模型**：不掛進 `platform_accounts.MODULES`，改成照「部門」判斷——
帳號的 `department` 是「財務部」、或是全平台管理員，才看得到卡片、進得去
頁面，跟人資部門/桃園所/高雄所同一套做法（見
`services/salary_repayment_service.has_finance_access()`）。

**只顯示「已核准」的紀錄**：試算表裡「已退回」的紀錄 GAS 那邊本來就會
直接刪除、不會留在表裡；「尚未審核」的財務不需要看到（只看確定要撥款
的），2026-09-22 使用者明確確認。
"""
from urllib.parse import quote

import base64

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response

import platform_accounts
from platform_templating import templates
from services.salary_repayment_service import DISPLAY_COLUMNS, get_all_approved_repayment_records, has_finance_access
from services.salary_repayment_submit_service import export_approved_salary_pdfs_zip

router = APIRouter()


def _require_access(request: Request):
    account = platform_accounts.current_account(request)
    if not account:
        return RedirectResponse(url="/login?next=/finance", status_code=303)
    if not has_finance_access(account):
        return RedirectResponse(url="/portal", status_code=303)
    return None


def _home_context(request: Request, export_error: str = ""):
    records, error = get_all_approved_repayment_records()
    return {
        "user": platform_accounts.current_account(request),
        "salary_repayment_columns": DISPLAY_COLUMNS,
        "salary_repayment_records": records,
        "salary_repayment_error": error,
        "export_error": export_error,
    }


@router.get("/finance")
def finance_home(request: Request, redirect=Depends(_require_access)):
    if redirect:
        return redirect
    return templates.TemplateResponse(request, "finance_home.html", _home_context(request))


@router.get("/finance/help")
def finance_help_page(request: Request, redirect=Depends(_require_access)):
    if redirect:
        return redirect
    return templates.TemplateResponse(request, "finance_help.html", {"user": platform_accounts.current_account(request)})


@router.post("/finance/export-pdf")
def finance_export_pdf(
    request: Request, start_date: str = Form(...), end_date: str = Form(...), redirect=Depends(_require_access)
):
    if redirect:
        return redirect

    if not start_date or not end_date or start_date > end_date:
        return templates.TemplateResponse(
            request,
            "finance_home.html",
            _home_context(request, "請確認日期區間，起始日期不能晚於結束日期。"),
            status_code=400,
        )

    result = export_approved_salary_pdfs_zip(start_date, end_date)
    if result.get("status") != "success":
        return templates.TemplateResponse(
            request,
            "finance_home.html",
            _home_context(request, result.get("message") or "下載失敗，請稍後再試。"),
            status_code=400,
        )

    zip_bytes = base64.b64decode(result["base64"])
    filename = result.get("filename") or f"薪資補款存查單_{start_date}_{end_date}.zip"
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )
