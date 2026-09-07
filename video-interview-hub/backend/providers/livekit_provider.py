from livekit import api

from .. import config


def build_client_config() -> dict:
    return {"provider": "livekit", "wsUrl": config.LIVEKIT_WS_URL}


def generate_token(room: str, identity: str) -> dict:
    token = (
        api.AccessToken(config.LIVEKIT_API_KEY, config.LIVEKIT_API_SECRET)
        .with_identity(identity)
        .with_name(identity)
        .with_grants(api.VideoGrants(room_join=True, room=room))
    )

    return {
        "provider": "livekit",
        "wsUrl": config.LIVEKIT_WS_URL,
        "room": room,
        "identity": identity,
        "token": token.to_jwt(),
    }
