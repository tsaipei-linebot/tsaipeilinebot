"""已上傳檔案（體檢報告/關懷紀錄附件/證照/教育訓練證明）的下載代理路由，
做法跟 delivery/routes/file_routes.py、management/routes/file_routes.py
一致：不用 GCS 公開或簽名網址，一律要先通過 login_required。
"""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response

from hr.auth import login_required
from hr.storage import download_file

router = APIRouter()


@router.get("/files/{blob_path:path}")
def get_file(blob_path: str, request: Request, redirect=Depends(login_required)):
    if redirect:
        return redirect
    content, content_type = download_file(blob_path)
    if content is None:
        return Response(status_code=404)
    return Response(content=content, media_type=content_type or "application/octet-stream")
