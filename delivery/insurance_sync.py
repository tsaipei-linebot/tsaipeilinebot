"""配送系統人員狀態變更 → 新北所(配送組)「每日加退保」暫存區（2026-09-24 新增，
見 HANDOFF.md「配送部按報到／離職自動進每日加退保暫存區」）。

- 待報到 → 在職（查詢人員的「報到」、或詳細頁把狀態改成在職）：加一筆**加保**，
  勞保加保日期＝報到日期
- 在職 → 離職：加一筆**退保**，勞保退保日期＝離職日期
- 按錯改回來（在職改回待報到／放棄報到、離職改回其他狀態）：把還沒送出的那筆
  **取消**（紀錄保留）；已經送出或下載的撤不回來，回傳提醒文字請同仁聯絡人資
- 放棄報到不用處理（沒加保過）

只是放進暫存區，同仁要到「每日加退保」按「送出給人資」才真正交出去。
身分證字號配送系統已經不收（2026-09-24 改版），舊資料有的話才帶入，沒有就留空
給人資補（使用者確認）。

暫存區寫入失敗不擋狀態變更（狀態已經改好了），回傳錯誤文字讓畫面提示同仁
手動新增。
"""
import logging

from delivery.config import DELIVERY_INSURANCE_DEPARTMENT, VENDOR_MAP
from hr import insurance_draft_repository as drafts

logger = logging.getLogger(__name__)

_NOT_YET_HIRED = {"pending_onboard", "onboard_withdrawn"}


def _fields(person: dict, **dates) -> dict:
    return {
        "vendor": VENDOR_MAP.get(person.get("vendor"), person.get("vendor") or ""),
        "name": person.get("name") or "",
        "id_number": person.get("id_number") or "",
        **dates,
    }


def _actor(user: dict) -> dict:
    return {"username": (user or {}).get("username", ""), "name": (user or {}).get("name", "")}


def sync_status_change(person: dict, old_status: str, new_status: str, user: dict,
                       hire_date: str = "", resign_date: str = "") -> dict:
    """人員狀態從 old_status 改成 new_status 之後呼叫。回傳 {"msg": ..., "err": ...}
    （都可能是空字串）給畫面顯示。"""
    if old_status == new_status:
        return {"msg": "", "err": ""}
    actor = _actor(user)
    personnel_id = person.get("id", "")
    notes, warnings = [], []
    try:
        if old_status == "pending_onboard" and new_status == "employed":
            drafts.add_draft(
                DELIVERY_INSURANCE_DEPARTMENT, _fields(person, insured_date=hire_date), actor,
                kind=drafts.KIND_ADD, personnel_id=personnel_id, note="配送系統按報到",
            )
            notes.append("已加入每日加退保的待送出清單（加保）")

        if old_status == "employed" and new_status in _NOT_YET_HIRED:
            result = drafts.cancel_pending_for_personnel(personnel_id, drafts.KIND_ADD, actor, "配送系統改回未報到")
            if result["cancelled"]:
                notes.append("待送出清單裡的加保已取消")
            elif result["already_sent"]:
                warnings.append("這個人的加保已經交給人資，系統撤不回來，請聯絡人資")

        if old_status == "resigned":
            result = drafts.cancel_pending_for_personnel(personnel_id, drafts.KIND_REMOVE, actor, "配送系統改回非離職")
            if result["cancelled"]:
                notes.append("待送出清單裡的退保已取消")
            elif result["already_sent"]:
                warnings.append("這個人的退保已經交給人資，系統撤不回來，請聯絡人資")

        if new_status == "resigned" and old_status == "employed":
            drafts.add_draft(
                DELIVERY_INSURANCE_DEPARTMENT, _fields(person, withdrawn_date=resign_date), actor,
                kind=drafts.KIND_REMOVE, personnel_id=personnel_id, note="配送系統按離職",
            )
            notes.append("已加入每日加退保的待送出清單（退保）")
    except Exception:
        logger.exception("加退保暫存區寫入失敗 personnel_id=%s %s->%s", personnel_id, old_status, new_status)
        return {"msg": "", "err": "狀態已更新，但加退保待送出清單沒有寫入成功，請到「每日加退保」手動新增。"}
    return {"msg": "；".join(notes), "err": "；".join(warnings)}


def records_for_personnel(personnel_id: str) -> list:
    """人員詳細頁的「加退保紀錄」。讀取失敗就當作沒有，不讓詳細頁打不開。"""
    try:
        return drafts.drafts_for_personnel(personnel_id)
    except Exception:
        logger.exception("讀取加退保紀錄失敗 personnel_id=%s", personnel_id)
        return []
