from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from delivery import repository
from delivery.auth import current_user, login_required
from delivery.config import VENDORS
from delivery.templating import templates

router = APIRouter()


@router.get("/function/repayment")
def repayment_form(request: Request, redirect=Depends(login_required)):
    if redirect:
        return redirect
    return templates.TemplateResponse(
        request,
        "repayment_form.html",
        {
            "user": current_user(request),
            "vendors": VENDORS,
            "records": repository.list_recent_repayments(),
            "error": None,
        },
    )


@router.post("/function/repayment")
def repayment_submit(
    request: Request,
    vendor: str = Form(...),
    personnel_name: str = Form(...),
    amount: str = Form(...),
    reason: str = Form(""),
    occurred_date: str = Form(...),
    redirect=Depends(login_required),
):
    if redirect:
        return redirect
    user = current_user(request)
    try:
        amount_value = float(amount)
    except ValueError:
        return templates.TemplateResponse(
            request,
            "repayment_form.html",
            {
                "user": user,
                "vendors": VENDORS,
                "records": repository.list_recent_repayments(),
                "error": "金額格式錯誤，請輸入數字",
            },
            status_code=400,
        )

    repository.create_repayment(
        personnel_id="",
        personnel_name=personnel_name,
        vendor=vendor,
        amount=amount_value,
        reason=reason,
        occurred_date=occurred_date,
        created_by=user["username"],
    )
    return RedirectResponse(url="/delivery/function/repayment", status_code=303)
