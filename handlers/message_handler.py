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
    extract_current_target_location, extract_shift_preference,
    detect_category_label, detect_brand_label, filter_jobs_by_category_tiered,
    build_progressive_question, build_ai_job_candidates, build_ai_faq_candidates,
    job_matches_category_filter
)
from services.ai_service import query_gemini_ai, format_full_job_detail_with_ai

def process_user_message(event, target_line_bot_api: LineBotApi):
    """處理求職端所有對話與按鈕點擊事件[cite: 2]"""
    reply_token = event.reply_token
    if reply_token in ["00000000000000000000000000000000", "ffffffffffffffffffffffffffffffff"]:
        return

    raw_msg = event.message.text.strip()
    user_id = getattr(event.source, 'user_id', 'USER')
    print(f"\n[收到使用者訊息]: 「{raw_msg}」 (User: {user_id})")

    # 1. 讀取 Notion 職缺與 FAQ[cite: 2]
    active_jobs = fetch_jobs_data()
    faq_list = fetch_faqs_data()

    # ---------------- 步驟 0-1：處理「了解詳細內容」（優先直接讀取 Notion 排版工作說明） ----------------
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
            
            # 優先直接使用 Notion 已建置之「排版工作說明」（0 毫秒極速反應，自帶完整規格與法規聲明）[cite: 2]
            formatted_detail = str(matched_job.get("排版工作說明") or "").strip()
            if not formatted_detail:
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

    # 2. 載入對話歷史紀錄 (7天)[cite: 2]
    history = get_user_history(user_id)
    history_text = "\n".join([f"{item['role']}: {item['text']}" for item in history])
    full_conversation_context = f"{history_text}\n求職者: {raw_msg}"
    current_location = extract_current_target_location(full_conversation_context)
    clean_input = clean_text_for_search(raw_msg)

    # 漸進式需求收集：即時更新目前已掌握的地區 / 工作類別 / 時段 條件[cite: 2]
    detected_shift = extract_shift_preference(raw_msg)
    detected_category_from_text = detect_category_label(clean_input)
    update_user_slots(user_id, location=current_location, category=detected_category_from_text, shift=detected_shift)

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

    # ---------------- 步驟 1：【最高優先級】精準「職務類別」多工種嚴格直達攔截 ----------------
    is_delivery_intent = any(k in clean_input for k in ["外送", "外送員", "配送員", "巡貨司機", "送貨司機", "外送工作"])
    is_store_intent = any(k in clean_input for k in ["門市", "店員", "門市人員", "蝦皮門市", "智取店", "店到店"]) and not is_delivery_intent
    is_momo_intent = any(k in clean_input for k in ["momo", "富邦", "富昇"])

    # ---------------- 步驟 1-0：像真人顧問一樣「漸進式需求收集」(地區 → 工作類別 → 時段) ----------------
    show_all_bypass_keywords = ["都給我看", "都要看", "都可以", "全部", "隨便", "看全部", "都看"]
    has_explicit_category_intent = is_delivery_intent or is_store_intent or is_momo_intent
    if has_explicit_category_intent and not any(k in clean_input for k in show_all_bypass_keywords):
        _slots_now = get_user_slots(user_id)
        _location_ready = bool(current_location or _slots_now.get("location"))
        _category_ready = bool(_slots_now.get("category"))
        _shift_ready = bool(_slots_now.get("shift"))
        if not (_location_ready and _category_ready and _shift_ready):
            question_text, question_buttons = build_progressive_question(user_id, current_location)
            if question_text:
                append_user_history(user_id, "求職者", raw_msg)
                append_user_history(user_id, "招募顧問沛沛", question_text)
                target_line_bot_api.reply_message(
                    reply_token,
                    TextSendMessage(text=question_text, quick_reply=QuickReply(items=question_buttons))
                )
                print(f"[漸進式需求收集] 條件尚未齊全（地區:{_location_ready} 類別:{_category_ready} 時段:{_shift_ready}），先引導求職者補充")
                return

    direct_matches = []

    # 1-1. 外送員 / 司機[cite: 2]
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
        if not direct_matches:
            for j in active_jobs:
                cat = str(j.get("_job_category", "")).lower()
                int_t = str(j.get("_internal_title", "")).lower()
                if any(k in cat for k in ["外送", "司機", "配送"]) or any(k in int_t for k in ["外送", "司機", "配送"]):
                    direct_matches.append(j)

    # 1-2. 門市人員 / 店到店 (精準排除外送衝突)[cite: 2]
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

        if not direct_matches:
            direct_matches = filter_jobs_by_category_tiered(active_jobs, "門市", _store_brand)

    # 1-3. momo / 富邦 / 富昇[cite: 2]
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

    # ---------------- 步驟 2：【泛意圖與全部瀏覽攔截】（「都給我看看」、「都可以」） ----------------
    show_all_keywords = ["都給我看", "都要看", "都可以", "全部", "隨便", "推薦一下", "有什麼工作", "還有什麼", "看全部", "都看"]
    if any(k in clean_input for k in show_all_keywords):
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
        _brand_for_filter = detect_brand_label(f"{history_text} {raw_msg}")

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
            print(f"[泛意圖攔截] 已保留指定類別/品牌條件，但查無符合職缺（類別:{_known_category_for_filter} 品牌:{_brand_for_filter}）")
            return

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

    # ---------------- 步驟 3：組合「候選」Notion 職缺/FAQ 索引給 Gemini 進行多輪推理 ----------------
    _current_slots_for_candidates = get_user_slots(user_id)
    ai_job_candidates = build_ai_job_candidates(
        active_jobs,
        f"{history_text} {raw_msg}",
        current_location,
        _current_slots_for_candidates,
        limit=40,
    )
    ai_faq_candidates = build_ai_faq_candidates(faq_list, raw_msg, limit=20)

    job_index_text = ""
    for idx, j in enumerate(ai_job_candidates):
        public_t = j.get("職缺名稱(對外)", "")
        internal_t = j.get("職缺名稱", "")
        cat_t = j.get("職務類別", "")
        loc = format_clean_location(j)
        shift = j.get("班別", "")
        ind = j.get("行業別", "")
        salary = j.get("薪資", "")
        # 使用精華亮點作為 AI 決策索引（大幅縮短 Prompt 長度與思考時間）[cite: 2]
        desc = j.get("精華亮點") or j.get("工作內容(對外)", "")
        job_index_text += f"[ID:{idx}] 內部名稱:{internal_t} | 職務類別:{cat_t} | 對外名稱:{public_t} | 地點:{loc} | 行業:{ind} | 班別:{shift} | 待遇:{salary} | 特色:{desc}\n"

    faq_index_text = ""
    for f in ai_faq_candidates:
        faq_index_text += f"問：{f.get('question')} => 答：{f.get('answer')}\n"

    _current_slots = get_user_slots(user_id)
    slot_location_text = current_location or _current_slots.get("location") or "尚未提供"
    slot_category_text = _current_slots.get("category") or "尚未提供"
    slot_shift_text = _current_slots.get("shift") or "尚未提供"

    ai_prompt = f"""你是一位「材霈有限公司」非常親切、高情商的真人在線人資招募顧問（名字叫「沛沛」）。
你的目標是：結合過去 7 天的對話歷史，以真人顧問口吻引導求職者，並在資料庫中有符合職缺時推薦。

【極重要規則（絕對禁止幻覺）】：
1. 自稱一律為「沛沛」。遵守就業服務法（無年齡性別限制）。
2. 【禁止擅自宣稱額滿或沒有職缺】：只要下方清單中存在該工種/職務類別（包含門市人員、外送員、司機、理貨、作業員等），一律視為開放招募中並直接推薦（ACTION:RECOMMEND）！
3. 【職務類別精確辨識】：
   - 求職者詢問「門市/店面」，推薦【職務類別:門市人員/店員】之職缺。
   - 求職者詢問「外送/司機」，推薦【職務類別:外送員/司機】之職缺。
4. 【求職者想看全部/隨便/都可以】：若求職者說「都給我看看」、「都可以」、「全部」，請直接推薦目前地區的所有職缺（ACTION:RECOMMEND），絕對不要繼續反問！
5. 【情境與按鈕規則】：
   - 目前對話鎖定的地區是：【{current_location if current_location else "未指定"}】。
   - 按鈕請一律圍繞該地區推薦，絕對不要跨縣市跳出不相干按鈕。
6. 【漸進式需求收集原則（像真人顧問一步一步了解需求）】：
   - 目前已掌握的條件 → 地區：【{slot_location_text}】、工作類別/行業別：【{slot_category_text}】、時段班別：【{slot_shift_text}】。
   - 除非求職者已明確表示「不限地區/都可以/隨便/全部/都給我看看」，否則請依序一次只確認一項缺少的條件：① 地區 → ② 工作類別/行業別 → ③ 時段班別，語氣自然親切，不要一次條列三個問題。
   - 只有在地區、工作類別/行業別、時段班別三項都已掌握（或求職者已表示不限/都可以），才可以輸出 ACTION:RECOMMEND；否則請輸出 ACTION:ASK，並在 REPLY 中只詢問「尚未提供」的那一項，絕對不要重複詢問已經掌握的條件。
   - 此原則不影響規則 2-4：只要條件確認齊全（或求職者表示都可以），只要清單中有符合的職缺，依然要直接推薦，不得宣稱額滿或無此職缺。

【公司官方常見問題庫 (FAQ)】：
{faq_index_text if faq_index_text else "（暫無額外 FAQ）"}

【目前公司招募中的職缺清單】：
{job_index_text if job_index_text else "（目前公司在全台灣北、中、南區均有開放各類優質職缺）"}

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

格式 B（求職者條件在清單中有符合的職缺，或求職者表示都可以/都給我看）：
ACTION:RECOMMEND
IDS:（符合的職缺數字，例如 0 或 0,1）
REPLY:（給求職者的溫暖推薦語）

格式 C（全台清單中確實完全沒有符合該條件的職缺）：
ACTION:NO_MATCH
REPLY:（以沛沛口吻說明目前暫無開放，並主動推薦同一地區的其他優質職缺）
BUTTONS:（提供目前所在地區的其他工種或班別選項）

請直接輸出："""

    ai_output = query_gemini_ai(ai_prompt)
    print(f"[Gemini 決策輸出]:\n{ai_output}\n")

    append_user_history(user_id, "求職者", raw_msg)

    # 4. 解析 AI 輸出[cite: 2]
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
            matched_jobs = ai_job_candidates[:3] if ai_job_candidates else active_jobs[:3]

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
        
        # 本地情境防呆按鈕生成[cite: 2]
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

    # 5. 智慧本地多輪上下文合流容錯檢索[cite: 2]
    print("[執行智慧本地多輪容錯比對]")
    combined_query = clean_text_for_search(history_text + " " + raw_msg)
    matched_jobs = []

    for j in active_jobs:
        s_text = j.get("_search_text", "")
        if any(k in combined_query for k in ["外送", "司機", "配送", "送貨"]) and any(k in s_text for k in ["外送", "司機", "配送", "送貨"]):
            matched_jobs.append(j)
            continue
        if any(k in combined_query for k in ["momo", "富邦", "富昇"]) and any(k in s_text for k in ["momo", "富邦", "富昇"]):
            matched_jobs.append(j)
            continue
        if any(k in combined_query for k in ["蝦皮", "門市", "店到店"]):
            _local_brand = "蝦皮" if "蝦皮" in combined_query else ""
            if job_matches_category_filter(j, "門市", _local_brand, allow_relaxed=True):
                matched_jobs.append(j)
                continue
        tokens = [t for t in ["板橋", "新莊", "三重", "台北", "新北", "桃園", "中壢", "龜山", "早班", "夜班", "理貨", "作業員"] if t in combined_query]
        if tokens and all(t in s_text for t in tokens):
            matched_jobs.append(j)

    if matched_jobs:
        reply_text = "太棒了！沛沛為您找到以下符合條件的推薦職缺，歡迎點擊下方「了解詳細內容」或線上應徵喔 😊"
        append_user_history(user_id, "招募顧問沛沛", reply_text)
        target_line_bot_api.reply_message(reply_token, [TextSendMessage(text=reply_text), create_job_flex_card(matched_jobs[:3], user_id, current_location)])
        return

    # 預設引導：優先以漸進式提問（客製化問句）[cite: 2]
    progressive_text, progressive_buttons = build_progressive_question(user_id, current_location)
    if progressive_text:
        append_user_history(user_id, "招募顧問沛沛", progressive_text)
        target_line_bot_api.reply_message(
            reply_token,
            TextSendMessage(text=progressive_text, quick_reply=QuickReply(items=progressive_buttons))
        )
        return

    # 條件皆已齊全但仍未匹配到任何職缺時的保底引導語[cite: 2]
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