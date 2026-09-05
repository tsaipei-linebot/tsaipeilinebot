from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from management.config import SESSION_SECRET_KEY
from management.routes import (
    announcement_routes,
    auth_routes,
    document_routes,
    file_routes,
    home_routes,
    meeting_routes,
)

management_app = FastAPI(title="管理部系統")

# session_cookie 名稱跟 secret_key 都跟配送部系統一致（見 delivery/app.py），
# 讓兩邊共用同一顆瀏覽器 cookie，同仁登入一次就能在有權限的部門之間切換，
# 不用重複登入。靜態檔案（CSS）直接沿用配送部系統的 /delivery/static，
# 不用另外重複一份。
management_app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET_KEY,
    session_cookie="delivery_session",
    max_age=14 * 24 * 3600,
)

management_app.include_router(auth_routes.router)
management_app.include_router(home_routes.router)
management_app.include_router(announcement_routes.router)
management_app.include_router(meeting_routes.router)
management_app.include_router(document_routes.router)
management_app.include_router(file_routes.router)
