"""職缺 AI 文案預覽比對（2026-09-26，GAS 搬家階段 3 第 1 個 PR，只有全平台管理員）。

- `/job-listings/migration`：Notion 裡非停招的職缺清單。
- `/job-listings/migration/preview/{page_id}`：左邊是 Notion 現在的內容（GAS 產的），右邊是平台用新規則重新產生的，
  加上防腦補檢查結果。**不寫回 Notion**，只是看。
- `/job-listings/migration/try`：自己填欄位試寫（調教語氣、規則時用）。

注意：舊職缺沒有保留同仁原文（GAS 送出時 AI 改寫的內容直接蓋掉原文），所以預覽是拿 Notion 的「工作內容(對外)」當原文。
"""
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

import platform_accounts
from platform_templating import templates
from services import job_copy

router = APIRouter()

_FIELDS = [("external_title", "對外標題"), ("external_desc", "對外工作內容"), ("highlight", "精華亮點"),
           ("formatted_detail", "排版工作說明")]


def _require_admin(request: Request):
    account = platform_accounts.current_account(request)
    if not account:
        return RedirectResponse(url="/login?next=/job-listings/migration", status_code=303)
    if not account.get("is_platform_admin"):
        return RedirectResponse(url="/portal", status_code=303)
    return None


@router.get("/job-listings/migration")
def job_copy_list(request: Request, redirect=Depends(_require_admin)):
    if redirect:
        return redirect
    try:
        jobs, error = job_copy.list_jobs(), ""
    except Exception as e:
        jobs, error = [], f"讀取 Notion 職缺失敗：{e}"
    return templates.TemplateResponse(request, "job_copy_list.html", {
        "user": platform_accounts.current_account(request), "jobs": jobs, "error": error})


@router.get("/job-listings/migration/preview/{page_id}")
def job_copy_preview(page_id: str, request: Request, redirect=Depends(_require_admin)):
    if redirect:
        return redirect
    job = job_copy.get_job(page_id)
    if not job:
        return RedirectResponse(url="/job-listings/migration", status_code=303)
    return _result_page(request, job, job_copy.generate(job), current=job)


@router.get("/job-listings/migration/try")
def job_copy_try_form(request: Request, redirect=Depends(_require_admin)):
    if redirect:
        return redirect
    return templates.TemplateResponse(request, "job_copy_try.html", {
        "user": platform_accounts.current_account(request), "form": {}})


@router.post("/job-listings/migration/try")
def job_copy_try(
    request: Request,
    title: str = Form(""),
    city: str = Form(""),
    district: str = Form(""),
    salary: str = Form(""),
    shift: str = Form(""),
    leave_type: str = Form(""),
    original_desc: str = Form(""),
    redirect=Depends(_require_admin),
):
    if redirect:
        return redirect
    job = {"title": title, "city": city, "district": district, "salary": salary, "shift": shift,
           "leave_type": leave_type, "original_desc": original_desc}
    return _result_page(request, job, job_copy.generate(job), current=None)


def _result_page(request: Request, job: dict, result: dict, current):
    return templates.TemplateResponse(request, "job_copy_preview.html", {
        "user": platform_accounts.current_account(request),
        "job": job,
        "current": current,
        "result": result,
        "fields": _FIELDS,
        "location": job_copy.format_smart_location(job.get("city"), job.get("district")),
    })
