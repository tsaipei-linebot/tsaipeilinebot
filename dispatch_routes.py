"""多所派遣媒合後台頁面（2026-09-22 重構自 `taoyuan_dispatch_routes.py`，
`/dispatch/{site}`）：人員管理、地點管理、需求時段管理（開需求/報名
審核）。網址依所別代碼區分，所有所共用同一套路由程式碼，`site` 這個
路徑參數決定讀寫哪個所的資料——詳細背景、權限模型都寫在
`services/dispatch_service.py`／`dispatch_sites.py` 開頭的說明，這裡只
負責頁面路由本身。
"""
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import PlainTextResponse, RedirectResponse

import platform_accounts
from config import TAIPEI_TZ
from dispatch_line import push_message
from dispatch_sites import get_site
from hr import insurance_repository as insurance_repo
from platform_templating import templates
from services.dispatch_service import (
    QUALIFICATION_MAP,
    QUALIFICATIONS,
    REGISTRATION_STATUS_APPROVED,
    REGISTRATION_STATUS_REJECTED,
    create_location,
    create_personnel,
    create_posting,
    find_personnel_by_name_and_phone,
    get_posting,
    get_registration,
    has_dispatch_access,
    list_locations,
    list_personnel,
    list_postings,
    list_registrations,
    parse_location_csv,
    parse_personnel_csv,
    set_location_active,
    set_personnel_active,
    set_posting_status,
    update_personnel_info,
    update_personnel_qualifications,
    update_registration_status,
)

router = APIRouter()

_MAX_UPLOAD_BYTES = 10 * 1024 * 1024

_PERSONNEL_TEMPLATE_CSV = "姓名,電話,人員資格\n王小明,0912345678,理貨、作業員\n"
_LOCATION_TEMPLATE_CSV = "地點,緯度,經度\n桃園火車站,24.9880,121.3141\n"


def _require_access(site: str, request: Request):
    site_config = get_site(site)
    if not site_config:
        return RedirectResponse(url="/portal", status_code=303)
    account = platform_accounts.current_account(request)
    if not account:
        return RedirectResponse(url=f"/login?next=/dispatch/{site}", status_code=303)
    if not has_dispatch_access(account, site):
        return RedirectResponse(url="/portal", status_code=303)
    return None


def _template_context(site: str, request: Request, **extra) -> dict:
    site_config = get_site(site)
    context = {
        "user": platform_accounts.current_account(request),
        "site": site,
        "site_name": site_config["name"] if site_config else "",
    }
    context.update(extra)
    return context


@router.get("/dispatch/{site}")
def dispatch_home(site: str, request: Request, redirect=Depends(_require_access)):
    if redirect:
        return redirect
    account = platform_accounts.current_account(request)
    return templates.TemplateResponse(
        request,
        "dispatch_home.html",
        _template_context(site, request, show_insurance_panel=insurance_repo.can_upload(account)),
    )


@router.get("/dispatch/{site}/help")
def dispatch_help_page(site: str, request: Request, redirect=Depends(_require_access)):
    if redirect:
        return redirect
    return templates.TemplateResponse(request, "dispatch_help.html", _template_context(site, request))


# ==========================================
# 人員管理
# ==========================================
@router.get("/dispatch/{site}/personnel")
def dispatch_personnel_page(site: str, request: Request, error: str = "", redirect=Depends(_require_access)):
    if redirect:
        return redirect
    return templates.TemplateResponse(
        request,
        "dispatch_personnel.html",
        _template_context(
            site,
            request,
            personnel=list_personnel(site),
            qualifications=QUALIFICATIONS,
            qualification_map=QUALIFICATION_MAP,
            error=error,
            import_result=None,
        ),
    )


@router.post("/dispatch/{site}/personnel/new")
async def create_dispatch_personnel(site: str, request: Request, redirect=Depends(_require_access)):
    if redirect:
        return redirect
    form = await request.form()
    name = (form.get("name") or "").strip()
    phone = (form.get("phone") or "").strip()
    qualifications = form.getlist("qualifications")
    if not name or not phone:
        return RedirectResponse(url=f"/dispatch/{site}/personnel?error=姓名跟電話都要填", status_code=303)
    account = platform_accounts.current_account(request)
    create_personnel(site, name, phone, qualifications, created_by=account["username"] if account else "")
    return RedirectResponse(url=f"/dispatch/{site}/personnel", status_code=303)


@router.post("/dispatch/{site}/personnel/{personnel_id}/edit")
async def update_dispatch_personnel(site: str, personnel_id: str, request: Request, redirect=Depends(_require_access)):
    if redirect:
        return redirect
    form = await request.form()
    name = (form.get("name") or "").strip()
    phone = (form.get("phone") or "").strip()
    qualifications = form.getlist("qualifications")
    update_personnel_info(site, personnel_id, name, phone)
    update_personnel_qualifications(site, personnel_id, qualifications)
    return RedirectResponse(url=f"/dispatch/{site}/personnel", status_code=303)


@router.post("/dispatch/{site}/personnel/{personnel_id}/active")
def update_dispatch_personnel_active(
    site: str, personnel_id: str, active: str = Form(...), redirect=Depends(_require_access)
):
    if redirect:
        return redirect
    set_personnel_active(site, personnel_id, active == "1")
    return RedirectResponse(url=f"/dispatch/{site}/personnel", status_code=303)


@router.get("/dispatch/{site}/personnel/import/template.csv")
def dispatch_personnel_import_template(site: str, redirect=Depends(_require_access)):
    if redirect:
        return redirect
    return PlainTextResponse(
        _PERSONNEL_TEMPLATE_CSV.encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=dispatch_personnel_template.csv"},
    )


@router.post("/dispatch/{site}/personnel/import")
async def dispatch_personnel_import_submit(
    site: str, request: Request, file: UploadFile = File(...), redirect=Depends(_require_access)
):
    if redirect:
        return redirect
    account = platform_accounts.current_account(request)
    content = await file.read()
    if len(content) > _MAX_UPLOAD_BYTES:
        import_result = {"header_error": "檔案超過 10MB 上限", "created": [], "skipped": [], "failed": []}
    else:
        rows, header_error = parse_personnel_csv(content)
        import_result = {"header_error": header_error, "created": [], "skipped": [], "failed": []}
        if not header_error:
            for row in rows:
                if not row["ok"]:
                    import_result["failed"].append(row)
                    continue
                existing = find_personnel_by_name_and_phone(site, row["name"], row["phone"])
                if existing:
                    import_result["skipped"].append({**row, "reason": "姓名+電話已存在"})
                    continue
                create_personnel(
                    site, row["name"], row["phone"], row["qualifications"],
                    created_by=account["username"] if account else "",
                )
                import_result["created"].append(row)
    return templates.TemplateResponse(
        request,
        "dispatch_personnel.html",
        _template_context(
            site,
            request,
            personnel=list_personnel(site),
            qualifications=QUALIFICATIONS,
            qualification_map=QUALIFICATION_MAP,
            error="",
            import_result=import_result,
        ),
    )


# ==========================================
# 地點管理
# ==========================================
@router.get("/dispatch/{site}/locations")
def dispatch_locations_page(site: str, request: Request, error: str = "", redirect=Depends(_require_access)):
    if redirect:
        return redirect
    return templates.TemplateResponse(
        request,
        "dispatch_locations.html",
        _template_context(site, request, locations=list_locations(site), error=error, import_result=None),
    )


@router.post("/dispatch/{site}/locations/new")
def create_dispatch_location(
    site: str, request: Request, name: str = Form(...), lat: str = Form(...), lng: str = Form(...),
    redirect=Depends(_require_access),
):
    if redirect:
        return redirect
    name = name.strip()
    try:
        lat_value, lng_value = float(lat), float(lng)
    except ValueError:
        return RedirectResponse(url=f"/dispatch/{site}/locations?error=緯度/經度要填數字", status_code=303)
    if not name:
        return RedirectResponse(url=f"/dispatch/{site}/locations?error=地點名稱不能空白", status_code=303)
    account = platform_accounts.current_account(request)
    create_location(site, name, lat_value, lng_value, created_by=account["username"] if account else "")
    return RedirectResponse(url=f"/dispatch/{site}/locations", status_code=303)


@router.post("/dispatch/{site}/locations/{location_id}/active")
def update_dispatch_location_active(
    site: str, location_id: str, active: str = Form(...), redirect=Depends(_require_access)
):
    if redirect:
        return redirect
    set_location_active(site, location_id, active == "1")
    return RedirectResponse(url=f"/dispatch/{site}/locations", status_code=303)


@router.get("/dispatch/{site}/locations/import/template.csv")
def dispatch_locations_import_template(site: str, redirect=Depends(_require_access)):
    if redirect:
        return redirect
    return PlainTextResponse(
        _LOCATION_TEMPLATE_CSV.encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=dispatch_locations_template.csv"},
    )


@router.post("/dispatch/{site}/locations/import")
async def dispatch_locations_import_submit(
    site: str, request: Request, file: UploadFile = File(...), redirect=Depends(_require_access)
):
    if redirect:
        return redirect
    account = platform_accounts.current_account(request)
    content = await file.read()
    if len(content) > _MAX_UPLOAD_BYTES:
        import_result = {"header_error": "檔案超過 10MB 上限", "created": [], "failed": []}
    else:
        rows, header_error = parse_location_csv(content)
        import_result = {"header_error": header_error, "created": [], "failed": []}
        if not header_error:
            for row in rows:
                if not row["ok"]:
                    import_result["failed"].append(row)
                    continue
                create_location(site, row["name"], row["lat"], row["lng"], created_by=account["username"] if account else "")
                import_result["created"].append(row)
    return templates.TemplateResponse(
        request,
        "dispatch_locations.html",
        _template_context(site, request, locations=list_locations(site), error="", import_result=import_result),
    )


# ==========================================
# 需求時段管理：開需求（地點/時段/人數/需要的人員資格）、審核人員報名。
# 人員在 LINE 上查詢需求／報名（見 dispatch_bot.py），核准/駁回時這裡會
# 直接推播 LINE 訊息通知人員（見 dispatch_line.py），不用像配送部報班
# 那樣要人員自己傳「查詢報名狀態」才知道結果——因為每個所的帳號自己有
# Channel Token，主動推播比配送部那套 GAS 轉發機制單純很多。
# ==========================================
def _posting_time_display(posting: dict) -> dict:
    posting["start_time_display"] = (
        datetime.fromtimestamp(posting["start_time"], TAIPEI_TZ).strftime("%Y-%m-%d %H:%M")
        if posting.get("start_time")
        else "-"
    )
    posting["end_time_display"] = (
        datetime.fromtimestamp(posting["end_time"], TAIPEI_TZ).strftime("%H:%M") if posting.get("end_time") else "-"
    )
    return posting


@router.get("/dispatch/{site}/postings")
def dispatch_postings_page(
    site: str, request: Request, location: str = "", date: str = "", error: str = "",
    redirect=Depends(_require_access),
):
    if redirect:
        return redirect
    items = [_posting_time_display(p) for p in list_postings(site, location_name=location, date_str=date)]
    return templates.TemplateResponse(
        request,
        "dispatch_postings.html",
        _template_context(
            site,
            request,
            postings=items,
            locations=list_locations(site, include_inactive=False),
            qualifications=QUALIFICATIONS,
            qualification_map=QUALIFICATION_MAP,
            filter_location=location,
            filter_date=date,
            error=error,
        ),
    )


@router.post("/dispatch/{site}/postings/new")
async def create_dispatch_posting(site: str, request: Request, redirect=Depends(_require_access)):
    if redirect:
        return redirect
    form = await request.form()
    location_name = (form.get("location_name") or "").strip()
    start_time_raw = (form.get("start_time") or "").strip()
    end_time_raw = (form.get("end_time") or "").strip()
    headcount_raw = (form.get("headcount") or "").strip()
    required_qualifications = form.getlist("required_qualifications")

    if not location_name:
        return RedirectResponse(url=f"/dispatch/{site}/postings?error=請從清單選擇一個地點", status_code=303)
    if not required_qualifications:
        return RedirectResponse(url=f"/dispatch/{site}/postings?error=請至少勾選一項人員資格", status_code=303)
    try:
        start_at = TAIPEI_TZ.localize(datetime.strptime(start_time_raw, "%Y-%m-%dT%H:%M")).timestamp()
        end_at = TAIPEI_TZ.localize(datetime.strptime(end_time_raw, "%Y-%m-%dT%H:%M")).timestamp()
        headcount = int(headcount_raw)
    except ValueError:
        return RedirectResponse(
            url=f"/dispatch/{site}/postings?error=請確認時間格式跟需求人數都正確", status_code=303
        )

    account = platform_accounts.current_account(request)
    create_posting(
        site,
        location_name,
        start_at,
        end_at,
        headcount,
        required_qualifications,
        created_by=account["username"] if account else "",
    )
    return RedirectResponse(url=f"/dispatch/{site}/postings", status_code=303)


@router.post("/dispatch/{site}/postings/{posting_id}/status")
def update_dispatch_posting_status(
    site: str, posting_id: str, status: str = Form(...), redirect=Depends(_require_access)
):
    if redirect:
        return redirect
    set_posting_status(site, posting_id, status)
    return RedirectResponse(url=f"/dispatch/{site}/postings", status_code=303)


@router.get("/dispatch/{site}/postings/{posting_id}/registrations")
def dispatch_posting_registrations_page(
    site: str, posting_id: str, request: Request, redirect=Depends(_require_access)
):
    if redirect:
        return redirect
    posting = get_posting(site, posting_id)
    if posting:
        _posting_time_display(posting)
    registrations = list_registrations(site, posting_id)
    for r in registrations:
        r["registered_at_display"] = (
            datetime.fromtimestamp(r["registered_at"], TAIPEI_TZ).strftime("%Y-%m-%d %H:%M")
            if r.get("registered_at")
            else "-"
        )
    return templates.TemplateResponse(
        request,
        "dispatch_posting_registrations.html",
        _template_context(site, request, posting=posting, registrations=registrations),
    )


@router.post("/dispatch/{site}/postings/{posting_id}/registrations/{registration_id}/status")
def update_dispatch_registration_status(
    site: str, posting_id: str, registration_id: str, status: str = Form(...), redirect=Depends(_require_access)
):
    if redirect:
        return redirect
    before = get_registration(site, registration_id)
    changed = update_registration_status(site, registration_id, status)
    if (
        changed
        and before
        and before["status"] != status
        and before.get("line_user_id")
        and status in (REGISTRATION_STATUS_APPROVED, REGISTRATION_STATUS_REJECTED)
    ):
        posting = get_posting(site, posting_id)
        if posting:
            start_display = (
                datetime.fromtimestamp(posting["start_time"], TAIPEI_TZ).strftime("%m/%d %H:%M")
                if posting.get("start_time")
                else ""
            )
            if status == REGISTRATION_STATUS_APPROVED:
                text = f"您報名的需求已核准！\n{posting['location_name']}　{start_display}\n請準時報到，謝謝。"
            else:
                text = (
                    f"您報名的需求很抱歉未能核准（額滿或不符資格）。\n{posting['location_name']}　{start_display}\n"
                    "可以傳「需求列表」查看其他開放中的需求。"
                )
            push_message(site, before["line_user_id"], text)
    return RedirectResponse(url=f"/dispatch/{site}/postings/{posting_id}/registrations", status_code=303)
