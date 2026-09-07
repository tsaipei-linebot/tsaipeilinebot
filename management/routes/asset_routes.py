from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from management import repository
from management.auth import admin_required, current_user, login_required
from management.config import ASSET_CATEGORIES, ASSET_CATEGORY_MAP, ASSET_STATUS_MAP, ASSET_STATUSES, DEFAULT_ASSET_STATUS
from management.templating import templates

router = APIRouter()


@router.get("/assets")
def asset_list(request: Request, category: str = "", status: str = "", name: str = "", redirect=Depends(login_required)):
    if redirect:
        return redirect
    return templates.TemplateResponse(
        request,
        "asset_list.html",
        {
            "user": current_user(request),
            "assets": repository.list_assets(category_filter=category, status_filter=status, name_filter=name),
            "categories": ASSET_CATEGORIES,
            "statuses": ASSET_STATUSES,
            "category_map": ASSET_CATEGORY_MAP,
            "status_map": ASSET_STATUS_MAP,
            "filter_category": category,
            "filter_status": status,
            "filter_name": name,
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


def _clean_sim_payment_day(category: str, raw_value: str) -> str:
    """只有分類是「門號」才會採用送出的繳費日，其他分類一律強制清空——
    避免表單被竄改或前端 JS 沒生效時，把不相干的值存進其他分類的資產。
    值不是 1~31 的整數（含空白、非數字）一律當成沒填。"""
    if category != "sim":
        return ""
    value = (raw_value or "").strip()
    try:
        day = int(value)
    except ValueError:
        return ""
    return str(day) if 1 <= day <= 31 else ""


@router.post("/assets/new")
def create_asset_submit(
    request: Request,
    category: str = Form(...),
    name: str = Form(...),
    assigned_to: str = Form(""),
    status: str = Form(DEFAULT_ASSET_STATUS),
    notes: str = Form(""),
    sim_payment_day: str = Form(""),
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
    repository.create_asset(
        category,
        name,
        assigned_to.strip(),
        status,
        notes.strip(),
        user["username"],
        user["name"],
        sim_payment_day=_clean_sim_payment_day(category, sim_payment_day),
    )
    return RedirectResponse(url="/management/assets", status_code=303)


@router.get("/assets/{asset_id}")
def asset_detail(asset_id: str, request: Request, redirect=Depends(login_required)):
    if redirect:
        return redirect
    asset = repository.get_asset(asset_id)
    if not asset:
        return RedirectResponse(url="/management/assets", status_code=303)
    return templates.TemplateResponse(
        request,
        "asset_detail.html",
        {
            "user": current_user(request),
            "asset": asset,
            "category_map": ASSET_CATEGORY_MAP,
            "status_map": ASSET_STATUS_MAP,
            "statuses": ASSET_STATUSES,
            "events": repository.list_asset_events(asset_id),
            "today": date.today().isoformat(),
        },
    )


@router.post("/assets/{asset_id}/update")
def update_asset_submit(
    asset_id: str,
    request: Request,
    status: str = Form(...),
    assigned_to: str = Form(""),
    event_date: str = Form(...),
    note: str = Form(""),
    redirect=Depends(admin_required),
):
    if redirect:
        return redirect
    user = current_user(request)
    repository.record_asset_event(
        asset_id, status, assigned_to.strip(), event_date, note.strip(), user["username"], user["name"]
    )
    return RedirectResponse(url=f"/management/assets/{asset_id}", status_code=303)


@router.post("/assets/{asset_id}/sim-payment-day")
def update_sim_payment_day_submit(
    asset_id: str,
    request: Request,
    sim_payment_day: str = Form(""),
    redirect=Depends(admin_required),
):
    if redirect:
        return redirect
    asset = repository.get_asset(asset_id)
    if asset and asset.get("category") == "sim":
        repository.update_asset_sim_payment_day(asset_id, _clean_sim_payment_day("sim", sim_payment_day))
    return RedirectResponse(url=f"/management/assets/{asset_id}", status_code=303)


@router.post("/assets/{asset_id}/delete")
def delete_asset_submit(asset_id: str, request: Request, redirect=Depends(admin_required)):
    if redirect:
        return redirect
    repository.delete_asset(asset_id)
    return RedirectResponse(url="/management/assets", status_code=303)
