"""上傳/下載身分證、駕照、強制險、良民證、病假收據等檔案用的 GCS 存取層。

這些檔案內容多半是個資（身分證影本等），刻意不把 bucket 設成公開或用
可直接分享的簽名網址，而是一律透過 delivery 子系統自己的、需要登入 session
才能呼叫的下載路由（見 routes/file_routes.py）來讀取，藉此把存取權限收斂在
應用程式的登入驗證範圍內。
"""
import uuid

from config import GCP_PROJECT_ID
from delivery.config import GCS_BUCKET_NAME

_client = None


class StorageNotConfigured(RuntimeError):
    pass


def is_configured() -> bool:
    return bool(GCS_BUCKET_NAME)


def _bucket():
    # google-cloud-storage 延遲到真正需要讀寫檔案時才 import/連線，
    # 跟 delivery/db.py 對 Firestore 的作法一致：避免只是匯入這個模組
    # （例如路由註冊階段）就必須要有 GCP 憑證或裝好這個套件。
    global _client
    if not GCS_BUCKET_NAME:
        raise StorageNotConfigured(
            "尚未設定 DELIVERY_GCS_BUCKET 環境變數，無法上傳或讀取檔案。"
        )
    if _client is None:
        from google.cloud import storage

        _client = storage.Client(project=GCP_PROJECT_ID)
    return _client.bucket(GCS_BUCKET_NAME)


def upload_file(category: str, entity_id: str, filename: str, content: bytes, content_type: str) -> str:
    """上傳檔案，回傳存放的 blob path（存進 Firestore 文件裡的那個值）。"""
    ext = ""
    if "." in filename:
        ext = "." + filename.rsplit(".", 1)[-1].lower()
    blob_path = f"delivery/{category}/{entity_id}/{uuid.uuid4().hex}{ext}"
    blob = _bucket().blob(blob_path)
    blob.upload_from_string(content, content_type=content_type)
    return blob_path


def download_file(blob_path: str):
    """回傳 (bytes, content_type)；檔案不存在時回傳 (None, None)。"""
    if not blob_path.startswith("delivery/"):
        return None, None
    blob = _bucket().blob(blob_path)
    if not blob.exists():
        return None, None
    blob.reload()
    return blob.download_as_bytes(), blob.content_type


def delete_entity_files(category: str, entity_id: str):
    """刪除某個人員/事件底下上傳過的所有檔案——人員一筆紀錄底下可能有身分證、
    良民證、強制險等好幾個各自獨立上傳的檔案（存在 `documents` 這個子物件裡，
    每個應備項目各自一個 file_path），不是單一一個 blob_path 欄位，所以刪除
    整筆人員紀錄時，用 `upload_file()` 存檔時就固定好的路徑前綴
    `delivery/{category}/{entity_id}/` 一次列出、一次刪光，不用逐一項目讀
    file_path 再各自刪一次。沒設定好 bucket、或這個人本來就沒有任何檔案，
    安靜跳過，不算錯誤——呼叫端（刪除整筆人員紀錄）不應該因為這裡失敗就
    整個中斷。"""
    if not is_configured() or not entity_id:
        return
    prefix = f"delivery/{category}/{entity_id}/"
    for blob in _bucket().list_blobs(prefix=prefix):
        blob.delete()
