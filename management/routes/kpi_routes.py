from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse

from management import repository
from management.auth import admin_required, current_user, login_required
from management.config import ALLOWED_UPLOAD_CONTENT_TYPES, MAX_UPLOAD_BYTES
from management.storage import StorageNotConfigured, upload_file
from management.templating import templates

router = APIRouter()


@router.get("/kpi-reports")
def kpi_report_list(request: Request, redirect=Depends(login_required)):
    if redirect:
        return redirect
    return templates.TemplateResponse(
        request, "kpi_report_list.html", {"user": current_user(request), "reports": repository.list_kpi_reports()}
    )


@router.get("/kpi-reports/new")
def new_kpi_report_form(request: Request, redirect=Depends(admin_required)):
    if redirect:
        return redirect
    return templates.TemplateResponse(request, "kpi_report_form.html", {"user": current_user(request), "error": ""})


@router.post("/kpi-reports/new")
async def create_kpi_report_submit(
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
    file: UploadFile = File(...),
    redirect=Depends(admin_required),
):
    if redirect:
        return redirect
    user = current_user(request)
    title = title.strip()

    error = ""
    if not title or not file.filename:
        error = "標題跟檔案都要提供。"
    else:
        content = await file.read()
        content_type = file.content_type or "application/octet-stream"
        if len(content) > MAX_UPLOAD_BYTES:
            error = "檔案超過 20MB 上限"
        elif content_type not in ALLOWED_UPLOAD_CONTENT_TYPES:
            error = "檔案格式不支援，請上傳 Excel/PDF/PPT/圖片"

    if error:
        return templates.TemplateResponse(
            request, "kpi_report_form.html", {"user": user, "error": error}, status_code=400
        )

    try:
        blob_path = upload_file("kpi-reports", user["username"], file.filename, content, content_type)
    except StorageNotConfigured:
        return templates.TemplateResponse(
            request,
            "kpi_report_form.html",
            {"user": user, "error": "檔案儲存空間尚未設定，請聯絡系統管理員。"},
            status_code=400,
        )

    repository.create_kpi_report(title, description.strip(), blob_path, file.filename, user["username"], user["name"])
    return RedirectResponse(url="/management/kpi-reports", status_code=303)


@router.get("/kpi-reports/{report_id}")
def kpi_report_detail(report_id: str, request: Request, redirect=Depends(login_required)):
    if redirect:
        return redirect
    report = repository.get_kpi_report(report_id)
    if not report:
        return RedirectResponse(url="/management/kpi-reports", status_code=303)
    return templates.TemplateResponse(request, "kpi_report_detail.html", {"user": current_user(request), "report": report})


@router.post("/kpi-reports/{report_id}/delete")
def delete_kpi_report_submit(report_id: str, request: Request, redirect=Depends(admin_required)):
    if redirect:
        return redirect
    repository.delete_kpi_report(report_id)
    return RedirectResponse(url="/management/kpi-reports", status_code=303)
