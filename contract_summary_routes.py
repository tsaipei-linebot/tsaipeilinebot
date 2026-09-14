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
# 抓回來的筆數接近上限時代表資料量已經逼近 _RECORDS_LIMIT，之後真的超過
# 的話，最舊的紀錄會悄悄從這個總表消失（其他頁面、原始資料都不受影響，
# 只有這個彙總畫面看不到）。訂在上限的 80%，讓使用者能提早發現、聯繫
# 工程師調高 _RECORDS_LIMIT，而不是等資料真的消失才發現（2026-09-14 新增）。
_NEAR_LIMIT_WARNING_THRESHOLD = int(_RECORDS_LIMIT * 0.8)


def _require_access(request: Request):
    account = platform_accounts.current_account(request)
    if not account:
        return RedirectResponse(url="/login?next=/contract-summary", status_code=303)
    vendor_lookup = build_vendor_lookup()
    if not viewer_has_any_department_access(account, vendor_lookup):
        return RedirectResponse(url="/portal", status_code=303)
    return None


def _near_limit_warning(client_total: int, dispatch_total: int) -> str:
    """任一邊抓回來的原始筆數（權限過濾前，代表系統裡實際的資料量，不是
    這個帳號看得到的筆數）接近 _RECORDS_LIMIT 時，回傳要顯示給使用者的
    警示文字；還早的話回傳空字串（模板用空字串判斷不顯示）。"""
    if client_total >= _NEAR_LIMIT_WARNING_THRESHOLD or dispatch_total >= _NEAR_LIMIT_WARNING_THRESHOLD:
        return (
            f"⚠️ 系統目前的合約／契約紀錄筆數已接近總表顯示上限"
            f"（{_RECORDS_LIMIT:,} 筆），日後如果繼續成長超過上限，"
            f"最舊的紀錄將不會再出現在這個總表畫面上（原始資料不會受影響）。"
            f"請聯繫工程師調高上限設定。"
        )
    return ""


def _visible_records(account: dict):
    """建一次 vendor_lookup、抓好兩邊全部紀錄、套用「服務部門」權限過濾——
    首頁跟三個匯出路由共用同一套準備流程，確保「看得到什麼」完全一致。
    回傳值多帶一個 near_limit_warning：權限過濾前的原始筆數算出來的接近
    上限警示文字，只有首頁會顯示，匯出路由用不到但為了共用同一套準備
    流程還是一起回傳。"""
    vendor_lookup = build_vendor_lookup()
    client_records_all = list_all_client_contracts(limit=_RECORDS_LIMIT)
    dispatch_records_all = list_all_dispatch_contracts(limit=_RECORDS_LIMIT)
    client_records = visible_client_contract_records(client_records_all, account, vendor_lookup)
    dispatch_records = visible_dispatch_contract_records(dispatch_records_all, account, vendor_lookup)
    near_limit_warning = _near_limit_warning(len(client_records_all), len(dispatch_records_all))
    return client_records, dispatch_records, near_limit_warning


@router.get("/contract-summary")
def contract_summary_home(request: Request, years: list = Query(default=[]), redirect=Depends(_require_access)):
    if redirect:
        return redirect
    account = platform_accounts.current_account(request)
    client_records, dispatch_records, near_limit_warning = _visible_records(account)

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
            "near_limit_warning": near_limit_warning,
        },
    )


@router.get("/contract-summary/export/client-contracts.xlsx")
def contract_summary_export_client_contracts(
    request: Request, years: list = Query(default=[]), redirect=Depends(_require_access)
):
    if redirect:
        return redirect
    account = platform_accounts.current_account(request)
    client_records, _, _ = _visible_records(account)
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
    _, dispatch_records, _ = _visible_records(account)
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
    client_records, dispatch_records, _ = _visible_records(account)
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
