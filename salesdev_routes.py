"""少凱業務開發專區（/salesdev）。

2026-09-24 改版（見 HANDOFF.md「業務開發整併」）：資料來源從 Google 試算表
改成 Firestore（`salesdev/repository.py`），職缺由平台自己每天抓
（`salesdev/pipeline.py`），同地點的職缺歸成一組（`salesdev/normalize.py`）。

- `/salesdev`：三個分頁——開發名單（一組一列）、非客戶線索（派遣公司徵
  自己內部員工的職缺）、新登記工廠；上方顯示最近一次自動抓取的結果
- `/salesdev/groups/{id}`：一組的詳細頁，看組裡每一筆職缺、填反查結果、
  備註、聯絡紀錄
- `/salesdev/export.xlsx`：下載 Excel
- `/salesdev/import-sheet`：一次性匯入舊試算表（模組管理員限定）
- `/salesdev?tab=hiring`：104 產線徵才公司（2026-09-25 新增，每週自動抓，見
  `salesdev/hiring_pipeline.py`）；`/salesdev/hiring/settings` 改搜尋條件（管理員）

跟 delivery/management/hr 不同，這個模組直接掛在根 app 上、複用同一顆
登入 session cookie（比照 portal_routes.py／accounts_routes.py 的做法）。
能不能進來由 /accounts 的權限設定決定。
"""
import re
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, Response

import platform_accounts
from platform_templating import templates
from salesdev import repository
from salesdev.excel_export import SOURCE_LABELS, build_workbook
from salesdev.sheet_import import import_from_sheet

router = APIRouter()

MODULE_CODE = "salesdev"
TABS = ("leads", "internal", "hiring", "factories")
HIRING_SIZE_FILTERS = ("big", "unknown", "small", "all")

STATUS_BADGES = {
    repository.STATUS_PENDING: "badge-pending",
    repository.STATUS_SELECTED: "badge-selected",
    repository.STATUS_DONE: "badge-ok",
    repository.STATUS_NOT_FOUND: "badge-resigned",
    repository.STATUS_SKIPPED: "badge-resigned",
}


def _require_access(request: Request):
    account = platform_accounts.current_account(request)
    if not account:
        return RedirectResponse(url=f"/login?next=/{MODULE_CODE}", status_code=303)
    if not platform_accounts.has_module_access(account, MODULE_CODE):
        return RedirectResponse(url="/portal", status_code=303)
    return None


def _username(request: Request) -> str:
    account = platform_accounts.current_account(request) or {}
    return account.get("name") or account.get("username") or ""


def _is_module_admin(request: Request) -> bool:
    account = platform_accounts.current_account(request)
    return platform_accounts.module_role(account, MODULE_CODE) == platform_accounts.ROLE_ADMIN


def _redirect(url: str, msg: str = "", err: str = "") -> RedirectResponse:
    sep = "&" if "?" in url else "?"
    if msg:
        url += f"{sep}msg={quote(msg)}"
    elif err:
        url += f"{sep}err={quote(err)}"
    return RedirectResponse(url=url, status_code=303)


def _common_context(request: Request) -> dict:
    return {
        "user": platform_accounts.current_account(request),
        "msg": request.query_params.get("msg", ""),
        "err": request.query_params.get("err", ""),
        "status_badges": STATUS_BADGES,
        "source_labels": SOURCE_LABELS,
    }


@router.get("/salesdev")
def salesdev_home(
    request: Request, tab: str = "leads", status: str = "", size: str = "big", redirect=Depends(_require_access)
):
    if redirect:
        return redirect
    tab = tab if tab in TABS else "leads"
    context = _common_context(request)
    context.update(
        {
            "tab": tab,
            "status_filter": status if status in repository.REVIEW_STATUSES else "",
            "review_statuses": repository.REVIEW_STATUSES,
            "latest_run": None,
            "status_counts": {},
            "groups": [],
            "internal_jobs": [],
            "factories": [],
            "hiring_companies": [],
            "hiring_counts": {},
            "hiring_size": size if size in HIRING_SIZE_FILTERS else "big",
            "hiring_settings": repository.default_hiring_settings(),
            "latest_hiring_run": None,
            "load_error": "",
            "is_module_admin": _is_module_admin(request),
        }
    )
    try:
        context["latest_run"] = repository.latest_run()
        if tab == "leads":
            groups = repository.list_groups()
            context["status_counts"] = {
                s: sum(1 for g in groups if g.get("review_status") == s) for s in repository.REVIEW_STATUSES
            }
            if context["status_filter"]:
                groups = [g for g in groups if g.get("review_status") == context["status_filter"]]
            context["groups"] = groups
        elif tab == "internal":
            context["internal_jobs"] = repository.list_internal_jobs()
        elif tab == "hiring":
            settings = repository.get_hiring_settings()
            companies = repository.list_hiring_companies()
            buckets = [(c, repository.hiring_size_bucket(c, settings["min_employees"])) for c in companies]
            context["hiring_counts"] = {
                key: sum(1 for _, b in buckets if b == key) for key in ("big", "unknown", "small")
            }
            context["hiring_counts"]["all"] = len(companies)
            size_filter = context["hiring_size"]
            context["hiring_companies"] = [c for c, b in buckets if size_filter == "all" or b == size_filter]
            context["hiring_settings"] = settings
            context["latest_hiring_run"] = repository.latest_hiring_run()
        else:
            context["factories"] = repository.list_factories()
    except Exception as exc:
        print(f"[業務開發] 讀取資料失敗：{exc}")
        context["load_error"] = f"讀取資料時發生錯誤：{exc}"
    return templates.TemplateResponse(request, "salesdev_home.html", context)


@router.get("/salesdev/help")
def salesdev_help(request: Request, redirect=Depends(_require_access)):
    if redirect:
        return redirect
    return templates.TemplateResponse(request, "salesdev_help.html", {"user": platform_accounts.current_account(request)})


@router.post("/salesdev/select")
async def salesdev_select(request: Request, redirect=Depends(_require_access)):
    """勾選「待審查」的組送出 →「已勾選待反查」。勾選框數量不固定，所以
    直接讀 request.form() 的同名多值。"""
    if redirect:
        return redirect
    form = await request.form()
    group_ids = [g for g in form.getlist("group_ids") if g]
    if not group_ids:
        return _redirect("/salesdev", err="請至少勾選一個地點再送出。")
    count = repository.select_groups_for_lookup(group_ids, _username(request))
    return _redirect("/salesdev", msg=f"已送出 {count} 個地點，狀態改成「已勾選待反查」。")


@router.get("/salesdev/groups/{group_id}")
def salesdev_group_detail(request: Request, group_id: str, redirect=Depends(_require_access)):
    if redirect:
        return redirect
    group = repository.get_group(group_id)
    if not group:
        return _redirect("/salesdev", err="找不到這個地點，可能已經被重新整理過，請回列表重新點選。")
    context = _common_context(request)
    context.update(
        {
            "group": group,
            "jobs": repository.list_jobs_in_group(group_id),
            "review_statuses": repository.REVIEW_STATUSES,
        }
    )
    return templates.TemplateResponse(request, "salesdev_group.html", context)


@router.post("/salesdev/groups/{group_id}/review")
async def salesdev_group_review(request: Request, group_id: str, redirect=Depends(_require_access)):
    if redirect:
        return redirect
    form = await request.form()
    lookup = {field: str(form.get(field, "")) for field in repository.LOOKUP_FIELDS}
    ok = repository.update_group_review(group_id, str(form.get("review_status", "")), lookup, _username(request))
    url = f"/salesdev/groups/{group_id}"
    return _redirect(url, msg="已儲存反查結果。") if ok else _redirect(url, err="儲存失敗：狀態不正確或找不到這個地點。")


@router.post("/salesdev/groups/{group_id}/note")
async def salesdev_group_note(request: Request, group_id: str, redirect=Depends(_require_access)):
    if redirect:
        return redirect
    form = await request.form()
    ok = repository.update_group_note(group_id, str(form.get("note", "")), _username(request))
    url = f"/salesdev/groups/{group_id}"
    return _redirect(url, msg="已儲存備註。") if ok else _redirect(url, err="儲存失敗：找不到這個地點。")


@router.post("/salesdev/groups/{group_id}/contact-log")
async def salesdev_group_contact_log(request: Request, group_id: str, redirect=Depends(_require_access)):
    if redirect:
        return redirect
    form = await request.form()
    ok = repository.add_contact_log(group_id, str(form.get("text", "")), _username(request))
    url = f"/salesdev/groups/{group_id}"
    return _redirect(url, msg="已新增聯絡紀錄。") if ok else _redirect(url, err="請先輸入聯絡內容再送出。")


@router.post("/salesdev/jobs/{job_id}/internal")
async def salesdev_job_internal(request: Request, job_id: str, redirect=Depends(_require_access)):
    """人工修正「是不是派遣公司內部職缺」。value=1 移到非客戶線索、
    value=0 放回開發名單。"""
    if redirect:
        return redirect
    form = await request.form()
    internal = str(form.get("value", "")) == "1"
    back = str(form.get("back", ""))
    job = repository.get_job(job_id)
    if not job:
        return _redirect("/salesdev?tab=internal", err="找不到這筆職缺。")
    original_group = job.get("group_id", "")
    new_group = repository.set_job_internal(job_id, internal, _username(request))
    if internal:
        url = f"/salesdev/groups/{original_group}" if back == "group" and original_group else "/salesdev?tab=internal"
        return _redirect(url, msg="已移到「非客戶線索」。")
    return _redirect(f"/salesdev/groups/{new_group}" if new_group else "/salesdev", msg="已放回開發名單。")


@router.get("/salesdev/export.xlsx")
def salesdev_export(request: Request, redirect=Depends(_require_access)):
    if redirect:
        return redirect
    content = build_workbook(
        repository.list_groups(),
        repository.list_all_jobs(),
        repository.list_factories(),
        repository.list_hiring_companies(),
    )
    filename = f"業務開發名單_{repository.today_str()}.xlsx"
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@router.post("/salesdev/import-sheet")
def salesdev_import_sheet(request: Request, redirect=Depends(_require_access)):
    if redirect:
        return redirect
    if not _is_module_admin(request):
        return _redirect("/salesdev", err="只有這個專區的管理員可以匯入舊試算表資料。")
    stats = import_from_sheet(_username(request))
    if stats["error"]:
        return _redirect("/salesdev", err=stats["error"])
    return _redirect(
        "/salesdev",
        msg=(
            f"匯入完成：試算表職缺 {stats['jobs_read']} 筆（新增 {stats['new_jobs']}、已存在更新 {stats['updated_jobs']}），"
            f"歸成新地點 {stats['new_groups']} 個、非客戶線索 {stats['internal_jobs']} 筆、沿用已勾選 {stats['selected_groups']} 個；"
            f"新登記工廠 {stats['factories_read']} 筆（新增 {stats['new_factories']}）。"
        ),
    )


_KEYWORD_SPLIT_RE = re.compile(r"[\s,，、;；]+")


@router.post("/salesdev/hiring/settings")
async def salesdev_hiring_settings(request: Request, redirect=Depends(_require_access)):
    """104 產線徵才公司的搜尋條件（管理員限定）。下一次每週自動抓取才會套用。"""
    if redirect:
        return redirect
    back = "/salesdev?tab=hiring"
    if not _is_module_admin(request):
        return _redirect(back, err="只有這個專區的管理員可以修改搜尋條件。")
    form = await request.form()
    keywords = []
    for keyword in _KEYWORD_SPLIT_RE.split(str(form.get("keywords", ""))):
        if keyword and keyword not in keywords:
            keywords.append(keyword)
    try:
        min_employees = int(str(form.get("min_employees", "")).strip())
        max_pages = int(str(form.get("max_pages", "")).strip())
    except ValueError:
        return _redirect(back, err="員工人數門檻、每個關鍵字抓幾頁都要填數字。")
    if not keywords:
        return _redirect(back, err="請至少填一個搜尋關鍵字。")
    if len(keywords) > 10:
        return _redirect(back, err="關鍵字最多 10 個（每個關鍵字都要花時間搜尋，太多會來不及在時間內抓完）。")
    if min_employees < 1 or not 1 <= max_pages <= 10:
        return _redirect(back, err="員工人數門檻要大於 0；每個關鍵字抓幾頁要在 1～10 之間。")
    repository.save_hiring_settings(keywords, min_employees, max_pages, _username(request))
    return _redirect(back, msg="已儲存搜尋條件，下一次每週自動抓取會照新的條件搜尋。")
