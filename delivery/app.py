import os

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from delivery.config import SESSION_SECRET_KEY
from delivery.routes import (
    auth_routes,
    file_routes,
    home_routes,
    repayment_routes,
    search_routes,
    sick_leave_routes,
    vendor_routes,
)

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

delivery_app = FastAPI(title="配送部系統")

# 只掛在這個子系統上，不影響掛載在主 app 上的 LINE webhook 路由。
delivery_app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET_KEY,
    session_cookie="delivery_session",
    max_age=14 * 24 * 3600,
)

delivery_app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

delivery_app.include_router(auth_routes.router)
delivery_app.include_router(home_routes.router)
delivery_app.include_router(vendor_routes.router)
delivery_app.include_router(search_routes.router)
delivery_app.include_router(repayment_routes.router)
delivery_app.include_router(sick_leave_routes.router)
delivery_app.include_router(file_routes.router)
