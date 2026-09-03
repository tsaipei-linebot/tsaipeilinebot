from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse

from delivery import repository
from delivery.auth import current_user, login_required
from delivery.config import (
    ALLOWED_UPLOAD_CONTENT_TYPES,
    CLIENT_MAP,
    CLIENTS,
    COOPERATION_TYPE_MAP,
    COOPERATION_TYPES,
    DOC_TYPE_MAP,
    MAX_UPLOAD_BYTES,
    VENDOR_MAP,
)
from delivery.ocr import extract_expiry_date
from delivery.storage import StorageNotConfigured, is_configured, upload_file
from delivery.templating import templates
from delivery.validators import is_valid_taiwan_id

router = APIRouter()


@router.get("/vendor/{vendor_code}")
def vendor_list(
    vendor_code: str,
    request: Request,
    name: str = "",
    phone: str = "",
    redirect=Depends(login_required),
):
    if redirect:
        return redirect
    if vendor_code not in VENDOR_MAP:
        return RedirectResponse(url="/delivery/", status_code=303)

    name_keyword = (name or "").strip()
    phone_keyword = (phone or "").strip()

    rows = []
    for p in repository.list_personnel_by_vendor(vendor_code):
        missing = repository.missing_documents(p)
        if repository.personnel_matches_filters(p, missing, name_keyword, phone_keyword):
            rows.append({"person": p, "missing": missing})

    return templates.TemplateResponse(
        request,
        "vendor_list.html",
        {
            "user": current_user(request),
            "vendor_code": vendor_code,
            "vendor_name": VENDOR_MAP[vendor_code],
            "rows": rows,
            "filter_name": name,
            "filter_phone": phone,
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
            "cooperation_types": COOPERATION_TYPES,
            "clients": CLIENTS,
        },
    )


@router.post("/vendor/{vendor_code}/new")
def create_personnel_submit(
    vendor_code: str,
    request: Request,
    name: str = Form(...),
    id_number: str = Form(""),
    phone: str = Form(""),
    cooperation_type: str = Form(""),
    client: str = Form(""),
    redirect=Depends(login_required),
):
    if redirect:
        return redirect
    if vendor_code not in VENDOR_MAP:
        return RedirectResponse(url="/delivery/", status_code=303)
    if cooperation_type not in COOPERATION_TYPE_MAP:
        cooperation_type = ""
    if client not in CLIENT_MAP:
        client = ""
    user = current_user(request)
    personnel_id = repository.create_personnel(
        name, id_number, phone, vendor_code, user["username"], cooperation_type=cooperation_type, client=client
    )
    return RedirectResponse(url=f"/delivery/personnel/{personnel_id}", status_code=303)


@router.get("/personnel/{personnel_id}")
def personnel_detail(personnel_id: str, request: Request, error: str = "", redirect=Depends(login_required)):
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
            "cooperation_types": COOPERATION_TYPES,
            "cooperation_type_name": COOPERATION_TYPE_MAP.get(person.get("cooperation_type"), ""),
            "clients": CLIENTS,
            "client_name": CLIENT_MAP.get(person.get("client"), ""),
            "doc_statuses": repository.all_document_statuses(person),
            "storage_configured": is_configured(),
            "error": error,
        },
    )


@router.post("/personnel/{personnel_id}/cooperation-type")
def update_cooperation_type(
    personnel_id: str,
    cooperation_type: str = Form(""),
    redirect=Depends(login_required),
):
    if redirect:
        return redirect
    if cooperation_type not in COOPERATION_TYPE_MAP:
        cooperation_type = ""
    repository.update_personnel_cooperation_type(personnel_id, cooperation_type)
    return RedirectResponse(url=f"/delivery/personnel/{personnel_id}", status_code=303)


@router.post("/personnel/{personnel_id}/client")
def update_client(
    personnel_id: str,
    client: str = Form(""),
    redirect=Depends(login_required),
):
    if redirect:
        return redirect
    if client not in CLIENT_MAP:
        client = ""
    repository.update_personnel_client(personnel_id, client)
    return RedirectResponse(url=f"/delivery/personnel/{personnel_id}", status_code=303)


@router.post("/personnel/{personnel_id}/id-number")
def update_id_number(
    personnel_id: str,
    id_number: str = Form(""),
    redirect=Depends(login_required),
):
    if redirect:
        return redirect
    id_number = id_number.strip().upper()
    if id_number and not is_valid_taiwan_id(id_number):
        return RedirectResponse(url=f"/delivery/personnel/{personnel_id}?error=id_number", status_code=303)
    repository.update_personnel_id_number(personnel_id, id_number)
    return RedirectResponse(url=f"/delivery/personnel/{personnel_id}", status_code=303)


@router.post("/personnel/{personnel_id}/document/{doc_code}/checkbox")
def update_checkbox(
    personnel_id: str,
    doc_code: str,
    checked: str = Form(None),
    redirect=Depends(login_required),
):
    if redirect:
        return redirect
    doc_type = DOC_TYPE_MAP.get(doc_code)
    if not doc_type or doc_type["kind"] != "checkbox":
        return RedirectResponse(url=f"/delivery/personnel/{personnel_id}", status_code=303)
    repository.update_personnel_checkbox(personnel_id, doc_code, checked=bool(checked))
    return RedirectResponse(url=f"/delivery/personnel/{personnel_id}", status_code=303)


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
    doc_type = DOC_TYPE_MAP.get(doc_code)
    if not person or not doc_type or doc_type["kind"] not in ("file_expiry", "file"):
        return RedirectResponse(url="/delivery/", status_code=303)

    file_path = None
    resolved_expiry_date = expiry_date.strip() or None
    if file is not None and file.filename:
        content = await file.read()
        content_type = file.content_type or "application/octet-stream"
        if len(content) <= MAX_UPLOAD_BYTES and content_type in ALLOWED_UPLOAD_CONTENT_TYPES:
            try:
                file_path = upload_file("personnel-docs", personnel_id, file.filename, content, content_type)
            except StorageNotConfigured:
                file_path = None

            # 同仁沒有手動填到期日時，交給 OCR 從剛上傳的檔案辨識；辨識不出來
            # 就維持空白，之後同仁還是可以用同一個表單手動補到期日。純上傳型
            # （例如自拍照）沒有到期日這回事，不用跑 OCR。
            if file_path and not resolved_expiry_date and doc_type["kind"] == "file_expiry":
                resolved_expiry_date = extract_expiry_date(content, content_type) or None

    if doc_type["kind"] == "file":
        resolved_expiry_date = None

    repository.update_personnel_document(
        personnel_id, doc_code, file_path=file_path, expiry_date=resolved_expiry_date
    )
    return RedirectResponse(url=f"/delivery/personnel/{personnel_id}", status_code=303)
