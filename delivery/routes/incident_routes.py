from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from delivery import repository
from delivery.auth import admin_required, current_user, login_required
from delivery.config import (
    DUTY_STATUSES,
    IDENTITY_TYPES,
    INCIDENT_STATUS_MAP,
    INCIDENT_STATUSES,
    RISK_LEVELS,
    VENDOR_MAP,
    VENDORS,
    YES_NO_VALUES,
)
from delivery.templating import templates

router = APIRouter()

_INCIDENT_REQUIRED_FIELDS = (
    "vendor",
    "identity_type",
    "personnel_name",
    "occurred_at",
    "location",
    "duty_status",
    "police_called",
    "injury",
    "family_contacted",
    "third_party_involved",
    "description",
)


def _validate_incident_form(data: dict) -> str:
    """驗證意外事件表單資料（新增／編輯共用同一套規則），回傳空字串代表
    通過，否則回傳要顯示給使用者看的錯誤訊息。"""
    if data["vendor"] not in VENDOR_MAP:
        return "廠商看不懂，請重新選擇。"
    if data["identity_type"] not in IDENTITY_TYPES:
        return "身分類別請選「雇傭」或「承攬」。"
    if data["duty_status"] not in DUTY_STATUSES:
        return "執行勤務中/上下班途中請重新選擇。"
    if (
        data["police_called"] not in YES_NO_VALUES
        or data["family_contacted"] not in YES_NO_VALUES
        or data["third_party_involved"] not in YES_NO_VALUES
    ):
        return "是否報警／是否聯繫家屬／是否牽扯他人請選「有」或「無」。"
    if any(not data[key] for key in _INCIDENT_REQUIRED_FIELDS):
        return "欄位都要填。"
    return ""


def _incident_form_data(
    vendor: str,
    identity_type: str,
    personnel_name: str,
    occurred_at: str,
    location: str,
    duty_status: str,
    police_called: str,
    injury: str,
    family_contacted: str,
    third_party_involved: str,
    description: str,
) -> dict:
    """把表單欄位整理成 repository.create_incident_event()／
    update_incident_event() 都吃得下的 dict。occurred_at 統一把瀏覽器
    `<input type="datetime-local">` 產生的 "T" 分隔符換成空白，跟 LINE
    群組回報正規化出來的 "YYYY-MM-DD HH:MM" 格式（見
    delivery/incident_report.py 的 _normalize_datetime）對齊，兩條路徑
    寫進 Firestore 的格式才會一致。"""
    return {
        "vendor": vendor,
        "identity_type": identity_type,
        "personnel_name": personnel_name.strip(),
        "occurred_at": occurred_at.strip().replace("T", " "),
        "location": location.strip(),
        "duty_status": duty_status,
        "police_called": police_called,
        "injury": injury.strip(),
        "family_contacted": family_contacted,
        "third_party_involved": third_party_involved,
        "description": description.strip(),
    }


@router.get("/incidents")
def incident_list(
    request: Request,
    personnel_name: str = "",
    vendor: str = "",
    status: str = "",
    risk_level: str = "",
    redirect=Depends(login_required),
):
    if redirect:
        return redirect
    incidents = repository.list_incident_events(
        vendor_filter=vendor,
        status_filter=status,
        risk_level_filter=risk_level,
        personnel_name_filter=personnel_name,
    )
    return templates.TemplateResponse(
        request,
        "incident_list.html",
        {
            "user": current_user(request),
            "vendors": VENDORS,
            "vendor_map": VENDOR_MAP,
            "incident_statuses": INCIDENT_STATUSES,
            "incident_status_map": INCIDENT_STATUS_MAP,
            "risk_levels": RISK_LEVELS,
            "incidents": incidents,
            "filter_personnel_name": personnel_name,
            "filter_vendor": vendor,
            "filter_status": status,
            "filter_risk_level": risk_level,
        },
    )


@router.get("/incidents/new")
def new_incident_form(request: Request, redirect=Depends(login_required)):
    """網站直接新增意外事件的表單。放在 `/incidents/{incident_id}` 之前
    註冊，不然 "new" 會被當成 incident_id 吃掉。"""
    if redirect:
        return redirect
    return templates.TemplateResponse(
        request,
        "incident_new.html",
        {
            "user": current_user(request),
            "vendors": VENDORS,
            "identity_types": IDENTITY_TYPES,
            "duty_statuses": DUTY_STATUSES,
            "yes_no_values": YES_NO_VALUES,
            "form_data": {},
            "error": "",
        },
    )


@router.post("/incidents/new")
def new_incident_submit(
    request: Request,
    vendor: str = Form(...),
    identity_type: str = Form(...),
    personnel_name: str = Form(...),
    occurred_at: str = Form(...),
    location: str = Form(...),
    duty_status: str = Form(...),
    police_called: str = Form(...),
    injury: str = Form(...),
    family_contacted: str = Form(...),
    third_party_involved: str = Form(...),
    description: str = Form(...),
    redirect=Depends(login_required),
):
    """網站直接新增一筆意外事件回報，走跟 LINE 群組回報完全一樣的
    `repository.create_incident_event()`——包含「人員名稱＋發生時間」跟
    既有紀錄相同就視為同一起事件、覆寫既有那筆而不是多開一筆重複紀錄的
    邏輯（見該函式的說明），確保 LINE 群組跟網站兩條路徑寫進去的資料
    規則一致。任何登入配送部系統的同仁都能新增，不限管理員——比照 LINE
    群組任何人都能回報，跟編輯既有內容／設定風險等級／結案（限管理員）
    是不同層級的操作。"""
    if redirect:
        return redirect

    data = _incident_form_data(
        vendor,
        identity_type,
        personnel_name,
        occurred_at,
        location,
        duty_status,
        police_called,
        injury,
        family_contacted,
        third_party_involved,
        description,
    )

    error = _validate_incident_form(data)
    if not error:
        incident_id, _created = repository.create_incident_event(data)
        return RedirectResponse(url=f"/delivery/incidents/{incident_id}", status_code=303)

    return templates.TemplateResponse(
        request,
        "incident_new.html",
        {
            "user": current_user(request),
            "vendors": VENDORS,
            "identity_types": IDENTITY_TYPES,
            "duty_statuses": DUTY_STATUSES,
            "yes_no_values": YES_NO_VALUES,
            "form_data": data,
            "error": error,
        },
        status_code=400,
    )


@router.get("/incidents/{incident_id}")
def incident_detail(incident_id: str, request: Request, redirect=Depends(login_required)):
    if redirect:
        return redirect
    incident = repository.get_incident_event(incident_id)
    if not incident:
        return RedirectResponse(url="/delivery/incidents", status_code=303)
    return templates.TemplateResponse(
        request,
        "incident_detail.html",
        {
            "user": current_user(request),
            "incident": incident,
            "vendor_name": VENDOR_MAP.get(incident.get("vendor"), incident.get("vendor")),
            "incident_status_map": INCIDENT_STATUS_MAP,
            "risk_levels": RISK_LEVELS,
        },
    )


@router.post("/incidents/{incident_id}/risk-level")
def set_incident_risk_level(
    incident_id: str, request: Request, risk_level: str = Form(...), redirect=Depends(admin_required)
):
    """設定風險等級只開放管理員操作（比照補款/假別核准機制）。"""
    if redirect:
        return redirect
    repository.set_incident_risk_level(incident_id, risk_level)
    return RedirectResponse(url=f"/delivery/incidents/{incident_id}", status_code=303)


@router.post("/incidents/{incident_id}/close")
def close_incident(incident_id: str, request: Request, redirect=Depends(admin_required)):
    """結案是單向操作，只開放管理員，沒有重新打開的路徑（比照補款/假別
    核准機制）。"""
    if redirect:
        return redirect
    repository.close_incident_event(incident_id)
    return RedirectResponse(url=f"/delivery/incidents/{incident_id}", status_code=303)


@router.get("/incidents/{incident_id}/edit")
def edit_incident_form(incident_id: str, request: Request, redirect=Depends(admin_required)):
    """修正原始回報內容（例如地點打錯字、經過描述要補充）只開放管理員，
    比照風險等級／結案的權限層級——這份是正式的意外事件記錄，跟車輛
    歷史紀錄（任何登入的同仁都能編輯自己補登的領還紀錄）性質不同。"""
    if redirect:
        return redirect
    incident = repository.get_incident_event(incident_id)
    if not incident:
        return RedirectResponse(url="/delivery/incidents", status_code=303)
    return templates.TemplateResponse(
        request,
        "incident_edit.html",
        {
            "user": current_user(request),
            "incident": incident,
            "vendors": VENDORS,
            "identity_types": IDENTITY_TYPES,
            "duty_statuses": DUTY_STATUSES,
            "yes_no_values": YES_NO_VALUES,
            "error": "",
        },
    )


@router.post("/incidents/{incident_id}/edit")
def edit_incident_submit(
    incident_id: str,
    request: Request,
    vendor: str = Form(...),
    identity_type: str = Form(...),
    personnel_name: str = Form(...),
    occurred_at: str = Form(...),
    location: str = Form(...),
    duty_status: str = Form(...),
    police_called: str = Form(...),
    injury: str = Form(...),
    family_contacted: str = Form(...),
    third_party_involved: str = Form(...),
    description: str = Form(...),
    redirect=Depends(admin_required),
):
    if redirect:
        return redirect
    incident = repository.get_incident_event(incident_id)
    if not incident:
        return RedirectResponse(url="/delivery/incidents", status_code=303)

    data = _incident_form_data(
        vendor,
        identity_type,
        personnel_name,
        occurred_at,
        location,
        duty_status,
        police_called,
        injury,
        family_contacted,
        third_party_involved,
        description,
    )

    error = _validate_incident_form(data)
    if not error:
        repository.update_incident_event(incident_id, data)
        return RedirectResponse(url=f"/delivery/incidents/{incident_id}", status_code=303)

    return templates.TemplateResponse(
        request,
        "incident_edit.html",
        {
            "user": current_user(request),
            "incident": {"id": incident_id, **data},
            "vendors": VENDORS,
            "identity_types": IDENTITY_TYPES,
            "duty_statuses": DUTY_STATUSES,
            "yes_no_values": YES_NO_VALUES,
            "error": error,
        },
        status_code=400,
    )
