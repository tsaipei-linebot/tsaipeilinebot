from datetime import datetime
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from delivery import repository
from delivery.auth import admin_required, current_user, login_required
from delivery.config import (
    APPLICANT_STATUSES,
    DEFAULT_TEST_DRIVE_STATUS,
    SELECTABLE_APPLICANT_STATUSES,
    TEST_DRIVE_REQUIRED_SHOPEE_COOPERATION_TYPES,
    TEST_DRIVE_REQUIRED_VENDORS,
    TEST_DRIVE_STATUS_MAP,
    TEST_DRIVE_STATUSES,
    VENDOR_MAP,
    VENDORS,
)
from delivery.form_webhook import other_answers
from delivery.templating import templates

router = APIRouter()


@router.get("/applicants")
def applicants_list(
    request: Request,
    name: str = "",
    phone: str = "",
    status: str = "",
    vendor: str = "",
    error: str = "",
    redirect=Depends(login_required),
):
    if redirect:
        return redirect
    rows = []
    for applicant in repository.list_applicants(
        name_keyword=name, phone_keyword=phone, status_filter=status, vendor_filter=vendor
    ):
        rows.append(
            {
                **applicant,
                "submitted_at": datetime.fromtimestamp(applicant.get("created_at", 0)).strftime("%Y-%m-%d %H:%M"),
                "other_answers": other_answers(applicant.get("answers")),
                "needs_test_drive": repository.applicant_needs_test_drive(
                    applicant.get("vendor", ""), applicant.get("cooperation_type", "")
                ),
            }
        )
    cooperation_types_by_vendor = {}
    for coop in repository.list_cooperation_types():
        for vendor_code in coop.get("vendors", []):
            cooperation_types_by_vendor.setdefault(vendor_code, []).append(
                {"id": coop["id"], "name": coop.get("name", "")}
            )
    return templates.TemplateResponse(
        request,
        "applicants_list.html",
        {
            "user": current_user(request),
            "applicants": rows,
            "vendors": VENDORS,
            "cooperation_types_by_vendor": cooperation_types_by_vendor,
            "test_drive_required_vendors": TEST_DRIVE_REQUIRED_VENDORS,
            "test_drive_required_shopee_cooperation_types": TEST_DRIVE_REQUIRED_SHOPEE_COOPERATION_TYPES,
            "test_drive_statuses": TEST_DRIVE_STATUSES,
            "all_statuses": APPLICANT_STATUSES,
            "selectable_statuses": SELECTABLE_APPLICANT_STATUSES,
            "filter_name": name,
            "filter_phone": phone,
            "filter_status": status,
            "filter_vendor": vendor,
            "error": error,
        },
    )


@router.post("/applicants/bulk-update")
async def bulk_update_applicants(request: Request, redirect=Depends(login_required)):
    if redirect:
        return redirect
    form = await request.form()

    updates = {}
    filters = {}
    for key, value in form.multi_items():
        if key.startswith("status_"):
            updates.setdefault(key[len("status_"):], {})["status"] = value
        elif key.startswith("vendor_"):
            updates.setdefault(key[len("vendor_"):], {})["vendor"] = value
        elif key.startswith("cooperation_type_"):
            updates.setdefault(key[len("cooperation_type_"):], {})["cooperation_type"] = value
        elif key.startswith("test_drive_"):
            updates.setdefault(key[len("test_drive_"):], {})["test_drive"] = value
        elif key.startswith("note_"):
            updates.setdefault(key[len("note_"):], {})["note"] = value
        elif key == "filter_name" and value:
            filters["name"] = value
        elif key == "filter_phone" and value:
            filters["phone"] = value
        elif key == "filter_status" and value:
            filters["status"] = value
        elif key == "filter_vendor" and value:
            filters["vendor"] = value

    repository.bulk_update_applicants(updates)

    query = urlencode(filters)
    return RedirectResponse(url=f"/delivery/applicants{'?' + query if query else ''}", status_code=303)


@router.post("/applicants/{applicant_id}/accept")
async def accept_applicant(applicant_id: str, request: Request, redirect=Depends(login_required)):
    if redirect:
        return redirect
    form = await request.form()
    vendor = form.get(f"vendor_{applicant_id}", "")
    cooperation_type = form.get(f"cooperation_type_{applicant_id}", "")
    test_drive = form.get(f"test_drive_{applicant_id}", "")
    note = form.get(f"note_{applicant_id}", "")

    if vendor not in VENDOR_MAP:
        return RedirectResponse(url="/delivery/applicants?error=vendor_required", status_code=303)
    coop = repository.get_cooperation_type(cooperation_type)
    if not coop or vendor not in coop.get("vendors", []):
        cooperation_type = ""
    if test_drive not in TEST_DRIVE_STATUS_MAP:
        test_drive = DEFAULT_TEST_DRIVE_STATUS

    applicant = repository.get_applicant(applicant_id)
    if not applicant or applicant.get("converted_personnel_id"):
        return RedirectResponse(url="/delivery/applicants", status_code=303)

    # 先把這次提交當下選的廠商/合作方式/試駕/備註存回應徵紀錄，即使下面的
    # 試駕檢查擋下錄取，同仁剛才選的/打的東西也不會不見、要重填一次。
    repository.bulk_update_applicants(
        {applicant_id: {"vendor": vendor, "cooperation_type": cooperation_type, "test_drive": test_drive, "note": note}}
    )

    if repository.applicant_needs_test_drive(vendor, cooperation_type) and test_drive != "passed":
        return RedirectResponse(url="/delivery/applicants?error=test_drive_required", status_code=303)

    user = current_user(request)
    create_kwargs = {"cooperation_type": cooperation_type} if cooperation_type else {}
    personnel_id = repository.create_personnel(
        applicant["name"], "", applicant.get("phone", ""), vendor, user["username"], **create_kwargs
    )
    repository.mark_applicant_hired(applicant_id, personnel_id)
    return RedirectResponse(url="/delivery/applicants", status_code=303)


@router.post("/applicants/{applicant_id}/delete")
def delete_applicant_submit(applicant_id: str, request: Request, redirect=Depends(admin_required)):
    """整筆刪除一筆應徵紀錄（2026-09-15 新增），只有主管能刪——比照
    vendor_routes.py 的 delete_personnel_submit()，不可逆的刪除動作一律
    走 admin_required，不是任何有配送部權限的帳號都能刪；前端要先跳確認
    對話框。刪除應徵紀錄不影響已經錄取建立的正式人員資料（見
    repository.delete_applicant() 的說明）。"""
    if redirect:
        return redirect
    repository.delete_applicant(applicant_id)
    return RedirectResponse(url="/delivery/applicants", status_code=303)
