"""配送部人員：新增、詳細頁、報到/放棄報到/離職、合作方式管理。

2026-09-24 改版（見 HANDOFF.md「配送部人員流程改版」）：
- 主頁拿掉「選擇廠商」卡片，原本的廠商人員清單頁（/vendor/{廠商}）改成
  轉到「查詢人員」頁（routes/search_routes.py），那邊變成主要的人員清單
- 新增人員改成 /personnel/new（在表單裡選廠商），不再綁在廠商清單底下
- 詳細頁只剩 4 種證明的到期日（不再上傳照片、不再有身分證字號/Email/勾選項）
- 查詢人員每一列的「報到」「放棄報到」「離職」按鈕打這裡的三個路由
"""
from datetime import date
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from delivery import repository
from delivery.auth import admin_required, current_user, login_required
from delivery.config import (
    CLIENT_MAP,
    CLIENT_VENDORS,
    CLIENTS,
    COOPERATION_CATEGORIES,
    COOPERATION_CATEGORY_MAP,
    PERSONNEL_STATUS_MAP,
    PERSONNEL_STATUSES,
    VENDOR_MAP,
    VENDORS,
)
from delivery.storage import delete_entity_files
from delivery.templating import templates

router = APIRouter()

SEARCH_URL = "/delivery/search"


def _safe_back(back: str) -> str:
    """按鈕送出後回到原本的查詢人員頁（保留篩選條件）；只接受查詢人員頁
    自己的網址，避免被塞外部網址轉走。"""
    back = (back or "").strip()
    return back if back.startswith(SEARCH_URL) else SEARCH_URL


def _with_message(url: str, key: str, value: str) -> str:
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}{key}={quote(value)}"


@router.get("/vendor/{vendor_code}")
def vendor_list(vendor_code: str, redirect=Depends(login_required)):
    """舊的廠商人員清單頁：2026-09-24 起改成直接轉到查詢人員頁並帶入廠商篩選，
    同仁存過的舊書籤/連結還能用。"""
    if redirect:
        return redirect
    if vendor_code not in VENDOR_MAP:
        return RedirectResponse(url=SEARCH_URL, status_code=303)
    return RedirectResponse(url=f"{SEARCH_URL}?{urlencode({'vendor': vendor_code})}", status_code=303)


@router.get("/vendor/{vendor_code}/new")
def legacy_new_personnel_form(vendor_code: str, redirect=Depends(login_required)):
    if redirect:
        return redirect
    query = f"?{urlencode({'vendor': vendor_code})}" if vendor_code in VENDOR_MAP else ""
    return RedirectResponse(url=f"/delivery/personnel/new{query}", status_code=303)


@router.get("/personnel/new")
def new_personnel_form(request: Request, vendor: str = "", redirect=Depends(login_required)):
    if redirect:
        return redirect
    return templates.TemplateResponse(
        request,
        "personnel_form.html",
        {
            "user": current_user(request),
            "vendors": VENDORS,
            "selected_vendor": vendor if vendor in VENDOR_MAP else "",
            "cooperation_types_by_vendor": repository.cooperation_types_by_vendor(),
            "clients": CLIENTS,
            "client_vendors": CLIENT_VENDORS,
            "error": "",
        },
    )


@router.post("/personnel/new")
def create_personnel_submit(
    request: Request,
    vendor: str = Form(""),
    name: str = Form(...),
    phone: str = Form(""),
    cooperation_type: str = Form(""),
    client: str = Form(""),
    employee_no: str = Form(""),
    redirect=Depends(login_required),
):
    if redirect:
        return redirect
    if vendor not in VENDOR_MAP or not name.strip():
        return RedirectResponse(url="/delivery/personnel/new", status_code=303)
    coop = repository.get_cooperation_type(cooperation_type)
    if not coop or vendor not in coop.get("vendors", []):
        cooperation_type = ""
    if client not in CLIENT_MAP or vendor not in CLIENT_VENDORS:
        client = ""
    user = current_user(request)
    personnel_id = repository.create_personnel(
        name.strip(),
        "",
        phone.strip(),
        vendor,
        user["username"],
        cooperation_type=cooperation_type,
        client=client,
        employee_no=employee_no.strip(),
    )
    return RedirectResponse(url=f"/delivery/personnel/{personnel_id}", status_code=303)


@router.get("/personnel/{personnel_id}")
def personnel_detail(personnel_id: str, request: Request, redirect=Depends(login_required)):
    if redirect:
        return redirect
    person = repository.get_personnel(personnel_id)
    if not person:
        return RedirectResponse(url=SEARCH_URL, status_code=303)
    vendor_code = person.get("vendor")

    # 裝備尚欠：名下還有借用未歸還的裝備時列出來，改成「離職」時會跳提醒
    # （見 personnel_detail.html 的 JS，以及查詢人員頁「離職」按鈕的確認視窗）。
    debt_rows = repository.list_equipment_debt(personnel_id=personnel_id)
    if debt_rows:
        item_map = {i["id"]: i["name"] for i in repository.list_equipment_items(include_inactive=True)}
        for row in debt_rows:
            row["item_name"] = item_map.get(row["item_id"], "（已刪除品項）")

    return templates.TemplateResponse(
        request,
        "personnel_detail.html",
        {
            "user": current_user(request),
            "person": person,
            "vendor_name": VENDOR_MAP.get(vendor_code, vendor_code),
            "vendors": VENDORS,
            "cooperation_types": repository.list_cooperation_types(vendor=vendor_code),
            "cooperation_types_by_vendor": repository.cooperation_types_by_vendor(),
            "clients": CLIENTS,
            "personnel_statuses": PERSONNEL_STATUSES,
            "current_employment_status": repository.personnel_employment_status(person),
            "show_client": vendor_code in CLIENT_VENDORS,
            "doc_statuses": repository.all_document_statuses(person),
            "equipment_debt": debt_rows,
        },
    )


@router.post("/personnel/{personnel_id}/delete")
def delete_personnel_submit(personnel_id: str, request: Request, redirect=Depends(admin_required)):
    """整筆刪除人員紀錄（2026-09-13 新增），只有主管能刪。真的整筆刪掉
    Firestore 紀錄跟以前上傳過的所有檔案，沒有回收機制，前端要先跳確認對話框。"""
    if redirect:
        return redirect
    if repository.get_personnel(personnel_id):
        delete_entity_files("personnel-docs", personnel_id)
        repository.delete_personnel(personnel_id)
    return RedirectResponse(url=SEARCH_URL, status_code=303)


def _valid_date(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        return ""


@router.post("/personnel/{personnel_id}/bulk-update")
async def bulk_update_personnel(personnel_id: str, request: Request, redirect=Depends(login_required)):
    """人員詳細頁的一鍵全部更新：廠商、狀態、合作方式、負責客戶、到職日期、
    工號、各項證明的到期日。到期日欄位留空＝清掉那一項的日期。"""
    if redirect:
        return redirect
    person = repository.get_personnel(personnel_id)
    if not person:
        return RedirectResponse(url=SEARCH_URL, status_code=303)

    form = await request.form()

    effective_vendor = person.get("vendor", "")
    if "vendor" in form:
        vendor = form.get("vendor", "")
        if vendor in VENDOR_MAP:
            repository.update_personnel_vendor(personnel_id, vendor)
            effective_vendor = vendor

    if "cooperation_type" in form:
        # 伺服器端也要擋「廠商 A 配上廠商 B 的合作方式」（表單被竄改或 JS 沒跑時）
        cooperation_type = form.get("cooperation_type", "")
        coop = repository.get_cooperation_type(cooperation_type)
        if not coop or effective_vendor not in coop.get("vendors", []):
            cooperation_type = ""
        repository.update_personnel_cooperation_type(personnel_id, cooperation_type)

    if "client" in form:
        client = form.get("client", "")
        repository.update_personnel_client(personnel_id, client if client in CLIENT_MAP else "")

    if "employment_status" in form:
        employment_status = form.get("employment_status", "")
        if employment_status in PERSONNEL_STATUS_MAP:
            repository.update_personnel_employment_status(personnel_id, employment_status)

    if "hire_date" in form:
        repository.update_personnel_hire_date(personnel_id, _valid_date(form.get("hire_date")))

    if "employee_no" in form:
        repository.update_personnel_employee_no(personnel_id, (form.get("employee_no") or "").strip())

    # 用畫面上顯示的那幾項（送出前的廠商）存：同仁看到什麼就存什麼
    for doc_type in repository.applicable_doc_types(person.get("vendor")):
        field = f"expiry_date_{doc_type['code']}"
        if field in form:
            repository.update_personnel_document(personnel_id, doc_type["code"], expiry_date=_valid_date(form.get(field)))

    return RedirectResponse(url=f"/delivery/personnel/{personnel_id}?saved=1", status_code=303)


@router.post("/personnel/{personnel_id}/onboard")
def onboard_personnel(personnel_id: str, hire_date: str = Form(""), back: str = Form(""), redirect=Depends(login_required)):
    """查詢人員頁的「報到」：狀態改在職，到職日期填入同仁選的日期。只有
    「待報到」的人可以按（畫面上也只有待報到才顯示這顆按鈕）。"""
    if redirect:
        return redirect
    back_url = _safe_back(back)
    person = repository.get_personnel(personnel_id)
    hire_date = _valid_date(hire_date)
    if not person or repository.personnel_employment_status(person) != "pending_onboard":
        return RedirectResponse(url=_with_message(back_url, "err", "這個人目前不是「待報到」，無法報到。"), status_code=303)
    if not hire_date:
        return RedirectResponse(url=_with_message(back_url, "err", "請選擇報到日期。"), status_code=303)
    repository.update_personnel_employment_status(personnel_id, "employed")
    repository.update_personnel_hire_date(personnel_id, hire_date)
    return RedirectResponse(url=_with_message(back_url, "msg", f"{person.get('name')} 已報到（{hire_date}）。"), status_code=303)


@router.post("/personnel/{personnel_id}/withdraw")
def withdraw_personnel(personnel_id: str, back: str = Form(""), redirect=Depends(login_required)):
    """「放棄報到」：只有待報到的人可以按。"""
    if redirect:
        return redirect
    back_url = _safe_back(back)
    person = repository.get_personnel(personnel_id)
    if not person or repository.personnel_employment_status(person) != "pending_onboard":
        return RedirectResponse(url=_with_message(back_url, "err", "這個人目前不是「待報到」。"), status_code=303)
    repository.update_personnel_employment_status(personnel_id, "onboard_withdrawn")
    return RedirectResponse(url=_with_message(back_url, "msg", f"{person.get('name')} 已改成放棄報到。"), status_code=303)


@router.post("/personnel/{personnel_id}/resign")
def resign_personnel(personnel_id: str, back: str = Form(""), redirect=Depends(login_required)):
    """「離職」：只有在職的人可以按。名下還有裝備沒還的提醒在按鈕的確認視窗
    裡（查詢人員頁），這裡不擋。"""
    if redirect:
        return redirect
    back_url = _safe_back(back)
    person = repository.get_personnel(personnel_id)
    if not person or repository.personnel_employment_status(person) != "employed":
        return RedirectResponse(url=_with_message(back_url, "err", "這個人目前不是「在職」。"), status_code=303)
    repository.update_personnel_employment_status(personnel_id, "resigned")
    return RedirectResponse(url=_with_message(back_url, "msg", f"{person.get('name')} 已改成離職。"), status_code=303)


@router.get("/cooperation-types")
def cooperation_types_page(request: Request, redirect=Depends(admin_required)):
    """合作方式管理，限管理員（2026-09-18 新增）——比照車輛服務區域管理
    （vehicle_routes.py 的 service_areas_page），差別在於一筆合作方式可以
    同時套用到多個廠商：蝦皮／蝦皮三輪速配倉需要繼續共用同一組合作方式
    （見 repository.py「合作方式管理」段落的說明），因為應徵名單的試駕規則
    是照 cooperation_type 這個字串值本身判斷，不是照「廠商 + 合作方式」的組合
    （2026-09-24 起到期證明只看廠商，不再看合作方式）。"""
    if redirect:
        return redirect
    types = repository.list_cooperation_types(include_inactive=True)
    for coop in types:
        coop["has_history"] = repository.cooperation_type_has_history(coop["id"])
    return templates.TemplateResponse(
        request,
        "cooperation_types.html",
        {
            "user": current_user(request),
            "cooperation_types": types,
            "vendors": VENDORS,
            "categories": COOPERATION_CATEGORIES,
            "category_map": COOPERATION_CATEGORY_MAP,
            "error": "",
        },
    )


@router.post("/cooperation-types/new")
async def create_cooperation_type_submit(request: Request, redirect=Depends(admin_required)):
    if redirect:
        return redirect
    form = await request.form()
    name = (form.get("name") or "").strip()
    vendors = [v for v in form.getlist("vendors") if v in VENDOR_MAP]
    category = form.get("category") or ""
    if category not in COOPERATION_CATEGORY_MAP:
        category = ""
    if name:
        repository.create_cooperation_type(name, vendors, category=category, created_by=current_user(request)["username"])
    return RedirectResponse(url="/delivery/cooperation-types", status_code=303)


@router.post("/cooperation-types/{type_id}/edit")
async def edit_cooperation_type_submit(type_id: str, request: Request, redirect=Depends(admin_required)):
    if redirect:
        return redirect
    form = await request.form()
    name = (form.get("name") or "").strip()
    vendors = [v for v in form.getlist("vendors") if v in VENDOR_MAP]
    category = form.get("category") or ""
    if category not in COOPERATION_CATEGORY_MAP:
        category = ""
    if name:
        repository.update_cooperation_type(type_id, name, vendors, category=category)
    return RedirectResponse(url="/delivery/cooperation-types", status_code=303)


@router.post("/cooperation-types/{type_id}/active")
def toggle_cooperation_type_active(
    type_id: str, request: Request, active: str = Form(...), redirect=Depends(admin_required)
):
    if redirect:
        return redirect
    repository.set_cooperation_type_active(type_id, active == "1")
    return RedirectResponse(url="/delivery/cooperation-types", status_code=303)


@router.post("/cooperation-types/{type_id}/delete")
def delete_cooperation_type_submit(type_id: str, request: Request, redirect=Depends(admin_required)):
    if redirect:
        return redirect
    repository.delete_cooperation_type(type_id)
    return RedirectResponse(url="/delivery/cooperation-types", status_code=303)
