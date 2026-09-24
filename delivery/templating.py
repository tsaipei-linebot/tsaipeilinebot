import os

from fastapi.templating import Jinja2Templates

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
templates = Jinja2Templates(directory=TEMPLATES_DIR)

# 加退保暫存區的操作時間（2026-09-24）
from hr.insurance_draft_repository import format_time as _format_time  # noqa: E402

templates.env.filters["taipei_time"] = _format_time
