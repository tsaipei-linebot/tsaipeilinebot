"""外送員接單媒合（即時接單／報班媒合，2026-09-19 新增）後台管理頁面：
門市當日可承接量、報班時段、騎士名單（啟用/停用）。

跟現有「服務區域管理」「裝備品項/放置點管理」同一種權限分工：一般會填寫
表單/查看清單的功能開放給任何有配送部模組權限的同仁（login_required），
「騎士名單管理」牽涉能不能使用這兩項功能，性質上更接近服務區域管理／
核銷這種限主管操作的項目，改用 admin_required。
"""
from datetime import datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from config import TAIPEI_TZ
from delivery import rider_repository
from delivery.auth import admin_required, current_user, login_required
from delivery.templating import templates

router = APIRouter()


def _today_str() -> str:
    return datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d")


# ==========================================
# 地點主檔（即時接單、報班媒合各自獨立一份，不共用）
# ==========================================
@router.get("/rider/locations")
def rider_locations_page(request: Request, redirect=Depends(login_required)):
    if redirect:
        return redirect
    locations = rider_repository.list_order_locations(include_inactive=True)
    return templates.TemplateResponse(
        request, "rider_locations.html", {"user": current_user(request), "locations": locations}
    )


@router.post("/rider/locations/new")
def create_rider_location(
    request: Request, name: str = Form(...), lat: str = Form(...), lng: str = Form(...), redirect=Depends(login_required)
):
    if redirect:
        return redirect
    name = name.strip()
    try:
        lat_value, lng_value = float(lat), float(lng)
        if name:
            account = current_user(request)
            rider_repository.create_order_location(name, lat_value, lng_value, account["username"])
    except ValueError:
        pass
    return RedirectResponse(url="/delivery/rider/locations", status_code=303)


@router.post("/rider/locations/{location_id}/active")
def update_rider_location_active(location_id: str, active: str = Form(...), redirect=Depends(login_required)):
    if redirect:
        return redirect
    rider_repository.set_order_location_active(location_id, active == "1")
    return RedirectResponse(url="/delivery/rider/locations", status_code=303)


@router.get("/rider/shift-locations")
def rider_shift_locations_page(request: Request, redirect=Depends(login_required)):
    if redirect:
        return redirect
    locations = rider_repository.list_shift_locations(include_inactive=True)
    return templates.TemplateResponse(
        request, "rider_shift_locations.html", {"user": current_user(request), "locations": locations}
    )


@router.post("/rider/shift-locations/new")
def create_rider_shift_location(
    request: Request, name: str = Form(...), lat: str = Form(...), lng: str = Form(...), redirect=Depends(login_required)
):
    if redirect:
        return redirect
    name = name.strip()
    try:
        lat_value, lng_value = float(lat), float(lng)
        if name:
            account = current_user(request)
            rider_repository.create_shift_location(name, lat_value, lng_value, account["username"])
    except ValueError:
        pass
    return RedirectResponse(url="/delivery/rider/shift-locations", status_code=303)


@router.post("/rider/shift-locations/{location_id}/active")
def update_rider_shift_location_active(location_id: str, active: str = Form(...), redirect=Depends(login_required)):
    if redirect:
        return redirect
    rider_repository.set_shift_location_active(location_id, active == "1")
    return RedirectResponse(url="/delivery/rider/shift-locations", status_code=303)


# ==========================================
# 門市當日可承接量
# ==========================================
@router.get("/rider/store-deliveries")
def rider_store_deliveries_page(request: Request, date: str = "", error: str = "", redirect=Depends(login_required)):
    if redirect:
        return redirect
    date_filter = date or _today_str()
    items = rider_repository.list_store_deliveries(date_filter)
    return templates.TemplateResponse(
        request,
        "rider_store_deliveries.html",
        {
            "user": current_user(request),
            "items": items,
            "filter_date": date_filter,
            "today": _today_str(),
            "locations": rider_repository.list_order_locations(),
            "error": error,
        },
    )


@router.post("/rider/store-deliveries/new")
def create_rider_store_delivery(
    request: Request,
    location_id: str = Form(...),
    date: str = Form(...),
    total_quantity: str = Form(...),
    redirect=Depends(login_required),
):
    if redirect:
        return redirect
    location = rider_repository.get_order_location(location_id)
    if not location or not location.get("active", True):
        return RedirectResponse(
            url=f"/delivery/rider/store-deliveries?date={date}&error=請從清單選擇一個地點，找不到您輸入的地點",
            status_code=303,
        )
    try:
        quantity_value = int(total_quantity)
    except ValueError:
        return RedirectResponse(
            url=f"/delivery/rider/store-deliveries?date={date}&error=可承接量請輸入正確的數字", status_code=303
        )
    if quantity_value <= 0:
        return RedirectResponse(
            url=f"/delivery/rider/store-deliveries?date={date}&error=可承接量要大於 0", status_code=303
        )
    account = current_user(request)
    rider_repository.create_store_delivery(
        location["name"], location["lat"], location["lng"], date, quantity_value, account["username"]
    )
    return RedirectResponse(url=f"/delivery/rider/store-deliveries?date={date}", status_code=303)


@router.post("/rider/store-deliveries/{store_id}/quantity")
def update_rider_store_delivery_quantity(
    store_id: str, request: Request, total_quantity: str = Form(...), date: str = Form(""), redirect=Depends(login_required)
):
    if redirect:
        return redirect
    account = current_user(request)
    try:
        quantity_value = int(total_quantity)
        rider_repository.update_store_delivery_quantity(store_id, quantity_value, account["username"])
    except ValueError:
        pass
    return RedirectResponse(url=f"/delivery/rider/store-deliveries?date={date}", status_code=303)


@router.post("/rider/store-deliveries/{store_id}/status")
def update_rider_store_delivery_status(
    store_id: str, request: Request, status: str = Form(...), date: str = Form(""), redirect=Depends(login_required)
):
    if redirect:
        return redirect
    account = current_user(request)
    rider_repository.set_store_delivery_status(store_id, status, account["username"])
    return RedirectResponse(url=f"/delivery/rider/store-deliveries?date={date}", status_code=303)


@router.get("/rider/store-deliveries/{store_id}/claims")
def rider_store_delivery_claims_page(store_id: str, request: Request, redirect=Depends(login_required)):
    if redirect:
        return redirect
    store = rider_repository.get_store_delivery(store_id)
    claims = rider_repository.list_claims(store_id)
    for claim in claims:
        claim["claimed_at_display"] = (
            datetime.fromtimestamp(claim["claimed_at"]).strftime("%Y-%m-%d %H:%M") if claim.get("claimed_at") else "-"
        )
    return templates.TemplateResponse(
        request,
        "rider_store_delivery_claims.html",
        {"user": current_user(request), "store": store, "claims": claims},
    )


# ==========================================
# 報班媒合時段
# ==========================================
@router.get("/rider/shifts")
def rider_shifts_page(request: Request, error: str = "", redirect=Depends(login_required)):
    if redirect:
        return redirect
    items = rider_repository.list_shift_postings()
    for item in items:
        item["start_time_display"] = datetime.fromtimestamp(item["start_time"]).strftime("%Y-%m-%d %H:%M") if item.get("start_time") else "-"
        item["end_time_display"] = datetime.fromtimestamp(item["end_time"]).strftime("%H:%M") if item.get("end_time") else "-"
    return templates.TemplateResponse(
        request,
        "rider_shifts.html",
        {"user": current_user(request), "items": items, "locations": rider_repository.list_shift_locations(), "error": error},
    )


@router.post("/rider/shifts/new")
def create_rider_shift(
    request: Request,
    location_id: str = Form(...),
    start_time: str = Form(...),
    end_time: str = Form(...),
    capacity: str = Form(...),
    redirect=Depends(login_required),
):
    if redirect:
        return redirect
    location = rider_repository.get_shift_location(location_id)
    if not location or not location.get("active", True):
        return RedirectResponse(url="/delivery/rider/shifts?error=請從清單選擇一個地點，找不到您輸入的地點", status_code=303)
    try:
        start_at = TAIPEI_TZ.localize(datetime.strptime(start_time, "%Y-%m-%dT%H:%M")).timestamp()
        end_at = TAIPEI_TZ.localize(datetime.strptime(end_time, "%Y-%m-%dT%H:%M")).timestamp()
        capacity_value = int(capacity)
    except ValueError:
        return RedirectResponse(url="/delivery/rider/shifts?error=請確認時間格式跟需求人數都正確", status_code=303)
    if capacity_value <= 0 or end_at <= start_at:
        return RedirectResponse(
            url="/delivery/rider/shifts?error=需求人數要大於 0，結束時間要晚於開始時間", status_code=303
        )
    account = current_user(request)
    rider_repository.create_shift_posting(account["username"], location["name"], start_at, end_at, capacity_value)
    return RedirectResponse(url="/delivery/rider/shifts", status_code=303)


@router.post("/rider/shifts/{shift_id}/status")
def update_rider_shift_status(shift_id: str, status: str = Form(...), redirect=Depends(login_required)):
    if redirect:
        return redirect
    rider_repository.set_shift_posting_status(shift_id, status)
    return RedirectResponse(url="/delivery/rider/shifts", status_code=303)


@router.get("/rider/shifts/{shift_id}/registrations")
def rider_shift_registrations_page(shift_id: str, request: Request, redirect=Depends(login_required)):
    if redirect:
        return redirect
    shift = rider_repository.get_shift_posting(shift_id)
    registrations = rider_repository.list_registrations(shift_id)
    for registration in registrations:
        registration["registered_at_display"] = (
            datetime.fromtimestamp(registration["registered_at"]).strftime("%Y-%m-%d %H:%M")
            if registration.get("registered_at")
            else "-"
        )
    return templates.TemplateResponse(
        request,
        "rider_shift_registrations.html",
        {"user": current_user(request), "shift": shift, "registrations": registrations},
    )


# ==========================================
# 騎士名單管理（啟用/停用）——限管理員
# ==========================================
@router.get("/rider/riders")
def rider_riders_page(request: Request, redirect=Depends(admin_required)):
    if redirect:
        return redirect
    riders = rider_repository.list_riders()
    for rider in riders:
        rider["feature_category"] = rider_repository.rider_feature_category(rider)
    return templates.TemplateResponse(request, "rider_riders.html", {"user": current_user(request), "riders": riders})


@router.post("/rider/riders/{user_id}/status")
def update_rider_status(user_id: str, status: str = Form(...), redirect=Depends(admin_required)):
    if redirect:
        return redirect
    rider_repository.set_rider_status(user_id, status)
    return RedirectResponse(url="/delivery/rider/riders", status_code=303)
