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

**跟「合約產生器」（/client-contracts）的串接（2026-09-12 新增）**：表單
上多一個「從合約產生器帶入」選單，列出目前這個帳號在合約產生器那邊看得到
的紀錄（送出者本人/主管/平台管理員，同一套可見範圍），選了之後前端 JS
會自動帶入廠商名稱（＝甲方公司名稱）、合作類別預設「派遣」、簽約模式依
`client_contract_service.CONTRACT_VERSIONS` 對應，並且用 fetch 把合約
產生器存的 Word 檔抓下來、組成 File 物件塞進原本的檔案上傳欄位（瀏覽器
安全限制不能直接用 JS 幫使用者「選好一個檔案」，但可以用
``DataTransfer`` 把抓下來的檔案塞進 ``<input type="file">``，效果一樣、
使用者也還是可以再手動換成別的檔案）——後端這裡完全不用另外處理檔案，
走的還是原本 multipart 上傳的同一條路。送出成功後，如果表單帶了
`from_client_contract_id`，會呼叫
`client_contract_service.mark_sent_to_project_contracts()` 標記那筆合約
產生器紀錄「已送出」，避免同仁不小心對同一份合約重複送出。
"""
import base64

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import RedirectResponse

import platform_accounts
from platform_templating import templates
from services.client_contract_service import CONTRACT_VERSIONS as CLIENT_CONTRACT_VERSIONS
from services.client_contract_service import can_view_submission as can_view_client_contract
from services.client_contract_service import get_submission as get_client_contract
from services.client_contract_service import list_visible_submissions as list_visible_client_contracts
from services.client_contract_service import mark_sent_to_project_contracts
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


def _dropdown_options_context(account: dict) -> dict:
    return {
        "coop_category_options": COOP_CATEGORY_OPTIONS,
        "contract_mode_options": CONTRACT_MODE_OPTIONS,
        "client_contract_options": _client_contract_options(account),
    }


def _client_contract_options(account: dict) -> list:
    """給表單「從合約產生器帶入」選單用：這個帳號在合約產生器（/client-
    contracts）看得到、而且真的有存到 Word 檔（`blob_path`）的紀錄，
    每筆多算一個 `project_contract_mode`（依 `contract_version` 對應到
    這裡的「簽約模式」選項，見 client_contract_service.CONTRACT_VERSIONS）
    方便前端 JS 直接拿來自動帶入下拉選單。"""
    options = []
    for record in list_visible_client_contracts(account):
        if not record.get("blob_path"):
            continue
        version = CLIENT_CONTRACT_VERSIONS.get(record.get("contract_version"), {})
        options.append({
            "id": record["id"],
            "party_a_name": record.get("party_a_name", ""),
            "created_at": record.get("created_at"),
            "project_contract_mode": version.get("project_contract_mode", ""),
        })
    return options


def _mark_client_contract_sent_if_applicable(account: dict, client_contract_id: str):
    """送出成功後，如果表單有帶 `from_client_contract_id`，標記那筆合約
    產生器紀錄「已送出」。送出前再檢查一次這個帳號看不看得到那筆紀錄
    （跟表單當初列出選單時同一個權限判斷），避免有人竄改表單欄位去標記
    別人的合約紀錄——標記失敗（id 是空的、查無紀錄、沒有權限）都直接
    安靜跳過，不影響這次送出本身已經成功的結果。"""
    if not client_contract_id:
        return
    record = get_client_contract(client_contract_id)
    if not record or not can_view_client_contract(account, record):
        return
    mark_sent_to_project_contracts(client_contract_id)


@router.get("/project-contracts")
def project_contract_form(request: Request, submitted: str = "", submit_unknown: str = "", redirect=Depends(_require_access)):
    if redirect:
        return redirect
    account = platform_accounts.current_account(request)
    context = {
        "user": account,
        "error": "",
        "form": {},
        "submitted": submitted,
        "submit_unknown": submit_unknown,
    }
    context.update(_dropdown_options_context(account))
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
        context.update(_dropdown_options_context(account))
        return templates.TemplateResponse(request, "project_contract_form.html", context, status_code=400)

    if contract_file is None or not contract_file.filename:
        context = {"user": account, "error": "請上傳合約檔案（PDF 或 WORD 格式）。", "form": form_values}
        context.update(_dropdown_options_context(account))
        return templates.TemplateResponse(request, "project_contract_form.html", context, status_code=400)

    filename_lower = contract_file.filename.lower()
    if not filename_lower.endswith(CONTRACT_FILE_ALLOWED_EXTENSIONS):
        context = {
            "user": account,
            "error": "合約檔案僅支援 PDF 或 WORD (.doc, .docx) 檔案格式，請換一個檔案再試一次。",
            "form": form_values,
        }
        context.update(_dropdown_options_context(account))
        return templates.TemplateResponse(request, "project_contract_form.html", context, status_code=400)

    content = await contract_file.read()
    if len(content) > CONTRACT_FILE_MAX_BYTES:
        context = {"user": account, "error": "合約檔案超過 20MB 上限，請換一個檔案較小的檔案。", "form": form_values}
        context.update(_dropdown_options_context(account))
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
        _mark_client_contract_sent_if_applicable(account, (form.get("from_client_contract_id") or "").strip())
        return RedirectResponse(url="/project-contracts?submitted=1", status_code=303)

    if result.get("status") == "unknown":
        return RedirectResponse(url="/project-contracts?submit_unknown=1", status_code=303)

    context = {"user": account, "error": result.get("message") or "送出失敗，請稍後再試。", "form": form_values}
    context.update(_dropdown_options_context(account))
    return templates.TemplateResponse(request, "project_contract_form.html", context, status_code=400)
