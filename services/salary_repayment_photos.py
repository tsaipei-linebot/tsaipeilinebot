"""薪資補款搬離 GAS 階段 2 的第 2 步（2026-09-25）：佐證照片 Google Drive → Cloud Storage。

GAS 收到補款單時把照片存進 Drive、每個檔案設成「知道連結的人都能看」，試算表「佐證照片網址」欄存
`https://lh3.googleusercontent.com/d/{檔案 ID}`（見 job-portal-gas-project `Project_Salary.js` 的
`uploadSalaryImageToDrive()`）。這裡把照片**複製**一份到平台自己的私有 bucket：

- **Drive 上的檔案、試算表的網址都不動**，舊連結照樣能開（LINE 卡片、通知信都還在用）。
- 因為檔案本來就是公開連結，下載不需要 Drive API 權限，也不用使用者先分享任何東西。先試
  `drive.google.com/uc?export=download`（原始檔），不行再試 `lh3` 網址。下載回來是 HTML 頁面（檔案
  被刪、權限被改成要登入時 Google 回的就是這個）就算失敗、記下原因；其他內容原樣照存，不限 JPG/PNG。
- 結果記在 `salary_repayments` 文件上：成功 `photo_blob`／`photo_content_type`／`photo_source_url`／
  `photo_copied_at`；失敗 `photo_error`／`photo_error_url`／`photo_error_at`。`photo_source_url` 跟
  目前試算表網址不一樣（照片換過）就當成還沒搬，會重搬。
- 一次按鈕只做一批（有時間上限），避免一次搬幾百張讓網頁請求逾時；按到「待搬 0 張」為止。
- GCS 路徑 `salary/photos/{文件 id}/{隨機}.{副檔名}`，跟 hr/、delivery/ 共用同一個私有 bucket。
"""
import re
import time
import uuid
from datetime import datetime, timezone

import requests

from config import GCP_PROJECT_ID, SALARY_PHOTO_GCS_BUCKET
from services import salary_repayment_store as store

# 試算表 U 欄的表頭，GAS 建分頁時寫的是「補款佐證(照片)」（`Project_Salary.js` 的標題列）。
# 2026-09-25 第一版寫成 HANDOFF 欄位對照表上的「佐證照片網址」，結果 218 筆全部判斷成沒有照片——所以
# 改成依序找：已知的表頭名稱 → 表頭含「佐證」→ 值看起來是 Drive／lh3 照片網址的欄位。
PHOTO_COLUMNS = ("補款佐證(照片)", "補款佐證（照片）", "佐證照片網址")
_PHOTO_URL = re.compile(r"https?://(lh3\.googleusercontent\.com/d/|drive\.google\.com/)")
BLOB_PREFIX = "salary/photos/"
MAX_BYTES = 25 * 1024 * 1024
DOWNLOAD_TIMEOUT = 30
BATCH_SECONDS = 45

STATUS_NONE = "none"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_PENDING = "pending"

_EXTENSIONS = {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif", "image/webp": ".webp", "image/heic": ".heic"}
_client = None


class StorageNotConfigured(RuntimeError):
    pass


def drive_file_id(url: str) -> str:
    """跟 GAS 寄信時抓檔案 ID 的規則一樣（`/[-\\w]{25,}/`）。"""
    match = re.search(r"[-\w]{25,}", url or "")
    return match.group(0) if match else ""


def _candidate_urls(file_id: str) -> list:
    return [
        f"https://drive.google.com/uc?export=download&id={file_id}",
        f"https://lh3.googleusercontent.com/d/{file_id}",
    ]


def download_drive_image(file_id: str) -> tuple:
    """回傳 (bytes, content_type, error)。成功時 error 是空字串。"""
    last_error = "下載失敗"
    for url in _candidate_urls(file_id):
        try:
            resp = requests.get(url, timeout=DOWNLOAD_TIMEOUT, allow_redirects=True)
        except requests.RequestException as e:
            last_error = f"連不上 Google（{e.__class__.__name__}）"
            continue
        if resp.status_code != 200:
            last_error = f"Google 回應 {resp.status_code}（檔案可能已刪除或不再公開）"
            continue
        content = resp.content
        if len(content) > MAX_BYTES:
            last_error = "檔案超過 25MB"
            continue
        header_type = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if not content or header_type.startswith("text/") or content.lstrip()[:1] == b"<":
            # 檔案被刪或改成要登入時，Google 回的是 HTML 頁面
            last_error = "下載回來的不是照片（檔案可能已刪除或不再公開）"
            continue
        return content, _content_type(content, header_type), ""
    return None, "", last_error


def _content_type(content: bytes, header_type: str) -> str:
    """原樣照搬：不是 JPEG/PNG 也照存，只是類型照 Google 給的（沒給就當一般檔案）。"""
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith(b"\x89PNG"):
        return "image/png"
    return header_type or "application/octet-stream"


def _bucket():
    global _client
    if not SALARY_PHOTO_GCS_BUCKET:
        raise StorageNotConfigured("尚未設定 DELIVERY_GCS_BUCKET 環境變數，沒辦法存照片。")
    if _client is None:
        from google.cloud import storage

        _client = storage.Client(project=GCP_PROJECT_ID)
    return _client.bucket(SALARY_PHOTO_GCS_BUCKET)


def upload_photo(doc_id: str, content: bytes, content_type: str) -> str:
    blob_path = f"{BLOB_PREFIX}{doc_id}/{uuid.uuid4().hex}{_EXTENSIONS.get(content_type, '.jpg')}"
    _bucket().blob(blob_path).upload_from_string(content, content_type=content_type)
    return blob_path


def download_photo(blob_path: str) -> tuple:
    """回傳 (bytes, content_type)；不是照片路徑或檔案不存在時回傳 (None, None)。"""
    if not (blob_path or "").startswith(BLOB_PREFIX):
        return None, None
    blob = _bucket().blob(blob_path)
    if not blob.exists():
        return None, None
    blob.reload()
    return blob.download_as_bytes(), blob.content_type


def photo_url(fields: dict) -> str:
    for column in PHOTO_COLUMNS:
        if column in fields:
            return (fields.get(column) or "").strip()
    for column, value in fields.items():
        if "佐證" in column:
            return (value or "").strip()
    for value in fields.values():
        if _PHOTO_URL.match((value or "").strip()):
            return value.strip()
    return ""


def photo_status(doc: dict) -> str:
    url = photo_url(doc.get("fields") or {})
    if not url:
        return STATUS_NONE
    if doc.get("photo_blob") and doc.get("photo_source_url") == url:
        return STATUS_DONE
    if doc.get("photo_error") and doc.get("photo_error_url") == url:
        return STATUS_FAILED
    return STATUS_PENDING


def _docs() -> list:
    return [(snap.id, snap.to_dict() or {}) for snap in store.records_ref().stream()]


def summary() -> dict:
    counts = {STATUS_NONE: 0, STATUS_DONE: 0, STATUS_FAILED: 0, STATUS_PENDING: 0}
    failed = []
    done_samples = []
    for doc_id, doc in sorted(_docs(), key=lambda d: d[1].get("row_number") or 0):
        status = photo_status(doc)
        counts[status] += 1
        salary_id = (doc.get("fields") or {}).get(store.ID_COLUMN, "")
        if status == STATUS_FAILED:
            failed.append({"doc_id": doc_id, "salary_id": salary_id, "row_number": doc.get("row_number"), "error": doc.get("photo_error")})
        elif status == STATUS_DONE and len(done_samples) < 5:
            done_samples.append({"doc_id": doc_id, "salary_id": salary_id})
    with_photo = counts[STATUS_DONE] + counts[STATUS_FAILED] + counts[STATUS_PENDING]
    return {"counts": counts, "with_photo": with_photo, "failed": failed, "done_samples": done_samples}


def copy_batch(include_failed: bool = False, seconds: float = BATCH_SECONDS) -> dict:
    """搬一批：待搬的（勾「重試失敗的」時連失敗的一起），時間到就停。回傳這批的結果。"""
    wanted = {STATUS_PENDING, STATUS_FAILED} if include_failed else {STATUS_PENDING}
    todo = [(doc_id, doc) for doc_id, doc in sorted(_docs(), key=lambda d: d[1].get("row_number") or 0) if photo_status(doc) in wanted]
    started = time.monotonic()
    result = {"copied": 0, "failed": 0, "remaining": len(todo)}
    for doc_id, doc in todo:
        if time.monotonic() - started > seconds:
            break
        url = photo_url(doc["fields"])
        now = datetime.now(timezone.utc)
        ref = store.records_ref().document(doc_id)
        file_id = drive_file_id(url)
        content, content_type, error = (None, "", "照片網址裡找不到 Drive 檔案 ID") if not file_id else download_drive_image(file_id)
        if not error:
            try:
                blob_path = upload_photo(doc_id, content, content_type)
            except Exception as e:
                error = f"存到 Cloud Storage 失敗：{e}"
        if error:
            ref.set({"photo_error": error, "photo_error_url": url, "photo_error_at": now}, merge=True)
            result["failed"] += 1
        else:
            ref.set(
                {
                    "photo_blob": blob_path,
                    "photo_content_type": content_type,
                    "photo_source_url": url,
                    "photo_copied_at": now,
                    "photo_error": "",
                    "photo_error_url": "",
                },
                merge=True,
            )
            result["copied"] += 1
        result["remaining"] -= 1
    return result


def get_photo_for_doc(doc_id: str) -> tuple:
    """回傳 (bytes, content_type)；還沒搬或找不到回傳 (None, None)。"""
    snapshot = store.records_ref().document(doc_id).get()
    doc = snapshot.to_dict() if snapshot.exists else None
    if not doc or photo_status(doc) != STATUS_DONE:
        return None, None
    return download_photo(doc["photo_blob"])
