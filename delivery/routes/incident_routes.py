from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from delivery import repository
from delivery.auth import admin_required, current_user, login_required
from delivery.config import (
    DUTY_STATUSES,
    IDENTITY_TYPES,
    INCIDENT_STATUS_MAP,
    INCIDENT_STATUSES,
    RISK_LEVELS,
    VENDOR_MAP,
    VENDORS,
    YES_NO_VALUES,
)
from delivery.templating import templates

router = APIRouter()

_INCIDENT_REQUIRED_FIELDS = (
    "vendor",
    "identity_type",
    "personnel_name",
    "occurred_at",
    "location",
    "duty_status",
    "police_called",
    "injury",
    "family_contacted",
    "third_party_involved",
    "description",
)


@router.get("/incidents")
def incident_list(
    request: Request,
    personnel_name: str = "",
    vendor: str = "",
    status: str = "",
    risk_level: str = "",
    redirect=Depends(login_required),
):
    if redirect:
        return redirect
    incidents = repository.list_incident_events(
        vendor_filter=vendor,
        status_filter=status,
        risk_level_filter=risk_level,
        personnel_name_filter=personnel_name,
    )
    return templates.TemplateResponse(
        request,
        "incident_list.html",
        {
            "user": current_user(request),
            "vendors": VENDORS,
            "vendor_map": VENDOR_MAP,
            "incident_statuses": INCIDENT_STATUSES,
            "incident_status_map": INCIDENT_STATUS_MAP,
            "risk_levels": RISK_LEVELS,
            "incidents": incidents,
            "filter_personnel_name": personnel_name,
            "filter_vendor": vendor,
            "filter_status": status,
            "filter_risk_level": risk_level,
        },
    )


@router.get("/incidents/{incident_id}")
def incident_detail(incident_id: str, request: Request, redirect=Depends(login_required)):
    if redirect:
        return redirect
    incident = repository.get_incident_event(incident_id)
    if not incident:
        return RedirectResponse(url="/delivery/incidents", status_code=303)
    return templates.TemplateResponse(
        request,
        "incident_detail.html",
        {
            "user": current_user(request),
            "incident": incident,
            "vendor_name": VENDOR_MAP.get(incident.get("vendor"), incident.get("vendor")),
            "incident_status_map": INCIDENT_STATUS_MAP,
            "risk_levels": RISK_LEVELS,
        },
    )


@router.post("/incidents/{incident_id}/risk-level")
def set_incident_risk_level(
    incident_id: str, request: Request, risk_level: str = Form(...), redirect=Depends(admin_required)
):
    """設定風險等級只開放管理員操作（比照補款/假別核准機制）。"""
    if redirect:
        return redirect
    repository.set_incident_risk_level(incident_id, risk_level)
    return RedirectResponse(url=f"/delivery/incidents/{incident_id}", status_code=303)


@router.post("/incidents/{incident_id}/close")
def close_incident(incident_id: str, request: Request, redirect=Depends(admin_required)):
    """結案是單向操作，只開放管理員，沒有重新打開的路徑（比照補款/假別
    核准機制）。"""
    if redirect:
        return redirect
    repository.close_incident_event(incident_id)
    return RedirectResponse(url=f"/delivery/incidents/{incident_id}", status_code=303)


@router.get("/incidents/{incident_id}/edit")
def edit_incident_form(incident_id: str, request: Request, redirect=Depends(admin_required)):
    """修正原始回報內容（例如地點打錯字、經過描述要補充）只開放管理員，
    比照風險等級／結案的權限層級——這份是正式的意外事件記錄，跟車輛
    歷史紀錄（任何登入的同仁都能編輯自己補登的領還紀錄）性質不同。"""
    if redirect:
        return redirect
    incident = repository.get_incident_event(incident_id)
    if not incident:
        return RedirectResponse(url="/delivery/incidents", status_code=303)
    return templates.TemplateResponse(
        request,
        "incident_edit.html",
        {
            "user": current_user(request),
            "incident": incident,
            "vendors": VENDORS,
            "identity_types": IDENTITY_TYPES,
            "duty_statuses": DUTY_STATUSES,
            "yes_no_values": YES_NO_VALUES,
            "error": "",
        },
    )


@router.post("/incidents/{incident_id}/edit")
def edit_incident_submit(
    incident_id: str,
    request: Request,
    vendor: str = Form(...),
    identity_type: str = Form(...),
    personnel_name: str = Form(...),
    occurred_at: str = Form(...),
    location: str = Form(...),
    duty_status: str = Form(...),
    police_called: str = Form(...),
    injury: str = Form(...),
    family_contacted: str = Form(...),
    third_party_involved: str = Form(...),
    description: str = Form(...),
    redirect=Depends(admin_required),
):
    if redirect:
        return redirect
    incident = repository.get_incident_event(incident_id)
    if not incident:
        return RedirectResponse(url="/delivery/incidents", status_code=303)

    data = {
        "vendor": vendor,
        "identity_type": identity_type,
        "personnel_name": personnel_name.strip(),
        "occurred_at": occurred_at.strip(),
        "location": location.strip(),
        "duty_status": duty_status,
        "police_called": police_called,
        "injury": injury.strip(),
        "family_contacted": family_contacted,
        "third_party_involved": third_party_involved,
        "description": description.strip(),
    }

    error = ""
    if vendor not in VENDOR_MAP:
        error = "廠商看不懂，請重新選擇。"
    elif identity_type not in IDENTITY_TYPES:
        error = "身分類別請選「雇傭」或「承攬」。"
    elif duty_status not in DUTY_STATUSES:
        error = "執行勤務中/上下班途中請重新選擇。"
    elif police_called not in YES_NO_VALUES or family_contacted not in YES_NO_VALUES or third_party_involved not in YES_NO_VALUES:
        error = "是否報警／是否聯繫家屬／是否牽扯他人請選「有」或「無」。"
    elif any(not data[key] for key in _INCIDENT_REQUIRED_FIELDS):
        error = "欄位都要填。"

    if not error:
        repository.update_incident_event(incident_id, data)
        return RedirectResponse(url=f"/delivery/incidents/{incident_id}", status_code=303)

    return templates.TemplateResponse(
        request,
        "incident_edit.html",
        {
            "user": current_user(request),
            "incident": {"id": incident_id, **data},
            "vendors": VENDORS,
            "identity_types": IDENTITY_TYPES,
            "duty_statuses": DUTY_STATUSES,
            "yes_no_values": YES_NO_VALUES,
            "error": error,
        },
        status_code=400,
    )
