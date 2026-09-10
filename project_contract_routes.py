"""專案合約維護（/project-contracts）：提報新的專案合作廠商資訊、上傳合約
檔案（Word 或 PDF），送出後轉手給職缺維護表單背後那支 GAS 程式（跟
`/job-listings`、`/me/salary-repayment/new` 同一套「方案 A」做法），由它
負責寄信通知財會/人資、寫入「專案合約紀錄」分頁、把檔案存進 Google Drive
——這裡不重做這些邏輯。

跟 `/job-listings` 不同的是，現有 Netlify 表單裡這個功能本來就只有「提報
新的一筆」，沒有「維護既有」這個模式（GAS 那邊也沒有對應的查詢/編輯端點），
所以這裡也只做提報表單，不多做一個「查詢我提報過的合約」這種現有系統沒有
的功能。

是否看得到這張卡片、能不能進來，由 `/accounts` 的權限設定決定（模組代碼
`project_contracts`）——任何有這個模組權限的帳號（不分「專員」/「主管」
角色，兩者體驗完全一樣）都能進來，跟 `/job-listings` 一樣。
"""
import base64

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import RedirectResponse

import platform_accounts
from platform_templating import templates
from services.project_contract_submit_service import (
    CONTRACT_MODE_OPTIONS,
    CONTRACT_FILE_ALLOWED_EXTENSIONS,
    CONTRACT_FILE_MAX_BYTES,
    COOP_CATEGORY_OPTIONS,
    build_submit_payload,
    submit_project_contract,
)

router = APIRouter()

MODULE_CODE = "project_contracts"

_REQUIRED_TEXT_FIELD_NAMES = ["vendor", "coop_category", "contract_mode", "interview_specialist", "visit_supervisor"]


def _require_access(request: Request):
    account = platform_accounts.current_account(request)
    if not account:
        return RedirectResponse(url=f"/login?next=/{MODULE_CODE.replace('_', '-')}", status_code=303)
    if not platform_accounts.has_module_access(account, MODULE_CODE):
        return RedirectResponse(url="/portal", status_code=303)
    return None


def _dropdown_options_context() -> dict:
    return {
        "coop_category_options": COOP_CATEGORY_OPTIONS,
        "contract_mode_options": CONTRACT_MODE_OPTIONS,
    }


@router.get("/project-contracts")
def project_contract_form(request: Request, submitted: str = "", submit_unknown: str = "", redirect=Depends(_require_access)):
    if redirect:
        return redirect
    context = {
        "user": platform_accounts.current_account(request),
        "error": "",
        "form": {},
        "submitted": submitted,
        "submit_unknown": submit_unknown,
    }
    context.update(_dropdown_options_context())
    return templates.TemplateResponse(request, "project_contract_form.html", context)


@router.post("/project-contracts")
async def project_contract_submit(
    request: Request,
    contract_file: UploadFile = File(None),
    redirect=Depends(_require_access),
):
    if redirect:
        return redirect
    account = platform_accounts.current_account(request)
    form = await request.form()

    text_values = {name: (form.get(name, "") or "").strip() for name in _REQUIRED_TEXT_FIELD_NAMES}
    form_values = dict(text_values)

    missing = [name for name in _REQUIRED_TEXT_FIELD_NAMES if not text_values[name]]
    if missing:
        context = {
            "user": account,
            "error": "還有必填欄位沒有填寫，請檢查表單上標示 * 的欄位。",
            "form": form_values,
        }
        context.update(_dropdown_options_context())
        return templates.TemplateResponse(request, "project_contract_form.html", context, status_code=400)

    if contract_file is None or not contract_file.filename:
        context = {"user": account, "error": "請上傳合約檔案（PDF 或 WORD 格式）。", "form": form_values}
        context.update(_dropdown_options_context())
        return templates.TemplateResponse(request, "project_contract_form.html", context, status_code=400)

    filename_lower = contract_file.filename.lower()
    if not filename_lower.endswith(CONTRACT_FILE_ALLOWED_EXTENSIONS):
        context = {
            "user": account,
            "error": "合約檔案僅支援 PDF 或 WORD (.doc, .docx) 檔案格式，請換一個檔案再試一次。",
            "form": form_values,
        }
        context.update(_dropdown_options_context())
        return templates.TemplateResponse(request, "project_contract_form.html", context, status_code=400)

    content = await contract_file.read()
    if len(content) > CONTRACT_FILE_MAX_BYTES:
        context = {"user": account, "error": "合約檔案超過 20MB 上限，請換一個檔案較小的檔案。", "form": form_values}
        context.update(_dropdown_options_context())
        return templates.TemplateResponse(request, "project_contract_form.html", context, status_code=400)

    file_base64 = base64.b64encode(content).decode("ascii")

    payload = build_submit_payload(
        applicant_name=account["name"],
        vendor=text_values["vendor"],
        coop_category=text_values["coop_category"],
        contract_mode=text_values["contract_mode"],
        interview_specialist=text_values["interview_specialist"],
        visit_supervisor=text_values["visit_supervisor"],
        file_base64=file_base64,
        file_filename=contract_file.filename,
        file_mime_type=contract_file.content_type or "application/octet-stream",
    )
    result = submit_project_contract(payload)

    if result.get("status") == "success":
        return RedirectResponse(url="/project-contracts?submitted=1", status_code=303)

    if result.get("status") == "unknown":
        return RedirectResponse(url="/project-contracts?submit_unknown=1", status_code=303)

    context = {"user": account, "error": result.get("message") or "送出失敗，請稍後再試。", "form": form_values}
    context.update(_dropdown_options_context())
    return templates.TemplateResponse(request, "project_contract_form.html", context, status_code=400)
