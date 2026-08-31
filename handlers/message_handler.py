import re
from linebot import LineBotApi
from linebot.models import (
    TextSendMessage, QuickReply, QuickReplyButton, MessageAction
)
from services.session_service import (
    get_user_history, append_user_history, get_user_slots, update_user_slots
)
from services.notion_service import (
    fetch_jobs_data, fetch_faqs_data, clean_text_for_search, sanitize_uri
)
from services.flex_service import (
    create_job_flex_card, format_clean_location, resolve_apply_url_by_industry
)
from services.matcher_service import (
    extract_current_target_location, extract_shift_preference, extract_leave_preference,
    detect_category_label, detect_brand_label, filter_jobs_by_category_tiered,
    build_progressive_question, build_ai_job_candidates, build_ai_faq_candidates,
    job_matches_category_filter
)
from services.ai_service import query_gemini_ai, format_full_job_detail_with_ai

def process_user_message(event, target_line_bot_api: LineBotApi):
    """處理求職端所有對話與按鈕點擊事件"""
    reply_token = event.reply_token
    if reply_token in ["00000000000000000000000000000000", "ffffffffffffffffffffffffffffffff"]:
        return

    raw_msg = event.message.text.strip()
    user_id = getattr(event.source, 'user_id', 'USER')
    print(f"\n[收到使用者訊息]: 「{raw_msg}」 (User: {user_id})")

    active_jobs = fetch_jobs_data()
    faq_list = fetch_faqs_data()

    # ---------------- 步驟 0-1：處理「了解詳細內容」（格式化抬頭） ----------------
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

    # 2. 載入對話歷史紀錄 (7天)
    history = get_user_history(user_id)
    history_text = "\n".join([f"{item['role']}: {item['text']}" for item in history])
    full_conversation_context = f"{history_text}\n求職者: {raw_msg}"
    current_location = extract_current_target_location(full_conversation_context)
    clean_input = clean_text_for_search(raw_msg)

    # 漸進式需求收集：更新已掌握條件
    detected_shift = extract_shift_preference(raw_msg)
    detected_leave = extract_leave_preference(raw_msg)
    detected_category_from_text = detect_category_label(clean_input)
    detected_brand = detect_brand_label(raw_msg, active_jobs)

    # 若求職者提及新廠商 (如 coupang)，主動覆寫舊廠商記憶，避免殘留 momo
    if detected_brand:
        update_user_slots(user_id, location=current_location, category=detected_category_from_text, shift=detected_shift, leave=detected_leave, brand=detected_brand)
    else:
        update_user_slots(user_id, location=current_location, category=detected_category_from_text, shift=detected_shift, leave=detected_leave)

    # ---------------- 步驟 0-2：就業服務法合規防呆攔截 ----------------
    age_gender_keywords = ["年齡限制", "幾歲", "年紀", "年齡", "限女性", "限男性", "性別限制", "幾歲以上", "幾歲以下", "高齡", "中高齡"]
    if any(k in raw_msg for k in age_gender_keywords) and ("有嗎" in raw_msg or "可以嗎" in raw_msg or "限制" in raw_msg or "能不能" in raw_msg or "可以做嗎" in raw_msg):
        legal_reply = (
            "您好呀！我是招募顧問沛沛 😊\n\n"
            "依《就業服務法》規定，材霈所有職缺皆【無性別與年齡限制】，歡迎所有求職朋友應徵！\n\n"
            "各廠區與工作主要評估實際工作內容的勝任度（例如：需配合走動作業、搬重或輪班需求）。只要體能與出勤狀況可配合，都非常歡迎線上填寫履歷應徵喔！\n\n"
            "👉 請問您目前希望在【哪個地區】找工作？偏好早班或夜班呢？"
        )
        append_user_history(user_id, "招募顧問沛沛", legal_reply)
        quick_reply = QuickReply(items=[
            QuickReplyButton(action=MessageAction(label="📍 桃園工作", text="桃園工作")),
            QuickReplyButton(action=MessageAction(label="📍 新莊/新北", text="新莊工作")),
            QuickReplyButton(action=MessageAction(label="☀️ 固定早班", text="早班工作")),
            QuickReplyButton(action=MessageAction(label="📦 momo理貨", text="momo理貨")),
            QuickReplyButton(action=MessageAction(label="🏬 蝦皮門市", text="蝦皮門市"))
        ])
        target_line_bot_api.reply_message(reply_token, TextSendMessage(text=legal_reply, quick_reply=quick_reply))
        return

    # ---------------- 步驟 0-3：泛意圖與全部瀏覽攔截（「都給我看看」） ----------------
    show_all_keywords = ["都給我看", "都要看", "都可以", "全部", "隨便", "推薦一下", "有什麼工作", "還有什麼", "看全部", "都看"]
    is_show_all = any(k in clean_input for k in show_all_keywords)

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

        if not matched_show_all and _known_category_for_filter and _known_category_for_filter != "不限":
            reply_text = f"目前{current_location if current_location else ''}沒有符合您指定條件的職缺喔！沛沛可以再幫您看看其他工作 😊"
            append_user_history(user_id, "求職者", raw_msg)
            append_user_history(user_id, "招募顧問沛沛", reply_text)
            buttons = [
                QuickReplyButton(action=MessageAction(label="🏬 其他門市", text=f"{current_location}門市" if current_location else "門市")),
                QuickReplyButton(action=MessageAction(label="📦 理貨/倉儲", text=f"{current_location}理貨" if current_location else "理貨")),
                QuickReplyButton(action=MessageAction(label="🛵 外送/司機", text=f"{current_location}外送" if current_location else "外送")),
                QuickReplyButton(action=MessageAction(label="👀 都給我看看", text="都給我看看"))
            ]
            target_line_bot_api.reply_message(reply_token, TextSendMessage(text=reply_text, quick_reply=QuickReply(items=buttons)))
            return

        if not matched_show_all:
            if current_location:
                loc_clean = current_location.replace("台", "臺")
                matched_show_all = [j for j in active_jobs if current_location in j.get("_search_text", "") or loc_clean in j.get("_search_text", "")]
            if not matched_show_all:
                matched_show_all = active_jobs[:3]

        _slots_to_complete = get_user_slots(user_id)
        if not _slots_to_complete.get("category"):
            _slots_to_complete["category"] = "不限"
        if not _slots_to_complete.get("shift"):
            _slots_to_complete["shift"] = "不限"

        reply_text = f"沒問題！沛沛馬上為您整理{current_location if current_location else ''}目前招募中的熱門職缺，歡迎點擊查看詳細說明或線上應徵喔 😊"
        append_user_history(user_id, "求職者", raw_msg)
        append_user_history(user_id, "招募顧問沛沛", reply_text)
        target_line_bot_api.reply_message(reply_token, [TextSendMessage(text=reply_text), create_job_flex_card(matched_show_all[:5], user_id, current_location)])
        print(f"[泛意圖攔截命中] 成功推播 {len(matched_show_all[:5])} 筆職缺！")
        return

    # ---------------- 步驟 0-4：【全域漸進式需求閘門】 ----------------
    faq_keywords = ["發薪", "領薪", "幾號", "薪水幾號", "面試要帶", "勞保", "健保", "體檢", "公司在哪", "統編", "電話", "聯絡", "休假制度"]
    is_faq_query = any(k in clean_input for k in faq_keywords)

    _slots_now = get_user_slots(user_id)
    _location_ready = bool(current_location or _slots_now.get("location"))
    _category_ready = bool(_slots_now.get("category"))
    _shift_ready = bool(_slots_now.get("shift"))
    all_slots_completed = _location_ready and _category_ready and _shift_ready

    # 若求職者有明確指定廠商 (如: coupang、美光) 或休假偏好，放行直接檢索
    if not is_faq_query and not all_slots_completed and not detected_brand and not detected_leave:
        question_text, question_buttons = build_progressive_question(user_id, current_location)
        if question_text:
            append_user_history(user_id, "求職者", raw_msg)
            append_user_history(user_id, "招募顧問沛沛", question_text)
            target_line_bot_api.reply_message(
                reply_token,
                TextSendMessage(text=question_text, quick_reply=QuickReply(items=question_buttons))
            )
            print(f"[全域漸進式引導] 條件未齊全，優先引導求職者補充")
            return

    # ---------------- 步驟 1：【條件齊全或指定廠商/休假】精準多工種直達攔截 ----------------
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
        if current_location:
            loc_clean = current_location.replace("台", "臺")
            for j in active_jobs:
                s_text = j.get("_search_text", "")
                if any(k in s_text for k in ["momo", "富邦", "富昇"]) and (current_location in s_text or loc_clean in s_text):
                    direct_matches.append(j)
        else:
            for j in active_jobs:
                if any(k in j.get("_search_text", "") for k in ["momo", "富邦", "富昇"]):
                    direct_matches.append(j)

    if direct_matches:
        reply_text = f"有的！沛沛為您找到符合條件的推薦職缺囉，歡迎點擊下方「了解詳細內容」或填寫線上履歷應徵喔 😊"
        append_user_history(user_id, "求職者", raw_msg)
        append_user_history(user_id, "招募顧問沛沛", reply_text)
        target_line_bot_api.reply_message(reply_token, [TextSendMessage(text=reply_text), create_job_flex_card(direct_matches[:3], user_id, current_location)])
        print(f"[最高優先級精準職務類別命中] 成功推播 {len(direct_matches)} 筆職缺！")
        return

    # ---------------- 步驟 2：組合「候選」Notion 職缺/FAQ 給 Gemini 推理 ----------------
    _current_slots_for_candidates = get_user_slots(user_id)
    ai_job_candidates = build_ai_job_candidates(
        active_jobs,
        f"{history_text} {raw_msg}",
        current_location,
        _current_slots_for_candidates,
        limit=40,
    )
    ai_faq_candidates = build_ai_faq_candidates(faq_list, raw_msg, limit=20)

    # 確定性檢索狀態判定（Deterministic Grounding）
    has_exact_vendor_match = False
    if detected_brand:
        has_exact_vendor_match = any(detected_brand.lower() in j.get("_search_text", "") for j in ai_job_candidates)

    system_grounding_note = ""
    if detected_brand and not has_exact_vendor_match:
        system_grounding_note = f"\n【系統真實檢索結果】：求職者詢問的特定廠商【{detected_brand}】目前在【{current_location if current_location else '全台'}】「無缺額」。請輸出 ACTION:NO_MATCH，以親切口吻誠實說明【{detected_brand}】目前暫無職缺開放，並主動引導求職者參考【{current_location if current_location else '目前地區'}】的其他優質大廠工作！"
    elif ai_job_candidates:
        system_grounding_note = f"\n【系統真實檢索結果】：清單中存在符合求職者條件之職缺，請務必輸出 ACTION:RECOMMEND 並推薦最吻合之職缺 ID！"

    job_index_text = ""
    for idx, j in enumerate(ai_job_candidates):
        public_t = j.get("職缺名稱(對外)", "")
        internal_t = j.get("職缺名稱", "")
        vendor_t = j.get("系統廠商名稱", "")
        cat_t = j.get("職務類別", "")
        loc = format_clean_location(j)
        shift = j.get("班別", "")
        leave_t = j.get("休假方式", "")
        ind = j.get("行業別", "")
        salary = j.get("薪資", "")
        desc = j.get("精華亮點") or j.get("工作內容(對外)", "")
        job_index_text += f"[ID:{idx}] 廠商:{vendor_t} | 內部名稱:{internal_t} | 職務類別:{cat_t} | 對外名稱:{public_t} | 地點:{loc} | 行業:{ind} | 班別:{shift} | 休假:{leave_t} | 待遇:{salary} | 特色:{desc}\n"

    faq_index_text = ""
    for f in ai_faq_candidates:
        faq_index_text += f"問：{f.get('question')} => 答：{f.get('answer')}\n"

    slot_location_text = current_location or _slots_now.get("location") or "尚未提供"
    slot_category_text = _slots_now.get("category") or "尚未提供"
    slot_shift_text = _slots_now.get("shift") or "尚未提供"

    ai_prompt = f"""你是一位「材霈有限公司」非常親切、高情商的真人在線人資招募顧問（名字叫「沛沛」）。
你的目標是：結合過去 7 天的對話歷史，以真人顧問口吻引導求職者，並在資料庫中有符合職缺時推薦。

【極重要規則（絕對禁止幻覺）】：
1. 自稱一律為「沛沛」。遵守就業服務法（無年齡性別限制）。
2. 【情境與地區約束】：目前鎖定地區為【{current_location if current_location else "未指定"}】，推薦之職缺與按鈕必須 100% 圍繞該地區，絕對嚴禁跨縣市推薦！
3. 【休假制度嚴格比對】：若求職者要求「週休/見紅休」，只能推薦休假方式包含「週休二日/見紅休」之職缺，絕對嚴禁推薦「四休二/做四休二/輪休」職缺！
4. 【廠商與無缺額原則】：若求職者指定之特定廠商在清單中確實不存在，請輸出 ACTION:NO_MATCH，誠實說明該廠商暫無開放，並主動引導同地區之其他優質大廠。
{system_grounding_note}

【公司官方常見問題庫 (FAQ)】：
{faq_index_text if faq_index_text else "（暫無額外 FAQ）"}

【目前公司招募中的職缺清單】：
{job_index_text if job_index_text else "（目前此地區暫無直接相符職缺）"}

【過去對話歷史】：
{history_text if history_text else "（對話剛開始）"}

【求職者剛說的話】：
「{raw_msg}」

【決策指令】：
請分析上下文，輸出下列其中一種格式：

格式 A（求職者剛打招呼、詢問FAQ、詢問公司名稱、或條件仍需進一步引導）：
ACTION:ASK
REPLY:（以沛沛口吻親切回答，約 40-70 字）
BUTTONS:（緊扣目前地區與當前話題的 3-5 個按鈕，逗號分隔）

格式 B（清單中有符合的職缺）：
ACTION:RECOMMEND
IDS:（符合的職缺數字，例如 0 或 0,1）
REPLY:（給求職者的溫暖推薦語）

格式 C（指定廠商暫無職缺或完全無符合條件）：
ACTION:NO_MATCH
REPLY:（誠實說明目前暫無開放，並主動推薦同一地區的其他優質職缺）
BUTTONS:（提供目前所在地區的其他工種或班別選項）

請直接輸出："""

    ai_output = query_gemini_ai(ai_prompt)
    print(f"[Gemini 決策輸出]:\n{ai_output}\n")

    append_user_history(user_id, "求職者", raw_msg)

    # 3. 解析 AI 輸出
    if "ACTION:RECOMMEND" in ai_output:
        ids_match = re.search(r'IDS:\s*([0-9,\s]+)', ai_output)
        reply_match = re.search(r'REPLY:\s*(.+)', ai_output, re.DOTALL)
        reply_text = reply_match.group(1).strip() if reply_match else "太棒了！沛沛為您推薦以下符合需求的職缺："
        append_user_history(user_id, "招募顧問沛沛", reply_text)

        matched_jobs = []
        if ids_match:
            indices = [int(n.strip()) for n in ids_match.group(1).split(",") if n.strip().isdigit() and int(n.strip()) < len(ai_job_candidates)]
            matched_jobs = [ai_job_candidates[i] for i in indices]

        if not matched_jobs:
            matched_jobs = ai_job_candidates[:3]

        flex_card = create_job_flex_card(matched_jobs, user_id, current_location)
        target_line_bot_api.reply_message(reply_token, [TextSendMessage(text=reply_text), flex_card])
        return

    elif "ACTION:ASK" in ai_output or "ACTION:NO_MATCH" in ai_output:
        reply_match = re.search(r'REPLY:\s*(.+?)(?=\nBUTTONS:|$)', ai_output, re.DOTALL)
        buttons_match = re.search(r'BUTTONS:\s*(.+)', ai_output)

        reply_text = reply_match.group(1).strip() if reply_match else f"您好呀！沛沛隨時為您服務，想請問您偏好哪個班別或工作類型呢？"
        append_user_history(user_id, "招募顧問沛沛", reply_text)

        buttons = []
        if buttons_match:
            raw_buttons = [b.strip() for b in buttons_match.group(1).split(",") if b.strip()]
            for b_label in raw_buttons[:6]:
                clean_txt = re.sub(r'^[📍☀️🌙📦🏭🏬🍽️🔄🛵\s]+', '', b_label)
                buttons.append(QuickReplyButton(action=MessageAction(label=b_label[:20], text=clean_txt)))
        
        if not buttons:
            if current_location:
                buttons = [
                    QuickReplyButton(action=MessageAction(label=f"☀️ {current_location}早班", text=f"{current_location}早班")),
                    QuickReplyButton(action=MessageAction(label=f"🌙 {current_location}夜班", text=f"{current_location}夜班")),
                    QuickReplyButton(action=MessageAction(label=f"📦 {current_location}理貨", text=f"{current_location}理貨")),
                    QuickReplyButton(action=MessageAction(label=f"🛵 {current_location}外送", text=f"{current_location}外送")),
                    QuickReplyButton(action=MessageAction(label="👀 都給我看看", text="都給我看看"))
                ]
            else:
                buttons = [
                    QuickReplyButton(action=MessageAction(label="📍 台北/新北", text="台北工作")),
                    QuickReplyButton(action=MessageAction(label="📍 桃園工作", text="桃園工作")),
                    QuickReplyButton(action=MessageAction(label="☀️ 固定早班", text="早班工作")),
                    QuickReplyButton(action=MessageAction(label="📦 momo理貨", text="momo理貨")),
                    QuickReplyButton(action=MessageAction(label="🏬 蝦皮門市", text="蝦皮門市"))
                ]

        quick_reply = QuickReply(items=buttons)
        target_line_bot_api.reply_message(reply_token, TextSendMessage(text=reply_text, quick_reply=quick_reply))
        return

    # 4. 保底引導
    progressive_text, progressive_buttons = build_progressive_question(user_id, current_location)
    if progressive_text:
        append_user_history(user_id, "招募顧問沛沛", progressive_text)
        target_line_bot_api.reply_message(
            reply_token,
            TextSendMessage(text=progressive_text, quick_reply=QuickReply(items=progressive_buttons))
        )
        return

    default_text = "您好呀！我是招募顧問沛沛 😊\n\n很高興為您服務！想了解您偏好在哪個地區上班？或是哪種工作類型與班別呢？"
    append_user_history(user_id, "招募顧問沛沛", default_text)
    quick_reply = QuickReply(items=[
        QuickReplyButton(action=MessageAction(label="📍 台北/新北", text="台北工作")),
        QuickReplyButton(action=MessageAction(label="📍 桃園工作", text="桃園工作")),
        QuickReplyButton(action=MessageAction(label="☀️ 固定早班", text="早班工作")),
        QuickReplyButton(action=MessageAction(label="📦 momo理貨", text="momo理貨")),
        QuickReplyButton(action=MessageAction(label="👀 都給我看看", text="都給我看看"))
    ])
    target_line_bot_api.reply_message(reply_token, TextSendMessage(text=default_text, quick_reply=quick_reply))