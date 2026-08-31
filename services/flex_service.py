import re
from linebot.models import FlexSendMessage
from config import DEFAULT_RESUME_URLS
from services.notion_service import sanitize_uri

def resolve_apply_url_by_industry(job: dict) -> str:
    """依職缺行業精準解析對應的線上履歷網址 (維持原設定)"""
    full_search_text = f"{job.get('職缺名稱(對外)', '')} {job.get('職缺名稱', '')} {job.get('職務類別', '')} {job.get('行業別', '')} {job.get('工作內容(對外)', '')}".lower()

    if any(k in full_search_text for k in ["蝦皮", "智取店", "店到店", "spx", "外送"]):
        return DEFAULT_RESUME_URLS["Spx"]

    if any(k in full_search_text for k in ["服務", "餐飲", "服飾", "門市", "專櫃", "店員", "廚助"]):
        return DEFAULT_RESUME_URLS["Service"]

    return DEFAULT_RESUME_URLS["Manufacture"]

def get_location_suffix_by_industry(job: dict) -> str:
    """依職缺產業類別動態回傳專屬地點描述語"""
    text = f"{job.get('行業別', '')} {job.get('職務類別', '')} {job.get('職缺名稱(對外)', '')} {job.get('職缺名稱', '')}".lower()
    
    # 1. 科技 / 半導體 / 製造 / 作業員
    if any(k in text for k in ["科技", "半導體", "製造", "作業員", "晶圓", "工程師", "電子", "廠", "美光", "欣興", "設備", "技術員"]):
        return "主要廠區/園區"
    
    # 2. 門市 / 零售 / 餐飲
    if any(k in text for k in ["門市", "零售", "餐飲", "專櫃", "店面", "店員", "服飾", "店到店"]):
        return "各區門市據點（自選區域）"
        
    # 3. 倉儲 / 物流 / 外送
    if any(k in text for k in ["倉儲", "物流", "外送", "理貨", "司機", "配送", "揀貨", "倉管"]):
        return "各區物流倉儲據點"
        
    # 4. 一般預設
    return "各區據點（自選區域）"

def format_clean_location(job: dict, target_location: str = "") -> str:
    """地點智慧聚合器：依產業別與行政區數量精準格式化"""
    county = str(job.get("縣市") or "").strip()
    district = str(job.get("行政區") or "").strip()
    suffix = get_location_suffix_by_industry(job)

    # 1. 使用者有明確指定行政區時，優先顯示該行政區
    if target_location:
        dist_list = [d.strip() for d in re.split(r'[,，、\s]+', district) if d.strip()]
        for d in dist_list:
            if target_location in d or d in target_location:
                return d
        
        county_list = [c.strip() for c in re.split(r'[,，、\s]+', county) if c.strip()]
        for c in county_list:
            if target_location in c or c in target_location:
                return f"{c} {suffix}".strip()

    # 2. 智慧地點聚合 (依行政區數量級距)
    dist_list = [d.strip() for d in re.split(r'[,，、\s]+', district) if d.strip()]
    dist_count = len(dist_list)

    if dist_count == 0:
        return county or "依公司指派地點"

    if dist_count <= 4:
        short_dist = "、".join(dist_list)
        return f"{county}（{short_dist}）" if county else short_dist

    # 行政區 >= 5 個時套用產業專屬描述語
    if county:
        return f"{county} {suffix}"
    return suffix

def create_job_flex_card(jobs: list, user_id: str, target_location: str = "") -> FlexSendMessage:
    """建構職缺推薦 Flex Carousel 輪播卡片"""
    bubbles = []
    badge_styles = {
        "shift": {"bg": "#E8F5E9", "text": "#2E7D32"},
        "industry": {"bg": "#E3F2FD", "text": "#1565C0"},
        "type": {"bg": "#FFF3E0", "text": "#E65100"},
        "category": {"bg": "#F3E5F5", "text": "#7B1FA2"}
    }

    for job in jobs[:10]:
        job_title = str(job.get("職缺名稱(對外)") or job.get("職缺名稱") or job.get("職務類別") or "優質職缺").strip()
        
        display_location = format_clean_location(job, target_location)
        salary = str(job.get("薪資") or "依公司規定").strip()
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
            highlight_desc = f"開放應徵【{job_title}】，環境單純、福利健全，歡迎點擊應徵！" if len(clean_raw) < 5 else (clean_raw[:40] + "...")
            
        final_apply_link = sanitize_uri(resolve_apply_url_by_industry(job))

        body_contents = [
            {"type": "text", "text": "🎯 材霈推薦職缺", "weight": "bold", "color": "#1DB446", "size": "xs"},
            {"type": "text", "text": job_title, "weight": "bold", "size": "lg", "margin": "xs", "wrap": True}
        ]
        
        if tags_contents:
            body_contents.append({"type": "box", "layout": "horizontal", "spacing": "xs", "margin": "sm", "contents": tags_contents})
            
        body_contents.extend([
            {"type": "separator", "margin": "md"},
            {
                "type": "box",
                "layout": "vertical",
                "margin": "md",
                "spacing": "xs",
                "contents": [
                    {"type": "text", "text": f"📍 地點：{display_location}", "size": "sm", "color": "#444444", "wrap": True},
                    {"type": "text", "text": f"💰 待遇：{salary}", "size": "sm", "color": "#D32F2F", "weight": "bold", "wrap": True},
                    {"type": "text", "text": f"✨ 特色：{highlight_desc}", "size": "xs", "color": "#555555", "wrap": True, "margin": "xs"}
                ]
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
                            "text": f"查看職缺詳情 {job_title}"
                        }
                    },
                    {
                        "type": "button",
                        "style": "primary",
                        "color": "#00B900",
                        "height": "sm",
                        "action": {"type": "uri", "label": "📄 填寫線上履歷", "uri": final_apply_link}
                    }
                ]
            }
        }
        bubbles.append(bubble)
        
    return FlexSendMessage(alt_text=f"為您找到 {len(bubbles)} 筆熱門職缺！", contents={"type": "carousel", "contents": bubbles})