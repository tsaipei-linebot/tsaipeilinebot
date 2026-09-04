from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from delivery import repository
from delivery.auth import admin_required, current_user, login_required
from delivery.config import (
    INCIDENT_STATUS_MAP,
    INCIDENT_STATUSES,
    RISK_LEVELS,
    VENDOR_MAP,
    VENDORS,
)
from delivery.templating import templates

router = APIRouter()


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
