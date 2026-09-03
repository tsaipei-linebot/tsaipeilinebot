from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from delivery import repository
from delivery.auth import current_user, login_required
from delivery.config import VEHICLE_STATUS_MAP, VEHICLE_STATUSES, VENDOR_MAP, VENDORS
from delivery.templating import templates
from delivery.vehicle_report import EVENT_ERROR_MESSAGES

router = APIRouter()


@router.get("/vehicles")
def vehicle_list(
    request: Request,
    vehicle_no: str = "",
    vendor: str = "",
    status: str = "",
    redirect=Depends(login_required),
):
    if redirect:
        return redirect
    vehicles = repository.list_vehicles(vendor_filter=vendor, status_filter=status, vehicle_no_filter=vehicle_no)
    return templates.TemplateResponse(
        request,
        "vehicle_list.html",
        {
            "user": current_user(request),
            "vendors": VENDORS,
            "vehicle_statuses": VEHICLE_STATUSES,
            "vehicle_status_map": VEHICLE_STATUS_MAP,
            "vendor_map": VENDOR_MAP,
            "vehicles": vehicles,
            "filter_vehicle_no": vehicle_no,
            "filter_vendor": vendor,
            "filter_status": status,
        },
    )


@router.get("/vehicles/new")
def new_vehicle_form(request: Request, redirect=Depends(login_required)):
    if redirect:
        return redirect
    return templates.TemplateResponse(
        request, "vehicle_form.html", {"user": current_user(request), "vendors": VENDORS, "error": ""}
    )


@router.post("/vehicles/new")
def create_vehicle_submit(
    request: Request,
    vehicle_no: str = Form(...),
    vendor: str = Form(...),
    redirect=Depends(login_required),
):
    if redirect:
        return redirect
    vehicle_no = vehicle_no.strip()
    user = current_user(request)

    error = ""
    if not vehicle_no or vendor not in VENDOR_MAP:
        error = "車號、廠商都要填。"
    elif not repository.create_vehicle(vehicle_no, vendor, user["username"]):
        error = "這個車號已經存在，請確認後再新增。"

    if error:
        return templates.TemplateResponse(
            request,
            "vehicle_form.html",
            {"user": user, "vendors": VENDORS, "error": error},
            status_code=400,
        )

    return RedirectResponse(url=f"/delivery/vehicles/{vehicle_no}", status_code=303)


@router.get("/vehicles/{vehicle_no}")
def vehicle_detail(vehicle_no: str, request: Request, error: str = "", redirect=Depends(login_required)):
    if redirect:
        return redirect
    vehicle = repository.get_vehicle(vehicle_no)
    if not vehicle:
        return RedirectResponse(url="/delivery/vehicles", status_code=303)
    return templates.TemplateResponse(
        request,
        "vehicle_detail.html",
        {
            "user": current_user(request),
            "vehicle": vehicle,
            "vendor_name": VENDOR_MAP.get(vehicle.get("vendor"), vehicle.get("vendor")),
            "vehicle_status_map": VEHICLE_STATUS_MAP,
            "vendors": VENDORS,
            "events": repository.list_vehicle_events(vehicle_no),
            "error": error,
            "error_message": EVENT_ERROR_MESSAGES.get(error, "這筆事件無法處理。") if error else "",
        },
    )


@router.post("/vehicles/{vehicle_no}/status")
def update_vehicle_status(
    vehicle_no: str, request: Request, status: str = Form(...), redirect=Depends(login_required)
):
    if redirect:
        return redirect
    repository.set_vehicle_status(vehicle_no, status)
    return RedirectResponse(url=f"/delivery/vehicles/{vehicle_no}", status_code=303)


@router.post("/vehicles/{vehicle_no}/manual-event")
def manual_vehicle_event(
    vehicle_no: str,
    request: Request,
    vendor: str = Form(...),
    personnel_name: str = Form(...),
    event_type: str = Form(...),
    event_date: str = Form(...),
    location: str = Form(...),
    redirect=Depends(login_required),
):
    """網頁手動補登一筆領車/還車事件，跟 LINE 群組回報共用同一套驗證邏輯
    （repository.record_vehicle_event），套用同一組「擋下」規則，避免網頁跟
    LINE 兩條路徑各自有各自的例外狀況。"""
    if redirect:
        return redirect
    user = current_user(request)
    ok, error = repository.record_vehicle_event(
        vehicle_no=vehicle_no,
        vendor=vendor,
        personnel_name=personnel_name,
        event_type=event_type,
        event_date=event_date,
        location=location,
        source="manual",
        reported_by=user["username"],
    )
    redirect_url = f"/delivery/vehicles/{vehicle_no}"
    if not ok:
        redirect_url += f"?error={error}"
    return RedirectResponse(url=redirect_url, status_code=303)
