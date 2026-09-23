"""每日抓取主流程（2026-09-24 新增）：由 Cloud Scheduler 每天 07:00 呼叫
`POST /internal/salesdev/scrape/run` 觸發。

抓 104 → 1111 → 小雞上工 → 寫 Firestore（去重＋歸併）→ 記錄這次結果
→（有設定的話）LINE 推播摘要。

**時間上限**：Cloud Run 一個請求預設最多跑 300 秒，超過會被中斷，前面
抓到的也全部白費。原本在 GitHub Actions 上一次要跑 4～5 分鐘（其中 104
詳情頁那段一律 404、白等 1 分半，已經拿掉）。這裡再加一道保險：整個抓取
過程超過 `SALESDEV_SCRAPE_TIME_BUDGET_SECONDS`（預設 240 秒）就停止，
先把已經抓到的存起來，畫面上的「最近一次抓取」會註明「時間到提早結束」。
"""
import logging
import time

from linebot.models import TextSendMessage

from config import SALESDEV_LINE_TARGET_ID, SALESDEV_SCRAPE_TIME_BUDGET_SECONDS, SERVICE_BASE_URL
from salesdev import repository
from salesdev.scrapers import jobs_104, jobs_1111, jobs_chickpt

logger = logging.getLogger(__name__)

SCRAPERS = (
    ("104", jobs_104),
    ("1111", jobs_1111),
    ("chickpt", jobs_chickpt),
)

SOURCE_LABELS = {"104": "104", "1111": "1111", "chickpt": "小雞上工"}


def _lead_to_dict(lead) -> dict:
    return {
        "source": lead.source,
        "job_id": lead.job_id,
        "job_title": lead.job_title,
        "company_name": lead.company_name,
        "job_url": lead.job_url,
        "company_url": lead.company_url,
        "area": lead.area,
        "work_address": lead.work_address,
        "update_date": lead.update_date,
        "phone": lead.phone,
        "phone_ext": lead.phone_ext,
        "email": lead.email,
        "matched_keyword": lead.matched_keyword,
    }


def build_line_summary(summary: dict) -> str:
    counts = "、".join(
        f"{SOURCE_LABELS.get(source, source)} {count} 筆" for source, count in summary.get("source_counts", {}).items()
    )
    lines = [
        "📋 業務開發每日職缺整理",
        f"今天抓到：{counts}",
        f"新職缺 {summary.get('new_jobs', 0)} 筆，其中新地點 {summary.get('new_groups', 0)} 個",
    ]
    if SERVICE_BASE_URL:
        lines.append(f"到平台審查 👉 {SERVICE_BASE_URL.rstrip('/')}/salesdev")
    return "\n".join(lines)


def run_daily_scrape(line_bot_api=None, time_budget_seconds: int = None) -> dict:
    started = time.monotonic()
    budget = time_budget_seconds or SALESDEV_SCRAPE_TIME_BUDGET_SECONDS
    deadline = started + budget
    summary = {
        "run_date": repository.today_str(),
        "started_at": repository.now_str(),
        "source_counts": {},
        "errors": [],
        "deadline_hit": False,
        "new_jobs": 0,
        "new_groups": 0,
        "internal_jobs": 0,
        "line_pushed": False,
    }

    leads = []
    for source, module in SCRAPERS:
        if time.monotonic() >= deadline:
            summary["deadline_hit"] = True
            summary["source_counts"][source] = 0
            summary["errors"].append(f"{SOURCE_LABELS[source]}：時間到，這次沒有抓")
            continue
        try:
            found = module.collect_leads(deadline=deadline)
        except Exception as exc:  # 單一來源壞掉不影響其他來源
            logger.exception("業務開發抓取：%s 失敗", source)
            summary["errors"].append(f"{SOURCE_LABELS[source]}：{exc}")
            found = []
        summary["source_counts"][source] = len(found)
        leads.extend(_lead_to_dict(lead) for lead in found)
        if time.monotonic() >= deadline:
            summary["deadline_hit"] = True

    try:
        stats = repository.upsert_jobs(leads)
        summary["new_jobs"] = stats["new_jobs"]
        summary["new_groups"] = stats["new_groups"]
        summary["internal_jobs"] = stats["internal_jobs"]
    except Exception as exc:
        logger.exception("業務開發抓取：寫入 Firestore 失敗")
        summary["errors"].append(f"寫入資料庫失敗：{exc}")

    summary["duration_seconds"] = round(time.monotonic() - started, 1)

    if SALESDEV_LINE_TARGET_ID and line_bot_api and summary["new_jobs"] > 0:
        try:
            line_bot_api.push_message(SALESDEV_LINE_TARGET_ID, TextSendMessage(text=build_line_summary(summary)))
            summary["line_pushed"] = True
        except Exception as exc:
            logger.warning("業務開發抓取：LINE 推播失敗 %s", exc)
            summary["errors"].append(f"LINE 推播失敗：{exc}")

    try:
        repository.record_run(summary)
    except Exception:
        logger.exception("業務開發抓取：記錄執行結果失敗")
    print(f"[業務開發抓取] {summary}")
    return summary
