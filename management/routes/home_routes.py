from fastapi import APIRouter, Depends, Request

from management.auth import current_user, login_required
from management.templating import templates

router = APIRouter()


@router.get("/")
def home(request: Request, redirect=Depends(login_required)):
    if redirect:
        return redirect
    return templates.TemplateResponse(request, "home.html", {"user": current_user(request)})


@router.get("/help")
def help_page(request: Request, redirect=Depends(login_required)):
    """管理部系統使用說明（2026-09-18 新增）。跟 home() 一樣用
    login_required，跟 /portal 卡片顯不顯示「使用說明」按鈕是同一組
    權限判斷（見 portal_routes.py 的說明）。"""
    if redirect:
        return redirect
    return templates.TemplateResponse(request, "help.html", {"user": current_user(request)})
