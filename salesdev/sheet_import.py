"""一次性把舊試算表「ai業務開發」（`SALESDEV_SHEET_ID`）的資料匯入 Firestore
（2026-09-24 新增）。

- 「Leads」分頁（外部爬蟲寫的職缺）→ `salesdev_jobs`，套用同一套去重＋歸併
- 「新登記工廠」分頁 → `salesdev_factories`

用表頭認分頁（有「職缺連結」＋「來源平台」的是職缺、有「工廠名稱」的是
工廠），不寫死分頁名稱。

**可以重複按**：職缺用「來源＋平台職缺編號」、工廠用統一編號/登記編號當
文件 ID，同一筆再匯一次只會更新、不會變兩筆。新舊並行那段期間，外部 GitHub
版還會繼續寫試算表，要把那段期間的資料也搬過來，再按一次就好。

**舊資料裡的反查結果只當參考**：9/17 以前外部程式會自動反查，寫進試算表的
「推測要派公司」「電話」「Email」品質很不穩（還出現過把材霈自己當成要派
公司），所以放在職缺的 `legacy` 欄位、畫面上標「舊版自動反查，僅供參考」，
不直接當成反查結果。9/17 以後的列沒有反查，電話/Email 是職缺內文裡刊登者
（派遣公司）自己的聯絡方式。原試算表不刪、不改。
"""
from config import SALESDEV_SHEET_ID
from salesdev import repository
from salesdev.classify import is_own_company
from salesdev.normalize import clean_text, job_doc_id, job_id_from_url

# 9/17 起外部程式不再自動反查（見 HANDOFF.md 2026-09-17 那節），從這天開始
# 試算表的電話/Email 是職缺內文抓的刊登者聯絡方式
_NO_AUTO_LOOKUP_SINCE = "2026-09-17"


def _read_all_tabs() -> list:
    """回傳 [(分頁名稱, 表頭, 資料列)]，不限列數（跟畫面用的讀取不同）。"""
    from google.auth import default as google_auth_default
    from googleapiclient.discovery import build

    credentials, _ = google_auth_default(scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
    service = build("sheets", "v4", credentials=credentials, cache_discovery=False)
    meta = service.spreadsheets().get(spreadsheetId=SALESDEV_SHEET_ID).execute()
    titles = [s["properties"]["title"] for s in meta.get("sheets", [])]
    if not titles:
        return []
    result = service.spreadsheets().values().batchGet(
        spreadsheetId=SALESDEV_SHEET_ID, ranges=[f"'{t}'!A:Z" for t in titles]
    ).execute()
    tabs = []
    for title, value_range in zip(titles, result.get("valueRanges", [])):
        values = value_range.get("values", [])
        if values:
            tabs.append((title, [str(h).strip() for h in values[0]], values[1:]))
    return tabs


def _row_dict(headers: list, row: list) -> dict:
    return {h: (row[i] if i < len(row) else "") for i, h in enumerate(headers) if h}


def _legacy_candidates(raw: str) -> str:
    names = [n.strip() for n in clean_text(raw).split("、") if n.strip()]
    return "、".join(n for n in names if not is_own_company(n))


def lead_from_sheet_row(row: dict):
    """把 Leads 分頁的一列轉成 (lead, legacy)；缺來源或連結的回傳 None。純函式。"""
    source = (row.get("來源平台") or "").strip()
    job_url = (row.get("職缺連結") or "").strip()
    if not source or not job_url:
        return None
    seen_date = (row.get("抓取日期") or "").strip()[:10]
    company = row.get("公司名稱", "")
    if is_own_company(company):
        return None
    after_cutover = seen_date >= _NO_AUTO_LOOKUP_SINCE
    lead = {
        "source": source,
        "job_id": job_id_from_url(source, job_url),
        "job_title": row.get("職缺名稱", ""),
        "company_name": company,
        "job_url": job_url,
        "company_url": row.get("公司頁面連結", ""),
        "area": row.get("地區", ""),
        "work_address": row.get("工作地址", ""),
        "update_date": row.get("職缺更新日期", ""),
        "matched_keyword": row.get("比對關鍵字", ""),
        "seen_date": seen_date or None,
        "phone": row.get("電話", "") if after_cutover else "",
        "phone_ext": row.get("分機", "") if after_cutover else "",
        "email": row.get("Email", "") if after_cutover else "",
    }
    legacy = {"review_status": (row.get("審查狀態") or "").strip()}
    if not after_cutover:
        legacy.update(
            {
                "candidate_client_company": _legacy_candidates(row.get("推測要派公司", "")),
                "candidate_client_note": clean_text(row.get("要派公司比對備註", "")),
                "phone": row.get("電話", ""),
                "phone_ext": row.get("分機", ""),
                "email": row.get("Email", ""),
            }
        )
    return lead, legacy


def factory_from_sheet_row(row: dict):
    from services.factory_watch_service import _dedup_key

    name = (row.get("工廠名稱") or "").strip()
    if not name:
        return None
    record = {
        "name": name,
        "tax_id": (row.get("統一編號") or "").strip(),
        "address": (row.get("工廠地址") or "").strip(),
        "industry": (row.get("行業別") or "").strip(),
        "products": (row.get("主要產品") or "").strip(),
        "approval_date_raw": (row.get("登記核准日期") or "").strip(),
        "reg_no": (row.get("工廠登記編號") or "").strip(),
        "found_date": (row.get("發現日期") or "").strip()[:10],
    }
    record["dedup_key"] = _dedup_key(record)
    return record


def import_from_sheet(username: str) -> dict:
    """回傳 {"jobs_read", "new_jobs", "updated_jobs", "new_groups", "internal_jobs",
    "selected_groups", "factories_read", "new_factories", "error"}。"""
    stats = {
        "jobs_read": 0, "new_jobs": 0, "updated_jobs": 0, "new_groups": 0, "internal_jobs": 0,
        "selected_groups": 0, "factories_read": 0, "new_factories": 0, "error": "",
    }
    if not SALESDEV_SHEET_ID:
        stats["error"] = "尚未設定 SALESDEV_SHEET_ID，不知道要從哪份試算表匯入。"
        return stats
    try:
        tabs = _read_all_tabs()
    except Exception as exc:
        stats["error"] = f"讀取試算表失敗：{exc}（請確認試算表有分享「檢視者」權限給 Cloud Run 服務帳戶）"
        return stats

    leads, legacy_by_doc, factories = [], {}, []
    for _title, headers, rows in tabs:
        header_set = set(headers)
        if {"職缺連結", "來源平台"} <= header_set:
            for row in rows:
                parsed = lead_from_sheet_row(_row_dict(headers, row))
                if parsed:
                    lead, legacy = parsed
                    leads.append(lead)
                    legacy_by_doc[job_doc_id(lead["source"], lead["job_id"])] = (lead, legacy)
        elif "工廠名稱" in header_set:
            for row in rows:
                record = factory_from_sheet_row(_row_dict(headers, row))
                if record:
                    factories.append(record)

    stats["jobs_read"] = len(leads)
    stats["factories_read"] = len(factories)
    try:
        job_stats = repository.upsert_jobs(leads)
        for key in ("new_jobs", "updated_jobs", "new_groups", "internal_jobs"):
            stats[key] = job_stats[key]

        operations = [
            (repository.jobs_ref().document(doc_id), {"legacy": legacy, "imported_from_sheet": True}, True)
            for doc_id, (_lead, legacy) in legacy_by_doc.items()
        ]
        repository._commit_in_batches(operations)

        # 試算表裡已經勾選「已勾選待反查」的，歸併後那一組也標成已勾選
        selected_group_ids = {
            repository.build_job_fields(lead)["home_group_id"]
            for lead, legacy in legacy_by_doc.values()
            if legacy.get("review_status") == repository.STATUS_SELECTED
        }
        stats["selected_groups"] = repository.select_groups_for_lookup(sorted(selected_group_ids), username or "舊試算表匯入")

        stats["new_factories"] = repository.upsert_factories(factories)
    except Exception as exc:
        stats["error"] = f"寫入資料庫失敗：{exc}"
    return stats
