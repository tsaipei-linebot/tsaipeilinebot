from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from hr.config import SESSION_SECRET_KEY
from hr.routes import (
    auth_routes,
    care_log_routes,
    file_routes,
    health_check_routes,
    home_routes,
    incident_routes,
    insurance_routes,
    license_routes,
    reminder_routes,
    training_routes,
    zone_routes,
)

hr_app = FastAPI(title="人資專區")

# session_cookie 名稱跟 secret_key 都跟配送部/管理部系統一致（見
# delivery/app.py／management/app.py），讓三邊共用同一顆瀏覽器 cookie，同仁
# 登入一次就能在有權限的部門之間切換，不用重複登入。靜態檔案（CSS）直接
# 沿用配送部系統的 /delivery/static，不用另外重複一份。
# https_only=True（2026-09-14 新增）：瀏覽器只在 HTTPS 連線時才會送出這顆
# 登入 cookie，見 main.py 同一處修改的說明。
hr_app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET_KEY,
    session_cookie="delivery_session",
    max_age=14 * 24 * 3600,
    https_only=True,
)

hr_app.include_router(auth_routes.router)
hr_app.include_router(home_routes.router)
hr_app.include_router(incident_routes.router)
hr_app.include_router(health_check_routes.router)
hr_app.include_router(care_log_routes.router)
hr_app.include_router(license_routes.router)
hr_app.include_router(training_routes.router)
hr_app.include_router(file_routes.router)
hr_app.include_router(reminder_routes.router)
hr_app.include_router(insurance_routes.router)
hr_app.include_router(zone_routes.router)
