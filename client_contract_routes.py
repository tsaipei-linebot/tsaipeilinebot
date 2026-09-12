"""合約產生器（/client-contracts）：詳細背景、套版邏輯、可見範圍限制都寫在
services/client_contract_service.py 開頭的說明，這裡只負責表單頁面、甲方
公司登記資料查詢的 AJAX 端點、送出後套版產生 Word 檔、下載/預覽。

是否看得到這張卡片、能不能進來，由 `/accounts` 的權限設定決定（模組代碼
`client_contracts`）。看得到「哪些紀錄」另外有一層限制：只有送出者本人、
送出者的主管、或是全平台管理員看得到某筆紀錄，列表頁、下載、預覽三個
地方都走 `services.client_contract_service.can_view_submission()` 這同一個
判斷，避免只擋列表頁、卻能用網址直接下載/預覽別人紀錄的漏洞——跟
`dispatch_contract_routes.py` 是同一套做法。

**「複製」功能（2026-09-12 新增）**：`GET /client-contracts/new` 多接受
一個 `duplicate_from` 查詢參數，帶某一筆看得到的紀錄 id，就會把那筆紀錄
的全部欄位（含合約期間、費率）預先帶入新增表單——給年底要用同樣條件
續下一年度合約的情境用，日期同仁還是要自己改成新的年度，這裡不會自動
幫忙加一年，避免猜錯使用者實際要的日期。
"""
from datetime import date, datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, Response

import client_contract_storage
import platform_accounts
import platform_companies
from platform_templating import templates
from services.client_contract_service import (
    CONTRACT_VERSIONS,
    DEFAULT_CONTRACT_VERSION,
    DEFAULT_REMIT_DAY,
    DEFAULT_REPLACE_NOTICE_DAYS,
    SEVERANCE_PAYER_OPTIONS,
    can_view_submission,
    convert_docx_to_pdf,
    default_contract_end_date,
    get_submission,
    list_visible_submissions,
    render_contract_docx,
    save_submission,
)
from services.company_registry_lookup import lookup_company

router = APIRouter()

MODULE_CODE = "client_contracts"

# 各合約版本各自需要哪些報價欄位才算填完整——時薪一口價要員工薪資+管理費
# 兩個數字，實支實付只要服務費那一格文字，兩者互不相干，送出時只檢查
# 這次選的版本實際用得到的欄位。
_PRICING_FIELDS_BY_VERSION = {
    "hourly_flat_rate": ["hourly_wage", "management_fee"],
    "actual_paid": ["service_fee"],
}


def _require_access(request: Request):
    account = platform_accounts.current_account(request)
    if not account:
        return RedirectResponse(url=f"/login?next=/{MODULE_CODE.replace('_', '-')}", status_code=303)
    if not platform_accounts.has_module_access(account, MODULE_CODE):
        return RedirectResponse(url="/portal", status_code=303)
    return None


def _parse_date(value: str):
    value = (value or "").strip()
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _build_filename(party_a_name: str, contract_year: int, ext: str) -> str:
    """檔名（包含存進 GCS 的名稱）都要帶「合約年」——2026-09-12 使用者
    要求，用合約起始日期的年份，不是同仁實際送出表單當下的年份（例如
    年底先產生下一年度的續約合約，檔名要用下一年度，不是今年）。"""
    return f"合約_{party_a_name}_{contract_year}.{ext}"


def _duplicate_form_values(record: dict) -> dict:
    """「複製」功能用：把一筆既有紀錄轉成表單預填用的 dict，key 要跟表單
    欄位的 name 屬性一致。"""
    return {
        "party_a_name": record.get("party_a_name", ""),
        "party_a_representative": record.get("party_a_representative", ""),
        "party_a_address": record.get("party_a_address", ""),
        "party_a_tax_id": record.get("party_a_tax_id", ""),
        "party_a_phone": record.get("party_a_phone", ""),
        "party_b_company_id": record.get("party_b_company_id", ""),
        "sign_date": record.get("sign_date", ""),
        "contract_start_date": record.get("contract_start_date", ""),
        "contract_end_date": record.get("contract_end_date", ""),
        "replace_notice_days": record.get("replace_notice_days", ""),
        "severance_payer": record.get("severance_payer", ""),
        "remit_day": record.get("remit_day", ""),
        "hourly_wage": record.get("hourly_wage", ""),
        "management_fee": record.get("management_fee", ""),
        "service_fee": record.get("service_fee", ""),
        "contract_version": record.get("contract_version", DEFAULT_CONTRACT_VERSION),
    }


def _form_context(*, user: dict, error: str = "", form: dict = None) -> dict:
    today = date.today()
    return {
        "user": user,
        "error": error,
        "form": form or {},
        "companies": platform_companies.list_companies(),
        "contract_versions": CONTRACT_VERSIONS,
        "default_contract_version": DEFAULT_CONTRACT_VERSION,
        "severance_payer_options": SEVERANCE_PAYER_OPTIONS,
        "default_replace_notice_days": DEFAULT_REPLACE_NOTICE_DAYS,
        "default_remit_day": DEFAULT_REMIT_DAY,
        "default_sign_date": today.isoformat(),
        "default_contract_end_date": default_contract_end_date(today).isoformat(),
    }


@router.get("/client-contracts")
def client_contract_home(request: Request, generated: str = "", redirect=Depends(_require_access)):
    if redirect:
        return redirect
    account = platform_accounts.current_account(request)
    records = list_visible_submissions(account)
    return templates.TemplateResponse(
        request,
        "client_contract_home.html",
        {"user": account, "records": records, "generated": generated, "contract_versions": CONTRACT_VERSIONS},
    )


@router.get("/client-contracts/new")
def client_contract_new_form(request: Request, duplicate_from: str = "", redirect=Depends(_require_access)):
    if redirect:
        return redirect
    account = platform_accounts.current_account(request)
    form_values = None
    if duplicate_from:
        source_record = get_submission(duplicate_from)
        if source_record and can_view_submission(account, source_record):
            form_values = _duplicate_form_values(source_record)
    return templates.TemplateResponse(request, "client_contract_form.html", _form_context(user=account, form=form_values))


@router.get("/client-contracts/company-lookup")
def client_contract_company_lookup(q: str, request: Request):
    """給表單甲方欄位「輸入公司名稱或統一編號自動查詢」用的 AJAX 端點——
    回傳 JSON，不是完整頁面。沒登入/沒模組權限一律回傳查無資料，不透露
    任何錯誤細節，也不會讓沒權限的人把這裡當成任意查詢外部公司資料的
    工具。查詢本身的容錯設計見 services/company_registry_lookup.py。"""
    account = platform_accounts.current_account(request)
    if not account or not platform_accounts.has_module_access(account, MODULE_CODE):
        return {"found": False}
    result = lookup_company(q)
    if not result:
        return {"found": False}
    return {"found": True, **result}


@router.post("/client-contracts/new")
async def client_contract_submit(request: Request, redirect=Depends(_require_access)):
    if redirect:
        return redirect
    account = platform_accounts.current_account(request)
    form = await request.form()

    party_a = {
        "name": (form.get("party_a_name") or "").strip(),
        "representative": (form.get("party_a_representative") or "").strip(),
        "address": (form.get("party_a_address") or "").strip(),
        "tax_id": (form.get("party_a_tax_id") or "").strip(),
        "phone": (form.get("party_a_phone") or "").strip(),
    }
    party_b_company_id = (form.get("party_b_company_id") or "").strip()
    sign_date = _parse_date(form.get("sign_date"))
    contract_start_date = _parse_date(form.get("contract_start_date"))
    contract_end_date = _parse_date(form.get("contract_end_date"))
    replace_notice_days = (form.get("replace_notice_days") or "").strip()
    severance_payer = (form.get("severance_payer") or "").strip()
    remit_day = (form.get("remit_day") or "").strip()
    hourly_wage = (form.get("hourly_wage") or "").strip()
    management_fee = (form.get("management_fee") or "").strip()
    service_fee = (form.get("service_fee") or "").strip()
    contract_version = (form.get("contract_version") or DEFAULT_CONTRACT_VERSION).strip()

    pricing_values = {"hourly_wage": hourly_wage, "management_fee": management_fee, "service_fee": service_fee}

    form_values = {
        **party_a,
        "party_b_company_id": party_b_company_id,
        "sign_date": form.get("sign_date") or "",
        "contract_start_date": form.get("contract_start_date") or "",
        "contract_end_date": form.get("contract_end_date") or "",
        "replace_notice_days": replace_notice_days,
        "severance_payer": severance_payer,
        "remit_day": remit_day,
        "contract_version": contract_version,
        **pricing_values,
    }

    party_b_company = platform_companies.get_company(party_b_company_id) if party_b_company_id else None
    required_pricing_fields = _PRICING_FIELDS_BY_VERSION.get(contract_version, [])

    error = ""
    if any(not party_a[field] for field in ["name", "representative", "address", "tax_id"]):
        error = "請填寫甲方（客戶公司）的公司名稱、代表人、地址、統一編號。"
    elif not party_b_company:
        error = "請選擇乙方（材霈旗下派遣公司）。"
    elif sign_date is None:
        error = "請填寫簽約日期。"
    elif contract_start_date is None:
        error = "請填寫合約起始日期。"
    elif contract_end_date is None:
        error = "請填寫合約結束日期。"
    elif not replace_notice_days:
        error = "請填寫撤換人員的通知期限（天數）。"
    elif severance_payer not in SEVERANCE_PAYER_OPTIONS:
        error = "請選擇資遣費用及預告工資由甲方或乙方負擔。"
    elif not remit_day:
        error = "請填寫匯款截止日。"
    elif contract_version not in CONTRACT_VERSIONS:
        error = "合約版本代碼不合法，請重新整理頁面再試一次。"
    elif any(not pricing_values[field] for field in required_pricing_fields):
        error = "請填寫報價欄位。"

    if error:
        context = _form_context(user=account, error=error, form=form_values)
        return templates.TemplateResponse(request, "client_contract_form.html", context, status_code=400)

    party_b = {
        "name": party_b_company["name"],
        "representative": party_b_company["responsible_person"],
        "address": party_b_company["address"],
        "tax_id": party_b_company["tax_id"],
        "phone": party_b_company["phone"],
    }

    docx_bytes = render_contract_docx(
        party_a=party_a,
        party_b=party_b,
        sign_date=sign_date,
        contract_start_date=contract_start_date,
        contract_end_date=contract_end_date,
        replace_notice_days=replace_notice_days,
        severance_payer=severance_payer,
        remit_day=remit_day,
        contract_version=contract_version,
        hourly_wage=hourly_wage,
        management_fee=management_fee,
        service_fee=service_fee,
    )

    filename = _build_filename(party_a["name"], contract_start_date.year, "docx")
    blob_path = ""
    pdf_blob_path = ""
    if client_contract_storage.is_configured():
        blob_path = client_contract_storage.upload_contract_docx(docx_bytes, filename)
        # PDF 轉檔失敗不影響這次送出，見 convert_docx_to_pdf() 的說明。
        pdf_bytes = convert_docx_to_pdf(docx_bytes)
        if pdf_bytes:
            pdf_filename = _build_filename(party_a["name"], contract_start_date.year, "pdf")
            pdf_blob_path = client_contract_storage.upload_contract_pdf(pdf_bytes, pdf_filename)

    save_submission(
        submitted_by=account["username"],
        contract_version=contract_version,
        party_a=party_a,
        party_b_company_id=party_b_company_id,
        party_b=party_b,
        sign_date=form.get("sign_date"),
        contract_start_date=form.get("contract_start_date"),
        contract_end_date=form.get("contract_end_date"),
        replace_notice_days=replace_notice_days,
        severance_payer=severance_payer,
        remit_day=remit_day,
        hourly_wage=hourly_wage,
        management_fee=management_fee,
        service_fee=service_fee,
        blob_path=blob_path,
        pdf_blob_path=pdf_blob_path,
    )

    encoded_filename = quote(filename)
    return Response(
        content=docx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}"},
    )


def _contract_year(record: dict) -> str:
    """從紀錄存的 contract_start_date（"YYYY-MM-DD" 字串）取出年份，給
    下載/預覽檔名用；欄位缺漏或格式異常（理論上不會發生，保險起見）就
    留空，檔名還是能正常組出來，只是少了年份那一段。"""
    value = record.get("contract_start_date") or ""
    return value[:4] if len(value) >= 4 and value[:4].isdigit() else ""


@router.get("/client-contracts/{submission_id}/download")
def client_contract_download(submission_id: str, request: Request, redirect=Depends(_require_access)):
    if redirect:
        return redirect
    account = platform_accounts.current_account(request)
    record = get_submission(submission_id)
    if not record or not record.get("blob_path"):
        return Response(status_code=404)
    if not can_view_submission(account, record):
        return Response(status_code=404)
    content, content_type = client_contract_storage.download_file(record["blob_path"])
    if content is None:
        return Response(status_code=404)
    filename = f"合約_{record.get('party_a_name', '')}_{_contract_year(record)}.docx"
    encoded_filename = quote(filename)
    return Response(
        content=content,
        media_type=content_type or "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}"},
    )


@router.get("/client-contracts/{submission_id}/preview")
def client_contract_preview(submission_id: str, request: Request, redirect=Depends(_require_access)):
    if redirect:
        return redirect
    account = platform_accounts.current_account(request)
    record = get_submission(submission_id)
    if not record or not record.get("pdf_blob_path"):
        return Response(status_code=404)
    if not can_view_submission(account, record):
        return Response(status_code=404)
    content, content_type = client_contract_storage.download_file(record["pdf_blob_path"])
    if content is None:
        return Response(status_code=404)
    filename = f"合約_{record.get('party_a_name', '')}_{_contract_year(record)}.pdf"
    encoded_filename = quote(filename)
    return Response(
        content=content,
        media_type=content_type or "application/pdf",
        headers={"Content-Disposition": f"inline; filename*=UTF-8''{encoded_filename}"},
    )
