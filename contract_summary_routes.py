"""總表（/contract-summary，2026-09-13 新增 Phase 1，2026-09-14 大改）：把
「合約產生器」「派遣契約產生器」各自的紀錄整理成主管盤點客戶用的總表，
網頁呈現＋匯出 Excel 都有。詳細設計、資料整理邏輯都寫在
`services/contract_summary_service.py` 開頭的說明，這裡只負責權限判斷、
串接兩個產生器的原始資料、頁面渲染跟匯出下載。

**2026-09-14 起，這裡完全不看合約產生器／派遣契約產生器的模組權限
（`client_contracts`／`dispatch_contracts`），也不沿用那兩個模組本身
「送出者本人／送出者的主管／全平台管理員」的可見範圍規則**——整個功能
改成完全看廠商管理（`/vendors`）那筆紀錄勾選的「服務部門」決定：全平台
管理員看得到全部；主管職級的帳號，自己的部門有被勾在某筆廠商紀錄的
服務部門裡，就看得到連到那筆廠商紀錄的合約/契約；其他帳號（專員角色、
或部門沒有服務任何廠商）完全看不到，連頁面本身都進不去。詳見
`services/contract_summary_service.py` 開頭「權限模型」那段說明。
"""
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse, Response

import platform_accounts
from platform_templating import templates
from services.client_contract_service import CONTRACT_VERSIONS
from services.client_contract_service import list_submissions as list_all_client_contracts
from services.contract_summary_excel import (
    build_client_contract_summary_workbook,
    build_dispatch_contract_summary_workbook,
    build_merged_summary_workbook,
)
from services.contract_summary_service import (
    available_client_contract_years,
    build_client_contract_summary_rows,
    build_dispatch_contract_summary_rows,
    build_merged_summary_rows,
    build_vendor_lookup,
    parse_selected_years,
    viewer_has_any_department_access,
    visible_client_contract_records,
    visible_dispatch_contract_records,
)
from services.dispatch_contract_service import list_submissions as list_all_dispatch_contracts

router = APIRouter()

# 抓紀錄時給一個夠大的上限——總表要盡量涵蓋這個帳號看得到的全部紀錄，不像
# 兩個產生器自己的列表頁只需要「最近幾百筆」讓同仁重新下載檔案，兩邊用途
# 不一樣，這裡刻意跟 list_submissions() 預設的 200 分開設定。
_RECORDS_LIMIT = 5000


def _require_access(request: Request):
    account = platform_accounts.current_account(request)
    if not account:
        return RedirectResponse(url="/login?next=/contract-summary", status_code=303)
    vendor_lookup = build_vendor_lookup()
    if not viewer_has_any_department_access(account, vendor_lookup):
        return RedirectResponse(url="/portal", status_code=303)
    return None


def _visible_records(account: dict):
    """建一次 vendor_lookup、抓好兩邊全部紀錄、套用「服務部門」權限過濾——
    首頁跟三個匯出路由共用同一套準備流程，確保「看得到什麼」完全一致。"""
    vendor_lookup = build_vendor_lookup()
    client_records_all = list_all_client_contracts(limit=_RECORDS_LIMIT)
    dispatch_records_all = list_all_dispatch_contracts(limit=_RECORDS_LIMIT)
    client_records = visible_client_contract_records(client_records_all, account, vendor_lookup)
    dispatch_records = visible_dispatch_contract_records(dispatch_records_all, account, vendor_lookup)
    return client_records, dispatch_records


@router.get("/contract-summary")
def contract_summary_home(request: Request, years: list = Query(default=[]), redirect=Depends(_require_access)):
    if redirect:
        return redirect
    account = platform_accounts.current_account(request)
    client_records, dispatch_records = _visible_records(account)

    available_years = available_client_contract_years(client_records)
    selected_years = parse_selected_years(years, available_years)
    client_rows = build_client_contract_summary_rows(client_records, selected_years)
    dispatch_rows = build_dispatch_contract_summary_rows(dispatch_records)
    merged_rows, max_shifts = build_merged_summary_rows(client_records, dispatch_records, selected_years)

    return templates.TemplateResponse(
        request,
        "contract_summary.html",
        {
            "user": account,
            "available_years": available_years,
            "selected_years": selected_years,
            "client_rows": client_rows,
            "dispatch_rows": dispatch_rows,
            "merged_rows": merged_rows,
            "shift_range": range(max_shifts),
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
    client_records, _ = _visible_records(account)
    available_years = available_client_contract_years(client_records)
    selected_years = parse_selected_years(years, available_years)
    rows = build_client_contract_summary_rows(client_records, selected_years)
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
    _, dispatch_records = _visible_records(account)
    rows = build_dispatch_contract_summary_rows(dispatch_records)
    content = build_dispatch_contract_summary_workbook(rows)
    encoded_filename = quote("派遣契約總表.xlsx")
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}"},
    )


@router.get("/contract-summary/export/merged.xlsx")
def contract_summary_export_merged(request: Request, years: list = Query(default=[]), redirect=Depends(_require_access)):
    if redirect:
        return redirect
    account = platform_accounts.current_account(request)
    client_records, dispatch_records = _visible_records(account)
    available_years = available_client_contract_years(client_records)
    selected_years = parse_selected_years(years, available_years)
    rows, max_shifts = build_merged_summary_rows(client_records, dispatch_records, selected_years)
    content = build_merged_summary_workbook(rows, max_shifts, CONTRACT_VERSIONS)
    encoded_filename = quote("合約契約合併總表.xlsx")
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}"},
    )
