"""上傳/下載合約產生器產出的 Word/PDF 檔用的 GCS 存取層。跟
dispatch_contract_storage.py 是同一套做法（私有 bucket，一律透過需要登入
session 的下載路由讀取），共用同一個 bucket，只是 blob 路徑前綴改成
client_contracts/，避免跟其他模組的檔案混在一起。
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
    """上傳產生好的合約 Word 檔，回傳存放的 blob path（存進 Firestore 文件
    裡的那個值）。"""
    blob_path = f"client_contracts/{uuid.uuid4().hex}/{filename}"
    blob = _bucket().blob(blob_path)
    blob.upload_from_string(
        content,
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    return blob_path


def upload_contract_pdf(content: bytes, filename: str) -> str:
    """上傳合約 Word 檔轉出的 PDF（給列表頁內嵌預覽用），回傳 blob path。"""
    blob_path = f"client_contracts/{uuid.uuid4().hex}/{filename}"
    blob = _bucket().blob(blob_path)
    blob.upload_from_string(content, content_type="application/pdf")
    return blob_path


def download_file(blob_path: str):
    """回傳 (bytes, content_type)；檔案不存在時回傳 (None, None)。"""
    if not blob_path.startswith("client_contracts/"):
        return None, None
    blob = _bucket().blob(blob_path)
    if not blob.exists():
        return None, None
    blob.reload()
    return blob.download_as_bytes(), blob.content_type


def delete_file(blob_path: str):
    """合約紀錄被刪除時一併清掉存好的檔案——`blob_path` 空字串（例如
    當初 PDF 轉檔失敗、根本沒存過）或檔案本來就不存在都安靜跳過，不算
    錯誤，呼叫端（刪除整筆紀錄）不應該因為這裡失敗就整個中斷。"""
    if not blob_path or not blob_path.startswith("client_contracts/"):
        return
    blob = _bucket().blob(blob_path)
    if blob.exists():
        blob.delete()
