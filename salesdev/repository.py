"""業務開發資料的 Firestore 讀寫（2026-09-24 新增，取代原本的 Google 試算表）。

**為什麼改存 Firestore**：①歸併是「一組底下掛很多筆職缺」的一對多關係，
試算表只有平面表格，硬塞很容易被改亂；②原本寫回試算表是靠「第幾列」，
同仁排序、插列、刪列之後就會寫錯列；③試算表越長 /salesdev 越慢，而且
每個分頁最多只顯示 500 列。平台其他部門的資料本來就都在 Firestore。

四個 collection：

- `salesdev_jobs`：每一筆職缺。文件 ID 是「來源_平台職缺編號」（見
  `normalize.job_doc_id()`），同一筆每天重抓只會更新 `last_seen`。
- `salesdev_groups`：歸併後的「一組」（同地點，或同公司同標題）。審查、
  反查、備註、聯絡紀錄都記在組上，不記在單筆職缺上——同一個地點只需要
  處理一次。
- `salesdev_factories`：每週新登記工廠（原本寫試算表「新登記工廠」分頁）。
- `salesdev_runs`：每次自動抓取的結果摘要，畫面上方顯示「最近一次抓取」
  用，同仁一眼就能看出排程有沒有在跑。

職缺是不是「派遣公司內部職缺」：`internal_reason`（自動判斷的關鍵字）＋
`internal_override`（人工改過就以人工為準：True=是內部職缺、False=不是、
None=沒改過）。內部職缺不屬於任何一組（`group_id` 是空字串），但
`home_group_id` 永遠記著它「本來應該在哪一組」，人工改回來時才知道要放回
哪裡。
"""
import time
from datetime import datetime

from config import TAIPEI_TZ
from platform_db import get_db
from salesdev.classify import internal_job_reason
from salesdev.normalize import clean_text, group_doc_id, group_key_for, job_doc_id, parse_address

JOBS_COLLECTION = "salesdev_jobs"
GROUPS_COLLECTION = "salesdev_groups"
FACTORIES_COLLECTION = "salesdev_factories"
RUNS_COLLECTION = "salesdev_runs"

STATUS_PENDING = "待審查"
STATUS_SELECTED = "已勾選待反查"
STATUS_DONE = "已反查（待人工確認）"
STATUS_NOT_FOUND = "查無結果"
STATUS_SKIPPED = "不追蹤"
REVIEW_STATUSES = (STATUS_PENDING, STATUS_SELECTED, STATUS_DONE, STATUS_NOT_FOUND, STATUS_SKIPPED)

# 反查結果欄位（階段 2 的 Cowork 反查輸入頁、以及現在同仁手動填寫都寫這幾欄）
LOOKUP_FIELDS = (
    "client_company",
    "client_phone",
    "client_phone_ext",
    "client_email",
    "client_source_url",
    "lookup_note",
)

_BATCH_LIMIT = 400  # Firestore 單一 batch 上限 500 筆寫入，留點餘裕


def jobs_ref():
    return get_db().collection(JOBS_COLLECTION)


def groups_ref():
    return get_db().collection(GROUPS_COLLECTION)


def factories_ref():
    return get_db().collection(FACTORIES_COLLECTION)


def runs_ref():
    return get_db().collection(RUNS_COLLECTION)


def today_str() -> str:
    return datetime.now(TAIPEI_TZ).date().isoformat()


def now_str() -> str:
    return datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M")


def _commit_in_batches(operations: list):
    """operations: [(ref, data, merge)]。"""
    db = get_db()
    for start in range(0, len(operations), _BATCH_LIMIT):
        batch = db.batch()
        for ref, data, merge in operations[start:start + _BATCH_LIMIT]:
            batch.set(ref, data, merge=merge)
        batch.commit()


# ---------------------------------------------------------------------------
# 職缺
# ---------------------------------------------------------------------------

def is_internal(job: dict) -> bool:
    override = job.get("internal_override")
    if override is not None:
        return bool(override)
    return bool(job.get("internal_reason"))


def build_job_fields(lead: dict) -> dict:
    """把爬蟲（或舊試算表）的一筆整理成要存的欄位（不含日期/人工欄位）。
    純函式。`lead` 至少要有 source、job_id、job_title、company_name、
    job_url、work_address。"""
    title = clean_text(lead.get("job_title", ""))
    company = clean_text(lead.get("company_name", ""))
    address = clean_text(lead.get("work_address", ""))
    key, label, kind = group_key_for(address, company, title)
    return {
        "source": lead.get("source", ""),
        "job_id": lead.get("job_id", ""),
        "job_title": title,
        "company_name": company,
        "job_url": (lead.get("job_url") or "").strip(),
        "company_url": (lead.get("company_url") or "").strip(),
        "area": clean_text(lead.get("area", "")),
        "work_address": address,
        "update_date": str(lead.get("update_date", "") or ""),
        "poster_phone": lead.get("phone", "") or "",
        "poster_phone_ext": lead.get("phone_ext", "") or "",
        "poster_email": lead.get("email", "") or "",
        "matched_keyword": lead.get("matched_keyword", "") or "",
        "internal_reason": internal_job_reason(title),
        "group_key": key,
        "group_label": label,
        "group_kind": kind,
        "home_group_id": group_doc_id(key),
    }


def upsert_jobs(leads: list, seen_date: str = None, extra_new_fields: dict = None) -> dict:
    """新增或更新一批職缺，並重算受影響的組。回傳統計：
    {"new_jobs", "updated_jobs", "internal_jobs", "new_groups", "new_job_ids", "touched_group_ids"}

    已存在的職缺只更新內容與 `last_seen`，**不會**動到人工設定的
    `internal_override`、第一次出現日期、舊試算表帶進來的 legacy 資料。
    `extra_new_fields` 只套用在「新建立」的職缺上（舊資料匯入用）。"""
    seen_date = seen_date or today_str()
    records = {}
    for lead in leads:
        fields = build_job_fields(lead)
        if not fields["source"] or not fields["job_id"]:
            continue
        records[job_doc_id(fields["source"], fields["job_id"])] = (fields, lead)

    stats = {
        "new_jobs": 0,
        "updated_jobs": 0,
        "internal_jobs": 0,
        "new_groups": 0,
        "new_job_ids": [],
        "touched_group_ids": [],
    }
    if not records:
        return stats

    refs = {doc_id: jobs_ref().document(doc_id) for doc_id in records}
    existing = {}
    for snapshot in get_db().get_all(list(refs.values())):
        if snapshot.exists:
            existing[snapshot.id] = snapshot.to_dict() or {}

    touched = set()
    operations = []
    for doc_id, (fields, lead) in records.items():
        old = existing.get(doc_id)
        data = dict(fields)
        # 舊試算表匯入時每一列有自己的「抓取日期」，放在 lead["seen_date"]
        lead_seen = lead.get("seen_date") or seen_date
        data["last_seen"] = max(lead_seen, (old or {}).get("last_seen", "") or "")
        if old is None:
            data["first_seen"] = lead_seen
            data["internal_override"] = None
            data["created_at"] = time.time()
            if extra_new_fields:
                data.update(extra_new_fields(lead) if callable(extra_new_fields) else extra_new_fields)
            stats["new_jobs"] += 1
            stats["new_job_ids"].append(doc_id)
        else:
            data["first_seen"] = min(lead_seen, old.get("first_seen") or lead_seen)
            data["internal_override"] = old.get("internal_override")
            stats["updated_jobs"] += 1
            if old.get("group_id"):
                touched.add(old["group_id"])

        data["group_id"] = "" if is_internal(data) else data["home_group_id"]
        if data["group_id"]:
            touched.add(data["group_id"])
        else:
            stats["internal_jobs"] += 1
        operations.append((refs[doc_id], data, True))

    _commit_in_batches(operations)
    stats["new_groups"] = len(recompute_groups(touched))
    stats["touched_group_ids"] = sorted(touched)
    return stats


def get_job(job_doc_id_value: str):
    snapshot = jobs_ref().document(job_doc_id_value).get()
    if not snapshot.exists:
        return None
    return {"id": snapshot.id, **(snapshot.to_dict() or {})}


def list_jobs_in_group(group_id: str) -> list:
    jobs = [{"id": s.id, **(s.to_dict() or {})} for s in jobs_ref().where("group_id", "==", group_id).stream()]
    jobs.sort(key=lambda j: (j.get("last_seen", ""), j.get("first_seen", "")), reverse=True)
    return jobs


def list_internal_jobs() -> list:
    jobs = [{"id": s.id, **(s.to_dict() or {})} for s in jobs_ref().where("group_id", "==", "").stream()]
    jobs.sort(key=lambda j: j.get("last_seen", ""), reverse=True)
    return jobs


def list_all_jobs() -> list:
    return [{"id": s.id, **(s.to_dict() or {})} for s in jobs_ref().stream()]


def set_job_internal(job_doc_id_value: str, internal: bool, username: str = "") -> str:
    """人工改「是不是內部職缺」。回傳這筆職缺之後所屬的組 ID（改成內部
    職缺時是空字串），找不到職缺回傳 None。"""
    ref = jobs_ref().document(job_doc_id_value)
    snapshot = ref.get()
    if not snapshot.exists:
        return None
    job = snapshot.to_dict() or {}
    old_group = job.get("group_id", "")
    new_group = "" if internal else job.get("home_group_id", "")
    ref.set(
        {
            "internal_override": bool(internal),
            "group_id": new_group,
            "internal_changed_by": username,
            "internal_changed_at": now_str(),
        },
        merge=True,
    )
    recompute_groups({g for g in (old_group, new_group) if g})
    return new_group


# ---------------------------------------------------------------------------
# 組
# ---------------------------------------------------------------------------

def aggregate_group(jobs: list) -> dict:
    """從組裡的職缺算出顯示用的彙總欄位。純函式。"""
    if not jobs:
        return {"job_count": 0, "agency_names": [], "sources": [], "sample_title": "", "first_seen": "", "last_seen": ""}
    latest = max(jobs, key=lambda j: (j.get("last_seen", ""), j.get("first_seen", "")))
    return {
        "job_count": len(jobs),
        "agency_names": sorted({j.get("company_name", "") for j in jobs if j.get("company_name")}),
        "sources": sorted({j.get("source", "") for j in jobs if j.get("source")}),
        "sample_title": latest.get("job_title", ""),
        "first_seen": min(j.get("first_seen", "") or "" for j in jobs),
        "last_seen": max(j.get("last_seen", "") or "" for j in jobs),
    }


def _new_group_fields(sample_job: dict) -> dict:
    parts = parse_address(sample_job.get("work_address", "")) if sample_job.get("group_kind") == "address" else {}
    return {
        "group_key": sample_job.get("group_key", ""),
        "kind": sample_job.get("group_kind", ""),
        "label": sample_job.get("group_label", ""),
        "county": parts.get("county", ""),
        "district": parts.get("district", ""),
        "road": parts.get("road", ""),
        "review_status": STATUS_PENDING,
        "note": "",
        "contact_logs": [],
        **{field: "" for field in LOOKUP_FIELDS},
        "created_at": time.time(),
    }


def recompute_groups(group_ids) -> list:
    """重算這些組的彙總欄位；組還不存在就建立（狀態「待審查」）。回傳新建立
    的組 ID。組裡的職缺全部被移走時組保留（反查結果、備註不能跟著消失），
    只是 job_count 變 0、畫面上不顯示。"""
    created = []
    for group_id in sorted(set(g for g in group_ids if g)):
        jobs = list_jobs_in_group(group_id)
        ref = groups_ref().document(group_id)
        snapshot = ref.get()
        data = aggregate_group(jobs)
        data["updated_at"] = time.time()
        if not snapshot.exists:
            if not jobs:
                continue
            data.update(_new_group_fields(jobs[0]))
            created.append(group_id)
        ref.set(data, merge=True)
    return created


def list_groups(include_empty: bool = False) -> list:
    groups = [{"id": s.id, **(s.to_dict() or {})} for s in groups_ref().stream()]
    if not include_empty:
        groups = [g for g in groups if g.get("job_count", 0) > 0]
    groups.sort(key=lambda g: (g.get("last_seen", ""), g.get("job_count", 0)), reverse=True)
    return groups


def get_group(group_id: str):
    snapshot = groups_ref().document(group_id).get()
    if not snapshot.exists:
        return None
    return {"id": snapshot.id, **(snapshot.to_dict() or {})}


def select_groups_for_lookup(group_ids: list, username: str) -> int:
    """把「待審查」的組改成「已勾選待反查」。寫入前重新讀一次目前狀態，
    只改現在真的還是「待審查」的——避免畫面停留很久才送出時，把別人剛
    處理好的結果蓋回去（跟原本試算表版本同一個保護）。"""
    count = 0
    for group_id in group_ids:
        group = get_group(group_id)
        if not group or group.get("review_status") != STATUS_PENDING:
            continue
        groups_ref().document(group_id).set(
            {"review_status": STATUS_SELECTED, "selected_by": username, "selected_at": now_str()}, merge=True
        )
        count += 1
    return count


def update_group_review(group_id: str, status: str, lookup: dict, username: str) -> bool:
    """同仁在組的詳細頁手動改狀態/填反查結果。"""
    if status not in REVIEW_STATUSES or not get_group(group_id):
        return False
    data = {field: (lookup.get(field) or "").strip() for field in LOOKUP_FIELDS}
    data.update({"review_status": status, "reviewed_by": username, "reviewed_at": now_str()})
    groups_ref().document(group_id).set(data, merge=True)
    return True


def update_group_note(group_id: str, note: str, username: str) -> bool:
    if not get_group(group_id):
        return False
    groups_ref().document(group_id).set(
        {"note": (note or "").strip(), "note_updated_by": username, "note_updated_at": now_str()}, merge=True
    )
    return True


def add_contact_log(group_id: str, text: str, username: str) -> bool:
    text = (text or "").strip()
    group = get_group(group_id)
    if not text or not group:
        return False
    logs = list(group.get("contact_logs") or [])
    logs.append({"at": now_str(), "by": username, "text": text})
    groups_ref().document(group_id).set({"contact_logs": logs}, merge=True)
    return True


# ---------------------------------------------------------------------------
# 新登記工廠
# ---------------------------------------------------------------------------

def upsert_factories(records: list, found_date: str = None) -> int:
    """records 是 factory_watch_service 整理好的工廠資料，`dedup_key` 當文件 ID。
    已經存在的不覆蓋「發現日期」。回傳新增筆數。"""
    found_date = found_date or today_str()
    if not records:
        return 0
    refs = {r["dedup_key"].replace("/", "_"): r for r in records if r.get("dedup_key")}
    existing_ids = set()
    ref_objs = {doc_id: factories_ref().document(doc_id) for doc_id in refs}
    for snapshot in get_db().get_all(list(ref_objs.values())):
        if snapshot.exists:
            existing_ids.add(snapshot.id)
    operations = []
    for doc_id, record in refs.items():
        data = {k: v for k, v in record.items() if k != "dedup_key"}
        if doc_id not in existing_ids:
            data.setdefault("found_date", found_date)
        else:
            data.pop("found_date", None)
        operations.append((ref_objs[doc_id], data, True))
    _commit_in_batches(operations)
    return len(set(refs) - existing_ids)


def list_factories() -> list:
    factories = [{"id": s.id, **(s.to_dict() or {})} for s in factories_ref().stream()]
    factories.sort(key=lambda f: (f.get("found_date", ""), f.get("approval_date_raw", "")), reverse=True)
    return factories


# ---------------------------------------------------------------------------
# 執行紀錄
# ---------------------------------------------------------------------------

def record_run(summary: dict):
    runs_ref().document(str(int(time.time() * 1000))).set({**summary, "created_at": time.time()})


def latest_run():
    runs = [s.to_dict() or {} for s in runs_ref().stream()]
    if not runs:
        return None
    return max(runs, key=lambda r: r.get("created_at", 0))
