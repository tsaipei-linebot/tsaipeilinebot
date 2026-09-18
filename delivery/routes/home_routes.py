from datetime import datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from delivery import repository
from delivery.auth import admin_required, current_user, login_required
from delivery.config import VENDORS
from delivery.repository import ANNOUNCEMENT_DEFAULT_DAYS
from delivery.templating import templates

router = APIRouter()


def _with_display_date(announcement: dict) -> dict:
    return {
        **announcement,
        "created_at_display": datetime.fromtimestamp(announcement.get("created_at", 0)).strftime("%Y-%m-%d"),
        "expires_at_display": datetime.fromtimestamp(announcement.get("expires_at", 0)).strftime("%Y-%m-%d"),
    }


@router.get("/")
def home(request: Request, redirect=Depends(login_required)):
    if redirect:
        return redirect
    open_incident_count = len(repository.list_open_incident_events())
    return templates.TemplateResponse(
        request,
        "home.html",
        {
            "user": current_user(request),
            "vendors": VENDORS,
            "open_incident_count": open_incident_count,
            "announcements": [_with_display_date(a) for a in repository.list_active_announcements()],
        },
    )


@router.get("/announcements")
def announcements_page(request: Request, redirect=Depends(admin_required)):
    """公告管理，限管理員（2026-09-18 新增）——比照合作方式/服務區域的
    管理頁，差別是公告沒有「有歷史紀錄不能刪除」的保護，隨時可以刪。"""
    if redirect:
        return redirect
    return templates.TemplateResponse(
        request,
        "announcements.html",
        {
            "user": current_user(request),
            "announcements": [_with_display_date(a) for a in repository.list_announcements()],
            "default_days": ANNOUNCEMENT_DEFAULT_DAYS,
            "error": "",
        },
    )


@router.post("/announcements/new")
def create_announcement_submit(
    request: Request,
    title: str = Form(...),
    content: str = Form(""),
    days: int = Form(ANNOUNCEMENT_DEFAULT_DAYS),
    redirect=Depends(admin_required),
):
    if redirect:
        return redirect
    title = title.strip()
    if title:
        repository.create_announcement(
            title, content.strip(), created_by=current_user(request)["username"], days=days if days > 0 else ANNOUNCEMENT_DEFAULT_DAYS
        )
    return RedirectResponse(url="/delivery/announcements", status_code=303)


@router.post("/announcements/{announcement_id}/active")
def toggle_announcement_active(
    announcement_id: str, request: Request, active: str = Form(...), redirect=Depends(admin_required)
):
    if redirect:
        return redirect
    repository.set_announcement_active(announcement_id, active == "1")
    return RedirectResponse(url="/delivery/announcements", status_code=303)


@router.post("/announcements/{announcement_id}/delete")
def delete_announcement_submit(announcement_id: str, request: Request, redirect=Depends(admin_required)):
    if redirect:
        return redirect
    repository.delete_announcement(announcement_id)
    return RedirectResponse(url="/delivery/announcements", status_code=303)
