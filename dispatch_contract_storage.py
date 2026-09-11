"""上傳/下載派遣契約產生器產出的 Word 檔用的 GCS 存取層。跟
delivery/storage.py、hr/storage.py、management/storage.py 是同一套做法
（私有 bucket，一律透過需要登入 session 的下載路由讀取），共用同一個
bucket，只是 blob 路徑前綴改成 dispatch_contracts/，避免跟其他模組的檔案
混在一起。
"""
import os
import uuid

from config import GCP_PROJECT_ID

GCS_BUCKET_NAME = os.getenv("DELIVERY_GCS_BUCKET", "")

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


def upload_contract_docx(content: bytes, filename: str) -> str:
    """上傳產生好的契約 Word 檔，回傳存放的 blob path（存進 Firestore 文件
    裡的那個值）。"""
    blob_path = f"dispatch_contracts/{uuid.uuid4().hex}/{filename}"
    blob = _bucket().blob(blob_path)
    blob.upload_from_string(
        content,
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    return blob_path


def upload_contract_pdf(content: bytes, filename: str) -> str:
    """上傳契約 Word 檔轉出的 PDF（給列表頁內嵌預覽用），回傳 blob path。"""
    blob_path = f"dispatch_contracts/{uuid.uuid4().hex}/{filename}"
    blob = _bucket().blob(blob_path)
    blob.upload_from_string(content, content_type="application/pdf")
    return blob_path


def download_file(blob_path: str):
    """回傳 (bytes, content_type)；檔案不存在時回傳 (None, None)。"""
    if not blob_path.startswith("dispatch_contracts/"):
        return None, None
    blob = _bucket().blob(blob_path)
    if not blob.exists():
        return None, None
    blob.reload()
    return blob.download_as_bytes(), blob.content_type
