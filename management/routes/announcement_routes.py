from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from management import repository
from management.auth import admin_required, current_user, login_required
from management.templating import templates

router = APIRouter()


@router.get("/announcements")
def announcement_list(request: Request, redirect=Depends(login_required)):
    if redirect:
        return redirect
    return templates.TemplateResponse(
        request,
        "announcement_list.html",
        {"user": current_user(request), "announcements": repository.list_announcements()},
    )


@router.get("/announcements/new")
def new_announcement_form(request: Request, redirect=Depends(admin_required)):
    if redirect:
        return redirect
    return templates.TemplateResponse(
        request, "announcement_form.html", {"user": current_user(request), "error": ""}
    )


@router.post("/announcements/new")
def create_announcement_submit(
    request: Request,
    title: str = Form(...),
    body: str = Form(...),
    redirect=Depends(admin_required),
):
    if redirect:
        return redirect
    title = title.strip()
    body = body.strip()
    if not title or not body:
        return templates.TemplateResponse(
            request,
            "announcement_form.html",
            {"user": current_user(request), "error": "標題跟內容都要填。"},
            status_code=400,
        )
    user = current_user(request)
    repository.create_announcement(title, body, user["username"], user["name"])
    return RedirectResponse(url="/management/announcements", status_code=303)


@router.get("/announcements/{announcement_id}")
def announcement_detail(announcement_id: str, request: Request, redirect=Depends(login_required)):
    if redirect:
        return redirect
    announcement = repository.get_announcement(announcement_id)
    if not announcement:
        return RedirectResponse(url="/management/announcements", status_code=303)
    return templates.TemplateResponse(
        request, "announcement_detail.html", {"user": current_user(request), "announcement": announcement}
    )


@router.post("/announcements/{announcement_id}/delete")
def delete_announcement_submit(announcement_id: str, request: Request, redirect=Depends(admin_required)):
    if redirect:
        return redirect
    repository.delete_announcement(announcement_id)
    return RedirectResponse(url="/management/announcements", status_code=303)
