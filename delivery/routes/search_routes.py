"""查詢人員（/delivery/search）。

2026-09-24 改版後這是配送部**主要的人員清單**：主頁拿掉「選擇廠商」卡片，
應徵名單按「錄取」建立的人（狀態「待報到」）直接出現在這裡。

- 一打開就列出人：預設「待報到＋在職」，待報到排最上面；「放棄報到」「離職」
  要用狀態篩選（或打姓名/電話找特定的人）才看得到
- 篩選：姓名、電話、廠商、狀態（身分證字號不再使用）
- 每一列的按鈕：待報到 →「報到」（選日期）「放棄報到」；在職 →「離職」
  （按鈕打的路由在 routes/vendor_routes.py）
- 頁首：新增人員、批次匯入、合作方式管理（主管），原本都在廠商清單頁上
"""
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request

from delivery import repository
from delivery.auth import current_user, login_required
from delivery.config import (
    PERSONNEL_STATUS_BADGE_CLASS,
    PERSONNEL_STATUS_MAP,
    PERSONNEL_STATUSES,
    VENDOR_MAP,
    VENDORS,
)
from delivery.templating import templates

router = APIRouter()


@router.get("/search")
def search_page(
    request: Request,
    name: str = "",
    phone: str = "",
    vendor: str = "",
    status: str = "",
    msg: str = "",
    err: str = "",
    redirect=Depends(login_required),
):
    if redirect:
        return redirect
    name = name.strip()
    phone = phone.strip()
    vendor = vendor if vendor in VENDOR_MAP else ""
    status = status if status in PERSONNEL_STATUS_MAP else ""

    people = repository.search_personnel(name=name, phone=phone, vendor=vendor, employment_status=status)

    # 名下還有裝備沒還的人：「離職」按鈕的確認視窗要提醒（一次撈全部尚欠，
    # 不要每一列各查一次）
    debt_count = {}
    for row in repository.list_equipment_debt():
        pid = row.get("personnel_id")
        debt_count[pid] = debt_count.get(pid, 0) + int(row.get("quantity_owed") or 0)

    results = []
    for p in people:
        employment_status = repository.personnel_employment_status(p)
        results.append(
            {
                "person": p,
                "doc_statuses": repository.all_document_statuses(p),
                "vendor_name": VENDOR_MAP.get(p.get("vendor"), p.get("vendor")),
                "employment_status": employment_status,
                "employment_status_name": PERSONNEL_STATUS_MAP.get(employment_status, ""),
                "employment_status_badge_class": PERSONNEL_STATUS_BADGE_CLASS.get(employment_status, "badge-pending"),
                "equipment_owed": debt_count.get(p["id"], 0),
            }
        )

    filters = {k: v for k, v in (("name", name), ("phone", phone), ("vendor", vendor), ("status", status)) if v}
    back_url = "/delivery/search" + (f"?{urlencode(filters)}" if filters else "")
    return templates.TemplateResponse(
        request,
        "search.html",
        {
            "user": current_user(request),
            "filter_name": name,
            "filter_phone": phone,
            "filter_vendor": vendor,
            "filter_status": status,
            "has_filters": bool(filters),
            "vendors": VENDORS,
            "personnel_statuses": PERSONNEL_STATUSES,
            "results": results,
            "back_url": back_url,
            "msg": msg,
            "err": err,
        },
    )
