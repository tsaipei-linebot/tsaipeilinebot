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

**廠商管理連動（2026-09-13 新增）**：「客戶名稱」欄位的自動完成清單
（`_client_name_suggestions()`）優先列出廠商管理（`/vendors`）裡的
廠商名稱，再補上這個模組自己歷史紀錄裡用過的名稱；送出成功後呼叫
`services/vendor_sync.py` 的 `sync_vendor_from_dispatch_contract()`，
廠商管理裡沒有同名紀錄才自動補一筆（只有名稱），詳見該檔案開頭的
說明。

**連動指定的合約（2026-09-14 新增）**：表單多了「選擇對應的合約」下拉
選單，選了某一份合約產生器的紀錄後，客戶名稱、`vendor_id` 都直接沿用
那份合約的（見 `services/dispatch_contract_service.py` 開頭的說明），
這種情況下**不會**呼叫 `sync_vendor_from_dispatch_contract()`——廠商資料
已經確定是哪一筆了，不用再靠名稱猜。沒有選合約時才會走原本手動輸入
客戶名稱、呼叫 `sync_vendor_from_dispatch_contract()` 的路徑。

**下拉選單能不能選到，看的是部門，不是送出人（2026-09-14 修正）**：
一開始這裡是照「跟 `/client-contracts` 首頁同一套可見範圍」（送出人本人
／送出人的主管）過濾下拉選單，但這樣如果合約跟契約是完全不同、也沒有
主管/部屬關係的兩個人各自負責，契約端的同仁會在下拉選單裡完全看不到
那份合約，選不到——不符合實際上「業務出合約、另一位同仁出契約」的
作業情境。改成用 `services/contract_summary_service.py` 的
`viewer_can_link_contract_vendor()` 判斷：只要這個帳號的部門有被勾在
那份合約連到的廠商紀錄的「服務部門」裡就能選（**不要求主管職級**，
一般同仁本來就常常是實際送出契約的人），全平台管理員永遠能選全部。
選了之後契約只會帶走客戶名稱跟廠商 ID，不會因此看到合約本身的價格、
統編等完整內容。**這代表合約送出後，要先到廠商管理把服務部門勾好，
契約端的同仁才有辦法在下拉選單選到**——這是延續「服務部門要同仁自己
維護」的既有設計，不是新的限制。送出時也會重新驗證一次，避免有人把
網址列/表單裡的 id 換成部門不相干的合約硬送。
"""
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, Response

import dispatch_contract_storage
import platform_accounts
import platform_vendors
from platform_templating import templates
from services.client_contract_service import (
    get_submission as get_client_contract_submission,
    list_submissions as list_all_client_contracts,
)
from services.contract_summary_service import (
    build_vendor_lookup,
    can_view_via_vendor_department_single,
    viewer_can_link_contract_vendor,
    viewer_has_any_department_access,
)
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
from services.vendor_sync import sync_vendor_from_dispatch_contract

router = APIRouter()

MODULE_CODE = "dispatch_contracts"


def _require_access(request: Request):
    account = platform_accounts.current_account(request)
    if not account:
        return RedirectResponse(url=f"/login?next=/{MODULE_CODE.replace('_', '-')}", status_code=303)
    if not platform_accounts.has_module_access(account, MODULE_CODE):
        return RedirectResponse(url="/portal", status_code=303)
    return None


def _client_name_suggestions() -> list:
    """客戶名稱欄位的自動完成清單：廠商管理（/vendors）裡的廠商名稱優先，
    再補上這個模組自己歷史紀錄裡用過、但廠商管理還沒有的名稱（2026-09-13
    廠商管理連動新增前既有的行為，保留著不讓舊的建議消失）。"""
    names = []
    seen = set()
    for v in platform_vendors.list_vendors():
        name = v["name"]
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    for name in list_recent_client_names():
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return names


def _client_contract_options(account: dict) -> list:
    """「選擇對應的合約」下拉選單的選項：不是看送出人鏈（送出合約的人
    可能跟送出契約的人完全不相干），而是看這個帳號的部門有沒有被勾在
    那份合約連到的廠商紀錄的「服務部門」裡（`viewer_can_link_contract_
    vendor()`，見該函式說明）。依客戶名稱＋合約起始日期年份組出畫面上
    顯示的文字，方便同仁辨識是哪一份。"""
    vendor_lookup = build_vendor_lookup()
    options = []
    for record in list_all_client_contracts():
        if not viewer_can_link_contract_vendor(account, record.get("vendor_id", ""), vendor_lookup):
            continue
        year = (record.get("contract_start_date") or "")[:4]
        label = record.get("party_a_name", "")
        if year.isdigit():
            label = f"{label}（{year}年）"
        options.append({"id": record["id"], "label": label})
    return options


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
        "recent_client_names": _client_name_suggestions(),
        "client_contract_options": _client_contract_options(user),
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
        {
            "user": account,
            "records": records,
            "generated": generated,
            "show_summary_link": viewer_has_any_department_access(account, build_vendor_lookup()),
        },
    )


@router.get("/dispatch-contracts/help")
def dispatch_contract_help(request: Request, redirect=Depends(_require_access)):
    """派遣契約產生器使用說明（2026-09-18 新增）。跟 dispatch_contract_home()
    一樣用 _require_access，跟 /portal 卡片顯不顯示「使用說明」按鈕是同一組
    權限判斷（見 portal_routes.py 的說明）。"""
    if redirect:
        return redirect
    return templates.TemplateResponse(request, "dispatch_contract_help.html", {"user": platform_accounts.current_account(request)})


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
    linked_client_contract_id = (form.get("linked_client_contract_id") or "").strip()

    # 選了「選擇對應的合約」的話，客戶名稱、廠商關聯一律直接沿用那份
    # 合約的資料（見 services/dispatch_contract_service.py 開頭的說明），
    # 不管這個欄位本來打了什麼都會被蓋掉；重新驗證這個帳號的部門真的
    # 有被勾在那份合約連到的廠商服務部門裡（不是看送出人鏈——合約跟
    # 契約很可能是完全不相干的兩個人各自負責，見上面「下拉選單能不能
    # 選到，看的是部門」的說明），避免有人把網址列/表單裡的 id 換成
    # 部門不相干的合約硬送。
    linked_vendor_id = ""
    error = ""
    if linked_client_contract_id:
        chosen_contract = get_client_contract_submission(linked_client_contract_id)
        vendor_lookup = build_vendor_lookup()
        if not chosen_contract or not viewer_can_link_contract_vendor(
            account, chosen_contract.get("vendor_id", ""), vendor_lookup
        ):
            error = "選擇的合約已經不存在或您無法連結，請重新選擇，或改為手動輸入客戶名稱。"
        else:
            client_name = chosen_contract.get("party_a_name", "") or client_name
            linked_vendor_id = chosen_contract.get("vendor_id", "")

    form_values = {
        "client_name": client_name,
        "work_address": work_address,
        "work_content": work_content,
        "pay_cycle": pay_cycle,
        "enabled_columns": enabled_columns,
        "linked_client_contract_id": linked_client_contract_id,
    }

    if not error:
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

    # 有選「對應的合約」就直接沿用那份合約帶出來的廠商 ID，不用再靠名稱
    # 去廠商管理猜；沒選的話才走原本「找同名沿用、沒有就新建」的備案路徑。
    if linked_client_contract_id:
        vendor_id = linked_vendor_id
    else:
        vendor_id = sync_vendor_from_dispatch_contract(client_name)

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
        vendor_id=vendor_id,
        linked_client_contract_id=linked_client_contract_id,
    )

    encoded_filename = quote(filename)
    return Response(
        content=docx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}"},
    )


def _can_preview_or_download(account: dict, record: dict) -> bool:
    """預覽／下載額外多開放給「服務部門主管」——跟原本送出人鏈的
    `can_view_submission()` 是「兩者符合一個即可」，不是取代掉原本的
    規則（刪除還是只看 `can_view_submission()`，見
    `dispatch_contract_delete()`）。這樣總表上列出來的紀錄，服務部門
    主管點進去的預覽/下載連結才不會變成「找不到」。"""
    return can_view_submission(account, record) or can_view_via_vendor_department_single(
        account, record.get("vendor_id", "")
    )


@router.get("/dispatch-contracts/{submission_id}/download")
def dispatch_contract_download(submission_id: str, request: Request, redirect=Depends(_require_access)):
    if redirect:
        return redirect
    account = platform_accounts.current_account(request)
    record = get_submission(submission_id)
    if not record or not record.get("blob_path"):
        return Response(status_code=404)
    if not _can_preview_or_download(account, record):
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
    if not _can_preview_or_download(account, record):
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
