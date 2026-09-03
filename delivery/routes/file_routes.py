"""已上傳檔案（身分證/駕照/強制險/良民證/病假收據）的下載代理路由。

刻意不用 GCS 公開網址或簽名網址，全部請求都要先通過 login_required，
確保這些個資檔案只有登入配送部系統的同仁才看得到。
"""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response

from delivery.auth import login_required
from delivery.storage import download_file

router = APIRouter()


@router.get("/files/{blob_path:path}")
def get_file(blob_path: str, request: Request, redirect=Depends(login_required)):
    if redirect:
        return redirect
    content, content_type = download_file(blob_path)
    if content is None:
        return Response(status_code=404)
    return Response(content=content, media_type=content_type or "application/octet-stream")
