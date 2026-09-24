"""每日加退保「暫存區」（2026-09-24 新增，見 HANDOFF.md「配送部按報到／離職
自動進每日加退保暫存區」）。

部門同仁一天下來把要加退保的人一筆一筆放進暫存區（新北所(配送組)的配送
系統按「報到」「離職」會自動放進來，也可以在上傳頁手動新增），人資收單前
按「送出給人資」才真正交出去——送出＝系統把暫存區組成跟部門上傳範本一樣的
11 欄 Excel，走既有的 `insurance_repository.save_upload()`，所以人資端的彙總
／收單／下載完全不用改。收單前來不及送的，可以下載成 Excel 自己交給人資。

**資料永遠不真的刪除**（使用者要求保留紀錄，以後才能追蹤同仁有沒有操作）：
每一筆有狀態——待送出 pending／已送出 sent／已下載 downloaded／已取消
cancelled（按錯改回來、或同仁手動刪掉），每次變更都往 `history` 加一筆
「誰、什麼時候、做了什麼」。

每一筆的欄位對照部門上傳範本（`hr/insurance_excel._SOURCE_HEADER`），日期
一律存 ISO 字串（YYYY-MM-DD）。
"""
import datetime
import time
import uuid

import platform_accounts
from hr.config import INSURANCE_DRAFT_DEPARTMENTS, INSURANCE_UPLOAD_DEPARTMENTS
from hr.db import insurance_drafts_ref

STATUS_PENDING = "pending"
STATUS_SENT = "sent"
STATUS_DOWNLOADED = "downloaded"
STATUS_CANCELLED = "cancelled"

STATUS_NAMES = {
    STATUS_PENDING: "待送出",
    STATUS_SENT: "已送出",
    STATUS_DOWNLOADED: "已下載",
    STATUS_CANCELLED: "已取消",
}

KIND_ADD = "add"        # 加保（配送系統按「報到」）
KIND_REMOVE = "remove"  # 退保（配送系統按「離職」）
KIND_MANUAL = "manual"  # 同仁手動新增

ACTION_NAMES = {
    "created": "建立",
    "edited": "修改",
    "sent": "送出給人資",
    "downloaded": "下載",
    "cancelled": "取消",
}

# 可以編輯的欄位 → 部門上傳範本的欄位名稱（Excel 表頭）
FIELD_HEADERS = {
    "vendor": "廠商",
    "shift": "班別",
    "name": "姓名",
    "id_number": "身分證",
    "insured_date": "勞保加保日期",
    "withdrawn_date": "勞保退保日期",
    "recovery_date": "勞保追退日期",
    "health_month": "健保加保月份",
    "dependents": "眷屬健保",
    "note": "備註",
}

_CANONICAL_DEPARTMENTS = {platform_accounts.normalize_department(d): d for d in INSURANCE_UPLOAD_DEPARTMENTS}
_DRAFT_DEPARTMENTS = {platform_accounts.normalize_department(d) for d in INSURANCE_DRAFT_DEPARTMENTS}


def canonical_department(department) -> str:
    """帳號部門字串 → 部門主檔的標準寫法（全形括號也認得），認不得就原樣回傳。"""
    return _CANONICAL_DEPARTMENTS.get(platform_accounts.normalize_department(department), department or "")


def department_has_drafts(department) -> bool:
    return platform_accounts.normalize_department(department) in _DRAFT_DEPARTMENTS


def draft_type_name(draft: dict) -> str:
    """畫面上的「類型」：看填了哪個日期，不是看怎麼建立的（手動新增的也一樣）。"""
    insured = bool(draft.get("insured_date"))
    withdrawn = bool(draft.get("withdrawn_date"))
    if insured and withdrawn:
        return "加退保"
    if insured:
        return "加保"
    if withdrawn:
        return "退保"
    if draft.get("recovery_date"):
        return "追退"
    return "-"


def _clean_fields(fields: dict) -> dict:
    return {key: (str(fields.get(key) or "")).strip() for key in FIELD_HEADERS if key in fields}


def _history_entry(action: str, actor: dict, note: str = "") -> dict:
    return {
        "action": action,
        "at": time.time(),
        "by": actor.get("username", ""),
        "by_name": actor.get("name", ""),
        "note": note,
    }


def _with_id(snapshot) -> dict:
    data = snapshot.to_dict() or {}
    data["id"] = snapshot.id
    return data


def add_draft(department: str, fields: dict, actor: dict, kind: str = KIND_MANUAL, personnel_id: str = "", note: str = "") -> str:
    draft_id = uuid.uuid4().hex
    now = time.time()
    data = {key: "" for key in FIELD_HEADERS}
    data.update(_clean_fields(fields))
    data.update({
        "department": canonical_department(department),
        "kind": kind,
        "personnel_id": personnel_id,
        "status": STATUS_PENDING,
        "created_at": now,
        "created_by": actor.get("username", ""),
        "created_by_name": actor.get("name", ""),
        "updated_at": now,
        "sent_work_date": "",
        "history": [_history_entry("created", actor, note)],
    })
    insurance_drafts_ref().document(draft_id).set(data)
    return draft_id


def get_draft(draft_id: str):
    snapshot = insurance_drafts_ref().document(draft_id).get()
    return _with_id(snapshot) if snapshot.exists else None


def _append_history(draft: dict, updates: dict, entry: dict) -> None:
    updates["history"] = list(draft.get("history") or []) + [entry]
    updates["updated_at"] = entry["at"]
    insurance_drafts_ref().document(draft["id"]).set(updates, merge=True)


def update_draft(draft_id: str, fields: dict, actor: dict) -> bool:
    """只有「待送出」的能改。回傳有沒有改成功。"""
    draft = get_draft(draft_id)
    if not draft or draft.get("status") != STATUS_PENDING:
        return False
    updates = _clean_fields(fields)
    changed = [FIELD_HEADERS[k] for k, v in updates.items() if v != (draft.get(k) or "")]
    _append_history(draft, updates, _history_entry("edited", actor, "、".join(changed)))
    return True


def cancel_draft(draft_id: str, actor: dict, reason: str = "") -> bool:
    draft = get_draft(draft_id)
    if not draft or draft.get("status") != STATUS_PENDING:
        return False
    _append_history(draft, {"status": STATUS_CANCELLED}, _history_entry("cancelled", actor, reason))
    return True


def list_drafts(department: str = "", status: str = "") -> list:
    """`department` 空字串＝全部部門（人資看紀錄用）。依建立時間舊到新。"""
    query = insurance_drafts_ref()
    if department:
        query = query.where("department", "==", canonical_department(department))
    drafts = [_with_id(s) for s in query.stream()]
    if status:
        drafts = [d for d in drafts if d.get("status") == status]
    drafts.sort(key=lambda d: d.get("created_at") or 0)
    return drafts


def list_pending(department: str) -> list:
    return list_drafts(department, STATUS_PENDING)


def drafts_for_personnel(personnel_id: str) -> list:
    drafts = [_with_id(s) for s in insurance_drafts_ref().where("personnel_id", "==", personnel_id).stream()]
    drafts.sort(key=lambda d: d.get("created_at") or 0, reverse=True)
    return drafts


def get_drafts(draft_ids: list) -> list:
    return [d for d in (get_draft(i) for i in draft_ids) if d]


def mark_sent(drafts: list, work_date: str, actor: dict) -> None:
    for draft in drafts:
        _append_history(
            draft,
            {"status": STATUS_SENT, "sent_work_date": work_date},
            _history_entry("sent", actor, f"交給人資的日期 {work_date}"),
        )


def mark_downloaded(drafts: list, actor: dict) -> None:
    for draft in drafts:
        _append_history(draft, {"status": STATUS_DOWNLOADED}, _history_entry("downloaded", actor))


def cancel_pending_for_personnel(personnel_id: str, kind: str, actor: dict, reason: str) -> dict:
    """配送系統「按錯改回來」用：把這個人這一類（加保/退保）還沒送出的都取消。
    回傳 {"cancelled": 取消幾筆, "already_sent": 最近一筆是不是已經送出/下載了}
    ——已經交出去的撤不回來，呼叫端要提醒同仁聯絡人資。"""
    drafts = [d for d in drafts_for_personnel(personnel_id) if d.get("kind") == kind]
    cancelled = 0
    for draft in drafts:
        if draft.get("status") == STATUS_PENDING and cancel_draft(draft["id"], actor, reason):
            cancelled += 1
    latest_active = next((d for d in drafts if d.get("status") != STATUS_CANCELLED), None)
    already_sent = bool(
        not cancelled and latest_active and latest_active.get("status") in (STATUS_SENT, STATUS_DOWNLOADED)
    )
    return {"cancelled": cancelled, "already_sent": already_sent}


def draft_to_source_row(draft: dict) -> dict:
    """轉成 `hr.insurance_excel.parse_department_workbook()` 回傳的同一種格式
    （key 是範本表頭），送出時才能跟手動上傳的 Excel 內容接在一起。"""
    return {header: draft.get(key) or "" for key, header in FIELD_HEADERS.items()}


def format_time(value) -> str:
    """時間戳 → 台灣時間「2026-09-24 14:05」，樣板用（hr/delivery 的 templating
    都註冊成 `taipei_time` filter）。"""
    if not value:
        return ""
    tz = datetime.timezone(datetime.timedelta(hours=8))
    return datetime.datetime.fromtimestamp(float(value), tz).strftime("%Y-%m-%d %H:%M")
