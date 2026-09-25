"""台北所(派遣組)／台北所(國際組)專區：待進人員、廠商維護、班別維護（2026-09-25 新增，見 HANDOFF.md
「台北所(派遣組)／台北所(國際組)專區」）。

專區的「每日加退保」分頁就是既有的 `/hr/insurance/upload`（hr/routes/insurance_routes.py），這支檔案只放
新的分頁。權限跟加退保上傳頁一樣只看帳號部門（不用勾人資專區模組），部門要在
`hr.config.INSURANCE_ZONE_DEPARTMENTS`；廠商／班別維護只有主管（帳號職級副主任以上）或全平台管理員。

規則（身分證必填、日期不同天自動拆兩列、重複檢查、多日期、匯入）在 hr/insurance_pending.py。
"""
import datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import RedirectResponse, Response

import platform_accounts
from file_type_sniff import is_allowed_upload
from hr import insurance_draft_repository as drafts
from hr import insurance_options as options
from hr import insurance_pending as pending
from hr import insurance_repository as repo
from hr.auth import current_user
from hr.config import ALLOWED_UPLOAD_CONTENT_TYPES, INSURANCE_ZONE_DEPARTMENTS, MAX_UPLOAD_BYTES
from hr.insurance_excel import build_department_workbook, parse_department_workbook
from hr.templating import templates

router = APIRouter()

_TAIPEI = datetime.timezone(datetime.timedelta(hours=8))
PENDING_URL = "/hr/zone/pending"


def _today() -> str:
    return datetime.datetime.now(_TAIPEI).date().isoformat()


def is_zone_department(department) -> bool:
    normalized = platform_accounts.normalize_department(department)
    return normalized in {platform_accounts.normalize_department(d) for d in INSURANCE_ZONE_DEPARTMENTS}


def is_zone_manager(user: dict) -> bool:
    return bool(user.get("is_platform_admin")) or platform_accounts.is_manager_rank(user.get("rank", ""))


def zone_tabs_context(user: dict, active: str) -> dict:
    """給樣板 `_zone_tabs.html` 用。"""
    return {"zone_tab": active, "zone_manager": is_zone_manager(user)}


def _require_zone(request: Request):
    """回傳 (user, department, redirect)。沒登入導去登入頁，不是台北所的帳號導回 /portal。"""
    account = platform_accounts.current_account(request)
    if not account:
        return None, "", RedirectResponse(url=f"/login?next={request.url.path}", status_code=303)
    user = current_user(request)
    department = drafts.canonical_department(user.get("department") or "")
    if not repo.can_upload(user) or not is_zone_department(department):
        return user, department, RedirectResponse(url="/portal", status_code=303)
    return user, department, None


def _with(url: str, key: str, value: str) -> str:
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}{key}={quote(value)}"


def _safe_back(back: str) -> str:
    back = (back or "").strip()
    return back if back.startswith("/hr/") and not back.startswith("//") else PENDING_URL


def _picker_context(department: str) -> dict:
    return {
        "vendor_names": options.active_names(department, options.TYPE_VENDOR),
        "shift_names": options.active_names(department, options.TYPE_SHIFT),
        "multi_types": pending.MULTI_TYPES,
    }


# ---------- 待進人員清單 ----------
def entry_day(r: dict) -> str:
    """這一列最早的日期（清單排序、日期篩選用）。"""
    days = [d for d in (r.get("insured_date"), r.get("withdrawn_date"), r.get("recovery_date")) if d]
    return min(days) if days else ""


def _list_context(user, department, date_from="", date_to="", name="", vendor="", status="open", msg="", err="", import_errors=()):
    rows = drafts.list_drafts(department)
    if status == "open":
        rows = [r for r in rows if r.get("status") in (drafts.STATUS_PENDING, drafts.STATUS_LATE)]
    elif status in drafts.STATUS_NAMES:
        rows = [r for r in rows if r.get("status") == status]
    if date_from:
        rows = [r for r in rows if entry_day(r) >= date_from]
    if date_to:
        rows = [r for r in rows if entry_day(r) <= date_to]
    name = (name or "").strip()
    if name:
        rows = [r for r in rows if name in (r.get("name") or "") or name.upper() in (r.get("id_number") or "")]
    if vendor:
        rows = [r for r in rows if r.get("vendor") == vendor]
    rows.sort(key=lambda r: (entry_day(r), r.get("name", "")))
    return {
        "user": user,
        "department": department,
        "rows": rows,
        "day_of": entry_day,
        "filters": {"date_from": date_from, "date_to": date_to, "name": name, "vendor": vendor, "status": status},
        "status_names": drafts.STATUS_NAMES,
        "type_name": drafts.draft_type_name,
        "all_vendor_names": [o["name"] for o in options.list_options(department, options.TYPE_VENDOR)],
        "msg": msg,
        "err": err,
        "import_errors": list(import_errors),
        **zone_tabs_context(user, "pending"),
    }


@router.get("/zone/pending")
def pending_list(
    request: Request,
    date_from: str = "",
    date_to: str = "",
    name: str = "",
    vendor: str = "",
    status: str = "open",
    msg: str = "",
    err: str = "",
):
    user, department, redirect = _require_zone(request)
    if redirect:
        return redirect
    return templates.TemplateResponse(
        request, "zone_pending_list.html",
        _list_context(user, department, date_from, date_to, name, vendor, status, msg, err),
    )


# ---------- 新增（單筆／多日期） ----------
def _form_context(user, department, values, mode, error="", back="", draft=None, dates=()):
    return {
        "user": user,
        "department": department,
        "values": values,
        "mode": mode,
        "dates": list(dates),
        "err": error,
        "back": back,
        "draft": draft,
        **_picker_context(department),
        **zone_tabs_context(user, "pending"),
    }


@router.get("/zone/pending/new")
def pending_new_page(request: Request, date: str = "", back: str = ""):
    user, department, redirect = _require_zone(request)
    if redirect:
        return redirect
    day = pending._iso(date) or ""
    values = {"insured_date": day, "withdrawn_date": day}
    return templates.TemplateResponse(
        request, "zone_pending_form.html", _form_context(user, department, values, "single", back=back, dates=[day] if day else [])
    )


@router.post("/zone/pending/new")
async def pending_new_submit(request: Request):
    user, department, redirect = _require_zone(request)
    if redirect:
        return redirect
    form = await request.form()
    mode = "multi" if form.get("mode") == "multi" else "single"
    back = form.get("back") or ""
    raw = {key: form.get(key) for key in drafts.FIELD_HEADERS}
    dates = [d for d in form.getlist("dates") if d]

    def _error(message):
        return templates.TemplateResponse(
            request,
            "zone_pending_form.html",
            _form_context(user, department, {**raw, "multi_type": form.get("multi_type")}, mode, message, back, dates=dates),
            status_code=400,
        )

    if mode == "multi":
        raw.update({"insured_date": "", "withdrawn_date": "", "recovery_date": ""})
        fields, error = pending.clean_fields(raw)
        entries = []
        if not error:
            # 先用一個假日期過欄位檢查（日期在 expand_multi 檢查）
            error = pending.validate(department, {**fields, "insured_date": "x"})
        if not error:
            entries, error = pending.expand_multi(fields, form.get("multi_type"), dates)
    else:
        fields, error = pending.clean_fields(raw)
        error = error or pending.validate(department, fields)
        entries = [] if error else pending.split(fields)
    if error:
        return _error(error)
    duplicates = pending.find_duplicates(department, entries)
    if duplicates:
        return _error("以下資料重複，沒有儲存：\n" + "\n".join(duplicates))
    pending.create_entries(department, entries, user, "待進人員新增" if mode == "single" else "待進人員多日期新增")
    note = f"已新增 {len(entries)} 筆：{fields['name']}"
    if mode == "single" and len(entries) == 2:
        note += "（加保、退保不同天，已自動拆成兩列）"
    return RedirectResponse(url=_with(_safe_back(back), "msg", note), status_code=303)


# ---------- 修改／刪除 ----------
def _own_open_draft(draft_id: str, department: str):
    draft = drafts.get_draft(draft_id)
    if not draft or draft.get("department") != department or draft.get("status") != drafts.STATUS_PENDING:
        return None
    return draft


@router.get("/zone/pending/{draft_id}/edit")
def pending_edit_page(draft_id: str, request: Request, back: str = ""):
    user, department, redirect = _require_zone(request)
    if redirect:
        return redirect
    draft = _own_open_draft(draft_id, department)
    if not draft:
        return RedirectResponse(url=_with(PENDING_URL, "err", "這一筆已經送出、補件中或取消，不能修改。"), status_code=303)
    return templates.TemplateResponse(
        request, "zone_pending_form.html", _form_context(user, department, draft, "edit", back=back, draft=draft)
    )


@router.post("/zone/pending/{draft_id}/edit")
async def pending_edit_submit(draft_id: str, request: Request):
    user, department, redirect = _require_zone(request)
    if redirect:
        return redirect
    form = await request.form()
    back = form.get("back") or ""
    draft = _own_open_draft(draft_id, department)
    if not draft:
        return RedirectResponse(url=_with(PENDING_URL, "err", "這一筆已經送出、補件中或取消，不能修改。"), status_code=303)
    raw = {key: form.get(key) for key in drafts.FIELD_HEADERS}
    fields, error = pending.clean_fields(raw)
    error = error or pending.validate(department, fields)
    entries = [] if error else pending.split(fields)
    if not error:
        duplicates = pending.find_duplicates(department, entries, exclude_ids={draft_id})
        if duplicates:
            error = "以下資料重複，沒有儲存：\n" + "\n".join(duplicates)
    if error:
        return templates.TemplateResponse(
            request, "zone_pending_form.html",
            _form_context(user, department, {**draft, **raw}, "edit", error, back, draft=draft), status_code=400,
        )
    drafts.update_draft(draft_id, entries[0], user)
    note = f"已修改：{fields['name']}"
    if len(entries) == 2:
        pending.create_entries(department, entries[1:], user, "修改時加保、退保不同天，自動拆出的退保")
        note += "（加保、退保不同天，已自動拆成兩列）"
    return RedirectResponse(url=_with(_safe_back(back), "msg", note), status_code=303)


@router.post("/zone/pending/{draft_id}/cancel")
async def pending_cancel(draft_id: str, request: Request):
    user, department, redirect = _require_zone(request)
    if redirect:
        return redirect
    form = await request.form()
    back = _safe_back(form.get("back") or "")
    draft = _own_open_draft(draft_id, department)
    if not draft or not drafts.cancel_draft(draft_id, user, "待進人員手動刪除"):
        return RedirectResponse(url=_with(back, "err", "這一筆已經送出、補件中或取消，不能刪除。"), status_code=303)
    return RedirectResponse(url=_with(back, "msg", f"已刪除：{draft.get('name')}（紀錄會保留）"), status_code=303)


# ---------- Excel 匯入 ----------
@router.get("/zone/pending/template.xlsx")
def pending_template(request: Request):
    user, department, redirect = _require_zone(request)
    if redirect:
        return redirect
    content = build_department_workbook([])
    filename = f"待進人員匯入範本_{department}.xlsx"
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@router.post("/zone/pending/import")
async def pending_import(request: Request, file: UploadFile = File(None)):
    user, department, redirect = _require_zone(request)
    if redirect:
        return redirect
    if file is None or not file.filename:
        return RedirectResponse(url=_with(PENDING_URL, "err", "請選擇要匯入的 Excel 檔。"), status_code=303)
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES or not is_allowed_upload(
        content, file.content_type or "application/octet-stream", ALLOWED_UPLOAD_CONTENT_TYPES
    ):
        return RedirectResponse(url=_with(PENDING_URL, "err", "檔案格式不支援或超過 20MB，請上傳 Excel 檔（.xlsx）。"), status_code=303)
    try:
        records = parse_department_workbook(content)
    except Exception:
        return RedirectResponse(url=_with(PENDING_URL, "err", "這份 Excel 讀不出來，請用「下載匯入範本」的格式。"), status_code=303)
    entries, errors = pending.prepare_import(department, records)
    if entries:
        pending.create_entries(department, entries, user, f"Excel 匯入（{file.filename}）")
    if not errors:
        return RedirectResponse(url=_with(PENDING_URL, "msg", f"已匯入 {len(entries)} 筆。"), status_code=303)
    # 有問題的列：留在清單頁、彈出視窗列出哪幾列沒匯入
    context = _list_context(
        user, department,
        msg=f"已匯入 {len(entries)} 筆" if entries else "",
        err=f"有 {len(errors)} 列沒有匯入，請修正後再匯入這幾列：",
        import_errors=errors,
    )
    return templates.TemplateResponse(request, "zone_pending_list.html", context, status_code=400)


# ---------- 廠商維護／班別維護（主管） ----------
def _require_manager(request: Request):
    user, department, redirect = _require_zone(request)
    if redirect:
        return user, department, redirect
    if not is_zone_manager(user):
        return user, department, RedirectResponse(url=_with(PENDING_URL, "err", "只有主管（副主任以上）能維護廠商、班別。"), status_code=303)
    return user, department, None


def _options_url(option_type: str) -> str:
    return f"/hr/zone/options/{option_type}"


@router.get("/zone/options/{option_type}")
def options_page(option_type: str, request: Request, msg: str = "", err: str = ""):
    user, department, redirect = _require_manager(request)
    if redirect:
        return redirect
    if option_type not in options.TYPE_NAMES:
        return RedirectResponse(url=PENDING_URL, status_code=303)
    return templates.TemplateResponse(
        request,
        "zone_options.html",
        {
            "user": user,
            "department": department,
            "option_type": option_type,
            "type_name": options.TYPE_NAMES[option_type],
            "rows": options.list_options(department, option_type),
            "platform_vendor_choices": options.platform_vendor_choices(department) if option_type == options.TYPE_VENDOR else [],
            "msg": msg,
            "err": err,
            **zone_tabs_context(user, option_type),
        },
    )


@router.post("/zone/options/{option_type}/new")
async def options_new(option_type: str, request: Request):
    user, department, redirect = _require_manager(request)
    if redirect:
        return redirect
    form = await request.form()
    error = options.add_option(department, option_type, form.get("name"), user, form.get("platform_vendor_name") or "")
    if error:
        return RedirectResponse(url=_with(_options_url(option_type), "err", error), status_code=303)
    return RedirectResponse(url=_with(_options_url(option_type), "msg", f"已新增：{(form.get('name') or '').strip()}"), status_code=303)


@router.post("/zone/options/item/{option_id}/active")
async def options_toggle(option_id: str, request: Request):
    user, department, redirect = _require_manager(request)
    if redirect:
        return redirect
    form = await request.form()
    option = options.get_option(option_id)
    option_type = (option or {}).get("type", options.TYPE_VENDOR)
    active = form.get("active") == "1"
    if not options.set_active(option_id, department, active):
        return RedirectResponse(url=_with(_options_url(option_type), "err", "找不到這個選項。"), status_code=303)
    word = "啟用" if active else "停用"
    return RedirectResponse(url=_with(_options_url(option_type), "msg", f"已{word}：{option.get('name')}"), status_code=303)


@router.post("/zone/options/item/{option_id}/platform-vendor")
async def options_link(option_id: str, request: Request):
    user, department, redirect = _require_manager(request)
    if redirect:
        return redirect
    form = await request.form()
    if not options.set_platform_vendor(option_id, department, form.get("platform_vendor_name") or ""):
        return RedirectResponse(url=_with(_options_url(options.TYPE_VENDOR), "err", "找不到這個廠商。"), status_code=303)
    return RedirectResponse(url=_with(_options_url(options.TYPE_VENDOR), "msg", "已更新對應主頁廠商。"), status_code=303)
