from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse

from management import repository
from management.auth import admin_required, current_user, login_required
from management.config import ALLOWED_UPLOAD_CONTENT_TYPES, DOCUMENT_CATEGORIES, DOCUMENT_CATEGORY_MAP, MAX_UPLOAD_BYTES
from management.storage import StorageNotConfigured, upload_file
from management.templating import templates

router = APIRouter()


@router.get("/documents")
def document_list(request: Request, category: str = "", redirect=Depends(login_required)):
    if redirect:
        return redirect
    return templates.TemplateResponse(
        request,
        "document_list.html",
        {
            "user": current_user(request),
            "documents": repository.list_documents(category_filter=category),
            "categories": DOCUMENT_CATEGORIES,
            "category_map": DOCUMENT_CATEGORY_MAP,
            "filter_category": category,
        },
    )


@router.get("/documents/new")
def new_document_form(request: Request, redirect=Depends(admin_required)):
    if redirect:
        return redirect
    return templates.TemplateResponse(
        request, "document_form.html", {"user": current_user(request), "categories": DOCUMENT_CATEGORIES, "error": ""}
    )


@router.post("/documents/new")
async def create_document_submit(
    request: Request,
    title: str = Form(...),
    category: str = Form(...),
    description: str = Form(""),
    file: UploadFile = File(...),
    redirect=Depends(admin_required),
):
    if redirect:
        return redirect
    user = current_user(request)
    title = title.strip()
    if category not in DOCUMENT_CATEGORY_MAP:
        category = "other"

    error = ""
    if not title or not file.filename:
        error = "標題跟檔案都要提供。"
    else:
        content = await file.read()
        content_type = file.content_type or "application/octet-stream"
        if len(content) > MAX_UPLOAD_BYTES:
            error = "檔案超過 20MB 上限"
        elif content_type not in ALLOWED_UPLOAD_CONTENT_TYPES:
            error = "檔案格式不支援，請上傳 PDF/Word/Excel/圖片"

    if error:
        return templates.TemplateResponse(
            request,
            "document_form.html",
            {"user": user, "categories": DOCUMENT_CATEGORIES, "error": error},
            status_code=400,
        )

    try:
        blob_path = upload_file("documents", user["username"], file.filename, content, content_type)
    except StorageNotConfigured:
        return templates.TemplateResponse(
            request,
            "document_form.html",
            {"user": user, "categories": DOCUMENT_CATEGORIES, "error": "檔案儲存空間尚未設定，請聯絡系統管理員。"},
            status_code=400,
        )

    repository.create_document(title, category, description.strip(), blob_path, file.filename, user["username"], user["name"])
    return RedirectResponse(url="/management/documents", status_code=303)


@router.get("/documents/{document_id}")
def document_detail(document_id: str, request: Request, redirect=Depends(login_required)):
    if redirect:
        return redirect
    document = repository.get_document(document_id)
    if not document:
        return RedirectResponse(url="/management/documents", status_code=303)
    return templates.TemplateResponse(
        request,
        "document_detail.html",
        {"user": current_user(request), "document": document, "category_map": DOCUMENT_CATEGORY_MAP},
    )


@router.post("/documents/{document_id}/delete")
def delete_document_submit(document_id: str, request: Request, redirect=Depends(admin_required)):
    if redirect:
        return redirect
    repository.delete_document(document_id)
    return RedirectResponse(url="/management/documents", status_code=303)
