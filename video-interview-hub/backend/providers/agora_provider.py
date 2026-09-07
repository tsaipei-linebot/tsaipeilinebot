import time

from agora_token_builder import RtcTokenBuilder

from .. import config

ROLE_PUBLISHER = 1
TOKEN_TTL_SECONDS = 3600


def build_client_config() -> dict:
    return {"provider": "agora", "appId": config.AGORA_APP_ID}


def generate_token(room: str, identity: str) -> dict:
    # Agora 頻道用數字 uid，這裡把 LINE userId 雜湊成一個整數；
    # 僅供 POC 測試識別身分，正式版要換成不會碰撞的對應表。
    uid = abs(hash(identity)) % 100000
    privilege_expired_ts = int(time.time()) + TOKEN_TTL_SECONDS

    token = RtcTokenBuilder.buildTokenWithUid(
        config.AGORA_APP_ID,
        config.AGORA_APP_CERTIFICATE,
        room,
        uid,
        ROLE_PUBLISHER,
        privilege_expired_ts,
    )

    return {
        "provider": "agora",
        "appId": config.AGORA_APP_ID,
        "channel": room,
        "uid": uid,
        "token": token,
    }
