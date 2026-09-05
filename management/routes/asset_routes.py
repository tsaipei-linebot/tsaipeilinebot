from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from management import repository
from management.auth import admin_required, current_user, login_required
from management.config import ASSET_CATEGORIES, ASSET_CATEGORY_MAP, ASSET_STATUS_MAP, ASSET_STATUSES, DEFAULT_ASSET_STATUS
from management.templating import templates

router = APIRouter()


@router.get("/assets")
def asset_list(request: Request, category: str = "", redirect=Depends(login_required)):
    if redirect:
        return redirect
    return templates.TemplateResponse(
        request,
        "asset_list.html",
        {
            "user": current_user(request),
            "assets": repository.list_assets(category_filter=category),
            "categories": ASSET_CATEGORIES,
            "category_map": ASSET_CATEGORY_MAP,
            "status_map": ASSET_STATUS_MAP,
            "filter_category": category,
        },
    )


@router.get("/assets/new")
def new_asset_form(request: Request, redirect=Depends(admin_required)):
    if redirect:
        return redirect
    return templates.TemplateResponse(
        request,
        "asset_form.html",
        {"user": current_user(request), "categories": ASSET_CATEGORIES, "statuses": ASSET_STATUSES, "error": ""},
    )


@router.post("/assets/new")
def create_asset_submit(
    request: Request,
    category: str = Form(...),
    name: str = Form(...),
    assigned_to: str = Form(""),
    status: str = Form(DEFAULT_ASSET_STATUS),
    notes: str = Form(""),
    redirect=Depends(admin_required),
):
    if redirect:
        return redirect
    user = current_user(request)
    name = name.strip()
    if category not in ASSET_CATEGORY_MAP:
        return templates.TemplateResponse(
            request,
            "asset_form.html",
            {"user": user, "categories": ASSET_CATEGORIES, "statuses": ASSET_STATUSES, "error": "分類不正確。"},
            status_code=400,
        )
    if status not in ASSET_STATUS_MAP:
        status = DEFAULT_ASSET_STATUS
    if not name:
        return templates.TemplateResponse(
            request,
            "asset_form.html",
            {"user": user, "categories": ASSET_CATEGORIES, "statuses": ASSET_STATUSES, "error": "名稱/編號不能空白。"},
            status_code=400,
        )
    repository.create_asset(category, name, assigned_to.strip(), status, notes.strip(), user["username"], user["name"])
    return RedirectResponse(url="/management/assets", status_code=303)


@router.post("/assets/{asset_id}/delete")
def delete_asset_submit(asset_id: str, request: Request, redirect=Depends(admin_required)):
    if redirect:
        return redirect
    repository.delete_asset(asset_id)
    return RedirectResponse(url="/management/assets", status_code=303)
