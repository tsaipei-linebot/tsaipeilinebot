import os

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from delivery.config import SESSION_SECRET_KEY
from delivery.routes import (
    applicant_routes,
    auth_routes,
    equipment_routes,
    file_routes,
    home_routes,
    import_routes,
    incident_routes,
    reminder_routes,
    repayment_routes,
    search_routes,
    sick_leave_routes,
    vehicle_routes,
    vendor_routes,
    webhook_routes,
)

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

delivery_app = FastAPI(title="配送部系統")

# 只掛在這個子系統上，不影響掛載在主 app 上的 LINE webhook 路由。
# https_only=True（2026-09-14 新增）：瀏覽器只在 HTTPS 連線時才會送出這顆
# 登入 cookie，見 main.py 同一處修改的說明。
delivery_app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET_KEY,
    session_cookie="delivery_session",
    max_age=14 * 24 * 3600,
    https_only=True,
)

delivery_app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

delivery_app.include_router(auth_routes.router)
delivery_app.include_router(home_routes.router)
delivery_app.include_router(vendor_routes.router)
delivery_app.include_router(import_routes.router)
delivery_app.include_router(search_routes.router)
delivery_app.include_router(repayment_routes.router)
delivery_app.include_router(sick_leave_routes.router)
delivery_app.include_router(file_routes.router)
delivery_app.include_router(applicant_routes.router)
delivery_app.include_router(webhook_routes.router)
delivery_app.include_router(reminder_routes.router)
delivery_app.include_router(vehicle_routes.router)
delivery_app.include_router(incident_routes.router)
delivery_app.include_router(equipment_routes.router)
