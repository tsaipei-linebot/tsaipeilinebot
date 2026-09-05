"""上傳/下載公告附件、會議記錄附件、規章/SOP 文件用的 GCS 存取層。跟
delivery/storage.py 是同一套做法（私有 bucket，一律透過需要登入 session
的下載路由讀取，不開放公開網址或簽名連結），共用同一個 bucket，只是
blob 路徑前綴改成 management/，避免跟配送部的檔案混在一起。
"""
import uuid

from config import GCP_PROJECT_ID
from management.config import GCS_BUCKET_NAME

_client = None


class StorageNotConfigured(RuntimeError):
    pass


def is_configured() -> bool:
    return bool(GCS_BUCKET_NAME)


def _bucket():
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
    blob_path = f"management/{category}/{entity_id}/{uuid.uuid4().hex}{ext}"
    blob = _bucket().blob(blob_path)
    blob.upload_from_string(content, content_type=content_type)
    return blob_path


def download_file(blob_path: str):
    """回傳 (bytes, content_type)；檔案不存在時回傳 (None, None)。"""
    if not blob_path.startswith("management/"):
        return None, None
    blob = _bucket().blob(blob_path)
    if not blob.exists():
        return None, None
    blob.reload()
    return blob.download_as_bytes(), blob.content_type
