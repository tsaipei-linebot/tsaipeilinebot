from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from management import repository
from management.auth import admin_required, current_user, login_required
from management.templating import templates

router = APIRouter()


@router.get("/meetings")
def meeting_list(request: Request, department: str = "", redirect=Depends(login_required)):
    if redirect:
        return redirect
    return templates.TemplateResponse(
        request,
        "meeting_list.html",
        {
            "user": current_user(request),
            "meetings": repository.list_meeting_notes(department_filter=department),
            "filter_department": department,
        },
    )


@router.get("/meetings/new")
def new_meeting_form(request: Request, redirect=Depends(admin_required)):
    if redirect:
        return redirect
    return templates.TemplateResponse(request, "meeting_form.html", {"user": current_user(request), "error": ""})


@router.post("/meetings/new")
def create_meeting_submit(
    request: Request,
    title: str = Form(...),
    meeting_date: str = Form(...),
    department: str = Form(...),
    content: str = Form(...),
    redirect=Depends(admin_required),
):
    if redirect:
        return redirect
    title = title.strip()
    department = department.strip()
    content = content.strip()
    if not title or not meeting_date or not department or not content:
        return templates.TemplateResponse(
            request,
            "meeting_form.html",
            {"user": current_user(request), "error": "標題、日期、部門、內容都要填。"},
            status_code=400,
        )
    user = current_user(request)
    repository.create_meeting_note(title, meeting_date, department, content, user["username"], user["name"])
    return RedirectResponse(url="/management/meetings", status_code=303)


@router.get("/meetings/{note_id}")
def meeting_detail(note_id: str, request: Request, redirect=Depends(login_required)):
    if redirect:
        return redirect
    meeting = repository.get_meeting_note(note_id)
    if not meeting:
        return RedirectResponse(url="/management/meetings", status_code=303)
    return templates.TemplateResponse(request, "meeting_detail.html", {"user": current_user(request), "meeting": meeting})


@router.post("/meetings/{note_id}/delete")
def delete_meeting_submit(note_id: str, request: Request, redirect=Depends(admin_required)):
    if redirect:
        return redirect
    repository.delete_meeting_note(note_id)
    return RedirectResponse(url="/management/meetings", status_code=303)
