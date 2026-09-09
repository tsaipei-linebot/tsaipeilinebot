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
from services.salary_repayment_submit_service import (
    DEDUCTION_FIELDS,
    EARNING_FIELDS,
    IS_CLAIMABLE_OPTIONS,
    PAY_TYPE_OPTIONS,
    build_payload,
    submit_salary_repayment,
    validate_taiwan_id,
)

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
def my_zone(request: Request, submitted: str = "", submit_unknown: str = "", redirect=Depends(_require_login)):
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
            "submit_unknown": submit_unknown,
        },
    )


def _amounts_from_form(form, fields: list, prefix: str) -> dict:
    """把加項/扣項固定十個欄位（field 的 name 屬性是 "{prefix}_{代號}"）
    組成 {英文代號: 金額} 的字典——代號照抄現有 Netlify 表單原始碼實際送給
    GAS 的 earnings/deductions 物件 key（見 EARNING_FIELDS/DEDUCTION_FIELDS
    的說明），不是用中文名稱。同仁沒填或打錯（不是數字）的欄位當成 0，
    不當成錯誤擋下整張表單——這些欄位本來就是選填。"""
    result = {}
    for slug, _label in fields:
        raw = form.get(f"{prefix}_{slug}", "")
        try:
            amount = float(raw) if str(raw).strip() else 0.0
        except (TypeError, ValueError):
            amount = 0.0
        if amount:
            result[slug] = amount
    return result


def _salary_repayment_form_context(user: dict, error: str, form: dict) -> dict:
    return {
        "user": user,
        "error": error,
        "form": form,
        "earning_fields": EARNING_FIELDS,
        "deduction_fields": DEDUCTION_FIELDS,
        "is_claimable_options": IS_CLAIMABLE_OPTIONS,
        "pay_type_options": PAY_TYPE_OPTIONS,
    }


@router.get("/me/salary-repayment/new")
def new_salary_repayment_form(request: Request, redirect=Depends(_require_login)):
    if redirect:
        return redirect
    return templates.TemplateResponse(
        request,
        "salary_repayment_form.html",
        _salary_repayment_form_context(platform_accounts.current_account(request), "", {}),
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

    # 必填欄位跟現有 Netlify 表單原始碼一致（2026-09-09 使用者提供原始碼
    # index_6.html 比對）：申請日/員工姓名/身分證字號/廠商店家/補請款月份/
    # 是否可請款/補款方式/備註說明都是必填，付款日跟扣分鐘月份選填。
    if not all([name.strip(), apply_date.strip(), id_card.strip(), vendor.strip(),
                compensate_month.strip(), is_claimable.strip(), pay_type.strip(), notes.strip()]):
        return templates.TemplateResponse(
            request,
            "salary_repayment_form.html",
            _salary_repayment_form_context(account, "「員工姓名」「身分證字號」「廠商/店家」「申請日」「補請款月份」「是否可請款」「補款方式」「備註說明」都是必填欄位。", form_values),
            status_code=400,
        )

    # 身分證字號一律轉大寫、檢查檢查碼是否合法，照抄現有表單
    # validateTaiwanId() 的行為（見 services/salary_repayment_submit_service.py
    # 的 validate_taiwan_id() 說明），同仁在這裡被擋下來的時機/原因跟原本
    # 表單一致，不是這次才新增的額外限制。
    id_card = id_card.strip().upper()
    form_values["id_card"] = id_card
    if not validate_taiwan_id(id_card):
        return templates.TemplateResponse(
            request,
            "salary_repayment_form.html",
            _salary_repayment_form_context(account, "身分證格式錯誤，請輸入有效的台灣身分證字號。", form_values),
            status_code=400,
        )

    form = await request.form()
    earnings = _amounts_from_form(form, EARNING_FIELDS, "earning")
    deductions = _amounts_from_form(form, DEDUCTION_FIELDS, "deduction")

    image_base64, image_filename = "", ""
    if image is not None and image.filename:
        content = await image.read()
        if len(content) > _MAX_IMAGE_BYTES:
            return templates.TemplateResponse(
                request,
                "salary_repayment_form.html",
                _salary_repayment_form_context(account, "佐證照片超過 20MB 上限，請換一張檔案較小的照片。", form_values),
                status_code=400,
            )
        image_base64 = base64.b64encode(content).decode("ascii")
        image_filename = image.filename

    payload = build_payload(
        applicant_name=account["name"],
        employee_name=name.strip(),
        id_card=id_card,
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

    if result.get("status") == "success":
        return RedirectResponse(url="/me?submitted=" + result.get("salaryId", ""), status_code=303)

    if result.get("status") == "unknown":
        # 這種情況代表「不確定 GAS 到底有沒有處理完這筆申請」（見
        # submit_salary_repayment() 的說明），絕對不能讓同仁停在原本填好
        # 的表單上、誘使他再按一次送出——會有重複申請的風險。改成導去
        # 「我的專區」，讓同仁自己確認這筆申請有沒有出現在紀錄裡。
        return RedirectResponse(url="/me?submit_unknown=1", status_code=303)

    return templates.TemplateResponse(
        request,
        "salary_repayment_form.html",
        _salary_repayment_form_context(account, result.get("message") or "送出失敗，請稍後再試。", form_values),
        status_code=400,
    )
