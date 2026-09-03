from fastapi import APIRouter, Depends, Request

from delivery.auth import current_user, login_required
from delivery.config import VENDORS
from delivery.templating import templates

router = APIRouter()


@router.get("/")
def home(request: Request, redirect=Depends(login_required)):
    if redirect:
        return redirect
    return templates.TemplateResponse(
        request, "home.html", {"user": current_user(request), "vendors": VENDORS}
    )
