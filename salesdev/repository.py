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

2026-09-25 新增「104 產線徵才公司」（見 salesdev/hiring_pipeline.py）：
- `salesdev_hiring_companies`：工廠自己在 104 刊產線職缺的公司，一間一筆，
  文件 ID「104_公司頁代碼」。
- `salesdev_hiring_runs`：每週抓取的結果摘要（跟每日抓職缺分開存，不然
  `latest_run()` 會拿到另一種格式的紀錄）。
- `salesdev_settings/hiring_104`：搜尋關鍵字、員工人數門檻，管理員在畫面上改。

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
HIRING_COLLECTION = "salesdev_hiring_companies"
HIRING_RUNS_COLLECTION = "salesdev_hiring_runs"
SETTINGS_COLLECTION = "salesdev_settings"
HIRING_SETTINGS_DOC = "hiring_104"

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


# ---------------------------------------------------------------------------
# 104 產線徵才公司（2026-09-25 新增）
# ---------------------------------------------------------------------------

HIRING_MAX_TITLES = 5
HIRING_MAX_AREAS = 8


def hiring_ref():
    return get_db().collection(HIRING_COLLECTION)


def hiring_runs_ref():
    return get_db().collection(HIRING_RUNS_COLLECTION)


def hiring_doc_id(source: str, cust_id: str) -> str:
    return f"{source}_{cust_id}".replace("/", "_")


def aggregate_hiring_jobs(jobs: list) -> list:
    """把一次抓到的職缺依公司彙總（一間一筆）。純函式。"""
    companies = {}
    for job in jobs:
        cust_id = job.get("cust_id", "")
        if not cust_id:
            continue
        company = companies.setdefault(
            cust_id,
            {
                "source": job.get("source", "104"),
                "cust_id": cust_id,
                "company_name": job.get("company_name", ""),
                "company_url": job.get("company_url", ""),
                "industry": "",
                "employee_count": None,
                "job_ids": set(),
                "titles": [],
                "areas": [],
                "keywords": [],
            },
        )
        company["job_ids"].add(job.get("job_id", ""))
        if job.get("industry") and not company["industry"]:
            company["industry"] = job["industry"]
        if job.get("employee_count") is not None:
            company["employee_count"] = max(company["employee_count"] or 0, job["employee_count"])
        title = job.get("job_title", "")
        if title and title not in company["titles"] and len(company["titles"]) < HIRING_MAX_TITLES:
            company["titles"].append(title)
        area = job.get("area", "")
        if area and area not in company["areas"] and len(company["areas"]) < HIRING_MAX_AREAS:
            company["areas"].append(area)
        keyword = job.get("keyword", "")
        if keyword and keyword not in company["keywords"]:
            company["keywords"].append(keyword)
    result = []
    for company in companies.values():
        company["latest_job_count"] = len(company.pop("job_ids"))
        company["latest_job_titles"] = company.pop("titles")
        result.append(company)
    return result


def upsert_hiring_companies(companies: list, seen_date: str = None) -> dict:
    """寫入這次抓到的公司。已經存在的只更新職缺數/標題/地區/最近出現；員工人數
    這次有公開才更新（這次沒公開就保留上次的），**不會**蓋掉已經對到的統一編號、
    第一次出現日期。"""
    seen_date = seen_date or today_str()
    stats = {"new": 0, "updated": 0}
    records = {hiring_doc_id(c.get("source", "104"), c["cust_id"]): c for c in companies if c.get("cust_id")}
    if not records:
        return stats
    refs = {doc_id: hiring_ref().document(doc_id) for doc_id in records}
    existing = {}
    for snapshot in get_db().get_all(list(refs.values())):
        if snapshot.exists:
            existing[snapshot.id] = snapshot.to_dict() or {}

    operations = []
    for doc_id, company in records.items():
        old = existing.get(doc_id)
        data = {
            "source": company.get("source", "104"),
            "cust_id": company["cust_id"],
            "company_name": company.get("company_name", ""),
            "company_url": company.get("company_url", ""),
            "latest_job_count": company.get("latest_job_count", 0),
            "latest_job_titles": company.get("latest_job_titles", []),
            "areas": company.get("areas", []),
            "keywords": company.get("keywords", []),
            "last_seen": seen_date,
        }
        if company.get("industry") or old is None:
            data["industry"] = company.get("industry", "")
        if company.get("employee_count") is not None:
            data["employee_count"] = company["employee_count"]
            data["employee_checked_at"] = seen_date
        elif old is None:
            data["employee_count"] = None
            data["employee_checked_at"] = ""
        if old is None:
            data.update(
                {
                    "first_seen": seen_date,
                    "tax_id": "",
                    "tax_id_source": "",
                    "created_at": time.time(),
                }
            )
            stats["new"] += 1
        else:
            stats["updated"] += 1
        operations.append((refs[doc_id], data, True))
    _commit_in_batches(operations)
    return stats


def list_hiring_companies() -> list:
    companies = [{"id": s.id, **(s.to_dict() or {})} for s in hiring_ref().stream()]
    companies.sort(
        key=lambda c: (c.get("last_seen", ""), c.get("latest_job_count", 0), c.get("employee_count") or 0),
        reverse=True,
    )
    return companies


def update_hiring_company(doc_id: str, fields: dict):
    hiring_ref().document(doc_id).set(fields, merge=True)


def hiring_size_bucket(company: dict, min_employees: int) -> str:
    """big＝達到門檻、small＝未滿門檻、unknown＝人數未知。純函式。"""
    count = company.get("employee_count")
    if count is None:
        return "unknown"
    return "big" if count >= min_employees else "small"


def default_hiring_settings() -> dict:
    from salesdev.scrapers import hiring_104

    return {
        "keywords": list(hiring_104.DEFAULT_KEYWORDS),
        "min_employees": hiring_104.DEFAULT_MIN_EMPLOYEES,
        "max_pages": hiring_104.DEFAULT_MAX_PAGES,
    }


def get_hiring_settings() -> dict:
    settings = default_hiring_settings()
    snapshot = get_db().collection(SETTINGS_COLLECTION).document(HIRING_SETTINGS_DOC).get()
    stored = (snapshot.to_dict() or {}) if snapshot.exists else {}
    if stored.get("keywords"):
        settings["keywords"] = [k for k in stored["keywords"] if k]
    for key in ("min_employees", "max_pages"):
        if isinstance(stored.get(key), int) and stored[key] > 0:
            settings[key] = stored[key]
    for key in ("updated_by", "updated_at"):
        if stored.get(key):
            settings[key] = stored[key]
    return settings


def save_hiring_settings(keywords: list, min_employees: int, max_pages: int, username: str):
    get_db().collection(SETTINGS_COLLECTION).document(HIRING_SETTINGS_DOC).set(
        {
            "keywords": keywords,
            "min_employees": min_employees,
            "max_pages": max_pages,
            "updated_by": username,
            "updated_at": now_str(),
        },
        merge=True,
    )


def record_hiring_run(summary: dict):
    hiring_runs_ref().document(str(int(time.time() * 1000))).set({**summary, "created_at": time.time()})


def latest_hiring_run():
    runs = [s.to_dict() or {} for s in hiring_runs_ref().stream()]
    if not runs:
        return None
    return max(runs, key=lambda r: r.get("created_at", 0))
