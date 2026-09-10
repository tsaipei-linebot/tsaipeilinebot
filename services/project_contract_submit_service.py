"""專案合約維護（/project-contracts）：材霈平台這邊只負責收表單，送出時把
資料原封不動轉手給職缺維護表單背後那支 GAS 程式的 Web App
（type=SUBMIT_PROJECT），後續寄送通知信給財會/人資、寫入「專案合約紀錄」
分頁、把合約檔案存進 Google Drive，完全由那支 GAS 程式繼續處理
（ProjectWorkflowService.processProjectSubmission()），材霈平台這邊不重做。

跟職缺維護、薪資補款送出表單是同一套「方案 A」精神（見 HANDOFF.md）。
差別是這個功能在現有 Netlify 表單裡本來就只有「提報新的一筆」，沒有
「維護既有」這個模式（GAS 那邊也沒有對應的查詢/編輯端點），所以這裡也
只做提報表單，不額外做「查詢我提報過的合約」這種現有系統沒有的功能。

跟 GAS 那支程式的請求格式，都是照 ProjectWorkflowService.processProjectSubmission()
現有的樣子照抄，包含下面兩個下拉選單的選項清單，都是直接照抄現有 Netlify
表單原始碼（index_6.html 的 #pf_coop_category / #pf_contract_mode），不是
我方自己另外調整過的清單。
"""
import requests

from config import JOB_PORTAL_GAS_WEBAPP_URL as GAS_WEBAPP_URL

_REQUEST_TIMEOUT_SECONDS = 30

COOP_CATEGORY_OPTIONS = ["派遣", "代招", "國際學生"]
CONTRACT_MODE_OPTIONS = ["實支實付", "一口價"]

# 合約檔案格式/大小限制，照抄現有表單 handleContractFileChange() 的檢查
# （20MB 上限、只收 PDF/WORD），前後端都要擋，避免有人繞過前端 JS 檢查。
CONTRACT_FILE_MAX_BYTES = 20 * 1024 * 1024
CONTRACT_FILE_ALLOWED_EXTENSIONS = (".pdf", ".doc", ".docx")


def build_submit_payload(
    *,
    applicant_name: str,
    vendor: str,
    coop_category: str,
    contract_mode: str,
    interview_specialist: str,
    visit_supervisor: str,
    file_base64: str,
    file_filename: str,
    file_mime_type: str,
) -> dict:
    """組出送給 GAS SUBMIT_PROJECT 端點的請求內容，欄位名稱、巢狀結構
    都照抄 index_6.html 的 handleProjectSubmit() 現有樣子。"""
    return {
        "type": "SUBMIT_PROJECT",
        "applicant": {"displayName": applicant_name},
        "fields": {
            "applicant_name": applicant_name,
            "vendor": vendor,
            "coop_category": coop_category,
            "contract_mode": contract_mode,
            "interview_specialist": interview_specialist,
            "visit_supervisor": visit_supervisor,
        },
        "file": {
            "base64": file_base64,
            "filename": file_filename,
            "mimeType": file_mime_type,
        },
    }


_AMBIGUOUS_OUTCOME_MESSAGE = (
    "這筆專案合約很可能其實已經送出成功了，只是材霈平台這邊沒辦法確認職缺維護系統的回應內容"
    "（這是對方系統偶爾會出現的已知狀況，不是這邊的程式錯誤）。請先確認財會/人資信箱有沒有收到"
    "通知信，如果沒有收到才需要重新送出一次，避免不小心送出兩筆重複的合約資料。"
)


def _post_to_gas(payload: dict):
    """統一處理跟 GAS 的 HTTP 溝通，回傳 (data, error_result)：成功解析出
    JSON 就回傳 (data, None)；任何失敗都回傳 (None, 結構統一的 dict)，跟
    services/job_listing_submit_service.py 是同一套「確定沒送到」跟
    「不確定有沒有處理完」分開處理的原則，這裡不重複貼一次完整說明。"""
    if not GAS_WEBAPP_URL:
        return None, {
            "status": "error",
            "message": "尚未設定 JOB_PORTAL_GAS_WEBAPP_URL 環境變數，請聯絡系統管理員設定後再試一次。",
        }
    try:
        response = requests.post(GAS_WEBAPP_URL, json=payload, timeout=_REQUEST_TIMEOUT_SECONDS)
    except requests.Timeout:
        return None, {"status": "unknown", "message": _AMBIGUOUS_OUTCOME_MESSAGE}
    except requests.RequestException as e:
        return None, {"status": "error", "message": f"連線到職缺維護系統失敗，請稍後再試：{e}"}

    try:
        data = response.json()
    except ValueError:
        return None, {"status": "unknown", "message": _AMBIGUOUS_OUTCOME_MESSAGE}
    if not isinstance(data, dict):
        return None, {"status": "unknown", "message": _AMBIGUOUS_OUTCOME_MESSAGE}
    return data, None


def submit_project_contract(payload: dict) -> dict:
    """把 payload 轉送給 GAS 的 SUBMIT_PROJECT 端點，回傳格式跟
    job_listing_submit_service.submit_job() 一致：成功時 status="success"；
    GAS 自己擋下來（例如尚未完成 LINE 綁定）時原樣轉發它的中文說明；
    連線層級的問題分成 "error"（確定沒送到，可以放心重送）跟
    "unknown"（不確定有沒有處理完，呼叫端不能引導同仁重送）。"""
    data, error_result = _post_to_gas(payload)
    if error_result:
        return error_result
    if "status" not in data:
        return {"status": "unknown", "message": _AMBIGUOUS_OUTCOME_MESSAGE}
    return data
