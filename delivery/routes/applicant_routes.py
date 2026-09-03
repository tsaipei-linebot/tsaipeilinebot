from datetime import datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from delivery import repository
from delivery.auth import current_user, login_required
from delivery.config import VENDOR_MAP, VENDORS
from delivery.form_webhook import other_answers
from delivery.templating import templates

router = APIRouter()


@router.get("/applicants")
def applicants_list(request: Request, redirect=Depends(login_required)):
    if redirect:
        return redirect
    rows = []
    for applicant in repository.list_applicants():
        rows.append(
            {
                **applicant,
                "submitted_at": datetime.fromtimestamp(applicant.get("created_at", 0)).strftime("%Y-%m-%d %H:%M"),
                "other_answers": other_answers(applicant.get("answers")),
            }
        )
    return templates.TemplateResponse(
        request, "applicants_list.html", {"user": current_user(request), "applicants": rows, "vendors": VENDORS}
    )


@router.post("/applicants/{applicant_id}/status")
def update_applicant_status(
    applicant_id: str,
    interviewed: str = Form(None),
    withdrawn: str = Form(None),
    redirect=Depends(login_required),
):
    if redirect:
        return redirect
    repository.update_applicant_status(applicant_id, interviewed=bool(interviewed), withdrawn=bool(withdrawn))
    return RedirectResponse(url="/delivery/applicants", status_code=303)


@router.post("/applicants/{applicant_id}/accept")
def accept_applicant(applicant_id: str, request: Request, vendor: str = Form(...), redirect=Depends(login_required)):
    if redirect:
        return redirect
    if vendor not in VENDOR_MAP:
        return RedirectResponse(url="/delivery/applicants", status_code=303)

    applicant = repository.get_applicant(applicant_id)
    if not applicant or applicant.get("converted_personnel_id"):
        return RedirectResponse(url="/delivery/applicants", status_code=303)

    user = current_user(request)
    personnel_id = repository.create_personnel(applicant["name"], "", applicant.get("phone", ""), vendor, user["username"])
    repository.mark_applicant_hired(applicant_id, personnel_id)
    return RedirectResponse(url="/delivery/applicants", status_code=303)
