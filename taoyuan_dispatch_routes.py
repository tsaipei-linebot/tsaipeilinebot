"""桃園所派遣媒合後台頁面（/taoyuan-dispatch）：人員管理、地點管理。詳細
背景、權限模型、下一階段規劃都寫在 services/taoyuan_dispatch_service.py
開頭的說明，這裡只負責頁面路由本身。
"""
from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import PlainTextResponse, RedirectResponse

import platform_accounts
from platform_templating import templates
from services.taoyuan_dispatch_service import (
    QUALIFICATION_MAP,
    QUALIFICATIONS,
    create_location,
    create_personnel,
    find_personnel_by_name_and_phone,
    has_taoyuan_access,
    list_locations,
    list_personnel,
    parse_location_csv,
    parse_personnel_csv,
    set_location_active,
    set_personnel_active,
    update_personnel_info,
    update_personnel_qualifications,
)

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
