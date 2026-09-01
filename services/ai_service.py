import os
from google import genai

# ==========================================
# Google Cloud / Vertex AI 設定
# ==========================================
GCP_PROJECT_ID = os.getenv("GCP_PROJECT_ID", "tsaipei-505807")
GCP_LOCATION = os.getenv("GCP_LOCATION", "us-central1")

ai_client = None
try:
    # 啟用 Vertex AI 模式（自動套用 Cloud Run 服務帳戶 IAM 權限）
    ai_client = genai.Client(
        vertexai=True,
        project=GCP_PROJECT_ID,
        location=GCP_LOCATION
    )
    print("[系統提示] Vertex AI (Gemini) 客戶端初始化成功！")
except Exception as e:
    print(f"[系統警告] Vertex AI 初始化失敗: {e}")


def query_gemini_ai(prompt: str) -> str:
    """呼叫 Vertex AI Gemini 進行招募問答與決策推理"""
    if not ai_client:
        return ""

    # 當前 Vertex AI 最新主流支援模型清單
    models = [
        "gemini-2.5-flash",
        "gemini-3.5-flash",
        "gemini-2.5-flash-lite"
    ]
    for m in models:
        try:
            res = ai_client.models.generate_content(
                model=m,
                contents=prompt
            )
            if res and hasattr(res, "text") and res.text:
                return res.text.strip()
        except Exception as e:
            print(f"[Vertex AI 呼叫異常 {m}]: {e}")
            continue
    return ""


def format_full_job_detail_with_ai(job: dict, location_display: str) -> str:
    """若 Notion 排版工作說明為空時的 AI 備援美化排版函式"""
    internal_title = job.get("職缺名稱") or job.get("_internal_title") or "招募職缺"
    category = job.get("職務類別") or job.get("_job_category") or "優質職務"
    salary = job.get("薪資") or "依公司規定"
    shift = job.get("班別") or "依排班規定"
    leave = job.get("休假方式") or "依排班規定"
    raw_desc = job.get("工作內容(對外)") or "歡迎點擊線上履歷應徵。"

    fallback_layout = (
        f"📋【職缺名稱：{internal_title} ｜ {category}】\n\n"
        f"📍 上班地點：{location_display}\n"
        f"💰 薪資待遇：{salary}\n"
        f"⏰ 工作班別：{shift}（休假制度：{leave}）\n\n"
        f"📝 工作內容詳細說明：\n{raw_desc}\n\n"
        f"💡 依《就業服務法》規定，所有職缺皆無性別、年齡限制，歡迎所有朋友應徵！"
    )

    if not ai_client:
        return fallback_layout

    prompt = f"""你是一位專業的人資顧問，請將以下職缺資料進行【優雅美化排版】並進行【就業服務法合規審查】：

職缺名稱：{internal_title}
職務類別：{category}
上班地點：{location_display}
薪資待遇：{salary}
工作班別：{shift}
休假方式：{leave}
原始工作內容：
{raw_desc}

【處理原則】：
1. 第一行必須為：📋【職缺名稱：{internal_title} ｜ {category}】
2. 遵守《就業服務法》第5條合規審查，移除年齡、性別等歧視性條件。
3. 清晰條列式排版，搭配適當 Emoji。

請直接輸出繁體中文內容："""

    models = [
        "gemini-2.5-flash",
        "gemini-3.5-flash"
    ]
    try:
        for m in models:
            res = ai_client.models.generate_content(
                model=m,
                contents=prompt
            )
            if res and hasattr(res, "text") and res.text:
                return res.text.strip()
    except Exception as e:
        print(f"[AI 詳細內容排版異常]: {e}")

    return fallback_layout
