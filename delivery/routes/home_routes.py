from fastapi import APIRouter, Depends, Request

from delivery import repository
from delivery.auth import current_user, login_required
from delivery.config import VENDORS
from delivery.templating import templates
from hr import insurance_repository as insurance_repo

router = APIRouter()


@router.get("/")
def home(request: Request, redirect=Depends(login_required)):
    if redirect:
        return redirect
    user = current_user(request)
    open_incident_count = len(repository.list_open_incident_events())
    return templates.TemplateResponse(
        request,
        "home.html",
        {
            "user": user,
            "vendors": VENDORS,
            "open_incident_count": open_incident_count,
            "show_insurance_panel": insurance_repo.can_upload(user),
        },
    )


@router.get("/help")
def help_page(request: Request, redirect=Depends(login_required)):
    """配送部系統使用說明（2026-09-18 新增）。走跟 home() 一樣的
    login_required（配送部模組權限），跟 /portal 卡片顯不顯示「使用說明」
    按鈕是同一組權限判斷（見 portal_routes.py 的說明）——不會有「按鈕沒有
    但網址還是看得到內容」的落差。內容是我自己維護的靜態頁面（不是
    Firestore 動態資料），之後配送部系統每上線一個新功能，我會同步更新
    這個頁面，跟維護 HANDOFF.md 是同一個習慣。"""
    if redirect:
        return redirect
    return templates.TemplateResponse(request, "help.html", {"user": current_user(request)})
