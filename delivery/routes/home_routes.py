from fastapi import APIRouter, Depends, Request

from delivery import repository
from delivery.auth import current_user, login_required
from delivery.config import VENDORS
from delivery.templating import templates

router = APIRouter()


@router.get("/")
def home(request: Request, redirect=Depends(login_required)):
    if redirect:
        return redirect
    open_incident_count = len(repository.list_open_incident_events())
    return templates.TemplateResponse(
        request,
        "home.html",
        {"user": current_user(request), "vendors": VENDORS, "open_incident_count": open_incident_count},
    )
