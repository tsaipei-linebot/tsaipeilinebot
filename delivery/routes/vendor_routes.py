from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from delivery import repository
from delivery.auth import current_user, login_required
from delivery.config import (
    ALLOWED_UPLOAD_CONTENT_TYPES,
    CLIENT_MAP,
    CLIENT_VENDORS,
    CLIENTS,
    COOPERATION_TYPE_MAP,
    COOPERATION_TYPE_VENDORS,
    COOPERATION_TYPES,
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
            "show_cooperation_type": vendor_code in COOPERATION_TYPE_VENDORS,
            "show_client": vendor_code in CLIENT_VENDORS,
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
    vendor_code = person.get("vendor")
    return templates.TemplateResponse(
        request,
        "personnel_detail.html",
        {
            "user": current_user(request),
            "person": person,
            "vendor_name": VENDOR_MAP.get(vendor_code, vendor_code),
            "cooperation_types": COOPERATION_TYPES,
            "clients": CLIENTS,
            "show_cooperation_type": vendor_code in COOPERATION_TYPE_VENDORS,
            "show_client": vendor_code in CLIENT_VENDORS,
            "doc_statuses": repository.all_document_statuses(person),
            "storage_configured": is_configured(),
            "error": error,
        },
    )


@router.post("/personnel/{personnel_id}/bulk-update")
async def bulk_update_personnel(personnel_id: str, request: Request, redirect=Depends(login_required)):
    """人員詳細頁改成一個大表單，所有應備項目（合作方式/負責客戶、身分證、
    email、勾選類、上傳類）一次送出、一鍵更新，取代原本每一列各自一個小
    表單、要分開送出很多次的做法。"""
    if redirect:
        return redirect
    person = repository.get_personnel(personnel_id)
    if not person:
        return RedirectResponse(url="/delivery/", status_code=303)

    form = await request.form()

    if "cooperation_type" in form:
        cooperation_type = form.get("cooperation_type", "")
        repository.update_personnel_cooperation_type(
            personnel_id, cooperation_type if cooperation_type in COOPERATION_TYPE_MAP else ""
        )

    if "client" in form:
        client = form.get("client", "")
        repository.update_personnel_client(personnel_id, client if client in CLIENT_MAP else "")

    id_number_error = False
    if "id_number" in form:
        id_number = (form.get("id_number") or "").strip().upper()
        if id_number and not is_valid_taiwan_id(id_number):
            id_number_error = True
        else:
            repository.update_personnel_id_number(personnel_id, id_number)

    if "email" in form:
        repository.update_personnel_email(personnel_id, (form.get("email") or "").strip())

    doc_types = repository.applicable_doc_types(
        person.get("vendor"), person.get("cooperation_type"), person.get("client")
    )
    for doc_type in doc_types:
        code = doc_type["code"]
        kind = doc_type["kind"]

        if kind == "checkbox":
            repository.update_personnel_checkbox(personnel_id, code, checked=form.get(f"checked_{code}") is not None)
            continue

        if kind not in ("file", "file_expiry"):
            continue

        file = form.get(f"file_{code}")
        file_path = None
        resolved_expiry_date = None
        if kind == "file_expiry":
            resolved_expiry_date = (form.get(f"expiry_date_{code}") or "").strip() or None

        if file is not None and getattr(file, "filename", None):
            content = await file.read()
            content_type = file.content_type or "application/octet-stream"
            if len(content) <= MAX_UPLOAD_BYTES and content_type in ALLOWED_UPLOAD_CONTENT_TYPES:
                try:
                    file_path = upload_file("personnel-docs", personnel_id, file.filename, content, content_type)
                except StorageNotConfigured:
                    file_path = None
                # 沒手動填到期日時交給 OCR 辨識；辨識不出來就維持空白，之後同仁
                # 還是可以再送一次表單手動補到期日。
                if kind == "file_expiry" and file_path and not resolved_expiry_date:
                    resolved_expiry_date = extract_expiry_date(content, content_type) or None

        if file_path is not None or resolved_expiry_date is not None:
            repository.update_personnel_document(personnel_id, code, file_path=file_path, expiry_date=resolved_expiry_date)

    redirect_url = f"/delivery/personnel/{personnel_id}"
    if id_number_error:
        redirect_url += "?error=id_number"
    return RedirectResponse(url=redirect_url, status_code=303)
