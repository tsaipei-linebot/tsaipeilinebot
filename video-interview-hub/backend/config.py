import os

from dotenv import load_dotenv

load_dotenv()

# "agora" 或 "livekit"，決定這次測試用哪一家 SDK
VIDEO_SDK_PROVIDER = os.getenv("VIDEO_SDK_PROVIDER", "agora")

LIFF_ID = os.getenv("LIFF_ID", "")
TEST_ROOM_NAME = os.getenv("TEST_ROOM_NAME", "poc-test-room")

AGORA_APP_ID = os.getenv("AGORA_APP_ID", "")
AGORA_APP_CERTIFICATE = os.getenv("AGORA_APP_CERTIFICATE", "")

LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY", "")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET", "")
LIVEKIT_WS_URL = os.getenv("LIVEKIT_WS_URL", "")
