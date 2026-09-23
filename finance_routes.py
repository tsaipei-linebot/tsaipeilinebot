"""財務部專區（/finance，2026-09-22 新增）：財務同仁查看所有「已核准」
的薪資補款紀錄（不像 /me 只看得到自己送出或轄下同仁送出的），並可依日期
區間下載存查用 PDF——打包成一個 ZIP 檔案（一筆補款單一份 PDF，跟核准信
附件同一份排版），由 job-portal-gas-project 的 `EXPORT_SALARY_PDFS`
端點現場產生，材霈平台這邊不重新刻一份排版邏輯。

**下載格式可以選 PDF 或圖片**（2026-09-23 新增）：財務留底的實際動作是
「一次全選、右鍵列印」，PDF 在檔案總管裡沒辦法多選一起印、要一個一個
開，圖片可以。選圖片時**內容完全不變**，一樣是上面那份存查單，只是平台
拿到 GAS 產的 PDF 之後多做一步轉檔（見 services/pdf_to_image.py）——
GAS 那一側一行都不用動。預設仍是 PDF，維持原本的行為。

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
from services.pdf_to_image import convert_pdf_zip_to_png_zip
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
    request: Request,
    start_date: str = Form(...),
    end_date: str = Form(...),
    export_format: str = Form("pdf"),
    redirect=Depends(_require_access),
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

    # 只有明確選「圖片」才轉檔，其他值（含沒帶這個欄位的舊表單）一律照舊
    # 給 PDF——新功能不該因為請求少帶一個欄位就改變原本的行為。
    if export_format == "image":
        png_zip_bytes = convert_pdf_zip_to_png_zip(zip_bytes)
        if not png_zip_bytes:
            return templates.TemplateResponse(
                request,
                "finance_home.html",
                _home_context(request, "轉換成圖片時發生問題，請改用 PDF 格式下載，或稍後再試一次。"),
                status_code=400,
            )
        zip_bytes = png_zip_bytes
        filename = f"薪資補款存查單_圖片_{start_date}_{end_date}.zip"

    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )
