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

**`/finance/migration`（2026-09-25 新增，只有全平台管理員）**：薪資補款搬離 GAS 階段 2 的第 1 步。
按鈕把試算表原樣同步進平台資料庫（見 `services/salary_repayment_store.py`），另一個開關決定 `/me`、
`/finance` 讀試算表還是平台資料，隨時可以切回去。
"""
from urllib.parse import quote

import base64

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response

import platform_accounts
from platform_templating import templates
from config import TAIPEI_TZ
from services import salary_repayment_store as store
from services.salary_repayment_service import (
    DISPLAY_COLUMNS,
    fetch_sheet_values,
    get_all_approved_repayment_records,
    has_finance_access,
)
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
        "read_source_name": store.SOURCE_NAMES[store.read_source()],
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


def _require_admin(request: Request):
    account = platform_accounts.current_account(request)
    if not account:
        return RedirectResponse(url="/login?next=/finance/migration", status_code=303)
    if not account.get("is_platform_admin"):
        return RedirectResponse(url="/finance", status_code=303)
    return None


def _taipei_time(value) -> str:
    if not value or not hasattr(value, "astimezone"):
        return ""
    return value.astimezone(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M")


def _migration_page(request: Request, error: str = "", notice: str = "", status_code: int = 200):
    state = store.get_state()
    return templates.TemplateResponse(
        request,
        "finance_migration.html",
        {
            "user": platform_accounts.current_account(request),
            "state": state,
            "read_source": store.read_source(),
            "source_names": store.SOURCE_NAMES,
            "last_synced_at": _taipei_time(state.get("last_synced_at")),
            "read_source_changed_at": _taipei_time(state.get("read_source_changed_at")),
            "result": state.get("last_result") or {},
            "error": error,
            "notice": notice,
        },
        status_code=status_code,
    )


@router.get("/finance/migration")
def finance_migration(request: Request, notice: str = "", redirect=Depends(_require_admin)):
    if redirect:
        return redirect
    notices = {"synced": "已經從試算表同步到平台。", "source": "讀取來源已經切換。"}
    return _migration_page(request, notice=notices.get(notice, ""))


@router.post("/finance/migration/sync")
def finance_migration_sync(request: Request, redirect=Depends(_require_admin)):
    if redirect:
        return redirect
    org_values, record_values, error = fetch_sheet_values()
    if error:
        return _migration_page(request, error=error, status_code=400)
    if not record_values:
        return _migration_page(request, error="試算表「薪資補款紀錄」分頁是空的，沒有同步。請確認分頁名稱是否正確。", status_code=400)
    store.sync_from_sheet(org_values, record_values, platform_accounts.current_account(request))
    return RedirectResponse(url="/finance/migration?notice=synced", status_code=303)


@router.post("/finance/migration/source")
def finance_migration_source(request: Request, source: str = Form(...), redirect=Depends(_require_admin)):
    if redirect:
        return redirect
    if source not in store.SOURCE_NAMES:
        return _migration_page(request, error="讀取來源不正確。", status_code=400)
    if source == store.SOURCE_FIRESTORE and not store.get_state().get("last_synced_at"):
        return _migration_page(request, error="還沒有從試算表同步過，平台上沒有資料，請先按「從試算表同步到平台」。", status_code=400)
    store.set_read_source(source, platform_accounts.current_account(request))
    return RedirectResponse(url="/finance/migration?notice=source", status_code=303)
