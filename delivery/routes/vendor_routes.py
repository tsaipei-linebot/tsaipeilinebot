from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse

from delivery import repository
from delivery.auth import current_user, login_required
from delivery.config import (
    ALLOWED_UPLOAD_CONTENT_TYPES,
    DOC_TYPE_MAP,
    MAX_UPLOAD_BYTES,
    VENDOR_MAP,
)
from delivery.storage import StorageNotConfigured, is_configured, upload_file
from delivery.templating import templates

router = APIRouter()


@router.get("/vendor/{vendor_code}")
def vendor_list(vendor_code: str, request: Request, redirect=Depends(login_required)):
    if redirect:
        return redirect
    if vendor_code not in VENDOR_MAP:
        return RedirectResponse(url="/delivery/", status_code=303)
    rows = [
        {"person": p, "missing": repository.missing_documents(p.get("documents"))}
        for p in repository.list_personnel_by_vendor(vendor_code)
    ]
    return templates.TemplateResponse(
        request,
        "vendor_list.html",
        {
            "user": current_user(request),
            "vendor_code": vendor_code,
            "vendor_name": VENDOR_MAP[vendor_code],
            "rows": rows,
        },
    )


@router.get("/vendor/{vendor_code}/new")
def new_personnel_form(vendor_code: str, request: Request, redirect=Depends(login_required)):
    if redirect:
        return redirect
    if vendor_code not in VENDOR_MAP:
        return RedirectResponse(url="/delivery/", status_code=303)
    return templates.TemplateResponse(
        request,
        "personnel_form.html",
        {
            "user": current_user(request),
            "vendor_code": vendor_code,
            "vendor_name": VENDOR_MAP[vendor_code],
        },
    )


@router.post("/vendor/{vendor_code}/new")
def create_personnel_submit(
    vendor_code: str,
    request: Request,
    name: str = Form(...),
    id_number: str = Form(""),
    phone: str = Form(""),
    redirect=Depends(login_required),
):
    if redirect:
        return redirect
    if vendor_code not in VENDOR_MAP:
        return RedirectResponse(url="/delivery/", status_code=303)
    user = current_user(request)
    personnel_id = repository.create_personnel(name, id_number, phone, vendor_code, user["username"])
    return RedirectResponse(url=f"/delivery/personnel/{personnel_id}", status_code=303)


@router.get("/personnel/{personnel_id}")
def personnel_detail(personnel_id: str, request: Request, redirect=Depends(login_required)):
    if redirect:
        return redirect
    person = repository.get_personnel(personnel_id)
    if not person:
        return RedirectResponse(url="/delivery/", status_code=303)
    return templates.TemplateResponse(
        request,
        "personnel_detail.html",
        {
            "user": current_user(request),
            "person": person,
            "vendor_name": VENDOR_MAP.get(person.get("vendor"), person.get("vendor")),
            "doc_statuses": repository.all_document_statuses(person.get("documents")),
            "storage_configured": is_configured(),
        },
    )


@router.post("/personnel/{personnel_id}/document/{doc_code}")
async def upload_document(
    personnel_id: str,
    doc_code: str,
    request: Request,
    expiry_date: str = Form(""),
    file: UploadFile = File(None),
    redirect=Depends(login_required),
):
    if redirect:
        return redirect
    person = repository.get_personnel(personnel_id)
    if not person or doc_code not in DOC_TYPE_MAP:
        return RedirectResponse(url="/delivery/", status_code=303)

    file_path = None
    if file is not None and file.filename:
        content = await file.read()
        content_type = file.content_type or "application/octet-stream"
        if len(content) <= MAX_UPLOAD_BYTES and content_type in ALLOWED_UPLOAD_CONTENT_TYPES:
            try:
                file_path = upload_file("personnel-docs", personnel_id, file.filename, content, content_type)
            except StorageNotConfigured:
                file_path = None

    repository.update_personnel_document(
        personnel_id, doc_code, file_path=file_path, expiry_date=expiry_date or None
    )
    return RedirectResponse(url=f"/delivery/personnel/{personnel_id}", status_code=303)
