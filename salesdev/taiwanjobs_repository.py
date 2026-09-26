"""台灣就業通（2026-09-26 新增）的 Firestore 讀寫。使用者要求**跟 104 等其他來源
分開**，所以用自己的 collection，不寫進 salesdev_jobs / salesdev_hiring_companies。

- `salesdev_tj_jobs`：每一筆符合關鍵字的職缺，文件 ID `EMPLOYER_ID_HIRE_ID`。
  `detail_status`：pending＝職缺頁還沒打開、done＝已經讀過、failed＝連續失敗到上限。
- `salesdev_tj_companies`：一間公司一筆（文件 ID `EMPLOYER_ID`），畫面一列一間。
  Email／聯絡人／電話是把這間公司每一筆職缺頁抓到的合併起來。
- `salesdev_tj_runs`：每次執行的結果摘要。
- `salesdev_settings/taiwanjobs`：關鍵字、郵遞區號（管理員在畫面上改）。

用 `repository.get_db()` 取連線，測試裡 patch 那一支就好。
"""
import hashlib
import time

from salesdev import repository
from salesdev.classify import match_dispatch_company

JOBS_COLLECTION = "salesdev_tj_jobs"
COMPANIES_COLLECTION = "salesdev_tj_companies"
RUNS_COLLECTION = "salesdev_tj_runs"
SETTINGS_DOC = "taiwanjobs"

DETAIL_PENDING = "pending"
DETAIL_DONE = "done"
DETAIL_FAILED = "failed"
DETAIL_MAX_FAILURES = 3

MAX_EMAILS = 10
MAX_CONTACTS = 5
MAX_TITLES = 5
MAX_AREAS = 8


def _db():
    return repository.get_db()


def jobs_ref():
    return _db().collection(JOBS_COLLECTION)


def companies_ref():
    return _db().collection(COMPANIES_COLLECTION)


def runs_ref():
    return _db().collection(RUNS_COLLECTION)


def company_key(job: dict) -> str:
    """有 EMPLOYER_ID 就用它（同一間公司的每一筆職缺都一樣）；沒有就用公司名稱的雜湊。"""
    if job.get("employer_id"):
        return str(job["employer_id"])
    return "name_" + hashlib.sha1(job.get("company_name", "").encode("utf-8")).hexdigest()[:20]


def job_key(job: dict) -> str:
    if job.get("employer_id") and job.get("hire_id"):
        return f"{job['employer_id']}_{job['hire_id']}"
    return "url_" + hashlib.sha1(job.get("job_url", "").encode("utf-8")).hexdigest()[:24]


def _append_unique(values: list, new_values, limit: int) -> list:
    result = list(values or [])
    for value in new_values:
        if value and value not in result and len(result) < limit:
            result.append(value)
    return result


# ---------------------------------------------------------------------------
# 職缺清單（API）
# ---------------------------------------------------------------------------

def upsert_listing(jobs: list, seen_date: str = None) -> dict:
    """寫入這次 API 抓到、符合關鍵字的職缺。新的職缺標成「職缺頁待讀」；已經讀過的
    不重讀（同一筆職缺的聯絡資訊不太會變）。公司的職缺名稱/地區/最近出現一起更新。"""
    seen_date = seen_date or repository.today_str()
    stats = {"new_jobs": 0, "new_companies": 0}
    records = {}
    for job in jobs:
        records.setdefault(job_key(job), job)
    if not records:
        return stats

    job_refs = {doc_id: jobs_ref().document(doc_id) for doc_id in records}
    existing_jobs = {s.id for s in _db().get_all(list(job_refs.values())) if s.exists}
    by_company = {}
    for doc_id, job in records.items():
        by_company.setdefault(company_key(job), []).append(job)
    company_refs = {key: companies_ref().document(key) for key in by_company}
    existing_companies = {s.id: (s.to_dict() or {}) for s in _db().get_all(list(company_refs.values())) if s.exists}

    operations = []
    for doc_id, job in records.items():
        data = {
            "company_key": company_key(job),
            "company_name": job.get("company_name", ""),
            "job_title": job.get("job_title", ""),
            "job_url": job.get("job_url", ""),
            "employer_id": job.get("employer_id", ""),
            "hire_id": job.get("hire_id", ""),
            "headcount": job.get("headcount", ""),
            "salary_low": job.get("salary_low", ""),
            "salary_high": job.get("salary_high", ""),
            "city": job.get("city", ""),
            "zipno": job.get("zipno", ""),
            "keyword": job.get("keyword", ""),
            "last_seen": seen_date,
        }
        if doc_id not in existing_jobs:
            data.update(
                {"first_seen": seen_date, "detail_status": DETAIL_PENDING, "detail_fail_count": 0, "created_at": time.time()}
            )
            stats["new_jobs"] += 1
        operations.append((job_refs[doc_id], data, True))

    for key, company_jobs in by_company.items():
        old = existing_companies.get(key)
        name = company_jobs[0].get("company_name", "")
        data = {
            "company_name": name,
            "employer_id": company_jobs[0].get("employer_id", ""),
            "is_dispatch": bool(match_dispatch_company(name)),
            "latest_job_titles": _append_unique(
                (old or {}).get("latest_job_titles", []) if old else [], [j.get("job_title", "") for j in company_jobs], MAX_TITLES
            ),
            "areas": _append_unique((old or {}).get("areas", []), [j.get("city", "") for j in company_jobs], MAX_AREAS),
            "last_seen": seen_date,
        }
        if old is None:
            data.update(
                {
                    "first_seen": seen_date,
                    "emails": [],
                    "email_sources": {},
                    "contact_names": [],
                    "contact_phones": [],
                    "addresses": [],
                    "job_count": 0,
                    "created_at": time.time(),
                }
            )
            stats["new_companies"] += 1
        operations.append((company_refs[key], data, True))

    repository._commit_in_batches(operations)
    # 公司的職缺數＝這間公司所有（曾經抓到的）職缺，重新數一次
    for key in by_company:
        count = sum(1 for _ in jobs_ref().where("company_key", "==", key).stream())
        companies_ref().document(key).set({"job_count": count}, merge=True)
    return stats


# ---------------------------------------------------------------------------
# 職缺頁（聯絡資訊）
# ---------------------------------------------------------------------------

def pending_detail_jobs() -> list:
    jobs = [{"id": s.id, **(s.to_dict() or {})} for s in jobs_ref().where("detail_status", "==", DETAIL_PENDING).stream()]
    jobs.sort(key=lambda j: (j.get("first_seen", ""), j.get("created_at", 0)), reverse=True)
    return jobs


def count_jobs_by_status() -> dict:
    counts = {DETAIL_PENDING: 0, DETAIL_DONE: 0, DETAIL_FAILED: 0}
    for snapshot in jobs_ref().stream():
        status = (snapshot.to_dict() or {}).get("detail_status", DETAIL_PENDING)
        counts[status] = counts.get(status, 0) + 1
    return counts


def save_job_contact(job: dict, contact: dict):
    """一筆職缺頁讀完：職缺記下讀到的內容，公司合併 Email/聯絡人/電話/地址。"""
    emails = contact.get("emails") or []
    jobs_ref().document(job["id"]).set(
        {
            "detail_status": DETAIL_DONE,
            "detail_fetched_at": repository.now_str(),
            "emails": emails,
            "contact_name": contact.get("contact_name", ""),
            "contact_phone": contact.get("contact_phone", ""),
            "job_address": contact.get("job_address", ""),
        },
        merge=True,
    )
    ref = companies_ref().document(job.get("company_key") or company_key(job))
    snapshot = ref.get()
    company = (snapshot.to_dict() or {}) if snapshot.exists else {}
    sources = dict(company.get("email_sources") or {})
    merged = _append_unique(company.get("emails", []), emails, MAX_EMAILS)
    for email in emails:
        if email in merged:
            # Firestore 欄位名稱不能有「.」，Email 當 key 要換掉
            sources.setdefault(email.replace(".", "·"), job.get("job_url", ""))
    ref.set(
        {
            "emails": merged,
            "email_sources": sources,
            "contact_names": _append_unique(company.get("contact_names", []), [contact.get("contact_name", "")], MAX_CONTACTS),
            "contact_phones": _append_unique(company.get("contact_phones", []), [contact.get("contact_phone", "")], MAX_CONTACTS),
            "addresses": _append_unique(company.get("addresses", []), [contact.get("job_address", "")], MAX_CONTACTS),
            "detail_updated_at": repository.now_str(),
        },
        merge=True,
    )


def mark_job_failed(job: dict, error: str):
    fail_count = (job.get("detail_fail_count") or 0) + 1
    jobs_ref().document(job["id"]).set(
        {
            "detail_fail_count": fail_count,
            "detail_error": str(error)[:300],
            "detail_status": DETAIL_FAILED if fail_count >= DETAIL_MAX_FAILURES else DETAIL_PENDING,
        },
        merge=True,
    )


# ---------------------------------------------------------------------------
# 畫面用
# ---------------------------------------------------------------------------

def email_source(company: dict, email: str) -> str:
    return (company.get("email_sources") or {}).get(email.replace(".", "·"), "")


def list_companies() -> list:
    companies = [{"id": s.id, **(s.to_dict() or {})} for s in companies_ref().stream()]
    companies.sort(
        key=lambda c: (bool(c.get("emails")), c.get("last_seen", ""), c.get("job_count", 0)), reverse=True
    )
    return companies


def default_settings() -> dict:
    from salesdev.scrapers import taiwanjobs

    return {"keywords": list(taiwanjobs.DEFAULT_KEYWORDS), "zipcodes": list(taiwanjobs.DEFAULT_ZIPCODES)}


def get_settings() -> dict:
    settings = default_settings()
    snapshot = _db().collection(repository.SETTINGS_COLLECTION).document(SETTINGS_DOC).get()
    stored = (snapshot.to_dict() or {}) if snapshot.exists else {}
    for key in ("keywords", "zipcodes"):
        if stored.get(key):
            settings[key] = [v for v in stored[key] if v]
    for key in ("updated_by", "updated_at", "last_listing_at", "last_listing_ts", "listing_cursor"):
        if stored.get(key):
            settings[key] = stored[key]
    return settings


def save_settings(keywords: list, zipcodes: list, username: str):
    _db().collection(repository.SETTINGS_COLLECTION).document(SETTINGS_DOC).set(
        {
            "keywords": keywords,
            "zipcodes": zipcodes,
            "updated_by": username,
            "updated_at": repository.now_str(),
            "listing_cursor": 0,  # 郵遞區號清單改了，抓到一半的進度就不準了，從頭抓
        },
        merge=True,
    )


def save_listing_cursor(next_index: int):
    """抓清單抓到一半時間到：記下下一個要抓的郵遞區號是第幾個，下次從這裡接著抓。"""
    _db().collection(repository.SETTINGS_COLLECTION).document(SETTINGS_DOC).set(
        {"listing_cursor": next_index}, merge=True
    )


def mark_listing_done():
    _db().collection(repository.SETTINGS_COLLECTION).document(SETTINGS_DOC).set(
        {"last_listing_at": repository.now_str(), "last_listing_ts": time.time(), "listing_cursor": 0}, merge=True
    )


def request_listing_refresh():
    """管理員按「立刻重抓職缺清單」：把上次抓清單的時間清掉，下一次執行就會重抓。"""
    _db().collection(repository.SETTINGS_COLLECTION).document(SETTINGS_DOC).set(
        {"last_listing_ts": 0, "listing_cursor": 0}, merge=True
    )


def record_run(summary: dict):
    runs_ref().document(str(int(time.time() * 1000))).set({**summary, "created_at": time.time()})


def latest_run():
    runs = [s.to_dict() or {} for s in runs_ref().stream()]
    return max(runs, key=lambda r: r.get("created_at", 0)) if runs else None
