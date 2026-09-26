"""台灣就業通抓取主流程（2026-09-26 新增）：Cloud Scheduler 每小時呼叫
`POST /internal/salesdev/taiwanjobs/run`（跟每日抓職缺共用同一把密鑰）。

每次執行做兩件事：
1. **職缺清單**：距離上次超過 `LISTING_INTERVAL_SECONDS`（約一天）才重抓。對每個郵遞
   區號打一次職缺 API（一次最多 1000 筆），職缺名稱符合關鍵字的寫進 Firestore，新的
   職缺標成「職缺頁待讀」。郵遞區號很多，一次抓不完就記下抓到第幾個，下一次接著抓。
2. **職缺頁**：剩下的時間逐一打開「待讀」的職缺頁抓 Email／聯絡人／電話，新的先讀。

**為什麼每小時跑、每次只跑一段**：Cloud Run 一個請求預設最多 300 秒，職缺頁每頁
至少間隔 1.5 秒，一次大約只能讀 100～150 頁；第一次抓清單可能有上千筆職缺，
分很多次慢慢讀完，每一次讀到的都會先存起來。時間上限
`SALESDEV_TJ_TIME_BUDGET_SECONDS`（預設 240 秒）。
"""
import logging
import time

from config import SALESDEV_TJ_TIME_BUDGET_SECONDS
from salesdev import repository
from salesdev import taiwanjobs_repository as tj_repo
from salesdev.scrapers import taiwanjobs
from salesdev.scrapers.base import DeadlineReached, HttpClient

logger = logging.getLogger(__name__)

LISTING_INTERVAL_SECONDS = 20 * 3600


def _listing_due(settings: dict, now: float) -> bool:
    if settings.get("listing_cursor"):
        return True  # 上次抓到一半，接著抓
    return now - (settings.get("last_listing_ts") or 0) >= LISTING_INTERVAL_SECONDS


def _refresh_listing(client: HttpClient, settings: dict, summary: dict):
    """每個郵遞區號抓完就先存，時間到的話記下抓到第幾個，下一次從那裡接著抓。"""
    zipcodes = settings["zipcodes"]
    start = settings.get("listing_cursor") or 0
    if start >= len(zipcodes):
        start = 0
    summary["listing_refreshed"] = True
    summary["listing_from"] = start
    failed = 0
    for index in range(start, len(zipcodes)):
        zipno = zipcodes[index]
        try:
            jobs = taiwanjobs.fetch_api_jobs(client, zipno)
        except DeadlineReached:
            tj_repo.save_listing_cursor(index)
            summary["listing_done"] = False
            raise
        except Exception as exc:
            logger.warning("台灣就業通 API 失敗 zipno=%s：%s", zipno, exc)
            failed += 1
            if failed == 1:
                summary["errors"].append(f"職缺 API 失敗（郵遞區號 {zipno}）：{exc}")
            continue
        summary["api_jobs"] += len(jobs)
        matched = []
        for job in jobs:
            keyword = taiwanjobs.title_matches(job.get("job_title", ""), settings["keywords"])
            if keyword:
                matched.append({**job, "keyword": keyword, "zipno": zipno})
        summary["matched_jobs"] += len(matched)
        stats = tj_repo.upsert_listing(matched)
        summary["new_jobs"] += stats["new_jobs"]
        summary["new_companies"] += stats["new_companies"]
        tj_repo.save_listing_cursor(index + 1)
    if failed > 1:
        summary["errors"].append(f"職缺 API 這次共 {failed} 個郵遞區號失敗")
    tj_repo.mark_listing_done()
    summary["listing_done"] = True
    if not summary["api_jobs"] and start == 0:
        summary["errors"].append("職缺 API 沒有回傳任何職缺（可能被擋，或 API 格式改了），請通知系統管理窗口")


# 連續這麼多頁讀取失敗，就當成可能被台灣就業通暫時擋下：這次先停，而且這幾頁不算
# 失敗次數（不然被擋一陣子，一大批職缺會失敗 3 次、被標成「讀不到」再也不讀）
BLOCK_STREAK = 5


def _read_job_pages(client: HttpClient, summary: dict):
    pending = tj_repo.pending_detail_jobs()
    streak = []  # 目前連續失敗、還沒算失敗次數的 (職缺, 錯誤)

    def flush_streak():
        # 中間有成功讀到的頁面＝不是被擋，是這幾頁本身有問題，照常算失敗次數
        for failed_job, error in streak:
            tj_repo.mark_job_failed(failed_job, error)
            summary["pages_failed"] += 1
            if summary["pages_failed"] == 1:
                summary["errors"].append(f"職缺頁讀取失敗（{failed_job.get('company_name', '')}）：{error}")
        streak.clear()

    for index, job in enumerate(pending):
        try:
            contact = taiwanjobs.fetch_contact(client, job.get("job_url", ""))
        except DeadlineReached:
            flush_streak()
            summary["deadline_hit"] = True
            summary["pages_pending"] = len(pending) - index
            return
        except Exception as exc:
            streak.append((job, exc))
            if len(streak) >= BLOCK_STREAK:
                summary["blocked_suspected"] = True
                summary["errors"].append(
                    f"連續 {BLOCK_STREAK} 個職缺頁讀取失敗（最後一次：{exc}），可能被台灣就業通暫時擋下，"
                    "這次先停止，下一次排程再試；這幾頁不算失敗次數。如果一直出現，請通知系統管理窗口"
                )
                summary["pages_pending"] = len(pending) - index + len(streak) - 1
                return
            continue
        flush_streak()
        tj_repo.save_job_contact(job, contact)
        summary["pages_read"] += 1
        if contact.get("emails"):
            summary["pages_with_email"] += 1
    flush_streak()
    summary["pages_pending"] = 0


def run_taiwanjobs(time_budget_seconds: int = None, client: HttpClient = None) -> dict:
    started = time.monotonic()
    budget = time_budget_seconds or SALESDEV_TJ_TIME_BUDGET_SECONDS
    client = client or HttpClient(deadline=started + budget)
    settings = tj_repo.get_settings()
    summary = {
        "started_at": repository.now_str(),
        "listing_refreshed": False,
        "listing_done": False,
        "listing_from": 0,
        "api_jobs": 0,
        "matched_jobs": 0,
        "new_jobs": 0,
        "new_companies": 0,
        "pages_read": 0,
        "pages_with_email": 0,
        "pages_failed": 0,
        "pages_pending": 0,
        "deadline_hit": False,
        "blocked_suspected": False,
        "errors": [],
    }
    try:
        if _listing_due(settings, time.time()):
            _refresh_listing(client, settings, summary)
        _read_job_pages(client, summary)
    except DeadlineReached:
        summary["deadline_hit"] = True
    except Exception as exc:
        logger.exception("台灣就業通抓取失敗")
        summary["errors"].append(f"執行失敗：{exc}")

    try:
        if summary["deadline_hit"] and not summary["pages_pending"]:
            summary["pages_pending"] = tj_repo.count_jobs_by_status().get(tj_repo.DETAIL_PENDING, 0)
        if summary["pages_read"] >= 10 and not summary["pages_with_email"]:
            summary["errors"].append(
                f"讀了 {summary['pages_read']} 個職缺頁都沒有 Email（職缺頁格式可能改了），請通知系統管理窗口"
            )
    except Exception:
        logger.exception("台灣就業通：統計失敗")
    summary["duration_seconds"] = round(time.monotonic() - started, 1)
    try:
        tj_repo.record_run(summary)
    except Exception:
        logger.exception("台灣就業通：記錄執行結果失敗")
    print(f"[台灣就業通] {summary}")
    return summary
