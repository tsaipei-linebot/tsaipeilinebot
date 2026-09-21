"""桃園所派遣媒合後台頁面（/taoyuan-dispatch）：人員管理、地點管理、需求
時段管理（開需求/報名審核）。詳細背景、權限模型都寫在
services/taoyuan_dispatch_service.py 開頭的說明，這裡只負責頁面路由本身。
"""
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import PlainTextResponse, RedirectResponse

import platform_accounts
from config import TAIPEI_TZ
from platform_templating import templates
from services.taoyuan_dispatch_service import (
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
    has_taoyuan_access,
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
from taoyuan_dispatch_line import push_message

router = APIRouter()

_MAX_UPLOAD_BYTES = 10 * 1024 * 1024

_PERSONNEL_TEMPLATE_CSV = "姓名,電話,人員資格\n王小明,0912345678,理貨、作業員\n"
_LOCATION_TEMPLATE_CSV = "地點,緯度,經度\n桃園火車站,24.9880,121.3141\n"


def _require_access(request: Request):
    account = platform_accounts.current_account(request)
    if not account:
        return RedirectResponse(url="/login?next=/taoyuan-dispatch", status_code=303)
    if not has_taoyuan_access(account):
        return RedirectResponse(url="/portal", status_code=303)
    return None


@router.get("/taoyuan-dispatch")
def taoyuan_dispatch_home(request: Request, redirect=Depends(_require_access)):
    if redirect:
        return redirect
    return templates.TemplateResponse(
        request, "taoyuan_dispatch_home.html", {"user": platform_accounts.current_account(request)}
    )


# ==========================================
# 人員管理
# ==========================================
@router.get("/taoyuan-dispatch/personnel")
def taoyuan_dispatch_personnel_page(request: Request, error: str = "", redirect=Depends(_require_access)):
    if redirect:
        return redirect
    return templates.TemplateResponse(
        request,
        "taoyuan_dispatch_personnel.html",
        {
            "user": platform_accounts.current_account(request),
            "personnel": list_personnel(),
            "qualifications": QUALIFICATIONS,
            "qualification_map": QUALIFICATION_MAP,
            "error": error,
            "import_result": None,
        },
    )


@router.post("/taoyuan-dispatch/personnel/new")
async def create_taoyuan_dispatch_personnel(request: Request, redirect=Depends(_require_access)):
    if redirect:
        return redirect
    form = await request.form()
    name = (form.get("name") or "").strip()
    phone = (form.get("phone") or "").strip()
    qualifications = form.getlist("qualifications")
    if not name or not phone:
        return RedirectResponse(url="/taoyuan-dispatch/personnel?error=姓名跟電話都要填", status_code=303)
    account = platform_accounts.current_account(request)
    create_personnel(name, phone, qualifications, created_by=account["username"] if account else "")
    return RedirectResponse(url="/taoyuan-dispatch/personnel", status_code=303)


@router.post("/taoyuan-dispatch/personnel/{personnel_id}/edit")
async def update_taoyuan_dispatch_personnel(personnel_id: str, request: Request, redirect=Depends(_require_access)):
    if redirect:
        return redirect
    form = await request.form()
    name = (form.get("name") or "").strip()
    phone = (form.get("phone") or "").strip()
    qualifications = form.getlist("qualifications")
    update_personnel_info(personnel_id, name, phone)
    update_personnel_qualifications(personnel_id, qualifications)
    return RedirectResponse(url="/taoyuan-dispatch/personnel", status_code=303)


@router.post("/taoyuan-dispatch/personnel/{personnel_id}/active")
def update_taoyuan_dispatch_personnel_active(
    personnel_id: str, active: str = Form(...), redirect=Depends(_require_access)
):
    if redirect:
        return redirect
    set_personnel_active(personnel_id, active == "1")
    return RedirectResponse(url="/taoyuan-dispatch/personnel", status_code=303)


@router.get("/taoyuan-dispatch/personnel/import/template.csv")
def taoyuan_dispatch_personnel_import_template(redirect=Depends(_require_access)):
    if redirect:
        return redirect
    return PlainTextResponse(
        _PERSONNEL_TEMPLATE_CSV.encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=taoyuan_dispatch_personnel_template.csv"},
    )


@router.post("/taoyuan-dispatch/personnel/import")
async def taoyuan_dispatch_personnel_import_submit(
    request: Request, file: UploadFile = File(...), redirect=Depends(_require_access)
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
                existing = find_personnel_by_name_and_phone(row["name"], row["phone"])
                if existing:
                    import_result["skipped"].append({**row, "reason": "姓名+電話已存在"})
                    continue
                create_personnel(
                    row["name"], row["phone"], row["qualifications"], created_by=account["username"] if account else ""
                )
                import_result["created"].append(row)
    return templates.TemplateResponse(
        request,
        "taoyuan_dispatch_personnel.html",
        {
            "user": account,
            "personnel": list_personnel(),
            "qualifications": QUALIFICATIONS,
            "qualification_map": QUALIFICATION_MAP,
            "error": "",
            "import_result": import_result,
        },
    )


# ==========================================
# 地點管理
# ==========================================
@router.get("/taoyuan-dispatch/locations")
def taoyuan_dispatch_locations_page(request: Request, error: str = "", redirect=Depends(_require_access)):
    if redirect:
        return redirect
    return templates.TemplateResponse(
        request,
        "taoyuan_dispatch_locations.html",
        {"user": platform_accounts.current_account(request), "locations": list_locations(), "error": error, "import_result": None},
    )


@router.post("/taoyuan-dispatch/locations/new")
def create_taoyuan_dispatch_location(
    request: Request, name: str = Form(...), lat: str = Form(...), lng: str = Form(...), redirect=Depends(_require_access)
):
    if redirect:
        return redirect
    name = name.strip()
    try:
        lat_value, lng_value = float(lat), float(lng)
    except ValueError:
        return RedirectResponse(url="/taoyuan-dispatch/locations?error=緯度/經度要填數字", status_code=303)
    if not name:
        return RedirectResponse(url="/taoyuan-dispatch/locations?error=地點名稱不能空白", status_code=303)
    account = platform_accounts.current_account(request)
    create_location(name, lat_value, lng_value, created_by=account["username"] if account else "")
    return RedirectResponse(url="/taoyuan-dispatch/locations", status_code=303)


@router.post("/taoyuan-dispatch/locations/{location_id}/active")
def update_taoyuan_dispatch_location_active(location_id: str, active: str = Form(...), redirect=Depends(_require_access)):
    if redirect:
        return redirect
    set_location_active(location_id, active == "1")
    return RedirectResponse(url="/taoyuan-dispatch/locations", status_code=303)


@router.get("/taoyuan-dispatch/locations/import/template.csv")
def taoyuan_dispatch_locations_import_template(redirect=Depends(_require_access)):
    if redirect:
        return redirect
    return PlainTextResponse(
        _LOCATION_TEMPLATE_CSV.encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=taoyuan_dispatch_locations_template.csv"},
    )


@router.post("/taoyuan-dispatch/locations/import")
async def taoyuan_dispatch_locations_import_submit(
    request: Request, file: UploadFile = File(...), redirect=Depends(_require_access)
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
                create_location(row["name"], row["lat"], row["lng"], created_by=account["username"] if account else "")
                import_result["created"].append(row)
    return templates.TemplateResponse(
        request,
        "taoyuan_dispatch_locations.html",
        {"user": account, "locations": list_locations(), "error": "", "import_result": import_result},
    )


# ==========================================
# 需求時段管理（Phase 2，2026-09-21 新增）：開需求（地點/時段/人數/需要
# 的人員資格）、審核人員報名。人員在 LINE 上查詢需求／報名（見
# taoyuan_dispatch_bot.py），核准/駁回時這裡會直接推播 LINE 訊息通知
# 人員（見 taoyuan_dispatch_line.py），不用像配送部報班那樣要人員自己
# 傳「查詢報名狀態」才知道結果——因為這組帳號自己有 Channel Token，
# 主動推播比配送部那套 GAS 轉發機制單純很多。
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


@router.get("/taoyuan-dispatch/postings")
def taoyuan_dispatch_postings_page(
    request: Request, location: str = "", date: str = "", error: str = "", redirect=Depends(_require_access)
):
    if redirect:
        return redirect
    items = [_posting_time_display(p) for p in list_postings(location_name=location, date_str=date)]
    return templates.TemplateResponse(
        request,
        "taoyuan_dispatch_postings.html",
        {
            "user": platform_accounts.current_account(request),
            "postings": items,
            "locations": list_locations(include_inactive=False),
            "qualifications": QUALIFICATIONS,
            "qualification_map": QUALIFICATION_MAP,
            "filter_location": location,
            "filter_date": date,
            "error": error,
        },
    )


@router.post("/taoyuan-dispatch/postings/new")
async def create_taoyuan_dispatch_posting(request: Request, redirect=Depends(_require_access)):
    if redirect:
        return redirect
    form = await request.form()
    location_name = (form.get("location_name") or "").strip()
    start_time_raw = (form.get("start_time") or "").strip()
    end_time_raw = (form.get("end_time") or "").strip()
    headcount_raw = (form.get("headcount") or "").strip()
    required_qualifications = form.getlist("required_qualifications")

    if not location_name:
        return RedirectResponse(url="/taoyuan-dispatch/postings?error=請從清單選擇一個地點", status_code=303)
    if not required_qualifications:
        return RedirectResponse(url="/taoyuan-dispatch/postings?error=請至少勾選一項人員資格", status_code=303)
    try:
        start_at = TAIPEI_TZ.localize(datetime.strptime(start_time_raw, "%Y-%m-%dT%H:%M")).timestamp()
        end_at = TAIPEI_TZ.localize(datetime.strptime(end_time_raw, "%Y-%m-%dT%H:%M")).timestamp()
        headcount = int(headcount_raw)
    except ValueError:
        return RedirectResponse(
            url="/taoyuan-dispatch/postings?error=請確認時間格式跟需求人數都正確", status_code=303
        )

    account = platform_accounts.current_account(request)
    create_posting(
        location_name,
        start_at,
        end_at,
        headcount,
        required_qualifications,
        created_by=account["username"] if account else "",
    )
    return RedirectResponse(url="/taoyuan-dispatch/postings", status_code=303)


@router.post("/taoyuan-dispatch/postings/{posting_id}/status")
def update_taoyuan_dispatch_posting_status(
    posting_id: str, status: str = Form(...), redirect=Depends(_require_access)
):
    if redirect:
        return redirect
    set_posting_status(posting_id, status)
    return RedirectResponse(url="/taoyuan-dispatch/postings", status_code=303)


@router.get("/taoyuan-dispatch/postings/{posting_id}/registrations")
def taoyuan_dispatch_posting_registrations_page(
    posting_id: str, request: Request, redirect=Depends(_require_access)
):
    if redirect:
        return redirect
    posting = get_posting(posting_id)
    if posting:
        _posting_time_display(posting)
    registrations = list_registrations(posting_id)
    for r in registrations:
        r["registered_at_display"] = (
            datetime.fromtimestamp(r["registered_at"], TAIPEI_TZ).strftime("%Y-%m-%d %H:%M")
            if r.get("registered_at")
            else "-"
        )
    return templates.TemplateResponse(
        request,
        "taoyuan_dispatch_posting_registrations.html",
        {"user": platform_accounts.current_account(request), "posting": posting, "registrations": registrations},
    )


@router.post("/taoyuan-dispatch/postings/{posting_id}/registrations/{registration_id}/status")
def update_taoyuan_dispatch_registration_status(
    posting_id: str, registration_id: str, status: str = Form(...), redirect=Depends(_require_access)
):
    if redirect:
        return redirect
    before = get_registration(registration_id)
    changed = update_registration_status(registration_id, status)
    if (
        changed
        and before
        and before["status"] != status
        and before.get("line_user_id")
        and status in (REGISTRATION_STATUS_APPROVED, REGISTRATION_STATUS_REJECTED)
    ):
        posting = get_posting(posting_id)
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
            push_message(before["line_user_id"], text)
    return RedirectResponse(url=f"/taoyuan-dispatch/postings/{posting_id}/registrations", status_code=303)
