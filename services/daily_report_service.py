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
    """第一層門檻：保底訊息（fallback_triggered）或 AI 決策安靜失敗
    （ai_decision_empty，見 services/monitoring_service.py 的說明——Gemini 呼叫
    優雅降級回傳空字串，不會丟例外，但使用者其實沒拿到真正的判斷結果）任一種
    出現 1 次就算異常，兩者根因不同、都要算，不能只看 fallback_triggered。
    第二層門檻：把 path=="ai_decision" 的事件依「固定時間區塊」（預設 5 分鐘）
    分組，任一區塊的 p95 延遲超過門檻（預設 12 秒）就算變慢——用固定區塊取代
    「任一 3 分鐘滑動窗口」，判斷邏輯簡單很多、效果差異不大（見 HANDOFF.md）。"""
    latency_threshold_seconds = (
        DAILY_REPORT_LATENCY_P95_THRESHOLD_SECONDS if latency_threshold_seconds is None else latency_threshold_seconds
    )
    bucket_minutes = DAILY_REPORT_LATENCY_BUCKET_MINUTES if bucket_minutes is None else bucket_minutes

    fallback_count = sum(1 for e in events if e.get("fallback_triggered"))
    ai_empty_count = sum(1 for e in events if e.get("path") == "ai_decision" and e.get("ai_decision_empty"))

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
        "ai_empty_count": ai_empty_count,
        "level1_triggered": fallback_count >= 1 or ai_empty_count >= 1,
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
# 純邏輯：週報——同步回覆／背景補發比例
# ==========================================
def compute_delivery_mode_summary(events: list) -> dict:
    """統計 path=="ai_decision" 事件裡，同步回覆（免費 `reply_message`）跟逾時後
    背景補發（計費 `push_message`）各自的次數與比例。供每週檢視
    `AI_DECISION_SYNC_TIMEOUT_SECONDS`（限時同步等待秒數）這個設定值調得好不
    好用：push 比例如果持續偏高，代表現在的秒數接不住多數請求、成本會偏高；
    調高這個秒數雖然能降低 push 比例，但也會壓縮跟 LINE 30 秒 reply_token
    上限之間的安全緩衝，兩者要一起看（見 HANDOFF.md 的壓測討論）。"""
    sync_count = sum(1 for e in events if e.get("path") == "ai_decision" and e.get("delivery_mode") == "sync")
    push_count = sum(1 for e in events if e.get("path") == "ai_decision" and e.get("delivery_mode") == "push")
    total = sync_count + push_count
    push_ratio_percent = round(push_count / total * 100, 1) if total else 0.0
    return {"sync_count": sync_count, "push_count": push_count, "push_ratio_percent": push_ratio_percent}


# ==========================================
# 報告文字組裝
# ==========================================
def build_report_text(
    health: dict, faq_candidates: list, keyword_gaps: list, period_label: str,
    delivery_mode: dict = None,
) -> str:
    lines = [f"📊 沛沛{period_label}報告", ""]

    lines.append("【健康狀況】")
    fallback_count = health.get("fallback_count", 0)
    ai_empty_count = health.get("ai_empty_count", 0)
    if fallback_count >= 1:
        lines.append(f"⚠️ 過去期間偵測到 {fallback_count} 次保底訊息（可能發生例外），建議查看 Cloud Run log")
    if ai_empty_count >= 1:
        lines.append(f"⚠️ 過去期間有 {ai_empty_count} 次 AI 決策安靜失敗（Gemini 沒有回覆有效結果，但沒有丟例外），建議查看 Cloud Run log／Vertex AI 配額")
    if fallback_count == 0 and ai_empty_count == 0:
        lines.append("✅ 無保底訊息／AI 決策失敗觸發")
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

    if delivery_mode and (delivery_mode.get("sync_count", 0) + delivery_mode.get("push_count", 0)) > 0:
        lines.append("")
        lines.append("【同步回覆／背景補發比例】（AI_DECISION_SYNC_TIMEOUT_SECONDS 調整參考）")
        lines.append(
            f"同步（免費）{delivery_mode['sync_count']} 次／"
            f"背景補發（計費）{delivery_mode['push_count']} 次"
            f"，push 比例 {delivery_mode['push_ratio_percent']}%"
        )

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

    faq_candidates, keyword_gaps, delivery_mode = [], [], None
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
        delivery_mode = compute_delivery_mode_summary(weekly_events)

    summary["faq_candidate_count"] = len(faq_candidates)
    summary["keyword_gap_count"] = len(keyword_gaps)
    summary["delivery_mode"] = delivery_mode

    report_text = build_report_text(health, faq_candidates, keyword_gaps, period_label, delivery_mode)
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
