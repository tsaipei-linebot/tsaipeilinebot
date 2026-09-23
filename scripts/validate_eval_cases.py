"""檢查考題格式（單句考題、多輪對話劇本）：欄位名稱、選項值、地名都要是程式認得的。
寫新考題之後先跑這支，避免考題本身寫錯（例如把「理貨」寫成類型，正確是「理貨/倉儲」）。

    python scripts/validate_eval_cases.py scripts/understanding_eval_cases.json
    python scripts/validate_eval_cases.py scripts/conversation_eval_cases.json
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from tests import _env  # noqa: E402,F401
from tests import _stub_gcp  # noqa: E402
_stub_gcp.install()

import services.understanding_service as us  # noqa: E402
from services.job_listing_submit_service import TAIWAN_CITY_DISTRICTS  # noqa: E402

_PLACES = set()
for _county, _districts in TAIWAN_CITY_DISTRICTS.items():
    _PLACES.update({_county, _county[:2], _county.replace("台", "臺")})
    for _full in _districts:
        _core = _full[len(_county):] if _full.startswith(_county) else _full
        _PLACES.update({_full, _core, _core[:-1]})

_ENUMS = {
    "categories": us.CATEGORIES, "exclude_categories": us.CATEGORIES, "shifts": us.SHIFTS, "exclude_shifts": us.SHIFTS,
    "leaves": us.LEAVES, "exclude_leaves": us.LEAVES, "pays": us.PAYS, "exclude_pays": us.PAYS,
    "broaden": us.BROADEN_DIMENSIONS, "worktype": [""] + us.WORKTYPES, "salary_kind": ["", "月薪", "時薪"],
    "handoff_reason": [""] + us.HANDOFF_REASONS, "intent": us.INTENTS,
}
_FORM_FIELDS = set(us._LIST_FIELDS) | set(us._STR_FIELDS) | {"salary_min"}
_CASE_KEYS = {"id", "text", "slots", "last_bot", "expect", "has", "any", "forbid", "nonempty", "intent_in", "intent_not", "note"}
_SLOTS = {"location", "category", "brand", "worktype", "shift", "leave", "pay", "salary", "benefit", "exclude"}
_SLOT_ENUMS = {
    "category": us.CATEGORIES + ["不限"], "shift": us.SHIFTS, "leave": us.LEAVES, "pay": us.PAYS, "worktype": us.WORKTYPES,
}
_TURN_KEYS = {"say", "slots", "slots_has", "slots_not", "reply_has_any", "reply_not", "jobs", "note"}
_EXCLUDE_KINDS = {"location", "category", "shift", "leave", "pay", "worktype", "benefit", "brand"}


def _place_ok(v):
    v = str(v).replace("臺", "台")
    return v in _PLACES or v == "不限"


def _check_value(field, value, where, errors):
    if field in _ENUMS:
        values = value if isinstance(value, list) else [value]
        for v in values:
            if v not in _ENUMS[field]:
                errors.append(f"{where}：{field} 的「{v}」不是選項（可以用：{'、'.join(x or '（空白）' for x in _ENUMS[field])}）")
    if field in ("locations", "exclude_locations"):
        for v in value:
            if not _place_ok(v):
                errors.append(f"{where}：{field} 的「{v}」不是台灣的縣市或行政區")


def _check_slot_value(slot, value, where, errors):
    values = value if isinstance(value, list) else ([value] if value else [])
    for v in values:
        if slot == "exclude":
            kind, _, label = str(v).partition(":")
            if kind not in _EXCLUDE_KINDS or not label:
                errors.append(f"{where}：exclude 要寫成「類別:值」，例如「shift:大夜班」，不是「{v}」")
            elif kind in ("category", "shift", "leave", "pay", "worktype"):
                _check_slot_value(kind, label, where, errors)
            elif kind == "location" and not _place_ok(label):
                errors.append(f"{where}：exclude 的地名「{label}」不認得")
        elif slot == "location":
            for part in str(v).split("|"):
                if not _place_ok(part):
                    errors.append(f"{where}：location 的「{part}」不是台灣的縣市或行政區")
        elif slot in _SLOT_ENUMS:
            for part in str(v).split("|"):
                if part not in _SLOT_ENUMS[slot]:
                    errors.append(f"{where}：{slot} 的「{part}」不是選項（可以用：{'、'.join(_SLOT_ENUMS[slot])}）")
        elif slot == "salary" and not (str(v).startswith(("月薪", "時薪")) and str(v)[2:].isdigit()):
            errors.append(f"{where}：salary 要寫成「月薪30000」或「時薪200」，不是「{v}」")


def validate_single(cases):
    errors, ids = [], set()
    for i, case in enumerate(cases):
        where = f"第{i + 1}題（{case.get('id', '')}）"
        if case.get("id") in ids:
            errors.append(f"{where}：id 重複")
        ids.add(case.get("id"))
        if not case.get("text"):
            errors.append(f"{where}：沒有 text")
        for key in case:
            if key not in _CASE_KEYS:
                errors.append(f"{where}：不認得的欄位「{key}」")
        for group in ("expect", "has", "any", "forbid"):
            for field, value in (case.get(group) or {}).items():
                if field not in _FORM_FIELDS:
                    errors.append(f"{where}：{group} 裡不認得的欄位「{field}」")
                    continue
                if group in ("has", "any", "forbid") and not isinstance(value, list):
                    errors.append(f"{where}：{group}.{field} 要是清單")
                    continue
                _check_value(field, value, where, errors)
        for field in ("intent_in", "intent_not"):
            _check_value("intent", case.get(field) or [], where, errors)
        for slot, value in (case.get("slots") or {}).items():
            if slot not in _SLOTS:
                errors.append(f"{where}：slots 裡不認得的條件「{slot}」")
            else:
                _check_slot_value(slot, value, where, errors)
        if not any(case.get(k) for k in ("expect", "has", "any", "forbid", "nonempty", "intent_in", "intent_not")):
            errors.append(f"{where}：沒有任何檢查項目")
    return errors


def validate_conversations(cases):
    errors, ids = [], set()
    for i, case in enumerate(cases):
        where0 = f"第{i + 1}段（{case.get('id', '')}）"
        if case.get("id") in ids:
            errors.append(f"{where0}：id 重複")
        ids.add(case.get("id"))
        if not case.get("turns"):
            errors.append(f"{where0}：沒有 turns")
        for j, turn in enumerate(case.get("turns") or []):
            where = f"{where0}第{j + 1}輪"
            if not turn.get("say"):
                errors.append(f"{where}：沒有 say")
            for key in turn:
                if key not in _TURN_KEYS:
                    errors.append(f"{where}：不認得的欄位「{key}」")
            for group in ("slots", "slots_has", "slots_not"):
                for slot, value in (turn.get(group) or {}).items():
                    if slot not in _SLOTS:
                        errors.append(f"{where}：{group} 裡不認得的條件「{slot}」")
                    else:
                        _check_slot_value(slot, value, where, errors)
    return errors


def main():
    path = sys.argv[1]
    cases = json.load(open(path, encoding="utf-8"))
    errors = validate_conversations(cases) if cases and "turns" in cases[0] else validate_single(cases)
    for e in errors:
        print("❌", e)
    print(f"共 {len(cases)} 筆，{len(errors)} 個問題")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
