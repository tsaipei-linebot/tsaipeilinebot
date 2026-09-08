from collections import defaultdict
from datetime import datetime, timedelta, timezone

from linebot.models import TextSendMessage

from config import (
    GCP_PROJECT_ID, DAILY_REPORT_LINE_TARGET_ID,
    DAILY_REPORT_LATENCY_P95_THRESHOLD_SECONDS, DAILY_REPORT_LATENCY_BUCKET_MINUTES,
    FAQ_CANDIDATE_KEYWORD_GAP_MIN_COUNT, FAQ_WEEKLY_REPORT_WEEKDAY, TAIPEI_TZ,
)
from services.monitoring_service import AI_DECISION_LOG_MARKER, parse_log_line
from services.notion_service import fetch_pending_faq_candidates

# 已經有專屬「精準工種直達攔截」的類別/廠商（見 handlers/message_handler.py），
# 這些不該出現在「建議新增的職缺關鍵字」清單裡——就算被問很多次，捷徑早就有了。
DIRECT_INTERCEPT_CATEGORIES = {"外送", "門市"}
DIRECT_INTERCEPT_BRANDS = {"momo"}


# ==========================================
# 純邏輯：健康狀況判斷（兩層門檻）
# ==========================================
def _percentile(values: list, pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, int(round(pct / 100 * (len(ordered) - 1)))))
    return ordered[idx]


def _parse_event_ts(event: dict):
    raw = event.get("ts", "")
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def compute_health_summary(
    events: list,
    latency_threshold_seconds: float = None,
    bucket_minutes: int = None,
) -> dict:
    """第一層門檻：保底訊息（fallback_triggered）出現 1 次就算異常。
    第二層門檻：把 path=="ai_decision" 的事件依「固定時間區塊」（預設 5 分鐘）
    分組，任一區塊的 p95 延遲超過門檻（預設 12 秒）就算變慢——用固定區塊取代
    「任一 3 分鐘滑動窗口」，判斷邏輯簡單很多、效果差異不大（見 HANDOFF.md）。"""
    latency_threshold_seconds = (
        DAILY_REPORT_LATENCY_P95_THRESHOLD_SECONDS if latency_threshold_seconds is None else latency_threshold_seconds
    )
    bucket_minutes = DAILY_REPORT_LATENCY_BUCKET_MINUTES if bucket_minutes is None else bucket_minutes

    fallback_count = sum(1 for e in events if e.get("fallback_triggered"))

    buckets = defaultdict(list)
    for e in events:
        if e.get("path") != "ai_decision":
            continue
        ts = _parse_event_ts(e)
        if ts is None:
            continue
        bucket_start = ts.replace(second=0, microsecond=0)
        bucket_start -= timedelta(minutes=bucket_start.minute % bucket_minutes)
        buckets[bucket_start].append(e.get("latency_seconds", 0.0))

    worst_bucket_p95 = 0.0
    worst_bucket_start = None
    for bucket_start, latencies in buckets.items():
        p95 = _percentile(latencies, 95)
        if p95 > worst_bucket_p95:
            worst_bucket_p95 = p95
            worst_bucket_start = bucket_start

    return {
        "total_events": len(events),
        "fallback_count": fallback_count,
        "level1_triggered": fallback_count >= 1,
        "worst_bucket_p95_seconds": round(worst_bucket_p95, 2),
        "worst_bucket_start": worst_bucket_start.isoformat() if worst_bucket_start else None,
        "level2_triggered": worst_bucket_p95 > latency_threshold_seconds,
    }


# ==========================================
# 純邏輯：FAQ 週報第二條線——建議新增的職缺關鍵字
# ==========================================
def compute_keyword_gap_candidates(events: list, min_count: int = None) -> list:
    """統計「繞去問 AI」的請求裡，有哪些職缺類別/廠商被問得夠多次、但目前沒有
    專屬直達路徑（見 HANDOFF.md「FAQ 週報／第二條線」）。只看 path=="ai_decision"
    的事件——已經有直達路徑接住的請求（path=="direct_intercept"）不需要再建議。"""
    min_count = FAQ_CANDIDATE_KEYWORD_GAP_MIN_COUNT if min_count is None else min_count
    counts = defaultdict(int)

    for e in events:
        if e.get("path") != "ai_decision":
            continue
        category = (e.get("matched_category") or "").strip()
        brand = (e.get("matched_brand") or "").strip()
        if category and category not in DIRECT_INTERCEPT_CATEGORIES:
            counts[f"類別:{category}"] += 1
        if brand and brand not in DIRECT_INTERCEPT_BRANDS:
            counts[f"廠商:{brand}"] += 1

    candidates = [{"label": label, "count": count} for label, count in counts.items() if count >= min_count]
    candidates.sort(key=lambda x: -x["count"])
    return candidates


# ==========================================
# 報告文字組裝
# ==========================================
def build_report_text(health: dict, faq_candidates: list, keyword_gaps: list, period_label: str) -> str:
    lines = [f"📊 沛沛{period_label}報告", ""]

    lines.append("【健康狀況】")
    if health.get("level1_triggered"):
        lines.append(f"⚠️ 過去期間偵測到 {health.get('fallback_count', 0)} 次保底訊息（可能發生例外），建議查看 Cloud Run log")
    else:
        lines.append("✅ 無保底訊息觸發")
    p95 = health.get("worst_bucket_p95_seconds", 0)
    if health.get("level2_triggered"):
        lines.append(f"⚠️ 有時段 p95 延遲達 {p95} 秒（門檻 {DAILY_REPORT_LATENCY_P95_THRESHOLD_SECONDS} 秒）")
    else:
        lines.append(f"✅ 延遲正常（最高時段 p95 {p95} 秒）")

    if faq_candidates:
        lines.append("")
        lines.append("【FAQ 候選清單】（政策類，待人工審核）")
        for i, question in enumerate(faq_candidates[:10], 1):
            lines.append(f"{i}. 「{question}」")
        if len(faq_candidates) > 10:
            lines.append(f"...等共 {len(faq_candidates)} 題，完整清單請看 Notion FAQ 資料庫")

    if keyword_gaps:
        lines.append("")
        lines.append("【建議新增的職缺關鍵字】（職缺類，可加快回覆速度）")
        for i, kw in enumerate(keyword_gaps[:10], 1):
            lines.append(f"{i}. {kw['label']} — 本期被問 {kw['count']} 次，目前沒有直達路徑")

    return "\n".join(lines).strip()


def is_weekly_report_day(now: datetime = None) -> bool:
    current = now if now is not None else datetime.now(TAIPEI_TZ)
    return current.weekday() == FAQ_WEEKLY_REPORT_WEEKDAY


# ==========================================
# 真正連 Cloud Logging 讀 log（正式執行才會用到，跟 factory_watch_service 的
# 網路呼叫一樣，開發環境測不到，只做過上面幾支純邏輯函式的單元測試）
# ==========================================
def fetch_recent_log_events(hours: int) -> list:
    from google.cloud import logging as cloud_logging

    client = cloud_logging.Client(project=GCP_PROJECT_ID)
    start_time = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    filter_str = (
        'resource.type="cloud_run_revision" '
        f'AND textPayload:"{AI_DECISION_LOG_MARKER}" '
        f'AND timestamp>="{start_time}"'
    )

    events = []
    for entry in client.list_entries(filter_=filter_str, page_size=1000):
        payload = entry.payload if isinstance(entry.payload, str) else str(entry.payload)
        parsed = parse_log_line(payload)
        if parsed:
            events.append(parsed)
    return events


# ==========================================
# 主流程：由 Cloud Scheduler 觸發的端點呼叫
# ==========================================
def run_daily_report(line_bot_api) -> dict:
    summary = {"health": None, "faq_candidate_count": 0, "keyword_gap_count": 0, "line_pushed": False, "errors": []}

    try:
        daily_events = fetch_recent_log_events(hours=24)
    except Exception as e:
        print(f"[每日/週報告] 讀取過去 24 小時 log 失敗: {e}")
        summary["errors"].append(f"fetch_daily_logs_failed: {e}")
        daily_events = []

    health = compute_health_summary(daily_events)
    summary["health"] = health

    faq_candidates, keyword_gaps = [], []
    period_label = "日"

    if is_weekly_report_day():
        period_label = "週"

        try:
            faq_candidates = fetch_pending_faq_candidates()
        except Exception as e:
            print(f"[每週報告] 讀取 FAQ 候選清單失敗: {e}")
            summary["errors"].append(f"faq_fetch_failed: {e}")

        try:
            weekly_events = fetch_recent_log_events(hours=24 * 7)
        except Exception as e:
            print(f"[每週報告] 讀取過去 7 天 log 失敗: {e}")
            summary["errors"].append(f"fetch_weekly_logs_failed: {e}")
            weekly_events = []
        keyword_gaps = compute_keyword_gap_candidates(weekly_events)

    summary["faq_candidate_count"] = len(faq_candidates)
    summary["keyword_gap_count"] = len(keyword_gaps)

    report_text = build_report_text(health, faq_candidates, keyword_gaps, period_label)
    print(f"[每日/週報告]\n{report_text}")

    if not DAILY_REPORT_LINE_TARGET_ID:
        print("[每日/週報告] 尚未設定 DAILY_REPORT_LINE_TARGET_ID，略過 LINE 推播")
        return summary

    if not line_bot_api:
        print("[每日/週報告] LINE Bot API 尚未初始化，略過推播")
        return summary

    try:
        line_bot_api.push_message(DAILY_REPORT_LINE_TARGET_ID, TextSendMessage(text=report_text))
        summary["line_pushed"] = True
    except Exception as e:
        print(f"[每日/週報告] LINE 推播失敗: {e}")
        summary["errors"].append(f"line_push_failed: {e}")

    return summary
