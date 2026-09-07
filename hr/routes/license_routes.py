from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse

from hr import repository
from hr.auth import admin_required, current_user, login_required
from hr.config import ALLOWED_UPLOAD_CONTENT_TYPES, MAX_UPLOAD_BYTES
from hr.storage import StorageNotConfigured, upload_file
from hr.templating import templates

router = APIRouter()


async def _read_optional_file(file: UploadFile):
    if file is None or not file.filename:
        return None, None, None, ""
    content = await file.read()
    content_type = file.content_type or "application/octet-stream"
    if len(content) > MAX_UPLOAD_BYTES:
        return None, None, None, "檔案超過 20MB 上限"
    if content_type not in ALLOWED_UPLOAD_CONTENT_TYPES:
        return None, None, None, "檔案格式不支援，請上傳 PDF/Word/Excel/圖片"
    return content, content_type, file.filename, ""


@router.get("/licenses")
def license_list(request: Request, name: str = "", redirect=Depends(login_required)):
    if redirect:
        return redirect
    return templates.TemplateResponse(
        request,
        "license_list.html",
        {"user": current_user(request), "records": repository.list_licenses(name_filter=name), "filter_name": name},
    )


@router.get("/licenses/new")
def new_license_form(request: Request, redirect=Depends(admin_required)):
    if redirect:
        return redirect
    return templates.TemplateResponse(request, "license_form.html", {"user": current_user(request), "error": ""})


@router.post("/licenses/new")
async def create_license_submit(
    request: Request,
    name: str = Form(...),
    issuer: str = Form(""),
    license_no: str = Form(""),
    expiry_date: str = Form(...),
    file: UploadFile = File(None),
    redirect=Depends(admin_required),
):
    if redirect:
        return redirect
    user = current_user(request)
    name = name.strip()
    if not name or not expiry_date:
        return templates.TemplateResponse(
            request, "license_form.html", {"user": user, "error": "證照名稱跟到期日都要填。"}, status_code=400
        )

    file_bytes, content_type, filename, error = await _read_optional_file(file)
    if error:
        return templates.TemplateResponse(request, "license_form.html", {"user": user, "error": error}, status_code=400)

    blob_path = ""
    if file_bytes is not None:
        try:
            blob_path = upload_file("licenses", user["username"], filename, file_bytes, content_type)
        except StorageNotConfigured:
            blob_path, filename = "", ""

    repository.create_license(
        name, issuer.strip(), license_no.strip(), expiry_date, blob_path, filename or "", user["username"], user["name"]
    )
    return RedirectResponse(url="/hr/licenses", status_code=303)


@router.get("/licenses/{license_id}")
def license_detail(license_id: str, request: Request, redirect=Depends(login_required)):
    if redirect:
        return redirect
    record = repository.get_license(license_id)
    if not record:
        return RedirectResponse(url="/hr/licenses", status_code=303)
    return templates.TemplateResponse(request, "license_detail.html", {"user": current_user(request), "record": record})


@router.post("/licenses/{license_id}/update")
async def update_license_submit(
    license_id: str,
    request: Request,
    name: str = Form(...),
    issuer: str = Form(""),
    license_no: str = Form(""),
    expiry_date: str = Form(...),
    file: UploadFile = File(None),
    redirect=Depends(admin_required),
):
    if redirect:
        return redirect
    user = current_user(request)
    file_bytes, content_type, filename, error = await _read_optional_file(file)
    blob_path = None
    if error:
        record = repository.get_license(license_id)
        return templates.TemplateResponse(
            request, "license_detail.html", {"user": user, "record": record, "error": error}, status_code=400
        )
    if file_bytes is not None:
        try:
            blob_path = upload_file("licenses", user["username"], filename, file_bytes, content_type)
        except StorageNotConfigured:
            blob_path = None

    repository.update_license(
        license_id, name.strip(), issuer.strip(), license_no.strip(), expiry_date,
        blob_path=blob_path, filename=filename if blob_path else None,
    )
    return RedirectResponse(url=f"/hr/licenses/{license_id}", status_code=303)


@router.post("/licenses/{license_id}/delete")
def delete_license_submit(license_id: str, request: Request, redirect=Depends(admin_required)):
    if redirect:
        return redirect
    repository.delete_license(license_id)
    return RedirectResponse(url="/hr/licenses", status_code=303)
