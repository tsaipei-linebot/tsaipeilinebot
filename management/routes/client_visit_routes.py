"""客戶拜訪紀錄：有管理部權限的同仁都可以新增，但刻意只有記錄本人跟管理部
主管（user["role"] == "admin"，全平台管理員也算在內）看得到——這是業務
同仁私下的拜訪紀錄，可見範圍比公告/會議記錄這些全部門共享的功能窄很多，
所以這裡的權限檢查沒有直接用 login_required/admin_required 就結束，還要
另外用 repository.can_view_client_visit() 檢查「這一筆是不是給我看的」。
"""
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from management import repository
from management.auth import current_user, login_required
from management.templating import templates

router = APIRouter()


@router.get("/client-visits")
def client_visit_list(request: Request, redirect=Depends(login_required)):
    if redirect:
        return redirect
    user = current_user(request)
    visits = repository.list_client_visits(user["username"], user["role"] == "admin")
    return templates.TemplateResponse(request, "client_visit_list.html", {"user": user, "visits": visits})


@router.get("/client-visits/new")
def new_client_visit_form(request: Request, redirect=Depends(login_required)):
    if redirect:
        return redirect
    return templates.TemplateResponse(request, "client_visit_form.html", {"user": current_user(request), "error": ""})


@router.post("/client-visits/new")
def create_client_visit_submit(
    request: Request,
    client_name: str = Form(...),
    visit_date: str = Form(...),
    arranged_by: str = Form(...),
    visitor: str = Form(...),
    follow_up_status: str = Form(...),
    notes: str = Form(""),
    redirect=Depends(login_required),
):
    if redirect:
        return redirect
    user = current_user(request)
    client_name = client_name.strip()
    arranged_by = arranged_by.strip()
    visitor = visitor.strip()
    follow_up_status = follow_up_status.strip()
    if not client_name or not visit_date or not arranged_by or not visitor or not follow_up_status:
        return templates.TemplateResponse(
            request,
            "client_visit_form.html",
            {"user": user, "error": "客戶名稱、拜訪日期、約訪人員、拜訪人員、跟進狀態都要填。"},
            status_code=400,
        )
    repository.create_client_visit(
        client_name, visit_date, arranged_by, visitor, follow_up_status, notes.strip(),
        user["username"], user["name"],
    )
    return RedirectResponse(url="/management/client-visits", status_code=303)


@router.get("/client-visits/{visit_id}")
def client_visit_detail(visit_id: str, request: Request, redirect=Depends(login_required)):
    if redirect:
        return redirect
    user = current_user(request)
    visit = repository.get_client_visit(visit_id)
    if not visit or not repository.can_view_client_visit(visit, user["username"], user["role"] == "admin"):
        return RedirectResponse(url="/management/client-visits", status_code=303)
    return templates.TemplateResponse(request, "client_visit_detail.html", {"user": user, "visit": visit})


@router.post("/client-visits/{visit_id}/delete")
def delete_client_visit_submit(visit_id: str, request: Request, redirect=Depends(login_required)):
    if redirect:
        return redirect
    user = current_user(request)
    visit = repository.get_client_visit(visit_id)
    if not visit or not repository.can_view_client_visit(visit, user["username"], user["role"] == "admin"):
        return RedirectResponse(url="/management/client-visits", status_code=303)
    repository.delete_client_visit(visit_id)
    return RedirectResponse(url="/management/client-visits", status_code=303)
