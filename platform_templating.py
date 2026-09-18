import os
from datetime import datetime, timezone

from fastapi.templating import Jinja2Templates

from config import TAIPEI_TZ

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
templates = Jinja2Templates(directory=TEMPLATES_DIR)


def taipei_time(value, fmt: str = "%Y-%m-%d %H:%M"):
    """各產生器存的 created_at 等時間戳都是 datetime.now(timezone.utc)，
    畫面上直接印會變成 UTC 時間（比台灣時間慢 8 小時），這裡統一轉成
    台灣時間再格式化，模板裡用 `{{ record.created_at | taipei_time }}`。"""
    if not isinstance(value, datetime):
        return value or ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(TAIPEI_TZ).strftime(fmt)


templates.env.filters["taipei_time"] = taipei_time
