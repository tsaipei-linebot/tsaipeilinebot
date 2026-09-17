from datetime import datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from delivery import repository
from delivery.auth import admin_required, current_user, login_required
from delivery.config import (
    EQUIPMENT_ADMIN_ONLY_TRANSACTION_TYPES,
    EQUIPMENT_ELIGIBLE_PERSONNEL_STATUS,
    EQUIPMENT_TRANSACTION_TYPE_MAP,
    EQUIPMENT_TRANSACTION_TYPES,
    EQUIPMENT_TRANSACTION_TYPES_REQUIRING_PERSONNEL,
    EQUIPMENT_TRANSACTION_TYPES_REQUIRING_TWO_LOCATIONS,
)
from delivery.templating import templates

router = APIRouter()

# 給畫面顯示用的錯誤說明，跟 vehicle_report.EVENT_ERROR_MESSAGES 是同一種做法——
# repository 層回傳的是短代碼（方便單元測試比對），畫面上要顯示的中文說明放在
# 路由層，不混進 repository。
_TRANSACTION_ERROR_MESSAGES = {
    "invalid_quantity": "數量請填大於 0 的整數。",
    "personnel_not_found": "請選擇一位騎士。",
    "personnel_missing_documents": "這位騎士的缺件資料還沒補齊，系統擋下不能借用，請先到人員詳細頁補齊文件。",
    "insufficient_stock": "這個放置點的庫存不夠這次數量，如果確定要借，需要有管理員權限的人勾選「主管特批」再送出。",
    "insufficient_debt": "數量超過這位騎士目前實際尚欠的數量，請確認後再送出。",
    "item_required": "請選擇品項。",
    "location_required": "請選擇放置點。",
    "same_location": "轉倉的「來源放置點」跟「目的放置點」不能相同。",
    "personnel_required": "請選擇騎士。",
    "buyout_price_not_set": "這個品項還沒設定買斷單價，請先到「品項管理」設定後再登記買斷。",
    "admin_only": "這個異動類型限管理員操作。",
    "reason_required": "核銷一定要填寫原因，方便事後追查。",
    "no_outstanding_debt": "這位騎士在這個品項目前沒有尚欠數量，沒有需要核銷的紀錄。",
    "name_required": "名稱要填。",
}


def _error_message(code: str) -> str:
    return _TRANSACTION_ERROR_MESSAGES.get(code, "這筆資料無法處理，請確認後再試一次。")


def _eligible_personnel() -> list:
    return repository.search_personnel(employment_status=EQUIPMENT_ELIGIBLE_PERSONNEL_STATUS)


def _transaction_form_context(request, is_admin: bool, form_data=None, error=""):
    transaction_types = EQUIPMENT_TRANSACTION_TYPES
    if not is_admin:
        transaction_types = [t for t in transaction_types if t["code"] not in EQUIPMENT_ADMIN_ONLY_TRANSACTION_TYPES]
    return {
        "user": current_user(request),
        "transaction_types": transaction_types,
        "requires_personnel": sorted(EQUIPMENT_TRANSACTION_TYPES_REQUIRING_PERSONNEL),
        "requires_two_locations": sorted(EQUIPMENT_TRANSACTION_TYPES_REQUIRING_TWO_LOCATIONS),
        "admin_only_types": sorted(EQUIPMENT_ADMIN_ONLY_TRANSACTION_TYPES),
        "items": repository.list_equipment_items(),
        "locations": repository.list_equipment_locations(),
        "personnel_list": _eligible_personnel(),
        "is_admin": is_admin,
        "form_data": form_data or {},
        "error": error,
    }


@router.get("/equipment")
def equipment_home(request: Request, redirect=Depends(login_required)):
    if redirect:
        return redirect
    items = repository.list_equipment_items()
    locations = repository.list_equipment_locations()
    stock_rows = repository.list_equipment_stock()
    stock_map = {(row["location_id"], row["item_id"]): row for row in stock_rows}

    matrix = []
    for location in locations:
        cells = []
        for item in items:
            stock = stock_map.get((location["id"], item["id"]), {"quantity": 0, "warning_threshold": 0})
            below = repository.equipment_stock_below_threshold(location["id"], item["id"])
            cells.append({"item": item, "quantity": stock.get("quantity", 0), "below_threshold": below})
        matrix.append({"location": location, "cells": cells})

    threshold_rows = [row for row in stock_rows if row.get("warning_threshold", 0) > 0]
    item_map = {i["id"]: i["name"] for i in items}
    location_map = {loc["id"]: loc["name"] for loc in locations}
    for row in threshold_rows:
        row["item_name"] = item_map.get(row["item_id"], "（已刪除品項）")
        row["location_name"] = location_map.get(row["location_id"], "（已刪除放置點）")

    outstanding_debt = repository.list_equipment_debt()
    for row in outstanding_debt:
        row["item_name"] = item_map.get(row["item_id"], "（已刪除品項）")
        personnel = repository.get_personnel(row["personnel_id"])
        row["personnel_name"] = personnel.get("name") if personnel else "（查無此人）"

    return templates.TemplateResponse(
        request,
        "equipment_home.html",
        {
            "user": current_user(request),
            "items": items,
            "locations": locations,
            "matrix": matrix,
            "threshold_rows": threshold_rows,
            "outstanding_debt": outstanding_debt,
        },
    )


@router.post("/equipment/stock/threshold")
def set_stock_threshold(
    request: Request,
    location_id: str = Form(...),
    item_id: str = Form(...),
    threshold: int = Form(0),
    redirect=Depends(admin_required),
):
    if redirect:
        return redirect
    if threshold < 0:
        threshold = 0
    repository.set_equipment_stock_threshold(location_id, item_id, threshold)
    return RedirectResponse(url="/delivery/equipment", status_code=303)


@router.get("/equipment/transaction")
def transaction_form(request: Request, type: str = "", redirect=Depends(login_required)):
    if redirect:
        return redirect
    user = current_user(request)
    is_admin = user["role"] == "admin"
    context = _transaction_form_context(request, is_admin, form_data={"transaction_type": type})
    return templates.TemplateResponse(request, "equipment_transaction_form.html", context)


def _validate_transaction_fields(
    transaction_type: str, item_id: str, from_location_id: str, to_location_id: str, personnel_id: str
) -> str:
    """路由層先做的基本欄位檢查（下拉選單有沒有選、轉倉兩個放置點是否合理），
    跟 repository.equipment_transaction_error() 那些「業務規則」（庫存夠不夠、
    缺件擋不擋）分開——這幾項就算沒填也不該讓 Firestore 讀取空字串文件 ID。"""
    if transaction_type not in EQUIPMENT_TRANSACTION_TYPE_MAP:
        return "admin_only"
    if not item_id:
        return "item_required"
    if transaction_type in EQUIPMENT_TRANSACTION_TYPES_REQUIRING_TWO_LOCATIONS:
        if not from_location_id or not to_location_id:
            return "location_required"
        if from_location_id == to_location_id:
            return "same_location"
    elif transaction_type == "purchase":
        if not to_location_id:
            return "location_required"
    elif transaction_type in ("borrow", "return"):
        if not from_location_id:
            return "location_required"
    if transaction_type in EQUIPMENT_TRANSACTION_TYPES_REQUIRING_PERSONNEL and not personnel_id:
        return "personnel_required"
    if transaction_type == "writeoff" and not personnel_id:
        return "personnel_required"
    return ""


@router.post("/equipment/transaction")
def transaction_submit(
    request: Request,
    transaction_type: str = Form(...),
    item_id: str = Form(...),
    quantity: int = Form(0),
    from_location_id: str = Form(""),
    to_location_id: str = Form(""),
    personnel_id: str = Form(""),
    payment_received: str = Form(""),
    reason: str = Form(""),
    override_stock_check: str = Form(""),
    redirect=Depends(login_required),
):
    if redirect:
        return redirect
    user = current_user(request)
    is_admin = user["role"] == "admin"

    if transaction_type in EQUIPMENT_ADMIN_ONLY_TRANSACTION_TYPES and not is_admin:
        error = "admin_only"
    else:
        error = _validate_transaction_fields(transaction_type, item_id, from_location_id, to_location_id, personnel_id)

    if not error and transaction_type == "writeoff":
        ok, err = repository.record_equipment_writeoff(
            personnel_id=personnel_id, item_id=item_id, reason=reason, operated_by=user["username"]
        )
        if not ok:
            error = err
    elif not error:
        unit_price = None
        if transaction_type == "buyout":
            item = repository.get_equipment_item(item_id)
            unit_price = item.get("buyout_unit_price") if item else None
            if unit_price is None:
                error = "buyout_price_not_set"
        if not error:
            ok, err = repository.record_equipment_transaction(
                transaction_type=transaction_type,
                item_id=item_id,
                quantity=quantity,
                from_location_id=from_location_id,
                to_location_id=to_location_id,
                personnel_id=personnel_id,
                unit_price=unit_price,
                payment_received=payment_received == "1",
                reported_by=user["username"],
                override_stock_check=is_admin and override_stock_check == "1",
            )
            if not ok:
                error = err

    if error:
        form_data = {
            "transaction_type": transaction_type,
            "item_id": item_id,
            "quantity": quantity,
            "from_location_id": from_location_id,
            "to_location_id": to_location_id,
            "personnel_id": personnel_id,
            "reason": reason,
        }
        context = _transaction_form_context(request, is_admin, form_data=form_data, error=_error_message(error))
        return templates.TemplateResponse(request, "equipment_transaction_form.html", context, status_code=400)

    return RedirectResponse(url="/delivery/equipment/records?submitted=1", status_code=303)


@router.get("/equipment/records")
def records(
    request: Request,
    location_id: str = "",
    item_id: str = "",
    personnel_id: str = "",
    transaction_type: str = "",
    redirect=Depends(login_required),
):
    if redirect:
        return redirect
    transactions = repository.list_equipment_transactions(
        location_id=location_id, item_id=item_id, personnel_id=personnel_id, transaction_type=transaction_type
    )
    items = repository.list_equipment_items(include_inactive=True)
    locations = repository.list_equipment_locations(include_inactive=True)
    item_map = {i["id"]: i["name"] for i in items}
    location_map = {loc["id"]: loc["name"] for loc in locations}

    personnel_cache = {}

    def personnel_name(pid: str) -> str:
        if not pid:
            return "-"
        if pid not in personnel_cache:
            person = repository.get_personnel(pid)
            personnel_cache[pid] = person.get("name") if person else "（查無此人）"
        return personnel_cache[pid]

    for t in transactions:
        t["item_name"] = item_map.get(t.get("item_id"), "（已刪除品項）")
        t["from_location_name"] = location_map.get(t.get("from_location_id"), "") if t.get("from_location_id") else ""
        t["to_location_name"] = location_map.get(t.get("to_location_id"), "") if t.get("to_location_id") else ""
        t["personnel_name"] = personnel_name(t.get("personnel_id"))
        t["type_name"] = EQUIPMENT_TRANSACTION_TYPE_MAP.get(t.get("type"), t.get("type"))
        t["created_at_display"] = (
            datetime.fromtimestamp(t["created_at"]).strftime("%Y-%m-%d %H:%M") if t.get("created_at") else "-"
        )

    return templates.TemplateResponse(
        request,
        "equipment_records.html",
        {
            "user": current_user(request),
            "transactions": transactions,
            "items": items,
            "locations": locations,
            "transaction_types": EQUIPMENT_TRANSACTION_TYPES,
            "filter_location_id": location_id,
            "filter_item_id": item_id,
            "filter_personnel_id": personnel_id,
            "filter_transaction_type": transaction_type,
        },
    )


@router.get("/equipment/records/{transaction_id}/edit")
def edit_transaction_form(transaction_id: str, request: Request, redirect=Depends(admin_required)):
    """修正一筆既有的裝備異動登記，只開放管理員。品項／異動類型不開放
    修改（見 repository.update_equipment_transaction() 的說明），畫面上
    只顯示這個類型實際會用到的欄位。"""
    if redirect:
        return redirect
    transaction = repository.get_equipment_transaction(transaction_id)
    if not transaction:
        return RedirectResponse(url="/delivery/equipment/records", status_code=303)
    item = repository.get_equipment_item(transaction.get("item_id", ""))
    return templates.TemplateResponse(
        request,
        "equipment_transaction_edit.html",
        {
            "user": current_user(request),
            "transaction": transaction,
            "item_name": item.get("name") if item else "（已刪除品項）",
            "type_name": EQUIPMENT_TRANSACTION_TYPE_MAP.get(transaction.get("type"), transaction.get("type")),
            "locations": repository.list_equipment_locations(),
            "personnel_list": _eligible_personnel(),
            "requires_personnel": EQUIPMENT_TRANSACTION_TYPES_REQUIRING_PERSONNEL,
            "requires_two_locations": EQUIPMENT_TRANSACTION_TYPES_REQUIRING_TWO_LOCATIONS,
            "error": "",
        },
    )


@router.post("/equipment/records/{transaction_id}/edit")
def edit_transaction_submit(
    transaction_id: str,
    request: Request,
    quantity: int = Form(0),
    from_location_id: str = Form(""),
    to_location_id: str = Form(""),
    personnel_id: str = Form(""),
    payment_received: str = Form(""),
    reason: str = Form(""),
    override_stock_check: str = Form(""),
    redirect=Depends(admin_required),
):
    if redirect:
        return redirect
    transaction = repository.get_equipment_transaction(transaction_id)
    if not transaction:
        return RedirectResponse(url="/delivery/equipment/records", status_code=303)
    transaction_type = transaction.get("type", "")
    user = current_user(request)

    if transaction_type == "writeoff":
        # 核銷只開放改「原因」，其餘參數 repository 端會直接忽略（見
        # update_equipment_transaction() 的說明），不需要跑欄位驗證。
        repository.update_equipment_transaction(transaction_id, quantity=0, reason=reason)
        return RedirectResponse(url="/delivery/equipment/records", status_code=303)

    error = _validate_transaction_fields(
        transaction_type, transaction.get("item_id", ""), from_location_id, to_location_id, personnel_id
    )
    unit_price = transaction.get("unit_price")
    if not error and transaction_type == "buyout":
        item = repository.get_equipment_item(transaction.get("item_id", ""))
        unit_price = item.get("buyout_unit_price") if item else None
        if unit_price is None:
            error = "buyout_price_not_set"

    if not error:
        ok, err = repository.update_equipment_transaction(
            transaction_id,
            quantity=quantity,
            from_location_id=from_location_id,
            to_location_id=to_location_id,
            personnel_id=personnel_id,
            unit_price=unit_price,
            payment_received=payment_received == "1",
            reason=reason,
            override_stock_check=user["role"] == "admin" and override_stock_check == "1",
        )
        if not ok:
            error = err

    if error:
        item = repository.get_equipment_item(transaction.get("item_id", ""))
        merged = {**transaction, "quantity": quantity, "from_location_id": from_location_id,
                  "to_location_id": to_location_id, "personnel_id": personnel_id, "reason": reason}
        return templates.TemplateResponse(
            request,
            "equipment_transaction_edit.html",
            {
                "user": user,
                "transaction": merged,
                "item_name": item.get("name") if item else "（已刪除品項）",
                "type_name": EQUIPMENT_TRANSACTION_TYPE_MAP.get(transaction_type, transaction_type),
                "locations": repository.list_equipment_locations(),
                "personnel_list": _eligible_personnel(),
                "requires_personnel": EQUIPMENT_TRANSACTION_TYPES_REQUIRING_PERSONNEL,
                "requires_two_locations": EQUIPMENT_TRANSACTION_TYPES_REQUIRING_TWO_LOCATIONS,
                "error": _error_message(error),
            },
            status_code=400,
        )

    return RedirectResponse(url="/delivery/equipment/records", status_code=303)


@router.post("/equipment/records/{transaction_id}/delete")
def delete_transaction(transaction_id: str, request: Request, redirect=Depends(admin_required)):
    """刪除一筆裝備異動登記，只開放管理員。見
    repository.delete_equipment_transaction()：刪除前會先復原這筆紀錄
    造成的庫存/尚欠效果，庫存/尚欠總表刪除後仍然正確。"""
    if redirect:
        return redirect
    repository.delete_equipment_transaction(transaction_id)
    return RedirectResponse(url="/delivery/equipment/records", status_code=303)


# ---------- 品項管理（限管理員） ----------

@router.get("/equipment/items")
def items_page(request: Request, redirect=Depends(admin_required)):
    if redirect:
        return redirect
    items = repository.list_equipment_items(include_inactive=True)
    for item in items:
        item["has_history"] = repository.equipment_item_has_history(item["id"])
    return templates.TemplateResponse(
        request, "equipment_items.html", {"user": current_user(request), "items": items, "error": ""}
    )


@router.post("/equipment/items/new")
def create_item(
    request: Request, name: str = Form(...), buyout_unit_price: str = Form(""), redirect=Depends(admin_required)
):
    if redirect:
        return redirect
    name = name.strip()
    price = None
    if buyout_unit_price.strip():
        try:
            price = int(buyout_unit_price)
        except ValueError:
            price = None
    if name:
        repository.create_equipment_item(name, buyout_unit_price=price, created_by=current_user(request)["username"])
    return RedirectResponse(url="/delivery/equipment/items", status_code=303)


@router.post("/equipment/items/{item_id}/edit")
def edit_item(
    item_id: str,
    request: Request,
    name: str = Form(...),
    buyout_unit_price: str = Form(""),
    redirect=Depends(admin_required),
):
    if redirect:
        return redirect
    name = name.strip()
    price = None
    if buyout_unit_price.strip():
        try:
            price = int(buyout_unit_price)
        except ValueError:
            price = None
    if name:
        repository.update_equipment_item(item_id, name, buyout_unit_price=price)
    return RedirectResponse(url="/delivery/equipment/items", status_code=303)


@router.post("/equipment/items/{item_id}/active")
def toggle_item_active(item_id: str, request: Request, active: str = Form(...), redirect=Depends(admin_required)):
    if redirect:
        return redirect
    repository.set_equipment_item_active(item_id, active == "1")
    return RedirectResponse(url="/delivery/equipment/items", status_code=303)


@router.post("/equipment/items/{item_id}/delete")
def delete_item(item_id: str, request: Request, redirect=Depends(admin_required)):
    if redirect:
        return redirect
    repository.delete_equipment_item(item_id)
    return RedirectResponse(url="/delivery/equipment/items", status_code=303)


# ---------- 放置點管理（限管理員） ----------

@router.get("/equipment/locations")
def locations_page(request: Request, redirect=Depends(admin_required)):
    if redirect:
        return redirect
    locations = repository.list_equipment_locations(include_inactive=True)
    for location in locations:
        location["has_history"] = repository.equipment_location_has_history(location["id"])
    return templates.TemplateResponse(
        request, "equipment_locations.html", {"user": current_user(request), "locations": locations, "error": ""}
    )


@router.post("/equipment/locations/new")
def create_location(request: Request, name: str = Form(...), redirect=Depends(admin_required)):
    if redirect:
        return redirect
    name = name.strip()
    if name:
        repository.create_equipment_location(name, created_by=current_user(request)["username"])
    return RedirectResponse(url="/delivery/equipment/locations", status_code=303)


@router.post("/equipment/locations/{location_id}/active")
def toggle_location_active(
    location_id: str, request: Request, active: str = Form(...), redirect=Depends(admin_required)
):
    if redirect:
        return redirect
    repository.set_equipment_location_active(location_id, active == "1")
    return RedirectResponse(url="/delivery/equipment/locations", status_code=303)


@router.post("/equipment/locations/{location_id}/delete")
def delete_location(location_id: str, request: Request, redirect=Depends(admin_required)):
    if redirect:
        return redirect
    repository.delete_equipment_location(location_id)
    return RedirectResponse(url="/delivery/equipment/locations", status_code=303)
