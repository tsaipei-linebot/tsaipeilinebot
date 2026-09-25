"""104 產線徵才公司每週抓取（2026-09-25 新增）：由 Cloud Scheduler 每週呼叫
`POST /internal/salesdev/hiring/run` 觸發（跟每日抓職缺共用同一把密鑰）。

流程：
1. 用畫面上設定的關鍵字搜 104（全台、電子資訊／半導體＋一般製造業），排除派遣
   公司跟 104 廣告位，依公司彙總、寫進 Firestore（`repository.upsert_hiring_companies()`）。
   員工人數直接用搜尋結果每一筆帶的 `employeeCount`（公司沒公開就是「人數未知」）。
2. 還沒有統一編號的公司，用經濟部《登記工廠名錄》（新登記工廠掃描用的同一份）
   以公司名稱對統一編號；名錄對不到的，再用 g0v 公司資料庫查（名稱要完全一樣
   才算）。都對不到就留空，交給 Cowork 查。

**時間上限**：Cloud Run 一個請求預設最多 300 秒。第 1 步在打 104，每次請求至少
間隔 1.5 秒，限制在 `SALESDEV_HIRING_TIME_BUDGET_SECONDS`（預設 180 秒）內；時間到
就停，已經抓到的照樣存。第 2 步不打 104，排在最後面。

（2026-09-25 第一版另外逐一打開 104 公司頁取員工人數，正式環境全部取不到、還把
時間用光，已經拿掉，見 salesdev/scrapers/hiring_104.py 開頭。）
"""
import logging
import time
import unicodedata

from config import SALESDEV_HIRING_TIME_BUDGET_SECONDS
from salesdev import repository
from salesdev.scrapers import hiring_104
from salesdev.scrapers.base import HttpClient

logger = logging.getLogger(__name__)

# g0v 公司資料庫一次查一間，每次最多查這麼多間，避免拖太久
G0V_LOOKUP_LIMIT = 20
# 整個請求最晚在開始後這麼多秒內結束（Cloud Run 預設 300 秒就會中斷請求）
HARD_LIMIT_SECONDS = 270


# ---------------------------------------------------------------------------
# 統一編號：公司名稱對經濟部登記工廠名錄
# ---------------------------------------------------------------------------

def registered_name(company_name: str) -> str:
    """104 的公司名稱常是「品牌_登記名稱」（例如「Garmin_台灣國際航電股份有限
    公司」），取底線後面那段，再統一全半形、台/臺、去空白。純函式。"""
    name = (company_name or "").rsplit("_", 1)[-1]
    name = unicodedata.normalize("NFKC", name).replace("臺", "台")
    return "".join(name.split())


def registry_company_key(factory_name: str) -> str:
    """工廠名稱常是「公司名稱＋廠名」（例如「德勝科技股份有限公司二廠」），
    取到「有限公司」為止當成公司名稱。純函式。"""
    name = registered_name(factory_name)
    index = name.find("有限公司")
    return name[: index + 4] if index >= 0 else name


def match_tax_ids(target_names, registry_records) -> dict:
    """回傳 {登記名稱: 統一編號}，只收名錄裡剛好對到一個統一編號的（同名對到
    好幾個不同統編就不猜）。純函式（registry_records 是可迭代的名錄資料）。"""
    targets = set(target_names)
    found = {}
    for record in registry_records:
        tax_id = (record.get("tax_id") or "").strip()
        if not tax_id:
            continue
        key = registry_company_key(record.get("name", ""))
        if key in targets:
            found.setdefault(key, set()).add(tax_id)
    return {name: next(iter(ids)) for name, ids in found.items() if len(ids) == 1}


def _fill_tax_ids(summary: dict, hard_deadline: float):
    pending = [c for c in repository.list_hiring_companies() if not c.get("tax_id")]
    if not pending:
        return
    by_name = {}
    for company in pending:
        by_name.setdefault(registered_name(company.get("company_name", "")), []).append(company["id"])

    matched = {}
    try:
        from services import factory_watch_service

        matched = match_tax_ids(by_name.keys(), factory_watch_service.iter_registry_records())
    except Exception as exc:
        logger.exception("104 產線公司：讀登記工廠名錄失敗")
        summary["errors"].append(f"讀經濟部登記工廠名錄失敗（統一編號這次沒對）：{exc}")
    for name, tax_id in matched.items():
        for doc_id in by_name[name]:
            repository.update_hiring_company(doc_id, {"tax_id": tax_id, "tax_id_source": "登記工廠名錄"})
    summary["tax_id_matched"] = sum(len(by_name[n]) for n in matched)

    try:
        from services.company_registry_lookup import lookup_company
    except Exception:
        return
    g0v_matched = 0
    for name in [n for n in by_name if n not in matched and n][:G0V_LOOKUP_LIMIT]:
        if time.monotonic() >= hard_deadline:
            break
        result = lookup_company(name)
        if result and result.get("tax_id") and registered_name(result.get("name", "")) == name:
            for doc_id in by_name[name]:
                repository.update_hiring_company(doc_id, {"tax_id": result["tax_id"], "tax_id_source": "g0v 公司資料庫"})
            g0v_matched += len(by_name[name])
    summary["tax_id_matched"] += g0v_matched


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def run_weekly_hiring_scan(time_budget_seconds: int = None, client: HttpClient = None) -> dict:
    started = time.monotonic()
    budget = time_budget_seconds or SALESDEV_HIRING_TIME_BUDGET_SECONDS
    settings = repository.get_hiring_settings()
    summary = {
        "run_date": repository.today_str(),
        "started_at": repository.now_str(),
        "keywords": settings["keywords"],
        "raw_items": 0,
        "kept_jobs": 0,
        "ad_skipped": 0,
        "dispatch_skipped": 0,
        "industry_skipped": 0,
        "companies_found": 0,
        "new_companies": 0,
        "employee_known": 0,
        "tax_id_matched": 0,
        "deadline_hit": False,
        "errors": [],
    }
    client = client or HttpClient(deadline=started + budget)

    jobs, stats = hiring_104.collect_hiring_jobs(client, settings["keywords"], settings["max_pages"])
    for key in ("raw_items", "ad_skipped", "dispatch_skipped", "industry_skipped"):
        summary[key] = stats[key]
    summary["errors"].extend(stats["errors"][:5])
    summary["deadline_hit"] = stats["deadline_hit"]
    summary["kept_jobs"] = len(jobs)
    if not stats["raw_items"]:
        summary["errors"].append("104 沒有回傳任何職缺（可能被擋，或 104 改了搜尋格式），請通知系統管理窗口")
    elif not jobs and stats["industry_skipped"]:
        summary["errors"].append(
            f"104 回傳 {stats['raw_items']} 筆，但產業都不符合，產業篩選條件可能要調整，請通知系統管理窗口"
        )

    try:
        companies = repository.aggregate_hiring_jobs(jobs)
        summary["companies_found"] = len(companies)
        summary["employee_known"] = sum(1 for c in companies if c.get("employee_count") is not None)
        summary["new_companies"] = repository.upsert_hiring_companies(companies)["new"]
        if len(companies) >= 10 and not summary["employee_known"]:
            summary["errors"].append(
                f"找到 {len(companies)} 間公司，但搜尋結果都沒有員工人數（104 可能改了欄位），請通知系統管理窗口"
            )
    except Exception as exc:
        logger.exception("104 產線公司：寫入 Firestore 失敗")
        summary["errors"].append(f"寫入資料庫失敗：{exc}")

    try:
        _fill_tax_ids(summary, started + HARD_LIMIT_SECONDS)
    except Exception as exc:
        logger.exception("104 產線公司：對統一編號失敗")
        summary["errors"].append(f"對統一編號失敗：{exc}")

    summary["duration_seconds"] = round(time.monotonic() - started, 1)
    try:
        repository.record_hiring_run(summary)
    except Exception:
        logger.exception("104 產線公司：記錄執行結果失敗")
    print(f"[104 產線公司] {summary}")
    return summary
