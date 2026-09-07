"""意外通報清單/詳細頁。新增只能透過 LINE 群組回報（見 hr/incident_report.py
＋ management/routes/line_webhook_routes.py），網頁端沒有「新增」表單，跟
配送部意外事件（delivery/routes/incident_routes.py）的作法一致。
"""
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from hr import repository
from hr.auth import admin_required, current_user, login_required
from hr.config import INCIDENT_STATUS_MAP, INCIDENT_STATUSES, RISK_LEVELS
from hr.templating import templates

router = APIRouter()


@router.get("/incidents")
def incident_list(
    request: Request,
    personnel_name: str = "",
    status: str = "",
    risk_level: str = "",
    redirect=Depends(login_required),
):
    if redirect:
        return redirect
    incidents = repository.list_incident_events(
        status_filter=status,
        risk_level_filter=risk_level,
        personnel_name_filter=personnel_name,
    )
    return templates.TemplateResponse(
        request,
        "incident_list.html",
        {
            "user": current_user(request),
            "incident_statuses": INCIDENT_STATUSES,
            "incident_status_map": INCIDENT_STATUS_MAP,
            "risk_levels": RISK_LEVELS,
            "incidents": incidents,
            "filter_personnel_name": personnel_name,
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
        return RedirectResponse(url="/hr/incidents", status_code=303)
    return templates.TemplateResponse(
        request,
        "incident_detail.html",
        {
            "user": current_user(request),
            "incident": incident,
            "incident_status_map": INCIDENT_STATUS_MAP,
            "risk_levels": RISK_LEVELS,
        },
    )


@router.post("/incidents/{incident_id}/risk-level")
def set_incident_risk_level(
    incident_id: str, request: Request, risk_level: str = Form(...), redirect=Depends(admin_required)
):
    if redirect:
        return redirect
    repository.set_incident_risk_level(incident_id, risk_level)
    return RedirectResponse(url=f"/hr/incidents/{incident_id}", status_code=303)


@router.post("/incidents/{incident_id}/close")
def close_incident(incident_id: str, request: Request, redirect=Depends(admin_required)):
    if redirect:
        return redirect
    repository.close_incident_event(incident_id)
    return RedirectResponse(url=f"/hr/incidents/{incident_id}", status_code=303)
