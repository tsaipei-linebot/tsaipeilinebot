"""已上傳檔案（規章/SOP 文件等）的下載代理路由，做法跟
delivery/routes/file_routes.py 一致：不用 GCS 公開或簽名網址，一律要先
通過 login_required，確保只有登入管理部系統的同仁才看得到。
"""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response

from management.auth import login_required
from management.storage import download_file

router = APIRouter()


@router.get("/files/{blob_path:path}")
def get_file(blob_path: str, request: Request, redirect=Depends(login_required)):
    if redirect:
        return redirect
    content, content_type = download_file(blob_path)
    if content is None:
        return Response(status_code=404)
    return Response(content=content, media_type=content_type or "application/octet-stream")
