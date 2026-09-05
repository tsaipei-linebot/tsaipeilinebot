from fastapi import APIRouter, Depends, Request

from management.auth import current_user, login_required
from management.templating import templates

router = APIRouter()


@router.get("/")
def home(request: Request, redirect=Depends(login_required)):
    if redirect:
        return redirect
    return templates.TemplateResponse(request, "home.html", {"user": current_user(request)})
