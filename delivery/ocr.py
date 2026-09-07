"""強制險/公會加保證明/營業用第三責任險/良民證等文件到期日的 OCR 辨識。

用 Vertex AI Gemini 的多模態能力直接讀圖辨識到期日，跟 LINE 招募機器人共用
同一個 GCP 專案的 Vertex AI 設定（GCP_PROJECT_ID / GCP_LOCATION，沿用 Cloud
Run 服務帳戶的 IAM 權限），不用另外申請金鑰。google-genai 的 client 延遲到
真正要辨識時才初始化，避免只是匯入這個模組就需要連線 GCP（跟 delivery/db.py、
delivery/storage.py 的作法一致）。

辨識失敗、看不出日期、或回傳格式不是合法日期時一律回傳空字串（不是丟例外），
讓呼叫端自然地退回「由同仁手動輸入到期日」，不會把猜錯的日期存進資料庫。
"""
import json
import re

from config import GCP_LOCATION, GCP_PROJECT_ID

_client = None

_MODEL = "gemini-2.5-flash"

_PROMPT = """這是一張保險保單或證明文件的照片或掃描檔。請找出文件上的「保險到期日」
或「有效期限」（不是保單簽發日、不是生效日、不是承保日），用西元 YYYY-MM-DD
格式回答。如果文件上是民國年，換算成西元年（民國年+1911）。如果完全找不到到期日
或看不清楚，expiry_date 欄位回傳空字串，不要用猜的。只回傳一個 JSON 物件。"""

_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {"expiry_date": {"type": "string"}},
    "required": ["expiry_date"],
}

_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _get_client():
    global _client
    if _client is None:
        from google import genai

        _client = genai.Client(vertexai=True, project=GCP_PROJECT_ID, location=GCP_LOCATION)
    return _client


def extract_expiry_date(content: bytes, content_type: str) -> str:
    """回傳辨識到的到期日（YYYY-MM-DD 字串），辨識不出來回傳空字串。"""
    try:
        from google.genai import types

        client = _get_client()
        response = client.models.generate_content(
            model=_MODEL,
            contents=[types.Part.from_bytes(data=content, mime_type=content_type), _PROMPT],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=_RESPONSE_SCHEMA,
            ),
        )
        if not response or not getattr(response, "text", None):
            return ""
        data = json.loads(response.text)
        value = (data.get("expiry_date") or "").strip()
        return value if _DATE_PATTERN.fullmatch(value) else ""
    except Exception as e:
        print(f"[配送部系統] 到期日 OCR 辨識失敗：{e}")
        return ""
