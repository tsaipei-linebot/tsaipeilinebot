import concurrent.futures
import json
import re
import traceback
from datetime import datetime
from linebot import LineBotApi
from linebot.models import (
    TextSendMessage, QuickReply, QuickReplyButton, MessageAction
)
from config import STAFFED_HOURS_START, STAFFED_HOURS_END, TAIPEI_TZ
from services.session_service import (
    get_user_history, append_user_history, get_user_slots, update_user_slots, clear_user_slots, CLEAR_SLOT
)
from services.notion_service import (
    fetch_jobs_data, fetch_faqs_data, clean_text_for_search, sanitize_uri,
    append_unresolved_faq_to_notion
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
    CATEGORY_KEYWORDS, KNOWN_BRANDS, find_high_confidence_faq_match
)
from services.ai_service import query_gemini_ai, format_full_job_detail_with_ai


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
AI_DECISION_SYNC_TIMEOUT_SECONDS = 8


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

    if not bypass_staffed_hours_guard and _is_staffed_hours():
        # 白天交給真人專員在 LINE 聊天模式手動回覆，沛沛不主動介入，避免兩邊
        # 同時回覆互相打架（詳見 HANDOFF.md「日夜接力」）。
        return

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
        full_reset_keywords = ["重新找", "重選", "重設", "清空條件", "重新開始", "重來", "重頭開始", "清除條件"]
        single_dimension_keywords = ["換個條件", "換一個條件", "改個條件", "換條件", "改條件", "換一下條件"]

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
            
            if not matched_job and active_jobs:
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

        explicit_any_location = any(k in clean_input for k in ["都可以", "不限地區", "隨便", "哪裡都", "不限地點", "全台", "全區"])

        extracted_loc = extract_current_target_location(raw_msg, "")
        negated_loc = detect_negated_location(raw_msg)

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

        if detected_category_from_text:
            category_slot_update = detected_category_from_text
        elif negated_category and negated_category == user_slots.get("category", ""):
            # 使用者明確排除掉目前鎖定的類別（例如「除了外送」）→ 清空，這輪查詢也不再沿用被排除的舊類別
            category_slot_update = CLEAR_SLOT
            detected_category_from_text = ""
        else:
            category_slot_update = ""
            detected_category_from_text = user_slots.get("category", "")

        # 廠商（brand）跟地點/類別的行為不同：地點/類別是持續性偏好，沿用到被明確取消為止；
        # 廠商比較像單次詢問，這句話沒有再提到某個廠商，就視為使用者已經看過、
        # 不應該讓候選集合被舊的廠商鎖住，所以每輪都重新判斷，沒偵測到就明確清空。
        detected_brand_this_turn = detect_brand_label(raw_msg, active_jobs)
        if detected_brand_this_turn:
            detected_brand = detected_brand_this_turn
            brand_slot_update = detected_brand_this_turn
        else:
            detected_brand = ""
            brand_slot_update = CLEAR_SLOT if user_slots.get("brand", "") else ""

        update_user_slots(
            user_id, 
            location=location_slot_update, 
            category=category_slot_update, 
            shift=detected_shift, 
            leave=detected_leave, 
            brand=brand_slot_update
        )

        # ---------------- 步驟 0-4：純泛意圖與全部瀏覽攔截[cite: 6] ----------------
        show_all_keywords = ["都給我看", "都要看", "都可以", "全部", "隨便", "推薦一下", "有什麼工作", "還有什麼", "看全部", "都看"]
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
                s_text = j.get("_search_text", "")
                if current_location:
                    loc_clean = current_location.replace("台", "臺")
                    if current_location in s_text or loc_clean in s_text:
                        matched_show_all.append(j)
                else:
                    matched_show_all.append(j)

            _slots_for_show_all = get_user_slots(user_id)
            _known_category_for_filter = _slots_for_show_all.get("category", "")
            _brand_for_filter = _slots_for_show_all.get("brand", "")

            if _known_category_for_filter and _known_category_for_filter != "不限":
                matched_show_all = filter_jobs_by_category_tiered(
                    matched_show_all,
                    _known_category_for_filter,
                    _brand_for_filter,
                )

            if not matched_show_all:
                if current_location:
                    loc_clean = current_location.replace("台", "臺")
                    matched_show_all = [j for j in active_jobs if current_location in j.get("_search_text", "") or loc_clean in j.get("_search_text", "")]
                if not matched_show_all:
                    matched_show_all = active_jobs[:5]

            reply_text = f"沒問題！沛沛馬上為您整理{current_location if current_location else ''}目前招募中的熱門職缺，歡迎點擊查看詳細說明或線上應徵喔 😊"
            append_user_history(user_id, "求職者", raw_msg)
            append_user_history(user_id, "招募顧問沛沛", reply_text)
            target_line_bot_api.reply_message(reply_token, [TextSendMessage(text=reply_text), create_job_flex_card(matched_show_all[:5], user_id, current_location)])
            return

        # ---------------- 步驟 1：精準工種直達攔截（含否定語氣防呆）[cite: 6] ----------------
        is_negative = has_negative_intent(raw_msg)
        # 同樣改用 CATEGORY_KEYWORDS/KNOWN_BRANDS 當唯一來源，跟 has_specific_intent
        # 共用同一份清單，避免各處關鍵字覆蓋範圍互相兜不起來。
        is_delivery_intent = any(k in clean_input for k in CATEGORY_KEYWORDS["外送"]) and not is_negative
        is_store_intent = any(k in clean_input for k in CATEGORY_KEYWORDS["門市"]) and not is_delivery_intent and not is_negative
        is_momo_intent = any(k in clean_input for k in KNOWN_BRANDS["momo"]) and not is_negative

        direct_matches = []

        if is_delivery_intent:
            for j in active_jobs:
                cat = str(j.get("_job_category", "")).lower()
                int_t = str(j.get("_internal_title", "")).lower()
                pub_t = str(j.get("職缺名稱(對外)", "")).lower()
                if any(k in cat for k in ["外送", "司機", "配送"]) or any(k in int_t for k in ["外送", "司機", "配送"]) or any(k in pub_t for k in ["外送", "司機", "配送"]):
                    if current_location:
                        loc_clean = current_location.replace("台", "臺")
                        if current_location in j.get("_search_text", "") or loc_clean in j.get("_search_text", ""):
                            direct_matches.append(j)
                    else:
                        direct_matches.append(j)

        elif is_store_intent:
            _store_brand = "蝦皮" if "蝦皮門市" in clean_input else ""
            _location_jobs = []
            for j in active_jobs:
                if current_location:
                    loc_clean = current_location.replace("台", "臺")
                    if current_location in j.get("_search_text", "") or loc_clean in j.get("_search_text", ""):
                        _location_jobs.append(j)
                else:
                    _location_jobs.append(j)

            direct_matches = filter_jobs_by_category_tiered(_location_jobs, "門市", _store_brand)

        elif is_momo_intent:
            momo_jobs = [j for j in active_jobs if any(k in j.get("_search_text", "") for k in ["momo", "富邦", "富昇"])]
            if current_location:
                loc_clean = current_location.replace("台", "臺")
                loc_momo = [j for j in momo_jobs if current_location in j.get("_search_text", "") or loc_clean in j.get("_search_text", "")]
                direct_matches = loc_momo if loc_momo else momo_jobs
            else:
                direct_matches = momo_jobs

        if direct_matches:
            reply_text = f"有的！沛沛為您找到符合條件的推薦職缺囉，歡迎點擊下方「了解詳細內容」或填寫線上履歷應徵喔 😊"
            append_user_history(user_id, "求職者", raw_msg)
            append_user_history(user_id, "招募顧問沛沛", reply_text)
            target_line_bot_api.reply_message(reply_token, [TextSendMessage(text=reply_text), create_job_flex_card(direct_matches[:4], user_id, current_location)])
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

        future = _AI_DECISION_EXECUTOR.submit(
            _compute_ai_decision_messages,
            user_id, raw_msg, active_jobs, faq_list, current_location, history_text,
        )
        try:
            messages = future.result(timeout=AI_DECISION_SYNC_TIMEOUT_SECONDS)
            target_line_bot_api.reply_message(reply_token, messages)
        except concurrent.futures.TimeoutError:
            ack_text = "收到您的訊息了！沛沛正在為您查詢最合適的資訊，請稍等一下下 🔍😊"
            # 刻意不把 ack_text 寫入對話歷史：這只是系統層級的「稍等」提示，不是真正
            # 的對話內容，寫進去會佔用歷史視窗（只留最後 10 則）、也會讓下一輪 AI
            # 看到的對話紀錄被這句話打斷，變成「求職者提問」後面接的不是「沛沛的
            # 正式答案」。
            target_line_bot_api.reply_message(reply_token, TextSendMessage(text=ack_text))
            future.add_done_callback(
                lambda fut: _push_ai_decision_messages(fut, user_id, target_line_bot_api)
            )
        return

    except Exception as e:
        print(f"[處理訊息嚴重異常 Traceback]: {traceback.format_exc()}")
        fallback_msg = "您好！我是招募顧問沛沛 😊 剛才系統稍有延遲，請問您想了解哪種類型的工作或發薪福利呢？"
        target_line_bot_api.reply_message(
            reply_token,
            TextSendMessage(
                text=fallback_msg,
                quick_reply=QuickReply(items=[
                    QuickReplyButton(action=MessageAction(label="📍 找新莊工作", text="新莊工作")),
                    QuickReplyButton(action=MessageAction(label="📍 找桃園工作", text="桃園工作")),
                    QuickReplyButton(action=MessageAction(label="💰 了解發薪日", text="發薪日是哪天")),
                    QuickReplyButton(action=MessageAction(label="👀 都給我看看", text="都給我看看"))
                ])
            )
        )


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
):
    """執行真正耗時的 AI 決策（候選集合建構 + Gemini 呼叫 + 解析），是
    process_user_message() 步驟 2 原本的內容搬過來的。這個函式故意只負責「算出
    答案」，不負責「怎麼送出去」——送出方式（免費的 reply_message 還是逾時後才
    用的 push_message）由呼叫端決定，這樣同一份決策邏輯才能同時給「限時同步等待」
    跟「逾時後背景補發」兩條路徑共用，不必重複兩份。

    保證不會往外拋出例外：任何步驟失敗都在這裡攔截並回傳保底訊息，讓呼叫端
    不需要再處理例外，只要送出這裡回傳的 messages 即可。"""
    try:
        _current_slots_for_candidates = get_user_slots(user_id)
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
            loc = format_clean_location(j)
            shift = j.get("班別", "")
            leave_t = j.get("休假方式", "")
            pay_method = j.get("領薪方式", "")
            salary = j.get("薪資", "")
            desc = j.get("精華亮點") or j.get("工作內容(對外)", "")
            job_index_text += f"[ID:{idx}] 廠商:{vendor_t} | 職缺:{internal_t} | 類別:{cat_t} | 地點:{loc} | 班別:{shift} | 休假:{leave_t} | 領薪方式:{pay_method or '未提供'} | 待遇:{salary} | 特色:{desc}\n"

        faq_index_text = ""
        for f in ai_faq_candidates:
            faq_index_text += f"問：{f.get('question')} => 答：{f.get('answer')}\n"

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
4. 【單一焦點追問】：若需引導求職者補充條件，每次僅拋出單一缺漏問題（優先順序：地區 -> 班別 -> 工作類型），避免一次詢問多個問題。

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
        ai_reply_text = str(decision.get("reply") or "").strip()
        ai_buttons = decision.get("buttons") if isinstance(decision.get("buttons"), list) else []
        ai_ids = decision.get("ids") if isinstance(decision.get("ids"), list) else []

        if action == "UNKNOWN_FAQ":
            # 自動將未收錄問題寫入 Notion FAQ 資料庫（寫入前已在 notion_service 做過去重）[cite: 6]
            append_unresolved_faq_to_notion(raw_msg)

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

            flex_card = create_job_flex_card(matched_jobs, user_id, current_location)
            return [TextSendMessage(text=reply_text), flex_card]

        elif action in ("ASK", "NO_MATCH"):
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
        return _fallback_messages()


def _push_ai_decision_messages(future: concurrent.futures.Future, user_id: str, target_line_bot_api: LineBotApi) -> None:
    """限時同步等待逾時後的補發路徑：process_user_message() 已經先用 reply_token
    回過「查詢中」的 ack，這裡是 future 算完後的 done-callback，改用沒有時間限制
    的 push_message(user_id, ...) 補發正式答案。_compute_ai_decision_messages()
    內部已經把所有例外都轉成保底訊息、保證不會往外拋例外，這裡的 try/except
    純粹是最後一道防線，避免使用者只收到 ack 就沒有下文。"""
    try:
        messages = future.result()
    except Exception:
        print(f"[限時等待逾時後取得 AI 決策結果失敗 Traceback]: {traceback.format_exc()}")
        messages = _fallback_messages()

    try:
        target_line_bot_api.push_message(user_id, messages)
    except Exception:
        print(f"[限時等待逾時後 push_message 補發失敗 Traceback]: {traceback.format_exc()}")


def process_image_message(event, target_line_bot_api: LineBotApi):
    """求職者傳送圖片（例如截圖）時的保底回覆。目前沒有解析圖片內容的能力，
    若完全不回應，使用者會誤以為機器人已讀不回或故障，所以主動引導改用文字描述需求。"""
    reply_token = event.reply_token
    if reply_token in ["00000000000000000000000000000000", "ffffffffffffffffffffffffffffffff"]:
        return

    if _is_staffed_hours():
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
