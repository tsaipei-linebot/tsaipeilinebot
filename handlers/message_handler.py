import re
import traceback
from linebot import LineBotApi
from linebot.models import (
    TextSendMessage, QuickReply, QuickReplyButton, MessageAction
)
from services.session_service import (
    get_user_history, append_user_history, get_user_slots, update_user_slots
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
    job_matches_category_filter
)
from services.ai_service import query_gemini_ai, format_full_job_detail_with_ai


def process_user_message(event, target_line_bot_api: LineBotApi):
    """處理求職端所有對話，支援 FAQ 精確回答與未收錄問題自動入庫"""
    reply_token = event.reply_token
    if reply_token in ["00000000000000000000000000000000", "ffffffffffffffffffffffffffffffff"]:
        return

    raw_msg = event.message.text.strip()
    user_id = getattr(event.source, 'user_id', 'USER')
    print(f"\n[收到使用者訊息]: 「{raw_msg}」 (User: {user_id})")

    try:
        active_jobs = fetch_jobs_data()
        faq_list = fetch_faqs_data()

        # ---------------- 步驟 0-1：處理「查看職缺詳情」 ----------------
        if raw_msg.startswith("查看職缺詳情"):
            target_title = raw_msg.replace("查看職缺詳情", "").strip()
            matched_job = None
            for j in active_jobs:
                if target_title and (target_title in j.get("_parsed_title", "") or j.get("_parsed_title", "") in target_title):
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

        # ---------------- 步驟 0-2：就業服務法合規攔截 (年齡/性別) ----------------
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

        # ---------------- 步驟 0-3：Session 載入與多輪動態槽位覆蓋 ----------------
        history = get_user_history(user_id)
        history_text = "\n".join([f"{item['role']}: {item['text']}" for item in history[-6:]])
        user_slots = get_user_slots(user_id)
        clean_input = clean_text_for_search(raw_msg)

        explicit_any_location = any(k in clean_input for k in ["都可以", "不限地區", "隨便", "哪裡都", "不限地點", "全台", "全區"])

        extracted_loc = extract_current_target_location(raw_msg, "")
        if extracted_loc:
            current_location = extracted_loc
        elif explicit_any_location:
            current_location = ""
        else:
            current_location = user_slots.get("location", "")

        detected_shift = extract_shift_preference(raw_msg) or user_slots.get("shift", "")
        detected_leave = extract_leave_preference(raw_msg) or user_slots.get("leave", "")
        detected_category_from_text = detect_category_label(clean_input) or user_slots.get("category", "")
        detected_brand = detect_brand_label(raw_msg, active_jobs) or user_slots.get("brand", "")

        update_user_slots(
            user_id, 
            location=current_location, 
            category=detected_category_from_text, 
            shift=detected_shift, 
            leave=detected_leave, 
            brand=detected_brand
        )

        # ---------------- 步驟 0-4：純泛意圖與全部瀏覽攔截 ----------------
        show_all_keywords = ["都給我看", "都要看", "都可以", "全部", "隨便", "推薦一下", "有什麼工作", "還有什麼", "看全部", "都看"]
        has_specific_intent = bool(detected_brand or detected_category_from_text or any(k in clean_input for k in ["momo", "蝦皮", "酷澎", "美光", "欣興", "外送", "門市", "作業員", "製造"]))
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

        # ---------------- 步驟 1：精準工種直達攔截 ----------------
        is_delivery_intent = any(k in clean_input for k in ["外送", "外送員", "配送員", "巡貨司機", "送貨司機", "外送工作"])
        is_store_intent = any(k in clean_input for k in ["門市", "店員", "門市人員", "蝦皮門市", "智取店", "店到店"]) and not is_delivery_intent
        is_momo_intent = any(k in clean_input for k in ["momo", "富邦", "富昇"])

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

        # ---------------- 步驟 2：Vertex AI 顧問推理 (FAQ 優先 + 未收錄捕獲) ----------------
        _current_slots_for_candidates = get_user_slots(user_id)
        ai_job_candidates = build_ai_job_candidates(
            active_jobs,
            f"{history_text} {raw_msg}",
            current_location,
            _current_slots_for_candidates,
            limit=35,
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
            salary = j.get("薪資", "")
            desc = j.get("精華亮點") or j.get("工作內容(對外)", "")
            job_index_text += f"[ID:{idx}] 廠商:{vendor_t} | 職缺:{internal_t} | 類別:{cat_t} | 地點:{loc} | 班別:{shift} | 休假:{leave_t} | 待遇:{salary} | 特色:{desc}\n"

        faq_index_text = ""
        for f in ai_faq_candidates:
            faq_index_text += f"問：{f.get('question')} => 答：{f.get('answer')}\n"

        ai_prompt = f"""你是一位「材霈有限公司」非常親切、高情商的線上招募顧問「沛沛」。
你的任務是：結合對話歷史，優先從常見問題庫 (FAQ) 精確解答，並在求職者尋找工作時推薦合適職缺。

【極重要原則（嚴格遵守）】：
1. 自稱一律為「沛沛」。遵守就業服務法（無年齡性別限制）。
2. 【FAQ 絕對依據】：凡詢問公司規章、福利、發薪日、勞健保、投保、體檢、面試文件、休假規範等非找工作問題：
   - 若【常見問題庫 (FAQ)】中有收錄，必須輸出 ACTION:ASK，並嚴格依據該內容回答！
   - 若【常見問題庫 (FAQ)】中「完全沒有」收錄且非詢問職缺，必須輸出 ACTION:UNKNOWN_FAQ！
3. 【職缺推薦】：若求職者是在尋找工作（指定地區、班別、工種），請從職缺清單中比對並輸出 ACTION:RECOMMEND。若完全無相符職缺才輸出 ACTION:NO_MATCH。

【常見問題庫 (FAQ)】：
{faq_index_text if faq_index_text else "（無相符 FAQ）"}

【目前招募中職缺清單】：
{job_index_text if job_index_text else "（目前此條件暫無直接相符職缺）"}

【過去對話】：
{history_text if history_text else "（剛開始對話）"}

【求職者最新輸入】：
「{raw_msg}」

請輸出以下四種格式之一：
格式 A（命中 FAQ、日常問候或引導找工作條件）：
ACTION:ASK
REPLY:（嚴格依 FAQ 內容以沛沛口吻親切回覆，約 35-70 字）
BUTTONS:（相關快速按鈕 3-5 個，逗號分隔）

格式 B（未收錄於 FAQ 的規章/制度/福利問題）：
ACTION:UNKNOWN_FAQ
REPLY:（親切說明已為求職者記錄此問題，會由招募專員確認，並主動詢問目前想看哪裡的工作，約 40-70 字）
BUTTONS:（找工作的推薦按鈕，如：新莊工作,桃園工作,早班工作,夜班工作）

格式 C（有符合推薦之職缺）：
ACTION:RECOMMEND
IDS:（符合的職缺數字 ID，例如 0 或 0,1）
REPLY:（推薦語 20-50 字）

格式 D（指定廠商/地區暫無職缺）：
ACTION:NO_MATCH
REPLY:（親切說明暫無缺額並主動推薦其他方向）
BUTTONS:（相關地區或工種按鈕，逗號分隔）
"""

        ai_output = query_gemini_ai(ai_prompt)
        print(f"[Gemini 決策輸出]:\n{ai_output}\n")

        append_user_history(user_id, "求職者", raw_msg)

        # ---------------- 步驟 3：解析 AI 輸出 ----------------
        if "ACTION:UNKNOWN_FAQ" in ai_output:
            # 自動將未收錄問題寫入 Notion FAQ 資料庫
            append_unresolved_faq_to_notion(raw_msg)

            reply_match = re.search(r'REPLY:\s*(.+?)(?=\nBUTTONS:|$)', ai_output, re.DOTALL)
            buttons_match = re.search(r'BUTTONS:\s*(.+)', ai_output)

            reply_text = reply_match.group(1).strip() if reply_match else "謝謝您的提問！沛沛已先幫您把這個問題記錄下來回報給招募專員囉 😊 請問您目前想先看看哪個地區或班別的工作呢？"
            append_user_history(user_id, "招募顧問沛沛", reply_text)

            buttons = []
            if buttons_match:
                raw_buttons = [b.strip() for b in buttons_match.group(1).split(",") if b.strip()]
                for b_label in raw_buttons[:5]:
                    clean_txt = re.sub(r'^[📍☀️🌙📦🏭🏬🍽️🔄🛵\s]+', '', b_label)
                    buttons.append(QuickReplyButton(action=MessageAction(label=b_label[:20], text=clean_txt)))

            if not buttons:
                buttons = [
                    QuickReplyButton(action=MessageAction(label="📍 新莊工作", text="新莊工作")),
                    QuickReplyButton(action=MessageAction(label="📍 桃園工作", text="桃園工作")),
                    QuickReplyButton(action=MessageAction(label="☀️ 固定早班", text="早班工作")),
                    QuickReplyButton(action=MessageAction(label="👀 都給我看看", text="都給我看看"))
                ]

            target_line_bot_api.reply_message(reply_token, TextSendMessage(text=reply_text, quick_reply=QuickReply(items=buttons)))
            return

        elif "ACTION:RECOMMEND" in ai_output:
            ids_match = re.search(r'IDS:\s*([0-9,\s]+)', ai_output)
            reply_match = re.search(r'REPLY:\s*(.+)', ai_output, re.DOTALL)
            reply_text = reply_match.group(1).strip() if reply_match else "太棒了！沛沛為您推薦以下符合需求的職缺："
            append_user_history(user_id, "招募顧問沛沛", reply_text)

            matched_jobs = []
            if ids_match:
                indices = [int(n.strip()) for n in ids_match.group(1).split(",") if n.strip().isdigit() and int(n.strip()) < len(ai_job_candidates)]
                matched_jobs = [ai_job_candidates[i] for i in indices]

            if not matched_jobs:
                matched_jobs = ai_job_candidates[:4]

            flex_card = create_job_flex_card(matched_jobs, user_id, current_location)
            target_line_bot_api.reply_message(reply_token, [TextSendMessage(text=reply_text), flex_card])
            return

        elif "ACTION:ASK" in ai_output or "ACTION:NO_MATCH" in ai_output:
            reply_match = re.search(r'REPLY:\s*(.+?)(?=\nBUTTONS:|$)', ai_output, re.DOTALL)
            buttons_match = re.search(r'BUTTONS:\s*(.+)', ai_output)

            reply_text = reply_match.group(1).strip() if reply_match else f"您好呀！沛沛隨時為您服務，想請問您偏好哪個地區或工作班別呢？"
            append_user_history(user_id, "招募顧問沛沛", reply_text)

            buttons = []
            if buttons_match:
                raw_buttons = [b.strip() for b in buttons_match.group(1).split(",") if b.strip()]
                for b_label in raw_buttons[:5]:
                    clean_txt = re.sub(r'^[📍☀️🌙📦🏭🏬🍽️🔄🛵\s]+', '', b_label)
                    buttons.append(QuickReplyButton(action=MessageAction(label=b_label[:20], text=clean_txt)))

            if not buttons:
                buttons = [
                    QuickReplyButton(action=MessageAction(label="📍 新莊工作", text="新莊工作")),
                    QuickReplyButton(action=MessageAction(label="📍 桃園工作", text="桃園工作")),
                    QuickReplyButton(action=MessageAction(label="☀️ 固定早班", text="早班工作")),
                    QuickReplyButton(action=MessageAction(label="🌙 固定夜班", text="夜班工作")),
                    QuickReplyButton(action=MessageAction(label="👀 都給我看看", text="都給我看看"))
                ]

            quick_reply = QuickReply(items=buttons)
            target_line_bot_api.reply_message(reply_token, TextSendMessage(text=reply_text, quick_reply=quick_reply))
            return

        # ---------------- 步驟 4：保底引導 ----------------
        default_text = "您好呀！我是招募顧問沛沛 😊\n\n很高興為您服務！想了解您偏好在哪個地區上班？或是偏好早班還是夜班呢？"
        append_user_history(user_id, "招募顧問沛沛", default_text)
        quick_reply = QuickReply(items=[
            QuickReplyButton(action=MessageAction(label="📍 新莊工作", text="新莊工作")),
            QuickReplyButton(action=MessageAction(label="📍 桃園工作", text="桃園工作")),
            QuickReplyButton(action=MessageAction(label="☀️ 固定早班", text="早班工作")),
            QuickReplyButton(action=MessageAction(label="🌙 固定夜班", text="夜班工作")),
            QuickReplyButton(action=MessageAction(label="👀 都給我看看", text="都給我看看"))
        ])
        target_line_bot_api.reply_message(reply_token, TextSendMessage(text=default_text, quick_reply=quick_reply))

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
