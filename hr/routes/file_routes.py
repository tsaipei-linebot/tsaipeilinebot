"""已上傳檔案（體檢報告/關懷紀錄附件/證照/教育訓練證明）的下載代理路由，
做法跟 delivery/routes/file_routes.py、management/routes/file_routes.py
一致：不用 GCS 公開或簽名網址，一律要先通過 login_required。

**2026-09-22 加退保檔案例外**：加退保（`hr/insurance/` 前綴的 blob path，
見 `hr/storage.py`）改成用 `has_insurance_access()`（7 個上傳部門之一或
人資）判斷，不再要求「人資專區」模組權限——跟 `insurance_routes.py` 的
`_require_login()` 是同一輪調整，理由一樣：加退保的操作入口（上傳／查
歷史）已經不用勾模組權限了，下載自己剛上傳那份檔案的連結卻還是被模組
權限擋下來，同仁會點了「下載」卻進不去，講不通。其他子功能的檔案
（體檢報告/關懷紀錄/證照/教育訓練）維持原本 login_required 的做法不變。
"""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, Response

import platform_accounts
from hr import insurance_repository as insurance_repo
from hr.auth import login_required
from hr.storage import download_file

router = APIRouter()

_INSURANCE_BLOB_PREFIX = "hr/insurance/"


def _require_access(blob_path: str, request: Request):
    if blob_path.startswith(_INSURANCE_BLOB_PREFIX):
        account = platform_accounts.current_account(request)
        if not account:
            return RedirectResponse(url=f"/login?next=/hr/files/{blob_path}", status_code=303)
        if not insurance_repo.has_insurance_access(account):
            return Response(status_code=404)
        return None
    return login_required(request)


@router.get("/files/{blob_path:path}")
def get_file(blob_path: str, request: Request, redirect=Depends(_require_access)):
    if redirect:
        return redirect
    content, content_type = download_file(blob_path)
    if content is None:
        return Response(status_code=404)
    return Response(content=content, media_type=content_type or "application/octet-stream")
