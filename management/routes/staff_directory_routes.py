from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from management import repository
from management.auth import admin_required, current_user, login_required
from management.templating import templates

router = APIRouter()


@router.get("/staff-directory")
def staff_directory_list(request: Request, redirect=Depends(login_required)):
    if redirect:
        return redirect
    return templates.TemplateResponse(
        request, "staff_directory_list.html", {"user": current_user(request), "staff": repository.list_staff_members()}
    )


@router.get("/staff-directory/org-chart")
def org_chart(request: Request, redirect=Depends(login_required)):
    if redirect:
        return redirect
    groups = repository.group_staff_by_department(repository.list_staff_members())
    return templates.TemplateResponse(request, "org_chart.html", {"user": current_user(request), "groups": groups})


@router.get("/staff-directory/new")
def new_staff_member_form(request: Request, redirect=Depends(admin_required)):
    if redirect:
        return redirect
    return templates.TemplateResponse(request, "staff_directory_form.html", {"user": current_user(request), "error": ""})


@router.post("/staff-directory/new")
def create_staff_member_submit(
    request: Request,
    department: str = Form(...),
    name: str = Form(...),
    title: str = Form(...),
    redirect=Depends(admin_required),
):
    if redirect:
        return redirect
    department = department.strip()
    name = name.strip()
    title = title.strip()
    if not department or not name or not title:
        return templates.TemplateResponse(
            request,
            "staff_directory_form.html",
            {"user": current_user(request), "error": "部門、姓名、職稱都要填。"},
            status_code=400,
        )
    user = current_user(request)
    repository.create_staff_member(department, name, title, user["username"], user["name"])
    return RedirectResponse(url="/management/staff-directory", status_code=303)


@router.post("/staff-directory/{staff_id}/delete")
def delete_staff_member_submit(staff_id: str, request: Request, redirect=Depends(admin_required)):
    if redirect:
        return redirect
    repository.delete_staff_member(staff_id)
    return RedirectResponse(url="/management/staff-directory", status_code=303)
