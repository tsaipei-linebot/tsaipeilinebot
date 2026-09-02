import time
import random
from google import genai
from config import GCP_PROJECT_ID, GCP_LOCATION

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


# ==========================================
# 模型 fallback 清單（query_gemini_ai / format_full_job_detail_with_ai 共用同一份，避免兩處清單不一致）
# ==========================================
MODEL_FALLBACK_LIST = [
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite"
]

# ==========================================
# 429（Dynamic Shared Quota 暫時用盡）重試設定
# 只針對 429 重試，其他錯誤（模型名稱錯誤、參數錯誤等）重試也沒用，直接換下一個 fallback 模型
# ==========================================
MAX_RETRIES_PER_MODEL = 2           # 單一模型最多重試次數（不含第一次嘗試）
RETRY_BASE_DELAY_SECONDS = 0.6      # 重試等待的基礎秒數（會逐次遞增 + 隨機抖動，避免多個請求同時重試又互相打架）


def _is_resource_exhausted_error(e: Exception) -> bool:
    """判斷例外是否為 429 RESOURCE_EXHAUSTED（Dynamic Shared Quota 當下共享池暫時滿載）"""
    code = getattr(e, "code", None)
    if code == 429:
        return True
    text = str(e)
    return "429" in text or "RESOURCE_EXHAUSTED" in text.upper()


def _generate_with_retry(model: str, prompt: str):
    """對單一模型呼叫 Gemini。遇到 429 才短暫等待後重試；其他類型錯誤直接往上拋出，
    交由呼叫端換下一個 fallback 模型，避免浪費時間重試注定失敗的請求。"""
    last_error = None
    for attempt in range(MAX_RETRIES_PER_MODEL + 1):
        try:
            return ai_client.models.generate_content(model=model, contents=prompt)
        except Exception as e:
            last_error = e
            if not _is_resource_exhausted_error(e):
                raise
            if attempt < MAX_RETRIES_PER_MODEL:
                wait_seconds = RETRY_BASE_DELAY_SECONDS * (attempt + 1) + random.uniform(0, 0.3)
                print(f"[Vertex AI 429 暫時性配額用盡 {model}] 第 {attempt + 1} 次重試前等待 {wait_seconds:.2f} 秒")
                time.sleep(wait_seconds)
    raise last_error


def query_gemini_ai(prompt: str) -> str:
    """呼叫 Vertex AI Gemini 進行招募問答與決策推理（含 429 重試 + 模型 fallback）"""
    if not ai_client:
        return ""

    for m in MODEL_FALLBACK_LIST:
        try:
            res = _generate_with_retry(m, prompt)
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

    for m in MODEL_FALLBACK_LIST:
        try:
            res = _generate_with_retry(m, prompt)
            if res and hasattr(res, "text") and res.text:
                return res.text.strip()
        except Exception as e:
            print(f"[AI 詳細內容排版異常 {m}]: {e}")
            continue

    return fallback_layout
