"""總表（/contract-summary，2026-09-13 新增，Phase 2）：把「合約產生器」
「派遣契約產生器」各自的紀錄整理成主管年底盤點客戶用的總表，網頁呈現＋
匯出 Excel 都有。詳細設計、資料整理邏輯都寫在
`services/contract_summary_service.py` 開頭的說明，這裡只負責權限判斷、
串接兩個產生器既有的 `list_visible_submissions()`、頁面渲染跟匯出下載。

**這裡沒有自己的模組代碼**（`platform_accounts.MODULES` 沒有新增
`contract_summary` 這一項，`/accounts` 也不會多一欄可以勾），能不能看到
這個功能完全「借用」合約產生器（`client_contracts`）／派遣契約產生器
（`dispatch_contracts`）這兩個模組既有的角色判斷：`platform_accounts.
module_role()` 回傳 `ROLE_ADMIN`（主管角色，全平台管理員也算）的那個
模組，總表就會顯示對應那一半；兩個模組都不是主管角色（包含「專員」
角色跟完全沒開放這兩個模組的帳號）一律導去 /portal，連頁面本身都進不
來——這是使用者明確要求的規則：「專員完全看不到，只有主管以上看得到」。

實際「看得到哪些紀錄」則是直接呼叫兩個產生器的 `list_visible_submissions()`
（自己送出的、自己是送出者的主管、或全平台管理員），這支功能完全沒有
重新實作可見範圍邏輯，也不會因為這裡的判斷方式不同而看到本來看不到的
紀錄。
"""
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse, Response

import platform_accounts
from platform_templating import templates
from services.client_contract_service import CONTRACT_VERSIONS
from services.client_contract_service import list_visible_submissions as list_visible_client_contracts
from services.contract_summary_excel import (
    build_client_contract_summary_workbook,
    build_dispatch_contract_summary_workbook,
)
from services.contract_summary_service import (
    available_client_contract_years,
    build_client_contract_summary_rows,
    build_dispatch_contract_summary_rows,
    parse_selected_years,
)
from services.dispatch_contract_service import list_visible_submissions as list_visible_dispatch_contracts

router = APIRouter()

CLIENT_MODULE_CODE = "client_contracts"
DISPATCH_MODULE_CODE = "dispatch_contracts"

# 抓紀錄時給一個夠大的上限——總表要盡量涵蓋這個帳號看得到的全部紀錄，不像
# 兩個產生器自己的列表頁只需要「最近幾百筆」讓同仁重新下載檔案，兩邊用途
# 不一樣，這裡刻意跟 list_visible_submissions() 預設的 200 分開設定。
_RECORDS_LIMIT = 5000


def _has_manager_access(account: dict, module_code: str) -> bool:
    """這個帳號在指定模組是不是「主管」角色（`ROLE_ADMIN`，全平台管理員
    也算）——`platform_accounts.module_role()` 已經處理好職級／全平台
    管理員的判斷，這裡只是包一層讓呼叫端讀起來更直接。"""
    return platform_accounts.module_role(account, module_code) == platform_accounts.ROLE_ADMIN


def _require_access(request: Request):
    account = platform_accounts.current_account(request)
    if not account:
        return RedirectResponse(url="/login?next=/contract-summary", status_code=303)
    if not _has_manager_access(account, CLIENT_MODULE_CODE) and not _has_manager_access(account, DISPATCH_MODULE_CODE):
        return RedirectResponse(url="/portal", status_code=303)
    return None


@router.get("/contract-summary")
def contract_summary_home(request: Request, years: list = Query(default=[]), redirect=Depends(_require_access)):
    if redirect:
        return redirect
    account = platform_accounts.current_account(request)
    has_client_access = _has_manager_access(account, CLIENT_MODULE_CODE)
    has_dispatch_access = _has_manager_access(account, DISPATCH_MODULE_CODE)

    available_years = []
    selected_years = []
    client_rows = []
    if has_client_access:
        client_records = list_visible_client_contracts(account, limit=_RECORDS_LIMIT)
        available_years = available_client_contract_years(client_records)
        selected_years = parse_selected_years(years, available_years)
        client_rows = build_client_contract_summary_rows(client_records, selected_years)

    dispatch_rows = []
    if has_dispatch_access:
        dispatch_records = list_visible_dispatch_contracts(account, limit=_RECORDS_LIMIT)
        dispatch_rows = build_dispatch_contract_summary_rows(dispatch_records)

    return templates.TemplateResponse(
        request,
        "contract_summary.html",
        {
            "user": account,
            "has_client_access": has_client_access,
            "has_dispatch_access": has_dispatch_access,
            "available_years": available_years,
            "selected_years": selected_years,
            "client_rows": client_rows,
            "dispatch_rows": dispatch_rows,
            "contract_versions": CONTRACT_VERSIONS,
        },
    )


@router.get("/contract-summary/export/client-contracts.xlsx")
def contract_summary_export_client_contracts(
    request: Request, years: list = Query(default=[]), redirect=Depends(_require_access)
):
    if redirect:
        return redirect
    account = platform_accounts.current_account(request)
    if not _has_manager_access(account, CLIENT_MODULE_CODE):
        return Response(status_code=404)
    records = list_visible_client_contracts(account, limit=_RECORDS_LIMIT)
    available_years = available_client_contract_years(records)
    selected_years = parse_selected_years(years, available_years)
    rows = build_client_contract_summary_rows(records, selected_years)
    content = build_client_contract_summary_workbook(rows, CONTRACT_VERSIONS)
    encoded_filename = quote("合約產生器總表.xlsx")
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}"},
    )


@router.get("/contract-summary/export/dispatch-contracts.xlsx")
def contract_summary_export_dispatch_contracts(request: Request, redirect=Depends(_require_access)):
    if redirect:
        return redirect
    account = platform_accounts.current_account(request)
    if not _has_manager_access(account, DISPATCH_MODULE_CODE):
        return Response(status_code=404)
    records = list_visible_dispatch_contracts(account, limit=_RECORDS_LIMIT)
    rows = build_dispatch_contract_summary_rows(records)
    content = build_dispatch_contract_summary_workbook(rows)
    encoded_filename = quote("派遣契約總表.xlsx")
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}"},
    )
