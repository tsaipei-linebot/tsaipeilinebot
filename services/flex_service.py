import re
from urllib.parse import quote
from linebot.models import FlexSendMessage
from config import DEFAULT_RESUME_URLS, SERVICE_BASE_URL
from services.notion_service import sanitize_uri

def resolve_apply_url_key_by_industry(job: dict) -> str:
    """依職缺行業判斷履歷網址分類鍵值（Spx／Service／Manufacture），純判斷、
    不組網址。拆出這支獨立函式是為了讓 create_job_flex_card() 組「先過我們
    自己伺服器記錄點擊、再轉址」的連結時，可以只帶這個白名單 key 過去（見
    main.py 的 /apply-click 端點），不必把完整外部網址放進求職者看得到、
    可能被竄改的網址參數裡（開放重導向風險）。"""
    full_search_text = f"{job.get('職缺名稱(對外)', '')} {job.get('職缺名稱', '')} {job.get('職務類別', '')} {job.get('行業別', '')} {job.get('工作內容(對外)', '')}".lower()

    if any(k in full_search_text for k in ["蝦皮", "智取店", "店到店", "spx", "外送"]):
        return "Spx"

    if any(k in full_search_text for k in ["服務", "餐飲", "服飾", "門市", "專櫃", "店員", "廚助"]):
        return "Service"

    return "Manufacture"

def resolve_apply_url_by_industry(job: dict) -> str:
    """依職缺行業精準解析對應的線上履歷網址 (維持原設定)[cite: 8]"""
    return DEFAULT_RESUME_URLS[resolve_apply_url_key_by_industry(job)]

def get_location_suffix_by_industry(job: dict) -> str:
    """依職缺產業類別動態回傳專屬地點描述語[cite: 8]"""
    text = f"{job.get('行業別', '')} {job.get('職務類別', '')} {job.get('職缺名稱(對外)', '')} {job.get('職缺名稱', '')}".lower()
    
    # 1. 科技 / 半導體 / 製造 / 作業員[cite: 8]
    if any(k in text for k in ["科技", "半導體", "製造", "作業員", "晶圓", "工程師", "電子", "廠", "美光", "欣興", "設備", "技術員"]):
        return "主要廠區/園區"
    
    # 2. 門市 / 零售 / 餐飲[cite: 8]
    if any(k in text for k in ["門市", "零售", "餐飲", "專櫃", "店面", "店員", "服飾", "店到店"]):
        return "各區門市據點（自選區域）"
        
    # 3. 倉儲 / 物流 / 外送[cite: 8]
    if any(k in text for k in ["倉儲", "物流", "外送", "理貨", "司機", "配送", "揀貨", "倉管"]):
        return "各區物流倉儲據點"
        
    # 4. 一般預設[cite: 8]
    return "各區據點（自選區域）"

def _strip_county_prefix(district: str, county: str) -> str:
    """行政區欄位有時候會被同仁習慣性地加上縣市前綴（例如寫成「桃園市八德區」
    而不是單純「八德區」），通常是為了避免同名行政區跨縣市搞混（例如中山區
    台北市、基隆市都有）。這不影響地區比對邏輯是否命中（比對時只看子字串
    有沒有出現），但直接拿去組成顯示文字，會變成「桃園市（桃園市八德區、
    桃園市蘆竹區）」這種重複縣市名稱的累贅呈現，這裡在顯示前先把每個行政區
    開頭重複的縣市名稱去掉。同時處理「台/臺」全半形不一致的情況（例如縣市
    欄位寫「台北市」、行政區欄位卻寫「臺北市中山區」）。"""
    county = county.strip()
    if not county:
        return district
    for variant in {county, county.replace("台", "臺"), county.replace("臺", "台")}:
        if variant and district.startswith(variant):
            return district[len(variant):].strip() or district
    return district


def format_clean_location(job: dict, target_location: str = "") -> str:
    """地點智慧聚合器：依產業別與行政區數量精準格式化[cite: 8]"""
    county = str(job.get("縣市") or "").strip()
    district = str(job.get("行政區") or "").strip()
    suffix = get_location_suffix_by_industry(job)

    dist_list = [
        _strip_county_prefix(d.strip(), county)
        for d in re.split(r'[,，、\s]+', district) if d.strip()
    ]

    # 1. 使用者有明確指定行政區時，優先顯示該行政區[cite: 8]
    if target_location:
        for d in dist_list:
            if target_location in d or d in target_location:
                return d

        county_list = [c.strip() for c in re.split(r'[,，、\s]+', county) if c.strip()]
        for c in county_list:
            if target_location in c or c in target_location:
                return f"{c} {suffix}".strip()

    # 2. 智慧地點聚合 (依行政區數量級距)[cite: 8]
    dist_count = len(dist_list)

    if dist_count == 0:
        return county or "依公司指派地點"

    if dist_count <= 4:
        short_dist = "、".join(dist_list)
        return f"{county}（{short_dist}）" if county else short_dist

    # 行政區 >= 5 個時套用產業專屬描述語[cite: 8]
    if county:
        return f"{county} {suffix}"
    return suffix

def create_job_flex_card(jobs: list, user_id: str, target_location: str = "") -> FlexSendMessage:
    """建構職缺推薦 Flex Carousel 輪播卡片（綁定 Notion 唯一職缺名稱）[cite: 8]"""
    bubbles = []
    # 材霈品牌色（跟 delivery/static/style.css 的 --brand/--brand-dark/--brand-bg
    # 同一套，公司內部系統網頁 /portal、/management、/hr、/delivery 都共用這套
    # 配色）：BRAND 用在最顯眼的類別標籤跟主要按鈕，讓卡片有材霈自己的識別，
    # 不是 LINE 預設的綠色。其餘標籤維持不同色系，方便一眼分辨班別/產業/類型。
    BRAND = "#ea580c"
    BRAND_DARK = "#c2410c"
    BRAND_BG = "#fff1e8"
    badge_styles = {
        "shift": {"bg": "#E8F5E9", "text": "#2E7D32"},
        "industry": {"bg": "#E3F2FD", "text": "#1565C0"},
        "type": {"bg": "#F3E5F5", "text": "#7B1FA2"},
        "category": {"bg": BRAND_BG, "text": BRAND_DARK}
    }

    for job in jobs[:10]:
        public_job_title = str(job.get("職缺名稱(對外)") or job.get("職缺名稱") or job.get("職務類別") or "優質職缺").strip()
        # Notion 唯一識別鍵：職缺名稱 (內部名稱)
        unique_internal_title = str(job.get("職缺名稱") or job.get("_internal_title") or public_job_title).strip()
        
        display_location = format_clean_location(job, target_location)
        salary = str(job.get("薪資") or "依公司規定").strip()
        pay_method = str(job.get("領薪方式") or "").strip()
        shift = str(job.get("班別") or "").strip()
        industry = str(job.get("行業別") or "").strip()
        job_type = str(job.get("全/兼職") or "").strip()
        job_category = str(job.get("職務類別") or "").strip()
        
        tags_contents = []
        if shift:
            tags_contents.append({"type": "box", "layout": "horizontal", "backgroundColor": badge_styles["shift"]["bg"], "cornerRadius": "sm", "paddingAll": "xs", "paddingStart": "sm", "paddingEnd": "sm", "contents": [{"type": "text", "text": shift[:8], "size": "xxs", "color": badge_styles["shift"]["text"], "weight": "bold"}]})
        if job_category:
            first_cat = job_category.split(",")[0].strip()
            tags_contents.append({"type": "box", "layout": "horizontal", "backgroundColor": badge_styles["category"]["bg"], "cornerRadius": "sm", "paddingAll": "xs", "paddingStart": "sm", "paddingEnd": "sm", "contents": [{"type": "text", "text": first_cat[:8], "size": "xxs", "color": badge_styles["category"]["text"], "weight": "bold"}]})
        elif industry:
            tags_contents.append({"type": "box", "layout": "horizontal", "backgroundColor": badge_styles["industry"]["bg"], "cornerRadius": "sm", "paddingAll": "xs", "paddingStart": "sm", "paddingEnd": "sm", "contents": [{"type": "text", "text": industry[:8], "size": "xxs", "color": badge_styles["industry"]["text"], "weight": "bold"}]})
        if job_type:
            tags_contents.append({"type": "box", "layout": "horizontal", "backgroundColor": badge_styles["type"]["bg"], "cornerRadius": "sm", "paddingAll": "xs", "paddingStart": "sm", "paddingEnd": "sm", "contents": [{"type": "text", "text": job_type[:8], "size": "xxs", "color": badge_styles["type"]["text"], "weight": "bold"}]})

        highlight_desc = str(job.get("精華亮點") or "").strip()
        if not highlight_desc:
            raw_desc = str(job.get("工作內容(對外)") or "").strip()
            clean_raw = re.sub(r'[*•▶►◆◇■□▲▼\r\n\t]+', ' ', raw_desc)
            highlight_desc = f"開放應徵【{public_job_title}】，環境單純、福利健全，歡迎點擊應徵！" if len(clean_raw) < 5 else (clean_raw[:40] + "...")
            
        resume_type_key = resolve_apply_url_key_by_industry(job)
        direct_apply_link = sanitize_uri(DEFAULT_RESUME_URLS[resume_type_key])
        if SERVICE_BASE_URL:
            # 先連到我們自己的 /apply-click 轉址端點記錄點擊（見 main.py），
            # 再由該端點 302 轉去真正的履歷網站；求職者感覺不出差異。
            # SERVICE_BASE_URL 沒設定時（尚未上線這項追蹤功能）維持原本行為，
            # 按鈕直接連到履歷網站。
            final_apply_link = (
                f"{SERVICE_BASE_URL.rstrip('/')}/apply-click"
                f"?uid={quote(str(user_id))}&type={quote(resume_type_key)}&job={quote(unique_internal_title)}"
            )
        else:
            final_apply_link = direct_apply_link

        body_contents = [
            {"type": "text", "text": "🎯 材霈推薦職缺", "weight": "bold", "color": BRAND, "size": "xs"},
            {"type": "text", "text": public_job_title, "weight": "bold", "size": "lg", "margin": "xs", "wrap": True}
        ]
        
        if tags_contents:
            body_contents.append({"type": "box", "layout": "horizontal", "spacing": "xs", "margin": "sm", "contents": tags_contents})
            
        detail_lines = [
            {"type": "text", "text": f"📍 地點：{display_location}", "size": "sm", "color": "#444444", "wrap": True},
            {"type": "text", "text": f"💰 待遇：{salary}", "size": "sm", "color": "#D32F2F", "weight": "bold", "wrap": True},
        ]
        if pay_method:
            detail_lines.append({"type": "text", "text": f"💵 領薪方式：{pay_method}", "size": "sm", "color": "#444444", "wrap": True})
        detail_lines.append({"type": "text", "text": f"✨ 特色：{highlight_desc}", "size": "xs", "color": "#555555", "wrap": True, "margin": "xs"})

        body_contents.extend([
            {"type": "separator", "margin": "md"},
            {
                "type": "box",
                "layout": "vertical",
                "margin": "md",
                "spacing": "xs",
                "contents": detail_lines
            }
        ])

        bubble = {
            "type": "bubble",
            "body": {"type": "box", "layout": "vertical", "contents": body_contents},
            "footer": {
                "type": "box",
                "layout": "vertical",
                "spacing": "sm",
                "contents": [
                    {
                        "type": "button",
                        "style": "secondary",
                        "color": "#F0F0F0",
                        "height": "sm",
                        "action": {
                            "type": "message",
                            "label": "📖 了解詳細內容",
                            "text": f"查看職缺詳情 {unique_internal_title}"
                        }
                    },
                    {
                        "type": "button",
                        "style": "primary",
                        "color": BRAND,
                        "height": "sm",
                        "action": {"type": "uri", "label": "📄 填寫線上履歷", "uri": final_apply_link}
                    },
                    {
                        "type": "button",
                        "style": "link",
                        "height": "sm",
                        "action": {
                            "type": "message",
                            "label": "📅 預約面試",
                            "text": f"預約面試 {unique_internal_title}"
                        }
                    }
                ]
            }
        }
        bubbles.append(bubble)
        
    return FlexSendMessage(alt_text=f"為您找到 {len(bubbles)} 筆熱門職缺！", contents={"type": "carousel", "contents": bubbles})
