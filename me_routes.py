"""我的專區（/me）：登入後每個帳號都自動有的個人化頁面。

跟部門模組（/delivery、/management、/hr）或功能模組不一樣——這裡不是「這個
帳號能不能打開這個頁面」的權限問題（任何登入的帳號都能打開），而是「頁面
裡的資料哪些是這個人可以看的」，由各個小工具自己的服務模組決定資料怎麼
篩選（目前有薪資補款紀錄的讀取，見 services/salary_repayment_service.py；
跟薪資補款的送出表單，見 services/salary_repayment_submit_service.py）。
之後如果要加其他跟個人相關的資訊，比照同樣的寫法各自加一個小工具、在這裡
多組一段資料即可，不需要改動這裡的權限判斷。
"""
import base64

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse

import platform_accounts
from platform_templating import templates
from services.salary_repayment_service import DISPLAY_COLUMNS, get_my_repayment_records
from services.salary_repayment_submit_service import build_payload, submit_salary_repayment

router = APIRouter()

# 送出佐證照片給 GAS 之前先擋一個大小上限，避免同仁誤傳超大原始檔案時，
# 整包 base64 JSON 太大讓請求逾時或被 GAS 那邊拒絕——20MB 是跟其他模組
# （hr/config.py 的 MAX_UPLOAD_BYTES）一致的上限。
_MAX_IMAGE_BYTES = 20 * 1024 * 1024


def _require_login(request: Request):
    if not platform_accounts.current_account(request):
        return RedirectResponse(url="/login?next=/me", status_code=303)
    return None


@router.get("/me")
def my_zone(request: Request, submitted: str = "", redirect=Depends(_require_login)):
    if redirect:
        return redirect
    account = platform_accounts.current_account(request)
    records, error = get_my_repayment_records(account["name"])
    return templates.TemplateResponse(
        request,
        "me_home.html",
        {
            "user": account,
            "salary_repayment_columns": DISPLAY_COLUMNS,
            "salary_repayment_records": records,
            "salary_repayment_error": error,
            "submitted_salary_id": submitted,
        },
    )


def _earnings_and_deductions_from_form(form) -> tuple:
    """把表單裡動態新增的加項/扣項明細列（同一個 name 出現多次）組成
    {名稱: 金額} 的字典。名稱空白或金額打錯（不是數字）的列直接跳過，不當
    成錯誤擋下整張表單——同仁可能就是手滑多按了一次「新增」又沒填。"""

    def _rows_to_dict(label_key: str, amount_key: str) -> dict:
        labels = form.getlist(label_key)
        amounts = form.getlist(amount_key)
        result = {}
        for label, amount in zip(labels, amounts):
            label = (label or "").strip()
            if not label:
                continue
            try:
                result[label] = float(amount)
            except (TypeError, ValueError):
                continue
        return result

    return (
        _rows_to_dict("earning_label", "earning_amount"),
        _rows_to_dict("deduction_label", "deduction_amount"),
    )


@router.get("/me/salary-repayment/new")
def new_salary_repayment_form(request: Request, redirect=Depends(_require_login)):
    if redirect:
        return redirect
    return templates.TemplateResponse(
        request,
        "salary_repayment_form.html",
        {"user": platform_accounts.current_account(request), "error": "", "form": {}},
    )


@router.post("/me/salary-repayment/new")
async def create_salary_repayment_submit(
    request: Request,
    name: str = Form(""),
    id_card: str = Form(""),
    vendor: str = Form(""),
    apply_date: str = Form(""),
    pay_date: str = Form(""),
    deduct_month: str = Form(""),
    compensate_month: str = Form(""),
    is_claimable: str = Form(""),
    pay_type: str = Form(""),
    notes: str = Form(""),
    image: UploadFile = File(None),
    redirect=Depends(_require_login),
):
    if redirect:
        return redirect
    account = platform_accounts.current_account(request)
    form_values = {
        "name": name,
        "id_card": id_card,
        "vendor": vendor,
        "apply_date": apply_date,
        "pay_date": pay_date,
        "deduct_month": deduct_month,
        "compensate_month": compensate_month,
        "is_claimable": is_claimable,
        "pay_type": pay_type,
        "notes": notes,
    }

    if not name.strip() or not apply_date.strip() or not notes.strip():
        return templates.TemplateResponse(
            request,
            "salary_repayment_form.html",
            {"user": account, "error": "「員工姓名」「申請日」「備註說明」都是必填欄位。", "form": form_values},
            status_code=400,
        )

    form = await request.form()
    earnings, deductions = _earnings_and_deductions_from_form(form)

    image_base64, image_filename = "", ""
    if image is not None and image.filename:
        content = await image.read()
        if len(content) > _MAX_IMAGE_BYTES:
            return templates.TemplateResponse(
                request,
                "salary_repayment_form.html",
                {"user": account, "error": "佐證照片超過 20MB 上限，請換一張檔案較小的照片。", "form": form_values},
                status_code=400,
            )
        image_base64 = base64.b64encode(content).decode("ascii")
        image_filename = image.filename

    payload = build_payload(
        applicant_name=account["name"],
        employee_name=name.strip(),
        id_card=id_card.strip(),
        vendor=vendor.strip(),
        apply_date=apply_date.strip(),
        pay_date=pay_date.strip(),
        deduct_month=deduct_month.strip(),
        compensate_month=compensate_month.strip(),
        is_claimable=is_claimable.strip(),
        pay_type=pay_type.strip(),
        notes=notes.strip(),
        earnings=earnings,
        deductions=deductions,
        image_base64=image_base64,
        image_filename=image_filename,
    )
    result = submit_salary_repayment(payload)

    if result.get("status") != "success":
        return templates.TemplateResponse(
            request,
            "salary_repayment_form.html",
            {"user": account, "error": result.get("message") or "送出失敗，請稍後再試。", "form": form_values},
            status_code=400,
        )

    return RedirectResponse(url="/me?submitted=" + result.get("salaryId", ""), status_code=303)
