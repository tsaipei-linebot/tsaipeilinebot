"""派遣契約產生器（/dispatch-contracts）：詳細背景、套版邏輯、``${...}``
系統公式為什麼完全不處理，都寫在 services/dispatch_contract_service.py
開頭的說明，這裡只負責表單頁面、送出後套版產生 Word 檔、下載。

是否看得到這張卡片、能不能進來，由 `/accounts` 的權限設定決定（模組代碼
`dispatch_contracts`）——這裡不分「專員」/「主管」角色，兩者都能建立契約，
體驗完全一樣。但看得到「哪些紀錄」有另外一層限制（2026-09-11 依使用者
要求收斂權限）：只有送出者本人、送出者的主管（`platform_accounts` 的
`manager_usernames`）、或是全平台管理員（`is_platform_admin`）看得到某筆
紀錄，其他有這個模組權限但跟這筆紀錄無關的帳號看不到——列表頁、下載、
預覽三個地方都要走 `services.dispatch_contract_service.can_view_submission()`
這同一個判斷，避免只擋列表頁、卻能用網址直接下載/預覽別人紀錄的漏洞。

**「刪除」功能（2026-09-12 新增）**：契約要作廢時，`POST /dispatch-
contracts/{id}/delete` 把 Firestore 那筆紀錄跟 GCS 上存的 Word/PDF 檔案
一起刪掉，能不能刪一樣走 `can_view_submission()` 那套可見範圍判斷（看
得到才能刪），沒有另外設更嚴格的權限，跟 `client_contract_routes.py`
的刪除功能是同一套做法。刪除沒有回收機制，是真的整筆刪掉，不是標記
隱藏。
"""
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, Response

import dispatch_contract_storage
import platform_accounts
from platform_templating import templates
from services.dispatch_contract_service import (
    CLAUSE_DEFAULTS,
    CLAUSE_LABELS,
    CLAUSE_ORDER,
    SHIFT_COLUMNS,
    build_shift_rows,
    can_view_submission,
    convert_docx_to_pdf,
    delete_submission,
    get_submission,
    list_recent_client_names,
    list_visible_submissions,
    render_contract_docx,
    save_submission,
)

router = APIRouter()

MODULE_CODE = "dispatch_contracts"


def _require_access(request: Request):
    account = platform_accounts.current_account(request)
    if not account:
        return RedirectResponse(url=f"/login?next=/{MODULE_CODE.replace('_', '-')}", status_code=303)
    if not platform_accounts.has_module_access(account, MODULE_CODE):
        return RedirectResponse(url="/portal", status_code=303)
    return None


def _form_context(*, user: dict, error: str = "", form: dict = None, shift_rows: list = None) -> dict:
    return {
        "user": user,
        "error": error,
        "form": form or {},
        "shift_columns": SHIFT_COLUMNS,
        "shift_rows": shift_rows or [{}, {}],
        "clause_order": CLAUSE_ORDER,
        "clause_labels": CLAUSE_LABELS,
        "clause_defaults": CLAUSE_DEFAULTS,
        "recent_client_names": list_recent_client_names(),
    }


@router.get("/dispatch-contracts")
def dispatch_contract_home(request: Request, generated: str = "", redirect=Depends(_require_access)):
    if redirect:
        return redirect
    account = platform_accounts.current_account(request)
    records = list_visible_submissions(account)
    return templates.TemplateResponse(
        request,
        "dispatch_contract_home.html",
        {"user": account, "records": records, "generated": generated},
    )


@router.get("/dispatch-contracts/new")
def dispatch_contract_new_form(request: Request, redirect=Depends(_require_access)):
    if redirect:
        return redirect
    account = platform_accounts.current_account(request)
    return templates.TemplateResponse(request, "dispatch_contract_form.html", _form_context(user=account))


def _parse_shift_rows(form) -> list:
    titles = form.getlist("shift_title")
    hours = form.getlist("shift_hours")
    wages = form.getlist("shift_wage")
    bonuses = form.getlist("shift_bonus")
    overtimes = form.getlist("shift_overtime")
    count = max(len(titles), len(hours), len(wages), len(bonuses), len(overtimes))
    rows = []
    for i in range(count):
        rows.append({
            "title": titles[i] if i < len(titles) else "",
            "hours": hours[i] if i < len(hours) else "",
            "wage": wages[i] if i < len(wages) else "",
            "bonus": bonuses[i] if i < len(bonuses) else "",
            "overtime": overtimes[i] if i < len(overtimes) else "",
        })
    return rows


@router.post("/dispatch-contracts/new")
async def dispatch_contract_submit(request: Request, redirect=Depends(_require_access)):
    if redirect:
        return redirect
    account = platform_accounts.current_account(request)
    form = await request.form()

    client_name = (form.get("client_name") or "").strip()
    work_address = (form.get("work_address") or "").strip()
    work_content = (form.get("work_content") or "").strip()
    pay_cycle = (form.get("pay_cycle") or "").strip()
    enabled_columns = form.getlist("enabled_columns")
    raw_shift_rows = _parse_shift_rows(form)
    clauses = {key: (form.get(f"clause_{key}") or "").strip() for key in CLAUSE_ORDER}

    form_values = {
        "client_name": client_name,
        "work_address": work_address,
        "work_content": work_content,
        "pay_cycle": pay_cycle,
        "enabled_columns": enabled_columns,
    }

    error = ""
    if not client_name:
        error = "請填寫客戶名稱。"
    elif not work_address:
        error = "請填寫工作地址。"
    elif not work_content:
        error = "請填寫工作內容。"
    elif not enabled_columns:
        error = "班別薪資表格請至少勾選一個欄位。"

    shifts = build_shift_rows(raw_shift_rows, enabled_columns) if not error else []
    if not error and not shifts:
        error = "班別薪資表格請至少填寫一列有資料的班別。"

    if error:
        context = _form_context(user=account, error=error, form=form_values, shift_rows=raw_shift_rows or None)
        # 條文段落如果同仁有動過，錯誤重新顯示時也要保留剛剛編輯的內容，
        # 不能整段被 clause_defaults 蓋回去。
        context["form"]["clauses"] = clauses
        return templates.TemplateResponse(request, "dispatch_contract_form.html", context, status_code=400)

    docx_bytes = render_contract_docx(
        work_address=work_address,
        work_content=work_content,
        pay_cycle=pay_cycle,
        shifts=shifts,
        clauses=clauses,
    )

    filename = f"派遣契約_{client_name}.docx"
    blob_path = ""
    pdf_blob_path = ""
    if dispatch_contract_storage.is_configured():
        blob_path = dispatch_contract_storage.upload_contract_docx(docx_bytes, filename)
        # PDF 轉檔失敗不影響這次送出——見 convert_docx_to_pdf() 的說明，
        # 失敗時回傳 None，這裡就直接不存 PDF，Word 檔案跟紀錄照樣正常。
        pdf_bytes = convert_docx_to_pdf(docx_bytes)
        if pdf_bytes:
            pdf_blob_path = dispatch_contract_storage.upload_contract_pdf(
                pdf_bytes, f"派遣契約_{client_name}.pdf"
            )

    save_submission(
        submitted_by=account["username"],
        client_name=client_name,
        work_address=work_address,
        work_content=work_content,
        pay_cycle=pay_cycle,
        enabled_columns=enabled_columns,
        shifts=shifts,
        clauses=clauses,
        blob_path=blob_path,
        pdf_blob_path=pdf_blob_path,
    )

    encoded_filename = quote(filename)
    return Response(
        content=docx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}"},
    )


@router.get("/dispatch-contracts/{submission_id}/download")
def dispatch_contract_download(submission_id: str, request: Request, redirect=Depends(_require_access)):
    if redirect:
        return redirect
    account = platform_accounts.current_account(request)
    record = get_submission(submission_id)
    if not record or not record.get("blob_path"):
        return Response(status_code=404)
    if not can_view_submission(account, record):
        return Response(status_code=404)
    content, content_type = dispatch_contract_storage.download_file(record["blob_path"])
    if content is None:
        return Response(status_code=404)
    filename = f"派遣契約_{record.get('client_name', '')}.docx"
    encoded_filename = quote(filename)
    return Response(
        content=content,
        media_type=content_type or "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}"},
    )


@router.get("/dispatch-contracts/{submission_id}/preview")
def dispatch_contract_preview(submission_id: str, request: Request, redirect=Depends(_require_access)):
    """回傳 PDF 讓瀏覽器用內建的 PDF 檢視器直接顯示（``inline``，不是強制
    下載）——沒有轉檔成功的紀錄（``pdf_blob_path`` 是空字串）回傳 404，
    列表頁只會在有 ``pdf_blob_path`` 時才顯示「預覽」連結，見
    dispatch_contract_home.html。"""
    if redirect:
        return redirect
    account = platform_accounts.current_account(request)
    record = get_submission(submission_id)
    if not record or not record.get("pdf_blob_path"):
        return Response(status_code=404)
    if not can_view_submission(account, record):
        return Response(status_code=404)
    content, content_type = dispatch_contract_storage.download_file(record["pdf_blob_path"])
    if content is None:
        return Response(status_code=404)
    filename = f"派遣契約_{record.get('client_name', '')}.pdf"
    encoded_filename = quote(filename)
    return Response(
        content=content,
        media_type=content_type or "application/pdf",
        headers={"Content-Disposition": f"inline; filename*=UTF-8''{encoded_filename}"},
    )


@router.post("/dispatch-contracts/{submission_id}/delete")
def dispatch_contract_delete(submission_id: str, request: Request, redirect=Depends(_require_access)):
    if redirect:
        return redirect
    account = platform_accounts.current_account(request)
    record = get_submission(submission_id)
    if record and can_view_submission(account, record):
        dispatch_contract_storage.delete_file(record.get("blob_path", ""))
        dispatch_contract_storage.delete_file(record.get("pdf_blob_path", ""))
        delete_submission(submission_id)
    return RedirectResponse(url="/dispatch-contracts", status_code=303)
