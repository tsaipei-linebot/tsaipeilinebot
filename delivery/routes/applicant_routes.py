from datetime import datetime
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from delivery import repository
from delivery.auth import current_user, login_required
from delivery.config import APPLICANT_STATUSES, SELECTABLE_APPLICANT_STATUSES, VENDOR_MAP, VENDORS
from delivery.form_webhook import other_answers
from delivery.templating import templates

router = APIRouter()


@router.get("/applicants")
def applicants_list(
    request: Request,
    name: str = "",
    phone: str = "",
    status: str = "",
    redirect=Depends(login_required),
):
    if redirect:
        return redirect
    rows = []
    for applicant in repository.list_applicants(name_keyword=name, phone_keyword=phone, status_filter=status):
        rows.append(
            {
                **applicant,
                "submitted_at": datetime.fromtimestamp(applicant.get("created_at", 0)).strftime("%Y-%m-%d %H:%M"),
                "other_answers": other_answers(applicant.get("answers")),
            }
        )
    return templates.TemplateResponse(
        request,
        "applicants_list.html",
        {
            "user": current_user(request),
            "applicants": rows,
            "vendors": VENDORS,
            "all_statuses": APPLICANT_STATUSES,
            "selectable_statuses": SELECTABLE_APPLICANT_STATUSES,
            "filter_name": name,
            "filter_phone": phone,
            "filter_status": status,
        },
    )


@router.post("/applicants/bulk-status")
async def bulk_update_status(request: Request, redirect=Depends(login_required)):
    if redirect:
        return redirect
    form = await request.form()

    status_by_id = {}
    filters = {}
    for key, value in form.multi_items():
        if key.startswith("status_"):
            status_by_id[key[len("status_"):]] = value
        elif key == "filter_name" and value:
            filters["name"] = value
        elif key == "filter_phone" and value:
            filters["phone"] = value
        elif key == "filter_status" and value:
            filters["status"] = value

    repository.bulk_set_applicant_status(status_by_id)

    query = urlencode(filters)
    return RedirectResponse(url=f"/delivery/applicants{'?' + query if query else ''}", status_code=303)


@router.post("/applicants/{applicant_id}/accept")
async def accept_applicant(applicant_id: str, request: Request, redirect=Depends(login_required)):
    if redirect:
        return redirect
    form = await request.form()
    vendor = form.get(f"vendor_{applicant_id}", "")

    if vendor not in VENDOR_MAP:
        return RedirectResponse(url="/delivery/applicants", status_code=303)

    applicant = repository.get_applicant(applicant_id)
    if not applicant or applicant.get("converted_personnel_id"):
        return RedirectResponse(url="/delivery/applicants", status_code=303)

    user = current_user(request)
    personnel_id = repository.create_personnel(applicant["name"], "", applicant.get("phone", ""), vendor, user["username"])
    repository.mark_applicant_hired(applicant_id, personnel_id)
    return RedirectResponse(url="/delivery/applicants", status_code=303)
