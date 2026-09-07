from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config
from .providers import agora_provider, livekit_provider

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

app = FastAPI(title="視訊面試中心 - POC")

PROVIDERS = {
    "agora": agora_provider,
    "livekit": livekit_provider,
}


class TokenRequest(BaseModel):
    identity: str
    room: str | None = None


def _active_provider():
    return PROVIDERS[config.VIDEO_SDK_PROVIDER]


@app.get("/api/config")
def get_config():
    return {
        "liffId": config.LIFF_ID,
        "testRoom": config.TEST_ROOM_NAME,
        **_active_provider().build_client_config(),
    }


@app.post("/api/token")
def issue_token(req: TokenRequest):
    room = req.room or config.TEST_ROOM_NAME
    return _active_provider().generate_token(room, req.identity)


# 放在所有 API 路由之後掛載，靜態頁面(含 index.html)才不會蓋掉上面的路由
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
