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

**「刪除」功能（2026-09-12 新增）**：合約要作廢時，`POST /client-
contracts/{id}/delete` 把 Firestore 那筆紀錄跟 GCS 上存的 Word/PDF 檔案
一起刪掉，能不能刪一樣走 `can_view_submission()` 那套可見範圍判斷（看
得到才能刪），沒有另外設更嚴格的權限——反正看不到的人本來就點不到
刪除連結。刪除沒有回收機制，是真的整筆刪掉，不是標記隱藏。

**第三、四個合約版本「白領代招」「台籍代招」（2026-09-12 新增）**：
`white_collar_referral`／`taiwanese_referral` 這兩個版本的主文結構跟前
兩版（時薪一口價／實支實付）完全不同，也沒有「簽約日期」「撤換條款」
這兩組共用欄位，`CONTRACT_VERSIONS[版本代碼]` 的 `requires_sign_date`／
`requires_severance_clause` 決定這裡的表單驗證要不要擋這些欄位——詳見
services/client_contract_service.py 開頭的版本說明。這兩個版本彼此的
報價欄位形狀很像（都是「自由文字費用＋收費月數上限」），但刻意用不同
的欄位名稱（白領代招是 `fee_amount`／`service_months`，台籍代招是
`referral_fee_percentage`／`referral_service_months`），因為兩組報價
區塊在表單上是同時存在、只是用 CSS 切換顯示/隱藏，欄位名稱共用的話
瀏覽器送出表單時會把兩個同名欄位的值都送出，後端可能抓到看不到的那個
欄位的值。
"""
from datetime import date, datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import RedirectResponse, Response

import client_contract_storage
import platform_accounts
import platform_companies
from platform_templating import templates
from services.client_contract_service import (
    CONTRACT_VERSIONS,
    DEFAULT_CONTRACT_VERSION,
    DEFAULT_REFERRAL_SERVICE_MONTHS,
    DEFAULT_REMIT_DAY,
    DEFAULT_REPLACE_NOTICE_DAYS,
    DEFAULT_SERVICE_MONTHS,
    SEVERANCE_PAYER_OPTIONS,
    can_view_submission,
    convert_docx_to_pdf,
    default_contract_end_date,
    delete_submission,
    get_submission,
    list_visible_submissions,
    render_contract_docx,
    save_submission,
    set_vendor_contract_file,
)
from services.company_registry_lookup import lookup_company
from services.contract_summary_service import build_vendor_lookup, can_view_via_vendor_department_single, viewer_has_any_department_access
from services.project_contract_submit_service import CONTRACT_FILE_ALLOWED_EXTENSIONS, CONTRACT_FILE_MAX_BYTES
from services.vendor_sync import sync_vendor_from_client_contract

router = APIRouter()

MODULE_CODE = "client_contracts"

# 各合約版本各自需要哪些報價欄位才算填完整——時薪一口價要員工薪資+管理費
# 兩個數字，實支實付只要服務費那一格文字，白領代招要服務費金額+收費月數
# 上限，台籍代招要服務費百分比+收費月數上限（跟白領代招欄位名稱刻意不
# 一樣，見 services/client_contract_service.py 開頭說明），四者互不相干，
# 送出時只檢查這次選的版本實際用得到的欄位。
_PRICING_FIELDS_BY_VERSION = {
    "hourly_flat_rate": ["hourly_wage", "management_fee"],
    "actual_paid": ["service_fee"],
    "white_collar_referral": ["fee_amount", "service_months"],
    "taiwanese_referral": ["referral_fee_percentage", "referral_service_months"],
}

# 上傳「廠商版本合約」失敗時的提示文字，見 client_contract_upload_vendor_
# file()。格式/大小限制沿用「專案合約維護」（/project-contracts）已經有
# 的規則（CONTRACT_FILE_ALLOWED_EXTENSIONS／CONTRACT_FILE_MAX_BYTES），
# 不另外重訂一套。
_UPLOAD_VENDOR_FILE_ERROR_MESSAGES = {
    "no_file": "請選擇要上傳的檔案。",
    "bad_format": "廠商版本合約僅支援 PDF 或 WORD (.doc, .docx) 檔案格式，請換一個檔案再試一次。",
    "too_large": "檔案超過 20MB 上限，請換一個檔案較小的檔案。",
    "not_configured": "尚未設定檔案儲存空間，請聯絡系統管理員設定後再試一次。",
    "not_found": "找不到這筆合約紀錄，或您沒有權限操作。",
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
        "fee_amount": record.get("fee_amount", ""),
        "service_months": record.get("service_months", ""),
        "referral_fee_percentage": record.get("referral_fee_percentage", ""),
        "referral_service_months": record.get("referral_service_months", ""),
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
        "default_service_months": DEFAULT_SERVICE_MONTHS,
        "default_referral_service_months": DEFAULT_REFERRAL_SERVICE_MONTHS,
        "default_sign_date": today.isoformat(),
        "default_contract_end_date": default_contract_end_date(today).isoformat(),
    }


@router.get("/client-contracts")
def client_contract_home(
    request: Request, generated: str = "", upload_error: str = "", redirect=Depends(_require_access)
):
    if redirect:
        return redirect
    account = platform_accounts.current_account(request)
    records = list_visible_submissions(account)
    return templates.TemplateResponse(
        request,
        "client_contract_home.html",
        {
            "user": account,
            "records": records,
            "generated": generated,
            "contract_versions": CONTRACT_VERSIONS,
            "show_summary_link": viewer_has_any_department_access(account, build_vendor_lookup()),
            "upload_error": upload_error,
            "upload_error_messages": _UPLOAD_VENDOR_FILE_ERROR_MESSAGES,
        },
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
    fee_amount = (form.get("fee_amount") or "").strip()
    service_months = (form.get("service_months") or "").strip()
    referral_fee_percentage = (form.get("referral_fee_percentage") or "").strip()
    referral_service_months = (form.get("referral_service_months") or "").strip()
    contract_version = (form.get("contract_version") or DEFAULT_CONTRACT_VERSION).strip()
    version_config = CONTRACT_VERSIONS.get(contract_version, {})
    requires_sign_date = version_config.get("requires_sign_date", True)
    requires_severance_clause = version_config.get("requires_severance_clause", True)

    pricing_values = {
        "hourly_wage": hourly_wage,
        "management_fee": management_fee,
        "service_fee": service_fee,
        "fee_amount": fee_amount,
        "service_months": service_months,
        "referral_fee_percentage": referral_fee_percentage,
        "referral_service_months": referral_service_months,
    }

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
    elif requires_sign_date and sign_date is None:
        error = "請填寫簽約日期。"
    elif contract_start_date is None:
        error = "請填寫合約起始日期。"
    elif contract_end_date is None:
        error = "請填寫合約結束日期。"
    elif requires_severance_clause and not replace_notice_days:
        error = "請填寫撤換人員的通知期限（天數）。"
    elif requires_severance_clause and severance_payer not in SEVERANCE_PAYER_OPTIONS:
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
        sign_date=sign_date if requires_sign_date else None,
        contract_start_date=contract_start_date,
        contract_end_date=contract_end_date,
        replace_notice_days=replace_notice_days,
        severance_payer=severance_payer,
        remit_day=remit_day,
        contract_version=contract_version,
        hourly_wage=hourly_wage,
        management_fee=management_fee,
        service_fee=service_fee,
        fee_amount=fee_amount,
        service_months=service_months,
        referral_fee_percentage=referral_fee_percentage,
        referral_service_months=referral_service_months,
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

    # 先同步廠商管理、拿到這次用到的廠商文件 ID，再存合約紀錄本身——這樣
    # 合約紀錄的 vendor_id 才能在同一次送出裡就記下來，不用事後再補一次
    # update（見 services/client_contract_service.py 開頭的說明）。
    vendor_id = sync_vendor_from_client_contract(
        name=party_a["name"],
        tax_id=party_a["tax_id"],
        contract_year=contract_start_date.year,
        company_id=party_b_company_id,
    )

    save_submission(
        submitted_by=account["username"],
        contract_version=contract_version,
        party_a=party_a,
        party_b_company_id=party_b_company_id,
        party_b=party_b,
        sign_date=form.get("sign_date") if requires_sign_date else "",
        contract_start_date=form.get("contract_start_date"),
        contract_end_date=form.get("contract_end_date"),
        replace_notice_days=replace_notice_days,
        severance_payer=severance_payer,
        remit_day=remit_day,
        hourly_wage=hourly_wage,
        management_fee=management_fee,
        service_fee=service_fee,
        fee_amount=fee_amount,
        service_months=service_months,
        referral_fee_percentage=referral_fee_percentage,
        referral_service_months=referral_service_months,
        blob_path=blob_path,
        pdf_blob_path=pdf_blob_path,
        vendor_id=vendor_id,
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


def _can_preview_or_download(account: dict, record: dict) -> bool:
    """預覽／下載額外多開放給「服務部門主管」——跟原本送出人鏈的
    `can_view_submission()` 是「兩者符合一個即可」，不是取代掉原本的
    規則（刪除還是只看 `can_view_submission()`，見
    `client_contract_delete()`）。這樣總表上列出來的紀錄，服務部門主管
    點進去的預覽/下載連結才不會變成「找不到」。"""
    return can_view_submission(account, record) or can_view_via_vendor_department_single(
        account, record.get("vendor_id", "")
    )


@router.get("/client-contracts/{submission_id}/download")
def client_contract_download(submission_id: str, request: Request, redirect=Depends(_require_access)):
    if redirect:
        return redirect
    account = platform_accounts.current_account(request)
    record = get_submission(submission_id)
    if not record or not record.get("blob_path"):
        return Response(status_code=404)
    if not _can_preview_or_download(account, record):
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
    if not _can_preview_or_download(account, record):
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


@router.get("/client-contracts/{submission_id}/vendor-file")
def client_contract_download_vendor_file(submission_id: str, request: Request, redirect=Depends(_require_access)):
    """下載同仁另外上傳的「廠商版本合約」，權限比照公司標準版的下載/
    預覽（見 _can_preview_or_download），不是另外一套規則。"""
    if redirect:
        return redirect
    account = platform_accounts.current_account(request)
    record = get_submission(submission_id)
    if not record or not record.get("vendor_contract_blob_path"):
        return Response(status_code=404)
    if not _can_preview_or_download(account, record):
        return Response(status_code=404)
    content, content_type = client_contract_storage.download_file(record["vendor_contract_blob_path"])
    if content is None:
        return Response(status_code=404)
    filename = record.get("vendor_contract_filename") or f"廠商版合約_{record.get('party_a_name', '')}"
    encoded_filename = quote(filename)
    return Response(
        content=content,
        media_type=content_type or "application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}"},
    )


@router.post("/client-contracts/{submission_id}/upload-vendor-file")
async def client_contract_upload_vendor_file(
    submission_id: str,
    request: Request,
    vendor_file: UploadFile = File(None),
    redirect=Depends(_require_access),
):
    """讓同仁補上傳「廠商自己版本」的合約檔案——有些客戶規定要用廠商指定
    格式的合約書，這個功能不限定廠商，任何一筆合約都可以選擇性上傳；
    不會取代系統自動產生的公司標準版，兩份並存（見 services/client_
    contract_service.py 的 set_vendor_contract_file() 說明）。格式/大小
    驗證沿用「專案合約維護」已經有的規則（CONTRACT_FILE_ALLOWED_
    EXTENSIONS／CONTRACT_FILE_MAX_BYTES）。權限比照下載/預覽，不是只看
    送出人鏈——服務部門主管也能幫忙補上傳廠商簽回來的合約。失敗一律導回
    列表頁、帶對應的錯誤代碼顯示提示訊息，不會讓同仁對著一片空白的畫面
    不知道發生什麼事。"""
    if redirect:
        return redirect
    account = platform_accounts.current_account(request)
    record = get_submission(submission_id)
    if not record or not _can_preview_or_download(account, record):
        return RedirectResponse(url="/client-contracts?upload_error=not_found", status_code=303)

    error_code = ""
    if vendor_file is None or not vendor_file.filename:
        error_code = "no_file"
    elif not vendor_file.filename.lower().endswith(CONTRACT_FILE_ALLOWED_EXTENSIONS):
        error_code = "bad_format"
    elif not client_contract_storage.is_configured():
        error_code = "not_configured"
    else:
        content = await vendor_file.read()
        if len(content) > CONTRACT_FILE_MAX_BYTES:
            error_code = "too_large"
        else:
            blob_path = client_contract_storage.upload_vendor_contract_file(
                content, vendor_file.filename, vendor_file.content_type or "application/octet-stream"
            )
            set_vendor_contract_file(submission_id, blob_path, vendor_file.filename, account["username"])

    redirect_url = "/client-contracts"
    if error_code:
        redirect_url += f"?upload_error={error_code}"
    return RedirectResponse(url=redirect_url, status_code=303)


@router.post("/client-contracts/{submission_id}/delete")
def client_contract_delete(submission_id: str, request: Request, redirect=Depends(_require_access)):
    if redirect:
        return redirect
    account = platform_accounts.current_account(request)
    record = get_submission(submission_id)
    if record and can_view_submission(account, record):
        client_contract_storage.delete_file(record.get("blob_path", ""))
        client_contract_storage.delete_file(record.get("pdf_blob_path", ""))
        client_contract_storage.delete_file(record.get("vendor_contract_blob_path", ""))
        delete_submission(submission_id)
    return RedirectResponse(url="/client-contracts", status_code=303)
