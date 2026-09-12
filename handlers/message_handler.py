import concurrent.futures
import json
import re
import time
import traceback
from datetime import datetime
from linebot import LineBotApi
from linebot.models import (
    TextSendMessage, QuickReply, QuickReplyButton, MessageAction
)
from config import (
    STAFFED_HOURS_START, STAFFED_HOURS_END, STAFFED_HOURS_GUARD_ENABLED, TAIPEI_TZ,
    AI_DECISION_SYNC_TIMEOUT_SECONDS,
)
from services.session_service import (
    get_user_history, append_user_history, get_user_slots, update_user_slots, clear_user_slots, CLEAR_SLOT
)
from services.notion_service import (
    fetch_jobs_data, fetch_faqs_data, clean_text_for_search, sanitize_uri,
    append_unresolved_faq_to_notion, append_unresolved_question_for_followup
)
from services.flex_service import (
    create_job_flex_card, format_clean_location, resolve_apply_url_by_industry
)
from services.matcher_service import (
    extract_current_target_location, extract_shift_preference, extract_leave_preference,
    extract_salary_preference, detect_category_label, detect_brand_label, filter_jobs_by_category_tiered,
    build_progressive_question, build_ai_job_candidates, build_ai_faq_candidates,
    job_matches_category_filter, has_negative_intent, extract_numeric_salary_preference,
    detect_negated_location, detect_negated_category, has_recognizable_category_or_brand_keyword,
    CATEGORY_KEYWORDS, KNOWN_BRANDS, find_high_confidence_faq_match,
    find_county_level_alternative_jobs, find_same_county_district_labels,
    resolve_county_for_location
)
from services.ai_service import query_gemini_ai, format_full_job_detail_with_ai
from services.monitoring_service import log_ai_decision_event


def _is_staffed_hours(now: datetime = None) -> bool:
    """判斷目前是否落在同仁上班時段（含 10 分鐘交接緩衝，設定值見 config.py）。
    這段時間內沛沛完全不主動回覆，交給真人專員在 LINE 聊天模式手動處理，避免
    跟同仁的人工回覆互相打架；求職者傳來的訊息會被靜默略過（reply_token 沒
    用到就自然過期，不會有任何副作用）。"""
    current = now if now is not None else datetime.now(TAIPEI_TZ)
    if current.tzinfo is None:
        current = TAIPEI_TZ.localize(current)
    else:
        current = current.astimezone(TAIPEI_TZ)
    return STAFFED_HOURS_START <= current.time() < STAFFED_HOURS_END


# ==========================================
# Gemini 決策輸出的結構化 JSON schema
# 開啟 response_schema 後，Gemini 回傳的內容在 API 層級就保證是符合這個結構的合法
# JSON，取代原本用 ACTION:/REPLY:/BUTTONS:/IDS: 文字格式 + 正則表達式手動解析的做法
# ——那種做法只要 Gemini 沒有完全照 prompt 範例排版（例如兩個欄位黏在同一行）就會
# 解析出錯，且無法窮舉所有可能出錯的排版方式。
# ==========================================
AI_DECISION_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "action": {
            "type": "STRING",
            "enum": ["ASK", "UNKNOWN_FAQ", "RECOMMEND", "NO_MATCH"],
        },
        "reply": {"type": "STRING"},
        "buttons": {
            "type": "ARRAY",
            "items": {"type": "STRING"},
        },
        "ids": {
            "type": "ARRAY",
            "items": {"type": "INTEGER"},
        },
    },
    "required": ["action", "reply"],
}

# ==========================================
# AI 決策：用「限時同步等待」取代「一律非同步 push」
#
# 背景：壓力測試證實 Gemini 決策在中高併發下 p99 延遲會超過 LINE 30 秒
# reply token 上限（見 HANDOFF.md）。但如果所有 AI 決策都改成「立即 ack
# + 背景 push_message」，等於把「絕大多數其實幾秒內就能算完」的正常請求
# 也一起從免費的 reply_message 改成計費、佔用月則數的 push_message
# ——這是不必要的成本，真正需要 push 的只有真的算比較久的少數請求（長尾）。
#
# 改用「限時同步等待」：把 AI 決策丟進執行緒池，主執行緒最多等
# AI_DECISION_SYNC_TIMEOUT_SECONDS 秒：
#   - 多數請求會在時限內算完 → 直接用 reply_token 回覆，完全免費、跟原本
#     行為一致。
#   - 少數算比較久的請求，時限一到就先用 reply_token 回一句「查詢中」
#     的 ack（reply_token 才不會逾時浪費掉），背景繼續算，算完後才改用
#     push_message 補發正式答案——只有這一小部分長尾請求才會用到則數。
#
# ThreadPoolExecutor 用固定 max_workers（而不是每個請求各開一條 thread）
# 除了避免高併發下無限增生執行緒之外，還有個附帶好處：對 Vertex AI 的呼叫
# 併發數會被自然限流在 max_workers 以內，緩解壓力測試觀察到的「請求併發
# 越高、429 重試退避疊加、越後面的請求越慢」的雪崩效應。
#
# 時限選 8 秒：從壓力測試結果看，多數請求在中低併發下幾秒內就有結果
# （p50 約 4-6 秒），8 秒足以讓「正常速度」的請求都吃到免費 reply_message；
# 選太長會讓真正變慢的請求也逼近甚至超過 30 秒 reply token 上限、失去用
# ack 兜底的意義，選太短則會讓太多本來免費就能處理完的請求也被迫改走
# 付費的 push_message。
# ==========================================
_AI_DECISION_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=32, thread_name_prefix="ai-decision"
)
# AI_DECISION_SYNC_TIMEOUT_SECONDS 改成從 config.py 讀（環境變數可調，預設 15，
# 見 config.py 該常數的說明），不再是這裡寫死的常數。


def _record_unanswered_question(raw_msg: str, user_id: str, target_line_bot_api: LineBotApi):
    """求職者這句話「沒有比對到答案」時（不管是常見問題庫沒收錄、還是完全沒有
    符合的職缺），統一記錄下來給招募專員回顧。初期資料量還不大，使用者希望
    不分職缺還是其他問題，一律先進同一份常見問答集，方便之後一次盤點求職者
    到底都在問什麼、好整理出真正該擴充的類別。
    - append_unresolved_faq_to_notion()：寫入前已在 notion_service 做過去重，
      同一句話（或高度相似的句子）不會重複堆積成一堆一樣的候選項。
    - append_unresolved_question_for_followup()：另外留一筆「是誰問的」，
      不去重，讓招募專員能回頭去 LINE 官方帳號後台找到這個人手動回覆。"""
    append_unresolved_faq_to_notion(raw_msg)

    display_name = ""
    if target_line_bot_api is not None:
        try:
            display_name = target_line_bot_api.get_profile(user_id).display_name
        except Exception as e:
            print(f"[取得 LINE 顯示名稱失敗，追蹤紀錄改用 user_id]: {e}")
    append_unresolved_question_for_followup(raw_msg, user_id, display_name)


def _build_quick_reply_buttons(labels: list, fallback: list) -> list:
    """把 AI 回傳的按鈕文字陣列轉成 LINE QuickReplyButton：最多取前 5 個、每個標籤截斷
    20 字、並去掉開頭殘留的 emoji（Gemini 有時會自己在文字前面加 emoji）。沒有任何有效
    標籤時退回呼叫端提供的預設按鈕組合，避免使用者收到沒有任何快速回覆按鈕的訊息。"""
    buttons = []
    for label in (labels or [])[:5]:
        label = str(label or "").strip()
        if not label:
            continue
        clean_txt = re.sub(r'^[📍☀️🌙📦🏭🏬🍽️🔄🛵\s]+', '', label)
        buttons.append(QuickReplyButton(action=MessageAction(label=label[:20], text=clean_txt)))
    return buttons or fallback


def process_user_message(event, target_line_bot_api: LineBotApi, bypass_staffed_hours_guard: bool = False):
    """處理求職端所有對話，支援 5 大優化與 4 項防呆精準升級[cite: 3, 6]

    bypass_staffed_hours_guard：只給 main.py 的 /internal/load-test-message 內部
    壓力測試端點使用，讓測試腳本不管實際執行的當下是白天還是晚上都能真的跑到
    AI 決策那段邏輯（壓力測試本來就是要測 Notion/Firestore/Gemini 這條路徑撐不
    撐得住，不該因為剛好在上班時間執行就被同仁時段的守門邏輯擋掉）。正式的
    LINE webhook（/callback、/test-callback）呼叫時一律不帶這個參數，維持預設
    的 False，同仁上班時段一樣會被擋下。"""
    reply_token = event.reply_token
    if reply_token in ["00000000000000000000000000000000", "ffffffffffffffffffffffffffffffff"]:
        return

    if STAFFED_HOURS_GUARD_ENABLED and not bypass_staffed_hours_guard and _is_staffed_hours():
        # 白天交給真人專員在 LINE 聊天模式手動回覆，沛沛不主動介入，避免兩邊
        # 同時回覆互相打架（詳見 HANDOFF.md「日夜接力」）。這個守門邏輯本身
        # 靠 STAFFED_HOURS_GUARD_ENABLED 這個總開關控制生不生效，見 config.py
        # 說明——還在測試頻道、LINE 後台排程還沒設定好之前保持關閉，避免白天
        # 測試時機器人看起來像故障。
        return

    _request_start = time.monotonic()
    raw_msg = event.message.text.strip()
    user_id = getattr(event.source, 'user_id', 'USER')
    source_type = getattr(event.source, 'type', 'unknown')
    group_id = getattr(event.source, 'group_id', None)
    # source_type/group_id 只用來在 log 裡看得到來源是誰、群組 ID 是多少
    # （例如要幫配送部系統的到期提醒設定要推播的 LINE 群組時查 ID 用），
    # 不影響任何既有的回覆邏輯。
    print(f"\n[收到使用者訊息]: 「{raw_msg}」 (User: {user_id}, Source: {source_type}{f', Group: {group_id}' if group_id else ''})")

    try:
        active_jobs = fetch_jobs_data()
        faq_list = fetch_faqs_data()

        # ---------------- 步驟 0-0A：槽位主動重置攔截 ----------------
        # 「真的想全部重來」跟「只想換一個條件」拆成兩種情境分開處理：
        # 全域重置才整組槽位清空；單一維度調整只詢問要換哪一項，讓後續訊息的
        # 槽位抽取（步驟 0-3）自然覆蓋對應欄位，其餘已鎖定的條件保留不動。
        full_reset_keywords = [
            "重新找", "重選", "重設", "清空條件", "重新開始", "重來", "重頭開始", "清除條件",
            "全部重來", "整個重來", "從頭來", "從頭開始", "重新來過", "砍掉重練", "清空重來",
            "重新設定條件", "全部條件清空", "條件全部清掉",
        ]
        single_dimension_keywords = [
            "換個條件", "換一個條件", "改個條件", "換條件", "改條件", "換一下條件",
            "調整條件", "改一下條件", "換個項目", "改個項目",
        ]

        if any(k in raw_msg for k in full_reset_keywords):
            clear_user_slots(user_id)
            reset_reply = "好的！沛沛已經為您清空先前的搜尋條件囉 😊\n\n請問您目前希望在哪個地區找工作？想找早班還是夜班呢？"
            append_user_history(user_id, "求職者", raw_msg)
            append_user_history(user_id, "招募顧問沛沛", reset_reply)
            quick_reply = QuickReply(items=[
                QuickReplyButton(action=MessageAction(label="📍 新莊工作", text="新莊工作")),
                QuickReplyButton(action=MessageAction(label="📍 桃園工作", text="桃園工作")),
                QuickReplyButton(action=MessageAction(label="☀️ 固定早班", text="早班工作")),
                QuickReplyButton(action=MessageAction(label="🌙 固定夜班", text="夜班工作")),
                QuickReplyButton(action=MessageAction(label="👀 都給我看看", text="都給我看看"))
            ])
            target_line_bot_api.reply_message(reply_token, TextSendMessage(text=reset_reply, quick_reply=quick_reply))
            return

        if any(k in raw_msg for k in single_dimension_keywords):
            adjust_reply = "好的，請問您想調整地區、班別，還是工作類型呢？其他已經確認的條件沛沛會繼續保留 😊"
            append_user_history(user_id, "求職者", raw_msg)
            append_user_history(user_id, "招募顧問沛沛", adjust_reply)
            quick_reply = QuickReply(items=[
                QuickReplyButton(action=MessageAction(label="📍 換地區", text="我想換地區")),
                QuickReplyButton(action=MessageAction(label="⏰ 換班別", text="我想換班別")),
                QuickReplyButton(action=MessageAction(label="🏭 換工作類型", text="我想換工作類型"))
            ])
            target_line_bot_api.reply_message(reply_token, TextSendMessage(text=adjust_reply, quick_reply=quick_reply))
            return

        # ---------------- 步驟 0-0B：禮貌性收尾處理 ----------------
        polite_close_keywords = [
            "謝謝", "謝謝沛沛", "感謝", "感恩", "辛苦了", "好的謝謝", "那我先去填履歷", "先去應徵",
            "了解了", "好的了解", "我知道了", "再見", "掰掰", "ok謝謝", "先這樣"
        ]
        # 帶轉折/追加語氣的詞（例如「謝謝，不過還想問⋯」）代表使用者其實還有問題要問，
        # 不能只靠有沒有問號判斷，否則這類句子會被誤判成單純道謝而整句被忽略。
        polite_override_keywords = ["不過", "但是", "但", "可是", "只是", "另外", "而且", "還想", "還想問", "還想知道", "還要問"]
        is_pure_polite = (
            any(k in raw_msg for k in polite_close_keywords)
            and not any(q in raw_msg for q in ["嗎", "有沒有", "還有", "請問", "？", "?"])
            and not any(t in raw_msg for t in polite_override_keywords)
        )
        if is_pure_polite:
            polite_reply = "不客氣呀！很高興能為您服務 😊 預祝您求職面試順利！\n\n如果後續有任何工作或制度上的疑問，隨時歡迎回來找沛沛聊聊喔！"
            append_user_history(user_id, "求職者", raw_msg)
            append_user_history(user_id, "招募顧問沛沛", polite_reply)
            target_line_bot_api.reply_message(reply_token, TextSendMessage(text=polite_reply))
            return

        # ---------------- 步驟 0-1：處理「查看職缺詳情」（Notion 唯一鍵精準定位）[cite: 6] ----------------
        if raw_msg.startswith("查看職缺詳情"):
            target_title = raw_msg.replace("查看職缺詳情", "").strip()
            matched_job = None
            
            # 1. 優先精準比對 Notion 唯一識別鍵「職缺名稱」
            for j in active_jobs:
                if target_title and (j.get("職缺名稱") == target_title or j.get("_internal_title") == target_title):
                    matched_job = j
                    break
            
            # 2. 次要包含比對
            if not matched_job and target_title:
                for j in active_jobs:
                    if target_title in j.get("_parsed_title", "") or j.get("_parsed_title", "") in target_title:
                        matched_job = j
                        break
            
            # 只有在使用者根本沒帶職缺名稱（單純傳「查看職缺詳情」）時，才用第一筆
            # 現有職缺當預設值——如果使用者有指定名稱、只是剛好沒比對到（職缺已經
            # 停招/改名/從 Notion 下架，這是常態會發生的事），不能就這樣隨便挑一筆
            # 不相關的職缺硬塞給使用者、讓他們誤以為看到的是自己點的那筆。這種情況
            # 讓 matched_job 保持 None，往下走一般對話流程（會進到 AI 決策，由 AI
            # 判斷怎麼回覆），而不是給出一個看似正確、實則答非所問的職缺詳情。
            if not matched_job and active_jobs and not target_title:
                matched_job = active_jobs[0]

            if matched_job:
                loc_display = format_clean_location(matched_job, "")
                apply_url = sanitize_uri(resolve_apply_url_by_industry(matched_job))
                
                internal_title = matched_job.get("職缺名稱") or matched_job.get("_internal_title") or "招募職缺"
                category = matched_job.get("職務類別") or matched_job.get("_job_category") or "優質職務"
                standard_header = f"📋【職缺名稱：{internal_title} ｜ {category}】"

                formatted_detail = str(matched_job.get("排版工作說明") or "").strip()
                if formatted_detail:
                    formatted_detail = re.sub(r'^📋【職缺名稱[：:][^】\n]+】', standard_header, formatted_detail)
                    if not formatted_detail.startswith("📋【職缺名稱"):
                        formatted_detail = f"{standard_header}\n\n{formatted_detail}"
                else:
                    formatted_detail = format_full_job_detail_with_ai(matched_job, loc_display)

                final_reply_text = f"{formatted_detail}\n\n👉 立即填寫線上履歷：\n{apply_url}"

                append_user_history(user_id, "求職者", raw_msg)
                append_user_history(user_id, "招募顧問沛沛", final_reply_text)
                quick_reply = QuickReply(items=[
                    QuickReplyButton(action=MessageAction(label="📄 立即線上應徵", text="我要應徵")),
                    QuickReplyButton(action=MessageAction(label="📍 看看其他工作", text="都給我看看")),
                    QuickReplyButton(action=MessageAction(label="💬 詢問發薪與福利", text="發薪日是什麼時候？"))
                ])
                target_line_bot_api.reply_message(reply_token, TextSendMessage(text=final_reply_text, quick_reply=quick_reply))
                return

        # ---------------- 步驟 0-2：就業服務法合規攔截 (年齡/性別)[cite: 6] ----------------
        age_gender_keywords = ["年齡限制", "幾歲", "年紀", "年齡", "限女性", "限男性", "性別限制", "幾歲以上", "幾歲以下", "高齡", "中高齡"]
        if any(k in raw_msg for k in age_gender_keywords) and any(k in raw_msg for k in ["有嗎", "可以嗎", "限制", "能不能", "可以做嗎", "超齡", "算老"]):
            legal_reply = (
                "您好呀！我是招募顧問沛沛 😊\n\n"
                "依《就業服務法》規定，材霈所有職缺皆【無性別與年齡限制】，歡迎所有求職朋友應徵！\n\n"
                "各廠區主要評估實際工作內容的勝任度（例如：需配合走動作業、搬重或輪班需求）。只要體能與出勤狀況可配合，都非常歡迎線上填寫履歷喔！\n\n"
                "👉 請問您目前希望在【哪個地區】找工作？偏好早班或夜班呢？"
            )
            append_user_history(user_id, "求職者", raw_msg)
            append_user_history(user_id, "招募顧問沛沛", legal_reply)
            quick_reply = QuickReply(items=[
                QuickReplyButton(action=MessageAction(label="📍 桃園工作", text="桃園工作")),
                QuickReplyButton(action=MessageAction(label="📍 新莊工作", text="新莊工作")),
                QuickReplyButton(action=MessageAction(label="☀️ 固定早班", text="早班工作")),
                QuickReplyButton(action=MessageAction(label="📦 momo理貨", text="momo理貨")),
                QuickReplyButton(action=MessageAction(label="🏬 蝦皮門市", text="蝦皮門市"))
            ])
            target_line_bot_api.reply_message(reply_token, TextSendMessage(text=legal_reply, quick_reply=quick_reply))
            return

        # ---------------- 步驟 0-3：Session 載入與多輪動態槽位覆蓋（含否定詞感知：能區分「不要 A」跟「想要 B」）[cite: 6] ----------------
        history = get_user_history(user_id)
        history_text = "\n".join([f"{item['role']}: {item['text']}" for item in history[-6:]])
        user_slots = get_user_slots(user_id)
        clean_input = clean_text_for_search(raw_msg)

        # 「都可以」「隨便」「都好」這種泛用表態語氣，講的通常是「我很有彈性」，
        # 不是特別針對地區/類別/廠商哪一個維度──跟真人招募專員的理解一樣，
        # 使用者說「都可以」，一般是指全部都可以，不是恰好只有其中一個維度
        # 可以。這份清單同時餵給地區/類別/廠商三處的「明確表示不限」判斷，
        # 一次講出來就會把三個維度一起解鎖，不用使用者逐一分開講。刻意不放
        # 「不限」這種太短的詞單獨進來──「不限地區」「不限廠商」都會被子字串
        # 誤觸發成三個維度一起清空，所以「不限」只留在各自維度專屬的完整詞組裡。
        generic_broaden_keywords = [
            "都可以", "都可以喔", "都好", "都ok", "隨便", "無所謂", "沒差",
            "什麼都行", "什麼都可以", "什麼都好", "都行",
        ]

        explicit_any_location = any(k in clean_input for k in generic_broaden_keywords + [
            "不限地區", "不限地點", "哪裡都", "全台", "全區", "不挑地區", "不挑地點",
        ])

        extracted_loc = extract_current_target_location(raw_msg, "", active_jobs)
        negated_loc = detect_negated_location(raw_msg, active_jobs)

        if extracted_loc:
            current_location = extracted_loc
            location_slot_update = extracted_loc
        elif explicit_any_location or (negated_loc and negated_loc == user_slots.get("location", "")):
            # 使用者明確表示不限地區，或否定了目前鎖定的那個地區 → 真正清空槽位，而不是只在本輪暫時忽略
            current_location = ""
            location_slot_update = CLEAR_SLOT
        else:
            current_location = user_slots.get("location", "")
            location_slot_update = ""

        detected_shift = extract_shift_preference(raw_msg) or user_slots.get("shift", "")
        detected_leave = extract_leave_preference(raw_msg) or user_slots.get("leave", "")

        detected_category_from_text = detect_category_label(clean_input)
        negated_category = detect_negated_category(clean_input)
        # 類別原本只有「否定掉目前鎖定的那個類別」（例如「除了外送」）才會清空，
        # 沒有像地區/廠商一樣的「明確表示不限」出口——使用者鎖定「門市」之後，
        # 講「不限類型」「什麼工作都可以」這種泛用表態，原本完全沒有辦法清空，
        # 現在補上跟地區/廠商一致的機制。
        explicit_any_category = any(k in clean_input for k in generic_broaden_keywords + [
            "不限類型", "不限工作類型", "不限職缺類型", "不限職種", "不挑工作", "不挑職缺",
            "什麼工作都可以", "什麼職缺都可以", "什麼類型都可以",
        ])

        if detected_category_from_text:
            category_slot_update = detected_category_from_text
        elif negated_category and negated_category == user_slots.get("category", ""):
            # 使用者明確排除掉目前鎖定的類別（例如「除了外送」）→ 清空，這輪查詢也不再沿用被排除的舊類別
            category_slot_update = CLEAR_SLOT
            detected_category_from_text = ""
        elif explicit_any_category:
            category_slot_update = CLEAR_SLOT if user_slots.get("category", "") else ""
            detected_category_from_text = ""
        else:
            category_slot_update = ""
            detected_category_from_text = user_slots.get("category", "")

        # 廠商（brand）改成比照地區/類別：沿用到使用者明確換掉、或明確表示不限
        # 廠商為止，不再「這句話沒提到就清空」。實測發現原本「每句話沒提到就
        # 清空」的設計，會讓使用者問完「蝦皮門市有嗎」、下一句只問「八德有缺嗎」
        # 這種自然的追問地區情境時，蝦皮這個條件整個消失，變成拿「不限廠商的
        # 門市」去查八德，而不是使用者真正想問的「蝦皮在八德有沒有」，導致
        # 回覆牛頭不對馬嘴（見 HANDOFF.md 案例）。
        explicit_any_brand = any(k in clean_input for k in generic_broaden_keywords + [
            "不限廠商", "不限品牌", "不限公司", "其他廠商", "別的廠商", "換一家", "不挑廠商",
        ])
        detected_brand_this_turn = detect_brand_label(raw_msg, active_jobs)
        if detected_brand_this_turn:
            detected_brand = detected_brand_this_turn
            brand_slot_update = detected_brand_this_turn
        elif explicit_any_brand:
            detected_brand = ""
            brand_slot_update = CLEAR_SLOT if user_slots.get("brand", "") else ""
        else:
            brand_slot_update = ""
            detected_brand = user_slots.get("brand", "")

        current_slots = update_user_slots(
            user_id,
            location=location_slot_update,
            category=category_slot_update,
            shift=detected_shift,
            leave=detected_leave,
            brand=brand_slot_update
        )

        # ---------------- 步驟 0-4：純泛意圖與全部瀏覽攔截[cite: 6] ----------------
        show_all_keywords = [
            "都給我看", "都要看", "都可以", "全部", "隨便", "推薦一下", "有什麼工作", "還有什麼", "看全部", "都看",
            "都貼給我", "職缺都給我", "全部都給我", "有的都給我", "都拿給我看", "全部推薦", "都推薦給我",
            "有哪些工作", "有哪些職缺", "什麼都看", "什麼工作都看", "有什麼職缺",
        ]
        # 統一意圖判斷來源：改用 matcher_service 集中維護的 CATEGORY_KEYWORDS/KNOWN_BRANDS
        # （has_recognizable_category_or_brand_keyword），取代原本這裡另外維護、
        # 覆蓋範圍不完整的手動白名單（原本漏掉「理貨」「餐飲」等類別）。
        has_specific_intent = bool(
            detected_brand
            or detected_category_from_text
            or has_recognizable_category_or_brand_keyword(clean_input)
        )
        is_show_all = any(k in clean_input for k in show_all_keywords) and not has_specific_intent

        if is_show_all:
            matched_show_all = []
            for j in active_jobs:
                # 地區比對要用 _location_search_text（只含縣市/行政區），不能用
                # _search_text（含自由文字，可能因為地址/文案剛好提到地名而誤判，
                # 見 notion_service.py 的欄位說明）。
                loc_text = j.get("_location_search_text", "")
                if current_location:
                    loc_clean = current_location.replace("台", "臺")
                    if current_location in loc_text or loc_clean in loc_text:
                        matched_show_all.append(j)
                else:
                    matched_show_all.append(j)

            # current_slots 是上面 update_user_slots() 寫回後直接拿到的最新合併結果，
            # 這裡不用再花一次 Firestore 讀取重新查一次一模一樣的資料（原本這裡另外
            # 呼叫 get_user_slots() 是多餘的網路來回，也有極小機率讀到跟這輪計算不
            # 一致的中間狀態，見 HANDOFF.md 說明）。
            _known_category_for_filter = current_slots.get("category", "")
            _brand_for_filter = current_slots.get("brand", "")

            if _known_category_for_filter and _known_category_for_filter != "不限":
                matched_show_all = filter_jobs_by_category_tiered(
                    matched_show_all,
                    _known_category_for_filter,
                    _brand_for_filter,
                )

            if not matched_show_all:
                if current_location:
                    loc_clean = current_location.replace("台", "臺")
                    matched_show_all = [j for j in active_jobs if current_location in j.get("_location_search_text", "") or loc_clean in j.get("_location_search_text", "")]
                if not matched_show_all:
                    matched_show_all = active_jobs[:5]

            append_user_history(user_id, "求職者", raw_msg)
            if matched_show_all:
                reply_text = f"沒問題！沛沛馬上為您整理{current_location if current_location else ''}目前招募中的熱門職缺，歡迎點擊查看詳細說明或線上應徵喔 😊"
                append_user_history(user_id, "招募顧問沛沛", reply_text)
                target_line_bot_api.reply_message(reply_token, [TextSendMessage(text=reply_text), create_job_flex_card(matched_show_all[:5], user_id, current_location)])
            else:
                # active_jobs 本身是空的（例如 Notion 職缺暫時全部停招，或剛好讀取失敗
                # 沿用了空的快取）——這時候完全沒有職缺可以組成 Flex 卡片,LINE 的
                # Carousel 格式要求至少要有 1 張卡片,傳空陣列會被 LINE API 拒絕。
                # 改成老實跟使用者說目前沒有職缺,而不是送出一個會失敗的空卡片。
                reply_text = "不好意思，沛沛這邊目前暫時沒有符合的職缺資料，麻煩稍後再試一次，或直接留言想找的地區/類型，我們會盡快為您確認喔 🙏"
                append_user_history(user_id, "招募顧問沛沛", reply_text)
                target_line_bot_api.reply_message(reply_token, TextSendMessage(text=reply_text))
            log_ai_decision_event(
                path="direct_intercept", intercept_type="show_all",
                matched_category=_known_category_for_filter, matched_brand=_brand_for_filter,
                latency_seconds=time.monotonic() - _request_start, delivery_mode="sync",
            )
            return

        # ---------------- 步驟 1：精準工種直達攔截（含否定語氣防呆）[cite: 6] ----------------
        is_negative = has_negative_intent(raw_msg)
        # 同樣改用 CATEGORY_KEYWORDS/KNOWN_BRANDS 當唯一來源，跟 has_specific_intent
        # 共用同一份清單，避免各處關鍵字覆蓋範圍互相兜不起來。
        is_delivery_intent = any(k in clean_input for k in CATEGORY_KEYWORDS["外送"]) and not is_negative
        is_store_intent = any(k in clean_input for k in CATEGORY_KEYWORDS["門市"]) and not is_delivery_intent and not is_negative
        is_momo_intent = any(k in clean_input for k in KNOWN_BRANDS["momo"]) and not is_negative

        # 追問地區延續前一輪已鎖定的類別/廠商：例如先問「蝦皮門市有嗎」，接著
        # 只問「八德有缺嗎」——這句話本身沒有再提到門市/外送/momo等關鍵字，
        # 只是單純問地區，原本因此會直接掉到後面的 AI 決策，AI 只把類別/廠商
        # 條件當成排序加分（不是硬性篩選），加上廠商原本每句話沒提到就清空，
        # 導致這種追問常常答非所問（見 HANDOFF.md 案例）。這裡改成：只有在這句
        # 話「有抓到明確地名、且沒有夾雜其他新的類別/廠商關鍵字」時，才視為延續
        # 前一輪的精準工種直達攔截、改用之前鎖定的類別/廠商去篩選；刻意要求
        # 「有抓到地名」，避免把「發薪日是什麼時候」這種抓不到地名的 FAQ 類問題
        # 也一起誤攔進來。
        is_bare_location_followup = bool(extracted_loc) and not has_recognizable_category_or_brand_keyword(clean_input) and not is_negative
        if is_bare_location_followup and not (is_delivery_intent or is_store_intent or is_momo_intent):
            if detected_category_from_text == "外送":
                is_delivery_intent = True
            elif detected_category_from_text == "門市":
                is_store_intent = True
            elif detected_brand == "momo":
                is_momo_intent = True

        direct_matches = []
        # 這三個分支各自的「類別/廠商比對通過、但還沒篩地區」候選池，供地區
        # 精準比對落空時，退一步找「同縣市」還有沒有符合條件的職缺用（見下面
        # 步驟 1-4 的同縣市鄰近地區退讓建議）。沒有走到對應分支時維持空清單，
        # 不影響原本的判斷。
        _category_matched_jobs_for_fallback = []
        _category_desc_for_fallback = ""

        if is_delivery_intent:
            _delivery_matched_jobs = []
            for j in active_jobs:
                cat = str(j.get("_job_category", "")).lower()
                int_t = str(j.get("_internal_title", "")).lower()
                pub_t = str(j.get("職缺名稱(對外)", "")).lower()
                if any(k in cat for k in ["外送", "司機", "配送"]) or any(k in int_t for k in ["外送", "司機", "配送"]) or any(k in pub_t for k in ["外送", "司機", "配送"]):
                    _delivery_matched_jobs.append(j)

            if current_location:
                loc_clean = current_location.replace("台", "臺")
                # 地區比對用 _location_search_text（只含縣市/行政區），見
                # notion_service.py 的欄位說明：不能用 _search_text，否則
                # 職缺描述文字裡剛好提到的地名（例如路名）會被誤判成該職缺
                # 真的位於那個行政區。
                direct_matches = [j for j in _delivery_matched_jobs if current_location in j.get("_location_search_text", "") or loc_clean in j.get("_location_search_text", "")]
            else:
                direct_matches = _delivery_matched_jobs
            _category_matched_jobs_for_fallback = _delivery_matched_jobs
            _category_desc_for_fallback = "外送"

        elif is_store_intent:
            # 改用 detected_brand（這輪偵測到的，或延續前一輪鎖定的廠商），
            # 不再只靠「蝦皮門市」這種字面上剛好連在一起的寫法做特例判斷——
            # 這樣「蝦皮門市有嗎」下一句接著問「八德有缺嗎」時，也能正確延續
            # 蝦皮這個廠商條件，不會變成查「不限廠商的門市」。
            _store_brand = detected_brand
            _location_jobs = []
            for j in active_jobs:
                if current_location:
                    loc_clean = current_location.replace("台", "臺")
                    if current_location in j.get("_location_search_text", "") or loc_clean in j.get("_location_search_text", ""):
                        _location_jobs.append(j)
                else:
                    _location_jobs.append(j)

            direct_matches = filter_jobs_by_category_tiered(_location_jobs, "門市", _store_brand)
            # 這裡刻意「另外」對全部 active_jobs（不先篩地區）再跑一次類別/廠商
            # 比對，只給同縣市退讓建議用，不會反過來影響上面 direct_matches 的
            # 判斷結果——避免因為改成「先比類別再篩地區」而讓嚴格/寬鬆兩層
            # 比對的判斷基準跟著地區篩選範圍變動，波及到已經驗證過的既有行為。
            _category_matched_jobs_for_fallback = filter_jobs_by_category_tiered(active_jobs, "門市", _store_brand)
            _category_desc_for_fallback = f"{_store_brand}門市" if _store_brand else "門市"

        elif is_momo_intent:
            # 地區沒有精準命中時不再退讓顯示「全部」momo 職缺——之前這樣設計
            # 會讓使用者收到跟他問的地區完全無關的職缺、卻被告知「找到符合
            # 條件的推薦職缺」，答非所問（見 HANDOFF.md 案例）。跟 delivery/
            # store 分支一致：地區沒有精準命中就是沒有直接命中，落到下面的
            # 同縣市退讓建議，還是沒有才落到 AI 決策，由 AI 依候選職缺清單
            # 判斷、老實回覆。
            momo_jobs = [j for j in active_jobs if any(k in j.get("_search_text", "") for k in ["momo", "富邦", "富昇"])]
            if current_location:
                loc_clean = current_location.replace("台", "臺")
                direct_matches = [j for j in momo_jobs if current_location in j.get("_location_search_text", "") or loc_clean in j.get("_location_search_text", "")]
            else:
                direct_matches = momo_jobs
            _category_matched_jobs_for_fallback = momo_jobs
            _category_desc_for_fallback = "momo"

        if direct_matches:
            reply_text = f"有的！沛沛為您找到符合條件的推薦職缺囉，歡迎點擊下方「了解詳細內容」或填寫線上履歷應徵喔 😊"
            append_user_history(user_id, "求職者", raw_msg)
            append_user_history(user_id, "招募顧問沛沛", reply_text)
            target_line_bot_api.reply_message(reply_token, [TextSendMessage(text=reply_text), create_job_flex_card(direct_matches[:4], user_id, current_location)])
            _intercept_type = "delivery" if is_delivery_intent else ("store" if is_store_intent else "momo")
            log_ai_decision_event(
                path="direct_intercept", intercept_type=_intercept_type,
                matched_brand="momo" if is_momo_intent else "",
                latency_seconds=time.monotonic() - _request_start, delivery_mode="sync",
            )
            return

        # ---------------- 步驟 1-4：同縣市鄰近地區退讓建議 ----------------
        # 真人派遣專員跟求職者對話時，通常會順口推薦鄰近或類似的工作——例如
        # 求職者問「蝦皮門市 八德有缺嗎」，八德沒有缺額時，會提「桃園市其他
        # 地方有喔」。這裡刻意做成確定性比對（只靠 resolve_county_for_location()
        # 查「同一個縣市」——優先查 LOCATION_TO_COUNTY 手動對照表，查不到再退一步
        # 從目前職缺資料動態解析，見 matcher_service.py 說明，不做地理相鄰推論），
        # 回覆文字也刻意明講「原本問的
        # 地區沒有，這是同縣市的其他地方」——不能讓使用者誤以為原本問的地區
        # 也有符合的職缺，那樣會重蹈這幾天才修好的「AI 自行推論地區涵蓋範圍」
        # 覆轍。只有在使用者真的有指定地區、且這句話有對應到門市/外送/momo
        # 其中一種精準攔截意圖時才會觸發；找不到同縣市的替代方案，就繼續往下
        # 落到 AI 決策，跟原本行為一致。
        if current_location and _category_matched_jobs_for_fallback:
            county_alt_jobs = find_county_level_alternative_jobs(_category_matched_jobs_for_fallback, current_location, active_jobs)
            if county_alt_jobs:
                county_name = resolve_county_for_location(current_location, active_jobs)
                # 能拆出具體同縣市行政區名稱時，直接列出來讓求職者知道確切
                # 有哪些地區可選（使用者要求這裡不設數量上限）；拆不出來時
                # （例如職缺沒有結構化的「行政區」欄位）退回原本的空泛說法，
                # 不能因為列不出清單就不回覆。
                district_labels = find_same_county_district_labels(county_alt_jobs, current_location, active_jobs)
                if district_labels:
                    fallback_reply_text = (
                        f"「{current_location}」目前沒有明確列出的{_category_desc_for_fallback}職缺，"
                        f"不過{county_name}的{'、'.join(district_labels)}有相關職缺，要不要參考看看呢？😊"
                    )
                else:
                    fallback_reply_text = (
                        f"「{current_location}」目前沒有明確列出的{_category_desc_for_fallback}職缺，"
                        f"不過同樣在{county_name}還有相關職缺，要不要參考看看呢？😊"
                    )
                append_user_history(user_id, "求職者", raw_msg)
                append_user_history(user_id, "招募顧問沛沛", fallback_reply_text)
                target_line_bot_api.reply_message(reply_token, [TextSendMessage(text=fallback_reply_text), create_job_flex_card(county_alt_jobs[:4], user_id, "")])
                _intercept_type = "delivery" if is_delivery_intent else ("store" if is_store_intent else "momo")
                log_ai_decision_event(
                    path="direct_intercept", intercept_type=f"{_intercept_type}_county_fallback",
                    matched_brand="momo" if is_momo_intent else "",
                    latency_seconds=time.monotonic() - _request_start, delivery_mode="sync",
                )
                return

        # ---------------- 步驟 1-5：FAQ 高信心比對，直接回傳 Notion 原文（不經 AI 改寫）----------------
        # 求職者問句完整命中某一筆 FAQ 問題本文時，代表這題有明確、已審核過的官方答案，
        # 直接回傳 Notion 原文即可：避免 AI 意譯規章/福利類文字造成合規風險，同時省下一次
        # Gemini 呼叫。命中不到才繼續往下走 AI 決策流程（FAQ 分數較低的候選仍會送給 AI 判斷）。
        high_confidence_faq = find_high_confidence_faq_match(faq_list, raw_msg)
        if high_confidence_faq:
            faq_reply_text = str(high_confidence_faq.get("answer", "")).strip()
            if faq_reply_text:
                append_user_history(user_id, "求職者", raw_msg)
                append_user_history(user_id, "招募顧問沛沛", faq_reply_text)
                quick_reply = QuickReply(items=[
                    QuickReplyButton(action=MessageAction(label="📍 新莊工作", text="新莊工作")),
                    QuickReplyButton(action=MessageAction(label="📍 桃園工作", text="桃園工作")),
                    QuickReplyButton(action=MessageAction(label="👀 都給我看看", text="都給我看看"))
                ])
                target_line_bot_api.reply_message(reply_token, TextSendMessage(text=faq_reply_text, quick_reply=quick_reply))
                log_ai_decision_event(
                    path="high_confidence_faq", action="ASK",
                    latency_seconds=time.monotonic() - _request_start, delivery_mode="sync",
                )
                return

        # ---------------- 步驟 2：限時同步等待 AI 決策，只有長尾請求才改走背景 push ----------------
        # 壓力測試證實（見 HANDOFF.md）：Gemini 決策在中高併發下 p99 延遲會超過 LINE
        # 30 秒 reply token 上限（實測併發 15 時 p99/max 達 40 秒）。但多數請求其實
        # 幾秒內就能算完，所以不能一律改成「先 ack 再背景 push」——那樣會讓所有 AI
        # 回覆都變成計費、佔用月則數的 push_message，即使原本用免費的 reply_message
        # 就能準時回覆。
        #
        # 做法：把 AI 決策丟進執行緒池，主執行緒最多同步等
        # AI_DECISION_SYNC_TIMEOUT_SECONDS 秒。時限內算完 → 直接用 reply_token 回覆
        # 正式答案，完全免費。時限一到還沒算完 → 才用 reply_token 回一句「查詢中」
        # 的 ack（讓 reply_token 不會逾時浪費掉），背景繼續算，算完後改用沒有時間
        # 限制的 push_message 補發正式答案——只有這一小部分真的算比較久的長尾請求
        # 才會用到則數。
        #
        # 重要部署前提：Cloud Run 預設只有在「處理請求期間」才配置 CPU，回應送出後
        # CPU 會被節流，背景執行緒可能因此卡住/變超慢。這個服務必須開啟「CPU 一律
        # 配置」（gcloud 的 --no-cpu-throttling，或 Console 編輯修訂版本頁「一律配置
        # CPU」），否則超過時限、真的走到背景 push 這條路的請求不保證能可靠跑完。
        append_user_history(user_id, "求職者", raw_msg)

        # log_ctx：讓 _compute_ai_decision_messages() 把「決策出的 action」「是否
        # 觸發保底訊息」這兩個內部才知道的資訊，透過這個共用 dict 帶出來給下面的
        # 結構化 log 使用，不用改變函式原本「回傳訊息內容」的回傳值型別（見
        # services/monitoring_service.py、HANDOFF.md「監控與告警機制」）。
        # 這個 dict 只會被背景執行緒寫入一次、主執行緒在 future 完成後才讀取，
        # 順序上不會有競爭寫入的問題。
        log_ctx = {}
        future = _AI_DECISION_EXECUTOR.submit(
            _compute_ai_decision_messages,
            user_id, raw_msg, active_jobs, faq_list, current_location, history_text, log_ctx,
            current_slots, target_line_bot_api,
        )
        try:
            messages = future.result(timeout=AI_DECISION_SYNC_TIMEOUT_SECONDS)
            try:
                target_line_bot_api.reply_message(reply_token, messages)
            except Exception:
                # reply_token 這裡失敗，通常代表 token 已經過期（例如這次請求在
                # 進到這段程式碼之前，已經因為排隊等執行緒等原因耗掉不少時間，
                # 我們量不到那段延遲）。這條路徑原本沒有任何備援：answer 已經算
                # 好了卻沒送出去、也沒有排進背景補發，使用者會完全收不到回覆。
                # 改成失敗時直接改用不受 reply_token 時效限制的 push_message
                # 補發，答案已經算好了，沒有理由白白浪費掉。
                print(f"[同步回覆送出失敗 Traceback，改用 push_message 補發]: {traceback.format_exc()}")
                try:
                    target_line_bot_api.push_message(user_id, messages)
                except Exception:
                    print(f"[push_message 補發也失敗 Traceback]: {traceback.format_exc()}")
            log_ai_decision_event(
                path="ai_decision", action=log_ctx.get("action", ""),
                fallback_triggered=log_ctx.get("fallback_triggered", False),
                ai_decision_empty=log_ctx.get("ai_decision_empty", False),
                matched_category=detected_category_from_text, matched_brand=detected_brand,
                latency_seconds=time.monotonic() - _request_start, delivery_mode="sync",
            )
        except concurrent.futures.TimeoutError:
            ack_text = "收到您的訊息了！沛沛正在為您查詢最合適的資訊，請稍等一下下 🔍😊"
            # 刻意不把 ack_text 寫入對話歷史：這只是系統層級的「稍等」提示，不是真正
            # 的對話內容，寫進去會佔用歷史視窗（只留最後 10 則）、也會讓下一輪 AI
            # 看到的對話紀錄被這句話打斷，變成「求職者提問」後面接的不是「沛沛的
            # 正式答案」。
            try:
                target_line_bot_api.reply_message(reply_token, TextSendMessage(text=ack_text))
            except Exception:
                # reply_token 這時可能已經過期（尤其高併發、或這個限時同步等待秒數
                # 設得比較接近 LINE 30 秒上限時更容易發生——從 LINE 送出訊息到這裡
                # 開始計時，中間可能已經有排隊延遲，我們量不到）。就算「查詢中」這句
                # 安慰訊息送失敗，也絕對不能放棄：下面 push_message 補發正式答案不
                # 受 reply_token 時效限制，一定要繼續排進去，不然使用者會完全收不到
                # 任何回覆（原本這裡沒有 try/except，安慰訊息送失敗會導致整個函式
                # 例外中斷、根本沒機會排進背景補發，見 HANDOFF.md 的說明）。
                print(f"[逾時 ack 訊息送出失敗 Traceback]: {traceback.format_exc()}")
            future.add_done_callback(
                lambda fut: _push_ai_decision_messages(
                    fut, user_id, target_line_bot_api, log_ctx,
                    _request_start, detected_category_from_text, detected_brand,
                )
            )
        return

    except Exception as e:
        print(f"[處理訊息嚴重異常 Traceback]: {traceback.format_exc()}")
        fallback_msg = "您好！我是招募顧問沛沛 😊 剛才系統稍有延遲，請問您想了解哪種類型的工作或發薪福利呢？"
        fallback_message = TextSendMessage(
            text=fallback_msg,
            quick_reply=QuickReply(items=[
                QuickReplyButton(action=MessageAction(label="📍 找新莊工作", text="新莊工作")),
                QuickReplyButton(action=MessageAction(label="📍 找桃園工作", text="桃園工作")),
                QuickReplyButton(action=MessageAction(label="💰 了解發薪日", text="發薪日是哪天")),
                QuickReplyButton(action=MessageAction(label="👀 都給我看看", text="都給我看看"))
            ])
        )
        # 這裡是最外層的保底：能走到這裡代表前面已經出了非預期的例外（例如
        # Firestore/Notion 一時連線異常），這個當下 reply_token 也可能已經因為
        # 前面處理耗時而過期。跟其他分支一樣，reply_message() 失敗時改用不受
        # 時效限制的 push_message 補發，不能讓使用者這一輪完全收不到任何回覆。
        try:
            target_line_bot_api.reply_message(reply_token, fallback_message)
        except Exception:
            print(f"[保底回覆送出失敗 Traceback，改用 push_message 補發]: {traceback.format_exc()}")
            try:
                target_line_bot_api.push_message(user_id, fallback_message)
            except Exception:
                print(f"[push_message 補發也失敗 Traceback]: {traceback.format_exc()}")


def _fallback_messages() -> TextSendMessage:
    """AI 決策流程內部發生未預期例外時的保底訊息，同時給同步（reply_message）跟
    逾時後背景（push_message）兩條路徑共用，確保無論走哪條路徑、保底文案都一致。"""
    fallback_msg = "您好！我是招募顧問沛沛 😊 剛才系統稍有延遲，請問您想了解哪種類型的工作或發薪福利呢？"
    return TextSendMessage(
        text=fallback_msg,
        quick_reply=QuickReply(items=[
            QuickReplyButton(action=MessageAction(label="📍 找新莊工作", text="新莊工作")),
            QuickReplyButton(action=MessageAction(label="📍 找桃園工作", text="桃園工作")),
            QuickReplyButton(action=MessageAction(label="💰 了解發薪日", text="發薪日是哪天")),
            QuickReplyButton(action=MessageAction(label="👀 都給我看看", text="都給我看看"))
        ])
    )


def _compute_ai_decision_messages(
    user_id: str,
    raw_msg: str,
    active_jobs: list,
    faq_list: list,
    current_location: str,
    history_text: str,
    log_ctx: dict = None,
    known_slots: dict = None,
    target_line_bot_api: LineBotApi = None,
):
    """執行真正耗時的 AI 決策（候選集合建構 + Gemini 呼叫 + 解析），是
    process_user_message() 步驟 2 原本的內容搬過來的。這個函式故意只負責「算出
    答案」，不負責「怎麼送出去」——送出方式（免費的 reply_message 還是逾時後才
    用的 push_message）由呼叫端決定，這樣同一份決策邏輯才能同時給「限時同步等待」
    跟「逾時後背景補發」兩條路徑共用，不必重複兩份。

    log_ctx：選填的共用 dict，用來把這裡才知道的「決策出的 action」「是否觸發
    保底訊息」帶出去給呼叫端記錄結構化 log（見 HANDOFF.md「監控與告警機制」），
    不影響這個函式原本的回傳值（訊息內容）。

    known_slots：呼叫端（process_user_message）呼叫 update_user_slots() 時，
    其實已經拿到最新合併好的槽位，這裡直接沿用即可，不用再花一次 Firestore
    讀取重新查一次一模一樣的資料——這個限時同步等待的時間窗口裡，每省一次
    網路來回都對延遲有幫助。沒有傳入時（例如舊測試直接呼叫這個函式）才退回
    原本自己查一次的行為，保持相容。

    target_line_bot_api：只在決策結果是 UNKNOWN_FAQ 或 NO_MATCH 時才會用到，
    呼叫 LINE 的 get_profile() 取得求職者的暱稱，連同 user_id 一起記錄到
    「求職者提問追蹤」資料庫，讓招募專員能回頭去 LINE 官方帳號後台找到這個人
    手動回覆（見 HANDOFF.md）。沒有傳入時（例如舊測試）就只記錄 user_id，
    不會出錯。

    保證不會往外拋出例外：任何步驟失敗都在這裡攔截並回傳保底訊息，讓呼叫端
    不需要再處理例外，只要送出這裡回傳的 messages 即可。"""
    try:
        _current_slots_for_candidates = known_slots if known_slots is not None else get_user_slots(user_id)
        ai_job_candidates = build_ai_job_candidates(
            active_jobs,
            f"{history_text} {raw_msg}",
            current_location,
            _current_slots_for_candidates,
            limit=70,
        )
        ai_faq_candidates = build_ai_faq_candidates(faq_list, raw_msg, limit=20)

        job_index_text = ""
        for idx, j in enumerate(ai_job_candidates):
            public_t = j.get("職缺名稱(對外)", "")
            internal_t = j.get("職缺名稱", "")
            vendor_t = j.get("系統廠商名稱", "")
            cat_t = j.get("職務類別", "")
            # 一定要帶入 current_location：像「全台/多縣市門市自選」這種涵蓋
            # 5 個以上行政區的職缺，不帶目標地區時只會回傳籠統的「各區門市據點
            # （自選區域）」，AI 看不出使用者問的地區到底有沒有明確包含在內，
            # 只能照規則保守回答「暫無明確列出」；但組卡片顯示用的地點文字
            # 有正確帶入 target_location，導致卡片老實顯示該地區、AI 文字回覆
            # 卻說沒有，兩邊資訊兜不起來（見 HANDOFF.md 板橋/蝦皮案例）。
            loc = format_clean_location(j, current_location)
            shift = j.get("班別", "")
            leave_t = j.get("休假方式", "")
            pay_method = j.get("領薪方式", "")
            salary = j.get("薪資", "")
            desc = j.get("精華亮點") or j.get("工作內容(對外)", "")
            job_index_text += f"[ID:{idx}] 廠商:{vendor_t} | 職缺:{internal_t} | 類別:{cat_t} | 地點:{loc} | 班別:{shift} | 休假:{leave_t} | 領薪方式:{pay_method or '未提供'} | 待遇:{salary} | 特色:{desc}\n"

        faq_index_text = ""
        for f in ai_faq_candidates:
            faq_index_text += f"問：{f.get('question')} => 答：{f.get('answer')}\n"

        # 求職者目前鎖定的條件（地區/類別/廠商）明確列給 AI 看，不要只靠對話
        # 歷史文字讓 AI 自己推測——同一件事「這句話本身有沒有重複提到」會讓
        # AI 給出不一致的答案：例如先問「蝦皮門市有嗎」，接著只問「八德有缺人
        # 嗎」，這句話本身沒再提到蝦皮/門市，AI 只能從歷史文字模糊推測，容易
        # 沒把「八德」跟「蝦皮門市」這個仍然生效的條件放在一起判斷，把不相關
        # 類別的職缺也一併推薦出來；但換成「蝦皮門市 八德有缺嗎」這種當下就
        # 完整重複條件的問法，AI 又能正確判斷沒有符合。把已鎖定的條件明講出來，
        # 讓 AI 不管這句話有沒有重複提到，都能穩定套用同一套判斷（見 HANDOFF.md
        # 案例）。
        _slot_location = current_location or _current_slots_for_candidates.get("location", "")
        _slot_category = _current_slots_for_candidates.get("category", "")
        _slot_brand = _current_slots_for_candidates.get("brand", "")
        _known_condition_parts = []
        if _slot_location:
            _known_condition_parts.append(f"地區={_slot_location}")
        if _slot_category and _slot_category != "不限":
            _known_condition_parts.append(f"工作類型={_slot_category}")
        if _slot_brand:
            _known_condition_parts.append(f"廠商={_slot_brand}")
        known_conditions_text = "、".join(_known_condition_parts) if _known_condition_parts else "（目前尚未鎖定任何條件）"

        ai_prompt = f"""你是一位「材霈有限公司」非常親切、高情商的線上招募顧問「沛沛」。
你的任務是：結合對話歷史，優先從常見問題庫 (FAQ) 精確解答，並在求職者尋找工作時推薦合適職缺。

【極重要原則（嚴格遵守）】：
1. 自稱一律為「沛沛」。遵守就業服務法（無年齡性別限制）。
2. 【FAQ 絕對依據】：凡詢問公司規章、福利、發薪日、勞健保、投保、體檢、面試文件、休假規範等問題：
   - 若【常見問題庫 (FAQ)】中有收錄，action 必須是 "ASK"，並嚴格依據該內容回答！
   - 若【常見問題庫 (FAQ)】中完全沒有收錄且非詢問職缺，action 必須是 "UNKNOWN_FAQ"！
3. 【條件退讓與職缺推薦原則】：
   - 若求職者指定條件在清單中有完全相符的職缺，action 為 "RECOMMEND" 推薦該職缺。
   - 若求職者的複合條件（例如：特定地區+班別+週休）無完全吻合項目，但有次要吻合（如：同地區但為排休制），action 仍為 "RECOMMEND"，並在 reply 誠懇說明（例如：「目前新莊暫無固定週休的夜班，但有排休制的優質夜班大廠職缺，為您推薦參考喔！」）。
4. 【地區判斷只能依據「地點:」欄位白紙黑字列出的內容，絕對不能自行推論】：
   - 每筆候選職缺的「地點:」欄位已經是同仁在系統裡實際勾選的正確行政區，不是模糊描述。
   - 求職者問到「地點:」欄位沒有明確列出的行政區時（即使那個行政區行政上屬於同一個縣市），一律視為「此條件無完全相符職缺」，不能因為同縣市有其他行政區的職缺、或地點欄位只寫到縣市層級，就自行推論或宣稱「這個行政區也涵蓋在內」。
   - 範例：地點欄位是「桃園市（蘆竹、龜山）」，求職者問「八德有沒有缺額」，不能回答「八德也涵蓋在內」——因為「地點:」欄位沒有列出八德。
   - 【每一輪都要重新核對，不能只因為之前推薦過同一筆職缺，就假設這次問的更精確地區也符合】：即使【過去對話】裡已經把某筆職缺推薦給求職者，只要求職者這次問的是更精確或不同的地區（例如先問「有蝦皮門市嗎」推薦了某筆職缺，後來追問「八德有缺嗎」），都要重新核對這筆職缺當下的「地點:」欄位有沒有明確列出這次問的地區，不能因為「之前才剛推薦過」就直接沿用、宣稱這筆職缺也符合這次的地區條件。
   - 【「地點:」欄位如果只是「各區OO據點（自選區域）」這種概括性描述】：代表這筆職缺涵蓋 5 個以上行政區、系統改用統稱顯示，不代表其中「每一個」行政區都確定涵蓋在內。求職者問「這裡面有哪些區」「OO區在不在裡面」時，絕對不能自己舉例點名說某個行政區「也算在內」，只能誠實說明目前系統只顯示概括範圍、無法確認單一行政區是否包含，建議直接應徵讓招募專員確認。「特色:」欄位的行銷文字（例如提到涵蓋幾個縣市、共幾間門市）只是概略描述，同樣不能拿來當作確認某個行政區有沒有涵蓋的依據。
   - 【絕對不能自我矛盾】：如果在【過去對話】裡，你自己已經對同一個行政區回答過「沒有明確列出」，這一輪即使換了問法（例如求職者這次問的是「有哪些區」而不是直接問那個行政區的名字），也絕對不能改口說那個行政區「也在範圍內」——同一個行政區在同一段對話裡，前後的判斷結果必須一致。
5. 【求職者已鎖定的條件要持續套用，不是只看這句話本身有沒有重複提到】：
   - 下面【求職者目前鎖定的條件】是求職者之前的對話裡已經確認、還沒有被取消或換掉的條件，即使「求職者最新輸入」這句話本身沒有再重複提到，也要當成這句話仍然帶著這些條件一起問。
   - 範例：已鎖定條件是「工作類型=門市、廠商=蝦皮」，求職者這句話只問「八德有缺人嗎」，要判斷成「蝦皮的門市類職缺，八德有沒有」，不能因為這句話沒提到門市/蝦皮，就放寬成「八德不限類型/廠商的職缺」通通推薦。
6. 【單一焦點追問】：若需引導求職者補充條件，每次僅拋出單一缺漏問題（優先順序：地區 -> 班別 -> 工作類型），避免一次詢問多個問題。

【求職者目前鎖定的條件】：
{known_conditions_text}

【常見問題庫 (FAQ)】：
{faq_index_text if faq_index_text else "（無相符 FAQ）"}

【目前招募中職缺清單】：
{job_index_text if job_index_text else "（目前此條件暫無直接相符職缺）"}

【過去對話】：
{history_text if history_text else "（剛開始對話）"}

【求職者最新輸入】：
「{raw_msg}」

請輸出一個 JSON 物件，欄位定義如下：
- action：以下四選一
  - "ASK"：命中 FAQ、日常問候或單一焦點引導
  - "UNKNOWN_FAQ"：未收錄於 FAQ 的規章/制度/福利問題
  - "RECOMMEND"：有符合或退讓推薦的職缺
  - "NO_MATCH"：指定廠商/地區完全無任何相近職缺
- reply：依 action 對應的回覆文字
  - action 為 "ASK" 時：嚴格依 FAQ 內容或以沛沛口吻親切回覆，約 35-70 字
  - action 為 "UNKNOWN_FAQ" 時：親切說明已為求職者記錄此問題，會由招募專員確認，並主動詢問目前想看哪裡的工作，約 40-70 字
  - action 為 "RECOMMEND" 時：推薦語或退讓說明，約 25-60 字
  - action 為 "NO_MATCH" 時：親切說明暫無缺額並主動推薦其他熱門方向
- buttons：字串陣列，3-5 個相關快速回覆按鈕文字（action 為 "ASK"/"UNKNOWN_FAQ"/"NO_MATCH" 時才需要，"RECOMMEND" 給空陣列即可）
- ids：整數陣列，符合或退讓推薦的職缺數字 ID（只有 action 為 "RECOMMEND" 時才需要，例如 [0] 或 [0, 1]，其他 action 給空陣列即可）
"""

        ai_output = query_gemini_ai(ai_prompt, response_schema=AI_DECISION_SCHEMA)
        print(f"[Gemini 決策輸出]:\n{ai_output}\n")

        # 開啟結構化輸出模式後 ai_output 保證是合法 JSON（或空字串，代表 AI 呼叫失敗）；
        # 這裡仍保留 try/except 當最後一道防線，任何非預期情況都會落到 action="" 走
        # 保底引導，不會讓例外往外拋出中斷整個對話。
        try:
            decision = json.loads(ai_output) if ai_output else {}
        except (json.JSONDecodeError, TypeError):
            print(f"[Gemini 決策輸出非合法 JSON，改走保底引導]: {ai_output!r}")
            decision = {}

        action = str(decision.get("action") or "").strip().upper()
        if log_ctx is not None:
            log_ctx["action"] = action
            if not action:
                # Gemini「優雅降級」回傳空字串或非預期格式時（例如 MODEL_FALLBACK_LIST
                # 每個模型都失敗、配額用盡），不會走到下面的 except Exception，而是
                # 直接落到後面的保底引導/預設問候語，使用者看起來像正常對話，但其實
                # 這句話完全沒有被 Gemini 真的判斷過——這是監控要抓的「安靜失敗」，
                # 見 services/monitoring_service.py 的說明。
                log_ctx["ai_decision_empty"] = True
        ai_reply_text = str(decision.get("reply") or "").strip()
        ai_buttons = decision.get("buttons") if isinstance(decision.get("buttons"), list) else []
        ai_ids = decision.get("ids") if isinstance(decision.get("ids"), list) else []

        if action == "UNKNOWN_FAQ":
            _record_unanswered_question(raw_msg, user_id, target_line_bot_api)

            reply_text = ai_reply_text or "謝謝您的提問！沛沛已先幫您把這個問題記錄下來回報給招募專員囉 😊 請問您目前想先看看哪個地區或班別的工作呢？"
            append_user_history(user_id, "招募顧問沛沛", reply_text)

            buttons = _build_quick_reply_buttons(ai_buttons, [
                QuickReplyButton(action=MessageAction(label="📍 新莊工作", text="新莊工作")),
                QuickReplyButton(action=MessageAction(label="📍 桃園工作", text="桃園工作")),
                QuickReplyButton(action=MessageAction(label="☀️ 固定早班", text="早班工作")),
                QuickReplyButton(action=MessageAction(label="👀 都給我看看", text="都給我看看"))
            ])
            return TextSendMessage(text=reply_text, quick_reply=QuickReply(items=buttons))

        elif action == "RECOMMEND":
            reply_text = ai_reply_text or "太棒了！沛沛為您推薦以下符合需求的職缺："
            append_user_history(user_id, "招募顧問沛沛", reply_text)

            matched_jobs = [
                ai_job_candidates[i] for i in ai_ids
                if isinstance(i, int) and 0 <= i < len(ai_job_candidates)
            ]
            if not matched_jobs:
                matched_jobs = ai_job_candidates[:4]

            if not matched_jobs:
                # ai_job_candidates 本身也是空的（例如 Notion 職缺暫時全部停招）——
                # 沒有任何職缺可以組 Flex 卡片，LINE 的 Carousel 格式不接受空陣列，
                # 改成純文字保底訊息，不要送出一定會被 LINE API 拒絕的空卡片。
                no_job_text = "不好意思，沛沛這邊目前暫時沒有符合的職缺資料，麻煩稍後再試一次，或直接留言想找的地區/類型，我們會盡快為您確認喔 🙏"
                append_user_history(user_id, "招募顧問沛沛", no_job_text)
                return TextSendMessage(text=no_job_text)

            flex_card = create_job_flex_card(matched_jobs, user_id, current_location)
            return [TextSendMessage(text=reply_text), flex_card]

        elif action in ("ASK", "NO_MATCH"):
            if action == "NO_MATCH":
                # 職缺完全找不到符合的（跟 UNKNOWN_FAQ 是同一類「沒有比對到答案」的
                # 情境，差別只在一個是問職缺、一個是問其他事情），初期同樣記錄下來，
                # 方便招募專員一次盤點求職者實際都在問什麼、有哪些地區/類型的職缺
                # 需求目前完全接不住。
                _record_unanswered_question(raw_msg, user_id, target_line_bot_api)

            reply_text = ai_reply_text or "您好呀！沛沛隨時為您服務，想請問您偏好哪個地區或工作班別呢？"
            append_user_history(user_id, "招募顧問沛沛", reply_text)

            buttons = _build_quick_reply_buttons(ai_buttons, [
                QuickReplyButton(action=MessageAction(label="📍 新莊工作", text="新莊工作")),
                QuickReplyButton(action=MessageAction(label="📍 桃園工作", text="桃園工作")),
                QuickReplyButton(action=MessageAction(label="☀️ 固定早班", text="早班工作")),
                QuickReplyButton(action=MessageAction(label="🌙 固定夜班", text="夜班工作")),
                QuickReplyButton(action=MessageAction(label="👀 都給我看看", text="都給我看看"))
            ])
            return TextSendMessage(text=reply_text, quick_reply=QuickReply(items=buttons))

        # ---------------- 保底引導[cite: 6] ----------------
        progressive_text, progressive_buttons = build_progressive_question(user_id, current_location)
        if progressive_text:
            append_user_history(user_id, "招募顧問沛沛", progressive_text)
            return TextSendMessage(text=progressive_text, quick_reply=QuickReply(items=progressive_buttons))

        default_text = "您好呀！我是招募顧問沛沛 😊\n\n很高興為您服務！想了解您偏好在哪個地區上班？或是偏好早班還是夜班呢？"
        append_user_history(user_id, "招募顧問沛沛", default_text)
        quick_reply = QuickReply(items=[
            QuickReplyButton(action=MessageAction(label="📍 新莊工作", text="新莊工作")),
            QuickReplyButton(action=MessageAction(label="📍 桃園工作", text="桃園工作")),
            QuickReplyButton(action=MessageAction(label="☀️ 固定早班", text="早班工作")),
            QuickReplyButton(action=MessageAction(label="🌙 固定夜班", text="夜班工作")),
            QuickReplyButton(action=MessageAction(label="👀 都給我看看", text="都給我看看"))
        ])
        return TextSendMessage(text=default_text, quick_reply=quick_reply)

    except Exception:
        print(f"[AI 決策異常 Traceback]: {traceback.format_exc()}")
        if log_ctx is not None:
            log_ctx["fallback_triggered"] = True
        return _fallback_messages()


def _push_ai_decision_messages(
    future: concurrent.futures.Future,
    user_id: str,
    target_line_bot_api: LineBotApi,
    log_ctx: dict = None,
    request_start: float = None,
    matched_category: str = "",
    matched_brand: str = "",
) -> None:
    """限時同步等待逾時後的補發路徑：process_user_message() 已經先用 reply_token
    回過「查詢中」的 ack，這裡是 future 算完後的 done-callback，改用沒有時間限制
    的 push_message(user_id, ...) 補發正式答案。_compute_ai_decision_messages()
    內部已經把所有例外都轉成保底訊息、保證不會往外拋例外，這裡的 try/except
    純粹是最後一道防線，避免使用者只收到 ack 就沒有下文。"""
    log_ctx = log_ctx if log_ctx is not None else {}
    try:
        messages = future.result()
    except Exception:
        print(f"[限時等待逾時後取得 AI 決策結果失敗 Traceback]: {traceback.format_exc()}")
        messages = _fallback_messages()
        log_ctx["fallback_triggered"] = True

    try:
        target_line_bot_api.push_message(user_id, messages)
    except Exception:
        print(f"[限時等待逾時後 push_message 補發失敗 Traceback]: {traceback.format_exc()}")

    log_ai_decision_event(
        path="ai_decision", action=log_ctx.get("action", ""),
        fallback_triggered=log_ctx.get("fallback_triggered", False),
        ai_decision_empty=log_ctx.get("ai_decision_empty", False),
        matched_category=matched_category, matched_brand=matched_brand,
        latency_seconds=(time.monotonic() - request_start) if request_start is not None else 0.0,
        delivery_mode="push",
    )


def process_image_message(event, target_line_bot_api: LineBotApi):
    """求職者傳送圖片（例如截圖）時的保底回覆。目前沒有解析圖片內容的能力，
    若完全不回應，使用者會誤以為機器人已讀不回或故障，所以主動引導改用文字描述需求。"""
    reply_token = event.reply_token
    if reply_token in ["00000000000000000000000000000000", "ffffffffffffffffffffffffffffffff"]:
        return

    if STAFFED_HOURS_GUARD_ENABLED and _is_staffed_hours():
        # 白天交給真人專員手動處理，理由同 process_user_message()。
        return

    user_id = getattr(event.source, 'user_id', 'USER')
    reply_text = "您好呀！我是招募顧問沛沛 😊\n\n沛沛目前還看不懂圖片內容喔，麻煩您用文字告訴我想找的地區、班別，或直接打字描述您的問題，我會盡快為您查詢喔！"

    try:
        append_user_history(user_id, "求職者", "[傳送了一張圖片]")
        append_user_history(user_id, "招募顧問沛沛", reply_text)
    except Exception:
        print(f"[圖片訊息保底回覆 - 寫入對話歷史失敗 Traceback]: {traceback.format_exc()}")

    quick_reply = QuickReply(items=[
        QuickReplyButton(action=MessageAction(label="📍 新莊工作", text="新莊工作")),
        QuickReplyButton(action=MessageAction(label="📍 桃園工作", text="桃園工作")),
        QuickReplyButton(action=MessageAction(label="☀️ 固定早班", text="早班工作")),
        QuickReplyButton(action=MessageAction(label="👀 都給我看看", text="都給我看看"))
    ])
    target_line_bot_api.reply_message(reply_token, TextSendMessage(text=reply_text, quick_reply=quick_reply))
