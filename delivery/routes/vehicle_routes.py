from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from delivery import group_notify, repository
from delivery.auth import admin_required, current_user, login_required
from delivery.config import (
    DEFAULT_WHEEL_TYPE,
    VEHICLE_STATUS_MAP,
    VEHICLE_STATUSES,
    VENDOR_MAP,
    VENDORS,
    WHEEL_TYPE_MAP,
    WHEEL_TYPES,
)
from delivery.templating import templates
from delivery.vehicle_filter_summary import describe_vehicle_filters
from delivery.vehicle_report import EVENT_ERROR_MESSAGES
from delivery.vehicle_status_report import build_fleet_status_report

router = APIRouter()


@router.get("/vehicles")
def vehicle_list(
    request: Request,
    vehicle_no: str = "",
    vendor: str = "",
    status: str = "",
    wheel_type: str = "",
    service_area: str = "",
    cooperation_type: str = "",
    redirect=Depends(login_required),
):
    if redirect:
        return redirect
    vehicles = repository.list_vehicles(
        vendor_filter=vendor,
        status_filter=status,
        vehicle_no_filter=vehicle_no,
        wheel_type_filter=wheel_type,
        service_area_filter=service_area,
    )
    # 騎手身份／手機號碼備援（2026-09-18／2026-09-19 新增）：車輛主檔沒有
    # 直接存合作方式，每台車都要反查一次目前使用人的人員資料才能顯示（見
    # repository.resolve_vehicle_rider_info() 的說明）；篩選也是靠這個
    # 反查出來的結果比對，不是車輛主檔本身的欄位，所以要先把全部（套用
    # 其他篩選條件後）的車輛都反查完，才能套用騎手身份篩選。手機號碼
    # 只有車輛主檔自己的 current_holder_phone 是空的時候才會用反查結果
    # 當備援顯示值，不會覆蓋車輛主檔本來就有填的電話。
    for v in vehicles:
        rider_info = repository.resolve_vehicle_rider_info(v)
        v["rider_cooperation_type"] = rider_info["cooperation_type"]
        v["rider_phone"] = v.get("current_holder_phone") or rider_info["phone"]
    if cooperation_type:
        vehicles = [
            v for v in vehicles
            if v["rider_cooperation_type"] and v["rider_cooperation_type"]["id"] == cooperation_type
        ]
    service_area_map = {a["id"]: a["name"] for a in repository.list_vehicle_service_areas(include_inactive=True)}
    cooperation_types = repository.list_cooperation_types()
    # 「找不到符合的車輛」的提示要分得出兩件事：系統裡本來就一台車都沒有
    # （第一次使用），還是有車、只是這組條件沒有符合的。後者才需要提示
    # 使用者檢查條件/清除篩選。有結果時不會多查這一趟（bool(vehicles)
    # 先短路掉）。
    has_any_vehicle = bool(vehicles) or bool(repository.list_vehicles())
    return templates.TemplateResponse(
        request,
        "vehicle_list.html",
        {
            "user": current_user(request),
            "has_any_vehicle": has_any_vehicle,
            "active_filter_descriptions": describe_vehicle_filters(
                vehicle_no=vehicle_no,
                vendor=vendor,
                status=status,
                wheel_type=wheel_type,
                service_area=service_area,
                cooperation_type=cooperation_type,
                vendor_map=VENDOR_MAP,
                vehicle_status_map=VEHICLE_STATUS_MAP,
                wheel_type_map=WHEEL_TYPE_MAP,
                service_area_map=service_area_map,
                cooperation_type_map={c["id"]: c["name"] for c in cooperation_types},
            ),
            "vendors": VENDORS,
            "vehicle_statuses": VEHICLE_STATUSES,
            "vehicle_status_map": VEHICLE_STATUS_MAP,
            "vendor_map": VENDOR_MAP,
            "wheel_types": WHEEL_TYPES,
            "wheel_type_map": WHEEL_TYPE_MAP,
            "service_areas": repository.list_vehicle_service_areas(),
            "service_area_map": service_area_map,
            "cooperation_types": cooperation_types,
            "vehicles": vehicles,
            "filter_vehicle_no": vehicle_no,
            "filter_vendor": vendor,
            "filter_status": status,
            "filter_wheel_type": wheel_type,
            "filter_service_area": service_area,
            "filter_cooperation_type": cooperation_type,
        },
    )


@router.get("/vehicles/status-report")
def vehicle_status_report_page(request: Request, redirect=Depends(login_required)):
    """「一鍵整理車輛狀況」：把全部車輛（不套用清單頁上的篩選條件，永遠是
    全部車輛）依廠商/服務區域彙整成文字報告，方便同仁複製貼到 LINE 群組。
    這個路由要註冊在 `/vehicles/{vehicle_no}` 之前，不然 "status-report"
    會被當成車號吃掉，永遠進不到這支函式。"""
    if redirect:
        return redirect
    service_areas = repository.list_vehicle_service_areas(include_inactive=True)
    report_text = build_fleet_status_report(repository.list_vehicles(), service_areas)
    return templates.TemplateResponse(
        request,
        "vehicle_status_report.html",
        {"user": current_user(request), "report_text": report_text},
    )


@router.get("/vehicles/service-areas")
def service_areas_page(request: Request, redirect=Depends(admin_required)):
    """服務區域管理，限管理員——比照裝備借還管理的品項/放置點管理，這個
    路由要註冊在 `/vehicles/{vehicle_no}` 之前，不然 "service-areas" 會被
    當成車號吃掉。"""
    if redirect:
        return redirect
    areas = repository.list_vehicle_service_areas(include_inactive=True)
    for area in areas:
        area["has_history"] = repository.vehicle_service_area_has_history(area["id"])
    return templates.TemplateResponse(
        request, "vehicle_service_areas.html", {"user": current_user(request), "areas": areas, "error": ""}
    )


@router.post("/vehicles/service-areas/new")
def create_service_area(request: Request, name: str = Form(...), redirect=Depends(admin_required)):
    if redirect:
        return redirect
    name = name.strip()
    if name:
        repository.create_vehicle_service_area(name, created_by=current_user(request)["username"])
    return RedirectResponse(url="/delivery/vehicles/service-areas", status_code=303)


@router.post("/vehicles/service-areas/{area_id}/active")
def toggle_service_area_active(
    area_id: str, request: Request, active: str = Form(...), redirect=Depends(admin_required)
):
    if redirect:
        return redirect
    repository.set_vehicle_service_area_active(area_id, active == "1")
    return RedirectResponse(url="/delivery/vehicles/service-areas", status_code=303)


@router.post("/vehicles/service-areas/{area_id}/delete")
def delete_service_area(area_id: str, request: Request, redirect=Depends(admin_required)):
    if redirect:
        return redirect
    repository.delete_vehicle_service_area(area_id)
    return RedirectResponse(url="/delivery/vehicles/service-areas", status_code=303)


@router.get("/vehicles/new")
def new_vehicle_form(request: Request, redirect=Depends(login_required)):
    if redirect:
        return redirect
    return templates.TemplateResponse(
        request,
        "vehicle_form.html",
        {
            "user": current_user(request),
            "vendors": VENDORS,
            "wheel_types": WHEEL_TYPES,
            "default_wheel_type": DEFAULT_WHEEL_TYPE,
            "service_areas": repository.list_vehicle_service_areas(),
            "error": "",
        },
    )


@router.post("/vehicles/new")
def create_vehicle_submit(
    request: Request,
    vehicle_no: str = Form(...),
    vendor: str = Form(...),
    wheel_type: str = Form(DEFAULT_WHEEL_TYPE),
    service_area: str = Form(...),
    site: str = Form(""),
    redirect=Depends(login_required),
):
    if redirect:
        return redirect
    vehicle_no = vehicle_no.strip()
    user = current_user(request)

    error = ""
    if not vehicle_no or vendor not in VENDOR_MAP or wheel_type not in WHEEL_TYPE_MAP:
        error = "車號、廠商都要填。"
    elif not repository.get_vehicle_service_area(service_area):
        error = "服務區域請重新選擇。"
    elif not repository.create_vehicle(
        vehicle_no, vendor, user["username"], wheel_type=wheel_type, service_area=service_area, site=site
    ):
        error = "這個車號已經存在，請確認後再新增。"

    if error:
        return templates.TemplateResponse(
            request,
            "vehicle_form.html",
            {
                "user": user,
                "vendors": VENDORS,
                "wheel_types": WHEEL_TYPES,
                "default_wheel_type": wheel_type or DEFAULT_WHEEL_TYPE,
                "service_areas": repository.list_vehicle_service_areas(),
                "error": error,
            },
            status_code=400,
        )

    return RedirectResponse(url=f"/delivery/vehicles/{vehicle_no}", status_code=303)


@router.get("/vehicles/personnel-lookup")
def vehicle_personnel_lookup(request: Request, vendor: str = "", name: str = ""):
    """登記領/還車表單「姓名」欄位自動帶出電話／騎手身份用的 AJAX 端點
    （2026-09-19 新增）——回傳 JSON，不是完整頁面。做法比照合約產生器
    甲方查詢（client_contract_routes.client_contract_company_lookup）：
    沒登入一律回傳查無資料，不透露任何錯誤細節。用「姓名+廠商」查在職
    人員（repository.find_personnel_by_name_vendor()，跟騎手身份反查
    共用同一個既有函式），查不到就回傳 found=False，交給同仁自己手動
    填，不擋住送出。

    **路由順序注意**：這個路徑要放在 `/vehicles/{vehicle_no}` 之前
    宣告，不然 FastAPI 會先比對到那個萬用路徑，把 "personnel-lookup"
    當成車號吃掉，這個端點永遠不會被呼叫到。"""
    if not current_user(request):
        return {"found": False}
    vendor = vendor.strip()
    name = name.strip()
    if not vendor or not name:
        return {"found": False}
    person = repository.find_personnel_by_name_vendor(vendor, name)
    if not person:
        return {"found": False}
    cooperation_type = repository.get_cooperation_type(person.get("cooperation_type") or "")
    return {
        "found": True,
        "phone": person.get("phone") or "",
        "cooperation_type_name": cooperation_type["name"] if cooperation_type else "",
    }


@router.get("/vehicles/{vehicle_no}")
def vehicle_detail(vehicle_no: str, request: Request, error: str = "", redirect=Depends(login_required)):
    if redirect:
        return redirect
    vehicle = repository.get_vehicle(vehicle_no)
    if not vehicle:
        return RedirectResponse(url="/delivery/vehicles", status_code=303)
    service_area_map = {a["id"]: a["name"] for a in repository.list_vehicle_service_areas(include_inactive=True)}
    # 騎手身份／手機號碼備援（2026-09-19）：清單頁 2026-09-18 就有騎手
    # 身份的反查邏輯，詳細頁一直沒有補上；同一次反查順便把手機號碼也
    # 拿出來，車輛主檔自己的 current_holder_phone 是空的時候當備援顯示
    # 值（不覆蓋車輛主檔本身的欄位，見 repository.resolve_vehicle_rider_
    # info() 的說明）。
    rider_info = repository.resolve_vehicle_rider_info(vehicle)
    rider_phone = vehicle.get("current_holder_phone") or rider_info["phone"]
    return templates.TemplateResponse(
        request,
        "vehicle_detail.html",
        {
            "user": current_user(request),
            "vehicle": vehicle,
            "rider_cooperation_type": rider_info["cooperation_type"],
            "rider_phone": rider_phone,
            "vendor_name": VENDOR_MAP.get(vehicle.get("vendor"), vehicle.get("vendor")),
            "vehicle_status_map": VEHICLE_STATUS_MAP,
            "vendors": VENDORS,
            "wheel_types": WHEEL_TYPES,
            "wheel_type_map": WHEEL_TYPE_MAP,
            "service_areas": repository.list_vehicle_service_areas(),
            "service_area_map": service_area_map,
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


@router.post("/vehicles/{vehicle_no}/vendor")
def update_vehicle_vendor(
    vehicle_no: str, request: Request, vendor: str = Form(...), redirect=Depends(login_required)
):
    if redirect:
        return redirect
    repository.set_vehicle_vendor(vehicle_no, vendor)
    return RedirectResponse(url=f"/delivery/vehicles/{vehicle_no}", status_code=303)


@router.post("/vehicles/{vehicle_no}/wheel-type")
def update_vehicle_wheel_type(
    vehicle_no: str, request: Request, wheel_type: str = Form(...), redirect=Depends(login_required)
):
    if redirect:
        return redirect
    repository.set_vehicle_wheel_type(vehicle_no, wheel_type)
    return RedirectResponse(url=f"/delivery/vehicles/{vehicle_no}", status_code=303)


@router.post("/vehicles/{vehicle_no}/service-area")
def update_vehicle_service_area(
    vehicle_no: str, request: Request, service_area: str = Form(""), redirect=Depends(login_required)
):
    if redirect:
        return redirect
    repository.set_vehicle_service_area(vehicle_no, service_area)
    return RedirectResponse(url=f"/delivery/vehicles/{vehicle_no}", status_code=303)


@router.post("/vehicles/{vehicle_no}/site")
def update_vehicle_site(vehicle_no: str, request: Request, site: str = Form(""), redirect=Depends(login_required)):
    if redirect:
        return redirect
    repository.set_vehicle_site(vehicle_no, site)
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
    phone: str = Form(""),
    note: str = Form(""),
    needs_maintenance: str = Form(""),
    redirect=Depends(login_required),
):
    """網頁手動補登一筆領車/還車事件，跟 LINE 群組回報共用同一套驗證邏輯
    （repository.record_vehicle_event），套用同一組「擋下」規則，避免網頁跟
    LINE 兩條路徑各自有各自的例外狀況。成功補登後額外推播一則通知到配送組
    作業群組（見 delivery/group_notify.py），讓同仁不用另外登入系統查，
    跟 LINE 群組回報的體驗一致；推播失敗不影響這筆補登本身是否成功。

    needs_maintenance 是表單下拉選單送出的值（"1" 代表勾選待維修，空字串
    代表沒勾），不是真的 checkbox，是為了避免 HTML checkbox「沒勾就不會送出
    這個欄位」的行為讓後端收不到值。"""
    if redirect:
        return redirect
    user = current_user(request)
    is_maintenance = needs_maintenance == "1"
    ok, error = repository.record_vehicle_event(
        vehicle_no=vehicle_no,
        vendor=vendor,
        personnel_name=personnel_name,
        event_type=event_type,
        event_date=event_date,
        location=location,
        source="manual",
        reported_by=user["username"],
        phone=phone,
        note=note,
        needs_maintenance=is_maintenance,
    )
    if ok:
        action_name = "領車" if event_type == "checkout" else "還車"
        text = f"📝［網站新增］✅ 已登記{action_name}：車號 {vehicle_no}，{personnel_name}，{event_date}，{location}"
        if phone:
            text += f"，電話 {phone}"
        if note:
            text += f"，備註：{note}"
        if is_maintenance:
            text += "\n🔧 已同步標記這台車為「待維修」狀態。"
        group_notify.notify_group(text)
    redirect_url = f"/delivery/vehicles/{vehicle_no}"
    if not ok:
        redirect_url += f"?error={error}"
    return RedirectResponse(url=redirect_url, status_code=303)


def _get_vehicle_and_own_event(vehicle_no: str, event_id: str):
    """共用查找：確認這台車存在、且這筆事件確實屬於這台車，避免用別台車的
    事件 ID 硬湊網址就能編輯到別台車的歷史紀錄。找不到任何一項回傳
    (None, None)。"""
    vehicle = repository.get_vehicle(vehicle_no)
    if not vehicle:
        return None, None
    event = repository.get_vehicle_event(event_id)
    if not event or event.get("vehicle_no") != vehicle.get("vehicle_no"):
        return vehicle, None
    return vehicle, event


@router.get("/vehicles/{vehicle_no}/events/{event_id}/edit")
def edit_vehicle_event_form(vehicle_no: str, event_id: str, request: Request, redirect=Depends(login_required)):
    if redirect:
        return redirect
    vehicle, event = _get_vehicle_and_own_event(vehicle_no, event_id)
    if not vehicle or not event:
        return RedirectResponse(url=f"/delivery/vehicles/{vehicle_no}", status_code=303)
    return templates.TemplateResponse(
        request,
        "vehicle_event_edit.html",
        {
            "user": current_user(request),
            "vehicle": vehicle,
            "event": event,
            "vendors": VENDORS,
            "error": "",
        },
    )


@router.post("/vehicles/{vehicle_no}/events/{event_id}/edit")
def edit_vehicle_event_submit(
    vehicle_no: str,
    event_id: str,
    request: Request,
    vendor: str = Form(...),
    personnel_name: str = Form(...),
    event_type: str = Form(...),
    event_date: str = Form(...),
    location: str = Form(...),
    phone: str = Form(""),
    note: str = Form(""),
    needs_maintenance: str = Form(""),
    redirect=Depends(login_required),
):
    """修正一筆既有的領還紀錄（例如日期、地點打錯）。不套用
    vehicle_event_error 那套「目前車輛狀態合不合理」的檢查——那是給
    新增事件用的，用來判斷這台車現在能不能再被領/還；編輯的是已經發生過
    的歷史紀錄，只要欄位都有填、事件類型合法即可，見
    repository.update_vehicle_event() 的說明。needs_maintenance 跟
    manual_vehicle_event() 一樣是下拉選單的 "1"/空字串，不是 checkbox。"""
    if redirect:
        return redirect
    vehicle, event = _get_vehicle_and_own_event(vehicle_no, event_id)
    if not vehicle or not event:
        return RedirectResponse(url=f"/delivery/vehicles/{vehicle_no}", status_code=303)

    vendor = vendor.strip()
    personnel_name = personnel_name.strip()
    location = location.strip()
    phone = phone.strip()
    note = note.strip()
    is_maintenance = needs_maintenance == "1"

    error = ""
    if not vendor or vendor not in VENDOR_MAP:
        error = "廠商要填。"
    elif not personnel_name or not event_date or not location:
        error = "姓名、日期、地點都要填。"
    elif event_type not in ("checkout", "return"):
        error = "事件類型要是領車或還車。"

    if not error:
        repository.update_vehicle_event(
            event_id=event_id,
            vendor=vendor,
            personnel_name=personnel_name,
            event_type=event_type,
            event_date=event_date,
            location=location,
            phone=phone,
            note=note,
            needs_maintenance=is_maintenance,
        )
        return RedirectResponse(url=f"/delivery/vehicles/{vehicle_no}", status_code=303)

    return templates.TemplateResponse(
        request,
        "vehicle_event_edit.html",
        {
            "user": current_user(request),
            "vehicle": vehicle,
            "event": {
                "id": event_id,
                "vendor": vendor,
                "personnel_name": personnel_name,
                "event_type": event_type,
                "event_date": event_date,
                "location": location,
                "phone": phone,
                "note": note,
                "needs_maintenance": is_maintenance,
            },
            "vendors": VENDORS,
            "error": error,
        },
        status_code=400,
    )


@router.post("/vehicles/{vehicle_no}/events/{event_id}/delete")
def delete_vehicle_event(vehicle_no: str, event_id: str, request: Request, redirect=Depends(admin_required)):
    """刪除一筆領還車歷史紀錄，只開放管理員（比照補款/假別/意外事件的
    刪除權限層級）。見 repository.delete_vehicle_event() 的說明：刪除
    不會連動改車輛主檔目前狀態，如果刪的剛好是最新一筆事件，需要的話
    請自行到上面用既有功能修正。"""
    if redirect:
        return redirect
    vehicle, event = _get_vehicle_and_own_event(vehicle_no, event_id)
    if vehicle and event:
        repository.delete_vehicle_event(event_id)
    return RedirectResponse(url=f"/delivery/vehicles/{vehicle_no}", status_code=303)
