import json
from datetime import datetime, timezone

# 每一行結構化 log 都以這個固定前綴開頭，供 daily_report_service.py 從 Cloud
# Logging 撈回文字 log 之後，用這個前綴切開、還原成 JSON（見 HANDOFF.md
# 「監控與告警機制」）。用 print() 而不是額外接 Cloud Logging 的結構化
# handler，是刻意選擇：跟現有程式碼風格一致（例外處理也只靠 print 寫
# Cloud Run log），不需要為此另外設定憑證/客戶端。
AI_DECISION_LOG_MARKER = "[AI_DECISION_LOG] "


def log_ai_decision_event(
    *,
    path: str,
    action: str = "",
    fallback_triggered: bool = False,
    ai_decision_empty: bool = False,
    latency_seconds: float = 0.0,
    delivery_mode: str = "",
    matched_category: str = "",
    matched_brand: str = "",
    intercept_type: str = "",
) -> None:
    """把一次請求的處理結果印成一行結構化 JSON log。

    path：這次請求走的是哪條處理路徑——
      "direct_intercept"（精準工種直達攔截／全部瀏覽，完全沒呼叫 AI）、
      "high_confidence_faq"（FAQ 高信心比對，直接回傳 Notion 原文）、
      "ai_decision"（送去給 Gemini 決策）。
    這份 log 同時供兩件事使用：① 每日健康報告的兩層異常門檻判斷（fallback_triggered／
    ai_decision_empty 次數、latency_seconds 分布）；② FAQ 週報「建議新增的職缺關鍵字」，
    找出常被問、但目前沒有專屬直達路徑（intercept_type）、每次都要繞去問 AI 的職缺
    類別/廠商（matched_category／matched_brand）。

    fallback_triggered 跟 ai_decision_empty 是兩種不同根因、但都代表「使用者沒有拿到
    真正答案」的失敗模式，兩個都要算進健康門檻，缺一不可：
      - fallback_triggered：`_compute_ai_decision_messages()` 內部真的丟出例外
        （Notion/Firestore/Gemini 任何一環出錯），走到保底文案。
      - ai_decision_empty：Gemini 呼叫「優雅降級」回傳空字串（例如 `MODEL_FALLBACK_LIST`
        每個模型都失敗、或配額用盡），沒有丟例外，`action` 解析出來是空字串，程式碼會
        改用「單一焦點引導」或預設問候語接住，使用者感覺像正常對話、不會發現其實
        Gemini 完全沒有真的判斷這句話，若只看 fallback_triggered 會完全漏掉這種
        「安靜失敗」。"""
    event = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "path": path,
        "intercept_type": intercept_type,
        "matched_category": matched_category,
        "matched_brand": matched_brand,
        "action": action,
        "fallback_triggered": bool(fallback_triggered),
        "ai_decision_empty": bool(ai_decision_empty),
        "latency_seconds": round(latency_seconds, 3),
        "delivery_mode": delivery_mode,
    }
    print(f"{AI_DECISION_LOG_MARKER}{json.dumps(event, ensure_ascii=False)}")


def parse_log_line(line: str):
    """把 Cloud Logging 撈回來的一行文字還原成結構化事件 dict。不是這裡寫入的格式
    （沒有 marker 前綴，或前綴後面不是合法 JSON）一律回傳 None，由呼叫端過濾掉，
    不讓格式不明的 log 行讓整批分析中斷。"""
    if not line or AI_DECISION_LOG_MARKER not in line:
        return None
    _, _, payload = line.partition(AI_DECISION_LOG_MARKER)
    try:
        parsed = json.loads(payload.strip())
    except (json.JSONDecodeError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None
