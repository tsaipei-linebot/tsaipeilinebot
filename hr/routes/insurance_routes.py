"""每日加退保彙總（2026-09-22 新增，/hr/insurance）。權限判斷不是走
hr.auth 的 admin/staff 兩層，是比對帳號的 `department` 字串（見
`hr/insurance_repository.py` 開頭的說明）——所以每個路由都先過
`login_required`（要有 hr 模組權限），再另外手動判斷這個帳號是「7 個
上傳部門之一」還是「人資」，不是這兩者就導回 /hr/，跟
`hr/routes/license_routes.py` 等既有路由「拿到 redirect 就先擋」的寫法
一致。
"""
import datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse, Response

from file_type_sniff import is_allowed_upload
from hr import insurance_repository as repo
from hr.auth import current_user, login_required
from hr.config import ALLOWED_UPLOAD_CONTENT_TYPES, INSURANCE_UPLOAD_DEPARTMENTS, MAX_UPLOAD_BYTES
from hr.insurance_excel import build_summary_workbook
from hr.storage import StorageNotConfigured, upload_file
from hr.templating import templates

router = APIRouter()


def _today() -> str:
    return datetime.date.today().isoformat()


def _access_redirect(user: dict):
    if not repo.has_insurance_access(user):
        return RedirectResponse(url="/hr/", status_code=303)
    return None


async def _read_upload_file(file: UploadFile):
    if file is None or not file.filename:
        return None, None, None, "請選擇要上傳的檔案。"
    content = await file.read()
    content_type = file.content_type or "application/octet-stream"
    if len(content) > MAX_UPLOAD_BYTES:
        return None, None, None, "檔案超過 20MB 上限。"
    if not is_allowed_upload(content, content_type, ALLOWED_UPLOAD_CONTENT_TYPES):
        return None, None, None, "檔案格式不支援，請上傳 Excel 檔（.xlsx/.xls）。"
    return content, content_type, file.filename, ""


@router.get("/insurance")
def insurance_home(request: Request, redirect=Depends(login_required)):
    if redirect:
        return redirect
    user = current_user(request)
    access_redirect = _access_redirect(user)
    if access_redirect:
        return access_redirect
    if repo.is_collector(user):
        return RedirectResponse(url="/hr/insurance/summary", status_code=303)
    return RedirectResponse(url="/hr/insurance/upload", status_code=303)


@router.get("/insurance/upload")
def upload_page(request: Request, work_date: str = "", redirect=Depends(login_required)):
    if redirect:
        return redirect
    user = current_user(request)
    if not repo.can_upload(user):
        return RedirectResponse(url="/hr/", status_code=303)
    department = user.get("department") or ""
    work_date = work_date or _today()
    return templates.TemplateResponse(
        request,
        "insurance_upload.html",
        {
            "user": user,
            "department": department,
            "work_date": work_date,
            "closed": repo.is_day_closed(work_date),
            "existing": repo.get_upload(department, work_date),
            "error": "",
        },
    )


@router.post("/insurance/upload")
async def upload_submit(
    request: Request,
    work_date: str = Form(...),
    file: UploadFile = File(None),
    redirect=Depends(login_required),
):
    if redirect:
        return redirect
    user = current_user(request)
    if not repo.can_upload(user):
        return RedirectResponse(url="/hr/", status_code=303)
    department = user.get("department") or ""

    def _render_error(message: str):
        return templates.TemplateResponse(
            request,
            "insurance_upload.html",
            {
                "user": user,
                "department": department,
                "work_date": work_date,
                "closed": repo.is_day_closed(work_date),
                "existing": repo.get_upload(department, work_date),
                "error": message,
            },
            status_code=400,
        )

    if not work_date:
        return _render_error("請選擇日期。")
    if not repo.can_upload_for_date(user, work_date):
        return _render_error("這一天已經收單，沒辦法再上傳，如果真的需要補傳請聯絡人資。")

    content, content_type, filename, error = await _read_upload_file(file)
    if error:
        return _render_error(error)

    try:
        blob_path = upload_file("insurance", f"{work_date}_{department}", filename, content, content_type)
    except StorageNotConfigured:
        return _render_error("檔案儲存空間尚未設定，請聯絡工程師。")

    repo.save_upload(department, work_date, blob_path, filename, user["username"], user["name"])
    return RedirectResponse(url=f"/hr/insurance/upload?work_date={work_date}", status_code=303)


@router.get("/insurance/history")
def history_page(request: Request, department: str = "", start_date: str = "", end_date: str = "", redirect=Depends(login_required)):
    if redirect:
        return redirect
    user = current_user(request)
    access_redirect = _access_redirect(user)
    if access_redirect:
        return access_redirect

    if repo.is_collector(user):
        records = repo.list_all_history(start_date, end_date)
        if department:
            records = [r for r in records if r.get("department") == department]
        department_options = INSURANCE_UPLOAD_DEPARTMENTS
    else:
        records = repo.list_department_history(user.get("department") or "")
        if start_date:
            records = [r for r in records if r.get("work_date", "") >= start_date]
        if end_date:
            records = [r for r in records if r.get("work_date", "") <= end_date]
        department_options = []

    return templates.TemplateResponse(
        request,
        "insurance_history.html",
        {
            "user": user,
            "records": records,
            "is_collector": repo.is_collector(user),
            "department_options": department_options,
            "filter_department": department,
            "filter_start_date": start_date,
            "filter_end_date": end_date,
        },
    )


@router.get("/insurance/summary")
def summary_page(request: Request, work_date: str = "", redirect=Depends(login_required)):
    if redirect:
        return redirect
    user = current_user(request)
    if not repo.is_collector(user):
        return RedirectResponse(url="/hr/", status_code=303)
    work_date = work_date or _today()
    return templates.TemplateResponse(
        request,
        "insurance_summary.html",
        {
            "user": user,
            "work_date": work_date,
            "closed": repo.is_day_closed(work_date),
            "rows": repo.summary_for_date(work_date),
        },
    )


@router.post("/insurance/summary/close")
def close_day_submit(request: Request, work_date: str = Form(...), redirect=Depends(login_required)):
    if redirect:
        return redirect
    user = current_user(request)
    if not repo.is_collector(user):
        return RedirectResponse(url="/hr/", status_code=303)
    repo.close_day(work_date, user["username"], user["name"])
    return RedirectResponse(url=f"/hr/insurance/summary?work_date={work_date}", status_code=303)


@router.get("/insurance/download")
def download_page(request: Request, redirect=Depends(login_required)):
    if redirect:
        return redirect
    user = current_user(request)
    if not repo.is_collector(user):
        return RedirectResponse(url="/hr/", status_code=303)
    today = _today()
    return templates.TemplateResponse(
        request,
        "insurance_download.html",
        {"user": user, "start_date": today, "end_date": today, "error": ""},
    )


@router.post("/insurance/download")
def download_submit(request: Request, start_date: str = Form(...), end_date: str = Form(...), redirect=Depends(login_required)):
    if redirect:
        return redirect
    user = current_user(request)
    if not repo.is_collector(user):
        return RedirectResponse(url="/hr/", status_code=303)

    if not start_date or not end_date or start_date > end_date:
        return templates.TemplateResponse(
            request,
            "insurance_download.html",
            {"user": user, "start_date": start_date, "end_date": end_date, "error": "請確認日期區間，起始日期不能晚於結束日期。"},
            status_code=400,
        )

    uploads = repo.list_all_history(start_date, end_date)
    workbook_bytes = build_summary_workbook(uploads)
    filename = f"全區域加退保紀錄表_{start_date}_{end_date}.xlsx"
    return Response(
        content=workbook_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )
