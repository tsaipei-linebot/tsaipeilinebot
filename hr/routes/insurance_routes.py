"""每日加退保彙總（2026-09-22 新增，/hr/insurance）。權限判斷不是走
hr.auth 的 admin/staff 兩層，是比對帳號的 `department` 字串（見
`hr/insurance_repository.py` 開頭的說明）。

**2026-09-22 跟使用者確認設計後拆成兩種登入門檻**（原本這裡所有路由都
先過 `login_required`，要求帳號勾了「人資專區」這個模組權限才能進來，
但 7 個上傳部門的同仁本來就不該需要那個模組權限——他們不需要、也不該
看到意外通報/體檢報告/員工關懷這些人資才看得到的功能）：
- **上傳／查歷史／首頁自動導向**（`_require_login`）：只要有登入即可，
  不用勾「人資專區」，靠 `repo.can_upload()`／`repo.is_collector()` 這層
  部門字串比對把關實際能看到什麼——`/portal` 現在會依帳號的部門直接給
  一張「{部門} 加退保」卡片（見 `portal_routes.py`），點進來就是走這幾
  支路由，不會先經過人資模組的登入頁。
- **彙總／收單／下載**（`login_required`，維持不變）：這幾頁是給人資
  （`INSURANCE_COLLECTOR_DEPARTMENT`）或全平台管理員收所有部門彙整資料
  用的，人資同仁本來就需要「人資專區」模組權限才能用意外通報等其他
  功能，這裡沿用同一個權限沒有額外負擔，維持原本「先過人資模組登入」
  的寫法不變。
"""
import datetime
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse, Response

import platform_accounts
from file_type_sniff import is_allowed_upload
from hr import insurance_draft_repository as drafts_repo
from hr import insurance_repository as repo
from hr.auth import current_user, login_required
from hr.config import (
    ALLOWED_UPLOAD_CONTENT_TYPES,
    INSURANCE_DRAFT_DEPARTMENTS,
    INSURANCE_UPLOAD_DEPARTMENTS,
    MAX_UPLOAD_BYTES,
)
from hr.insurance_excel import (
    build_department_workbook,
    build_draft_records_workbook,
    build_summary_workbook,
    parse_department_workbook,
)
from hr.storage import StorageNotConfigured, download_file, upload_file
from hr.templating import templates

router = APIRouter()


_TAIPEI = datetime.timezone(datetime.timedelta(hours=8))


def _today() -> str:
    """台灣時間的今天（Cloud Run 主機是 UTC，早上 8 點前用 date.today() 會變成
    前一天——2026-09-24 加暫存區「送出給人資」時一起修正）。"""
    return datetime.datetime.now(_TAIPEI).date().isoformat()


def _require_login(request: Request):
    """比照 `dispatch_routes.py`／`finance_routes.py` 的做法：只確認有
    登入，不要求任何模組權限——沒登入導回根層級的 `/login`（不是
    `/hr/login`，因為打這幾支路由的同仁不一定有「人資專區」模組權限，
    導去人資模組自己的登入頁登入完也還是會被模組權限擋下來），`next`
    帶原本要去的網址，登入完直接回到原本要看的頁面。"""
    if not platform_accounts.current_account(request):
        return RedirectResponse(url=f"/login?next={request.url.path}", status_code=303)
    return None


def _access_redirect(user: dict):
    if not repo.has_insurance_access(user):
        return RedirectResponse(url="/portal", status_code=303)
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
def insurance_home(request: Request, redirect=Depends(_require_login)):
    if redirect:
        return redirect
    user = current_user(request)
    access_redirect = _access_redirect(user)
    if access_redirect:
        return access_redirect
    if repo.is_collector(user):
        return RedirectResponse(url="/hr/insurance/summary", status_code=303)
    return RedirectResponse(url="/hr/insurance/upload", status_code=303)


@router.get("/insurance/help")
def insurance_help_page(request: Request, redirect=Depends(_require_login)):
    """2026-09-22 新增：獨立於 `/hr/help` 之外的加退保使用說明頁，給
    `_require_login()` 那組不需要人資模組權限的帳號用（7 個上傳部門的
    同仁不一定有人資模組權限，借用 `/hr/help` 會被模組權限擋下來，見
    這個檔案開頭的說明）。內容只寫部門同仁需要知道的部分（上傳/查
    歷史），人資才需要的收單/下載彙總維持只放在 `/hr/help`。"""
    if redirect:
        return redirect
    user = current_user(request)
    access_redirect = _access_redirect(user)
    if access_redirect:
        return access_redirect
    return templates.TemplateResponse(request, "insurance_help.html", {"user": user})


def _account_department(user: dict) -> str:
    """帳號部門的標準寫法（全形括號也認得），上傳、暫存區都用同一個 key，
    人資彙總頁（依 INSURANCE_UPLOAD_DEPARTMENTS 查）才找得到。"""
    return drafts_repo.canonical_department(user.get("department") or "")


def _upload_context(user: dict, department: str, work_date: str, error: str = "", msg: str = "") -> dict:
    existing = repo.get_upload(department, work_date)
    drafts_enabled = drafts_repo.department_has_drafts(department)
    return {
        "user": user,
        "department": department,
        "work_date": work_date,
        "closed": repo.is_day_closed(work_date),
        "existing": existing,
        "error": error,
        "msg": msg,
        "drafts_enabled": drafts_enabled,
        "pending": drafts_repo.list_pending(department) if drafts_enabled else [],
        "type_name": drafts_repo.draft_type_name,
        # 這一天已經從暫存區送出、還在人資那份檔案裡的筆數：再手動上傳 Excel
        # 會把它們蓋掉，畫面要提醒
        "sent_in_existing": len((existing or {}).get("draft_ids") or []) if (existing or {}).get("generated_from_drafts") else 0,
    }


@router.get("/insurance/upload")
def upload_page(request: Request, work_date: str = "", msg: str = "", err: str = "", redirect=Depends(_require_login)):
    if redirect:
        return redirect
    user = current_user(request)
    if not repo.can_upload(user):
        return RedirectResponse(url="/portal", status_code=303)
    work_date = work_date or _today()
    return templates.TemplateResponse(
        request, "insurance_upload.html", _upload_context(user, _account_department(user), work_date, err, msg)
    )


@router.post("/insurance/upload")
async def upload_submit(
    request: Request,
    work_date: str = Form(...),
    file: UploadFile = File(None),
    redirect=Depends(_require_login),
):
    if redirect:
        return redirect
    user = current_user(request)
    if not repo.can_upload(user):
        return RedirectResponse(url="/portal", status_code=303)
    department = _account_department(user)

    def _render_error(message: str):
        return templates.TemplateResponse(
            request, "insurance_upload.html", _upload_context(user, department, work_date, message), status_code=400
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
def history_page(request: Request, department: str = "", start_date: str = "", end_date: str = "", redirect=Depends(_require_login)):
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
        records = repo.list_department_history(_account_department(user))
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


# ==========================================
# 加退保暫存區（2026-09-24 新增，見 hr/insurance_draft_repository.py）
# ==========================================
def _upload_url(work_date: str = "", key: str = "", value: str = "") -> str:
    params = {k: v for k, v in (("work_date", work_date), (key, value)) if k and v}
    return "/hr/insurance/upload" + (f"?{urlencode(params)}" if params else "")


def _draft_user(request: Request):
    """暫存區的操作只給「有暫存區的部門」（INSURANCE_DRAFT_DEPARTMENTS）的同仁。
    回傳 (user, department, redirect)。"""
    user = current_user(request)
    department = _account_department(user)
    if not repo.can_upload(user) or not drafts_repo.department_has_drafts(department):
        return user, department, RedirectResponse(url="/portal", status_code=303)
    return user, department, None


def _valid_date(value) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    try:
        return datetime.date.fromisoformat(value).isoformat()
    except ValueError:
        return ""


def _draft_fields_from_form(form) -> tuple:
    """回傳 (fields, error)。姓名必填，加保/退保/追退日期至少填一個。"""
    fields = {key: (form.get(key) or "").strip() for key in drafts_repo.FIELD_HEADERS}
    for key in ("insured_date", "withdrawn_date", "recovery_date"):
        if fields[key] and not _valid_date(fields[key]):
            return fields, "日期格式不正確。"
        fields[key] = _valid_date(fields[key])
    if not fields["name"]:
        return fields, "請填姓名。"
    if not (fields["insured_date"] or fields["withdrawn_date"] or fields["recovery_date"]):
        return fields, "勞保加保日期、退保日期、追退日期至少要填一個。"
    return fields, ""


@router.post("/insurance/drafts/new")
async def drafts_new(request: Request, redirect=Depends(_require_login)):
    if redirect:
        return redirect
    user, department, deny = _draft_user(request)
    if deny:
        return deny
    form = await request.form()
    work_date = _valid_date(form.get("work_date"))
    fields, error = _draft_fields_from_form(form)
    if error:
        return RedirectResponse(url=_upload_url(work_date, "err", error), status_code=303)
    drafts_repo.add_draft(department, fields, user, kind=drafts_repo.KIND_MANUAL, note="手動新增")
    return RedirectResponse(url=_upload_url(work_date, "msg", f"已加入待送出清單：{fields['name']}"), status_code=303)


def _own_pending_draft(draft_id: str, department: str):
    draft = drafts_repo.get_draft(draft_id)
    if not draft or draft.get("department") != department:
        return None
    return draft


@router.get("/insurance/drafts/{draft_id}/edit")
def drafts_edit_page(draft_id: str, request: Request, work_date: str = "", redirect=Depends(_require_login)):
    if redirect:
        return redirect
    user, department, deny = _draft_user(request)
    if deny:
        return deny
    draft = _own_pending_draft(draft_id, department)
    if not draft or draft.get("status") != drafts_repo.STATUS_PENDING:
        return RedirectResponse(url=_upload_url(work_date, "err", "這一筆已經送出、下載或取消，不能再修改。"), status_code=303)
    return templates.TemplateResponse(
        request,
        "insurance_draft_edit.html",
        {"user": user, "draft": draft, "work_date": work_date, "error": ""},
    )


@router.post("/insurance/drafts/{draft_id}/edit")
async def drafts_edit_submit(draft_id: str, request: Request, redirect=Depends(_require_login)):
    if redirect:
        return redirect
    user, department, deny = _draft_user(request)
    if deny:
        return deny
    form = await request.form()
    work_date = _valid_date(form.get("work_date"))
    draft = _own_pending_draft(draft_id, department)
    if not draft:
        return RedirectResponse(url=_upload_url(work_date), status_code=303)
    fields, error = _draft_fields_from_form(form)
    if error:
        return templates.TemplateResponse(
            request,
            "insurance_draft_edit.html",
            {"user": user, "draft": {**draft, **fields}, "work_date": work_date, "error": error},
            status_code=400,
        )
    if not drafts_repo.update_draft(draft_id, fields, user):
        return RedirectResponse(url=_upload_url(work_date, "err", "這一筆已經送出、下載或取消，不能再修改。"), status_code=303)
    return RedirectResponse(url=_upload_url(work_date, "msg", f"已修改：{fields['name']}"), status_code=303)


@router.post("/insurance/drafts/{draft_id}/cancel")
def drafts_cancel(draft_id: str, request: Request, work_date: str = Form(""), redirect=Depends(_require_login)):
    if redirect:
        return redirect
    user, department, deny = _draft_user(request)
    if deny:
        return deny
    draft = _own_pending_draft(draft_id, department)
    if not draft or not drafts_repo.cancel_draft(draft_id, user, "手動刪除"):
        return RedirectResponse(url=_upload_url(work_date, "err", "這一筆已經送出、下載或取消。"), status_code=303)
    return RedirectResponse(url=_upload_url(work_date, "msg", f"已從待送出清單移除：{draft.get('name')}（紀錄會保留）"), status_code=303)


@router.post("/insurance/drafts/send")
def drafts_send(request: Request, work_date: str = Form(""), redirect=Depends(_require_login)):
    """送出給人資：把待送出清單組成部門範本格式的 Excel，存成「這個部門、這一天」
    的上傳檔（跟手動上傳同一個位置，人資端完全一樣）。

    同一天可以送很多次、也可能先手動上傳過 Excel——每次送出都**重新組一份
    完整的檔案**：當天手動上傳的那份 Excel 內容（`base_manual_blob_path`）＋
    這一天之前已經送出的（`draft_ids`）＋這次的待送出，不會蓋掉前面交過的。"""
    if redirect:
        return redirect
    user, department, deny = _draft_user(request)
    if deny:
        return deny
    work_date = _valid_date(work_date)
    if not work_date:
        return RedirectResponse(url=_upload_url("", "err", "請選擇日期。"), status_code=303)
    if not repo.can_upload_for_date(user, work_date):
        return RedirectResponse(
            url=_upload_url(work_date, "err", "這一天已經收單，沒辦法送出。可以按「下載待送出清單」把 Excel 交給人資處理。"),
            status_code=303,
        )
    pending = drafts_repo.list_pending(department)
    if not pending:
        return RedirectResponse(url=_upload_url(work_date, "err", "待送出清單是空的。"), status_code=303)

    existing = repo.get_upload(department, work_date) or {}
    if existing.get("generated_from_drafts"):
        base_blob = existing.get("base_manual_blob_path") or ""
        previous_ids = list(existing.get("draft_ids") or [])
    else:
        base_blob = existing.get("blob_path") or ""
        previous_ids = []

    rows = []
    if base_blob:
        content, _ = download_file(base_blob)
        if content is None:
            return RedirectResponse(url=_upload_url(work_date, "err", "讀不到這一天手動上傳的 Excel，請聯絡工程師。"), status_code=303)
        try:
            rows.extend(parse_department_workbook(content))
        except Exception:
            return RedirectResponse(
                url=_upload_url(work_date, "err", "這一天手動上傳的 Excel 讀不出來（格式跟範本不同），沒有送出，請聯絡人資或工程師。"),
                status_code=303,
            )
    previous = drafts_repo.get_drafts(previous_ids)
    rows.extend(drafts_repo.draft_to_source_row(d) for d in previous + pending)

    filename = f"{work_date}_{department}_加退保.xlsx"
    try:
        blob_path = upload_file(
            "insurance",
            f"{work_date}_{department}",
            filename,
            build_department_workbook(rows),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    except StorageNotConfigured:
        return RedirectResponse(url=_upload_url(work_date, "err", "檔案儲存空間尚未設定，請聯絡工程師。"), status_code=303)

    repo.save_upload(
        department, work_date, blob_path, filename, user["username"], user["name"],
        generated_from_drafts=True,
        draft_ids=[d["id"] for d in previous] + [d["id"] for d in pending],
        base_manual_blob_path=base_blob,
    )
    drafts_repo.mark_sent(pending, work_date, user)
    return RedirectResponse(url=_upload_url(work_date, "msg", f"已送出 {len(pending)} 筆給人資（{work_date}）。"), status_code=303)


def _xlsx_response(content: bytes, filename: str) -> Response:
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@router.post("/insurance/drafts/download")
def drafts_download(request: Request, work_date: str = Form(""), redirect=Depends(_require_login)):
    """收單前來不及送出：把待送出清單下載成範本格式的 Excel，同仁自己交給人資。
    下載後那幾筆改成「已下載」（紀錄保留），不會再出現在待送出清單、也不會
    之後又被送出一次。"""
    if redirect:
        return redirect
    user, department, deny = _draft_user(request)
    if deny:
        return deny
    pending = drafts_repo.list_pending(department)
    if not pending:
        return RedirectResponse(url=_upload_url(_valid_date(work_date), "err", "待送出清單是空的。"), status_code=303)
    content = build_department_workbook([drafts_repo.draft_to_source_row(d) for d in pending])
    drafts_repo.mark_downloaded(pending, user)
    return _xlsx_response(content, f"{_today()}_{department}_加退保（未送出）.xlsx")


def _taipei_date(timestamp) -> str:
    return drafts_repo.format_time(timestamp)[:10]


@router.get("/insurance/drafts/records")
def drafts_records(
    request: Request,
    department: str = "",
    start_date: str = "",
    end_date: str = "",
    name: str = "",
    status: str = "",
    export: str = "",
    redirect=Depends(_require_login),
):
    """加退保操作紀錄：暫存區每一筆（含已取消）都查得到，依建立日期、姓名、
    狀態篩選；人資看得到全部部門。`export=1` 下載成 Excel（只是查紀錄，不會
    改變任何一筆的狀態）。"""
    if redirect:
        return redirect
    user = current_user(request)
    own_department = _account_department(user)
    is_collector = repo.is_collector(user)
    if not is_collector and not (repo.can_upload(user) and drafts_repo.department_has_drafts(own_department)):
        return RedirectResponse(url="/portal", status_code=303)

    start_date, end_date = _valid_date(start_date), _valid_date(end_date)
    name = name.strip()
    status = status if status in drafts_repo.STATUS_NAMES else ""
    department = drafts_repo.canonical_department(department) if is_collector else own_department

    records = drafts_repo.list_drafts(department, status)
    if start_date:
        records = [r for r in records if _taipei_date(r.get("created_at")) >= start_date]
    if end_date:
        records = [r for r in records if _taipei_date(r.get("created_at")) <= end_date]
    if name:
        records = [r for r in records if name in (r.get("name") or "")]
    records.reverse()  # 新到舊

    if export:
        content = build_draft_records_workbook(records, drafts_repo.STATUS_NAMES, drafts_repo.draft_type_name)
        return _xlsx_response(content, f"加退保操作紀錄_{_today()}.xlsx")

    filters = {k: v for k, v in (
        ("department", department if is_collector else ""), ("start_date", start_date),
        ("end_date", end_date), ("name", name), ("status", status),
    ) if v}
    return templates.TemplateResponse(
        request,
        "insurance_draft_records.html",
        {
            "user": user,
            "records": records,
            "is_collector": is_collector,
            "department_options": INSURANCE_DRAFT_DEPARTMENTS if is_collector else [],
            "filter_department": department if is_collector else "",
            "filter_start_date": start_date,
            "filter_end_date": end_date,
            "filter_name": name,
            "filter_status": status,
            "status_names": drafts_repo.STATUS_NAMES,
            "action_names": drafts_repo.ACTION_NAMES,
            "type_name": drafts_repo.draft_type_name,
            "export_url": "/hr/insurance/drafts/records?" + urlencode({**filters, "export": "1"}),
        },
    )
