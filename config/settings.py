import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
STORAGE_DIR = BASE_DIR / "storage"
DOWNLOADS_DIR = STORAGE_DIR / "downloads"
RENDERS_DIR = STORAGE_DIR / "renders"
TEMP_DIR = STORAGE_DIR / "temp"
DB_PATH = STORAGE_DIR / "gta6_clipper.db"
PRESETS_PATH = BASE_DIR / "config" / "gta6_presets.yaml"

# Ensure storage directories exist
for directory in (STORAGE_DIR, DOWNLOADS_DIR, RENDERS_DIR, TEMP_DIR):
    directory.mkdir(parents=True, exist_ok=True)

# Target Output Specifications (Strict Full-Bleed 9:16 Portrait)
TARGET_WIDTH = 1080
TARGET_HEIGHT = 1920
TARGET_ASPECT_RATIO = 9 / 16  # 0.5625
TARGET_FPS = 30

# Clip Duration Constraints (YouTube Shorts sweet spot: 15s - 55s)
DEFAULT_CLIP_DURATION = 28.0
MIN_CLIP_DURATION = 15.0
MAX_CLIP_DURATION = 58.0

# Font paths (prioritize Roboto-Black for viral Shorts look, fallback to DejaVuSans-Bold)
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/roboto/unhinted/RobotoTTF/Roboto-Black.ttf",
    "/usr/share/fonts/truetype/roboto/unhinted/RobotoCondensed-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


def get_primary_font() -> str:
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            return path
    return FONT_CANDIDATES[-1]


# YouTube Data API v3 Quota Settings (100% Free Tier)
YOUTUBE_DAILY_QUOTA_LIMIT = 10000
YOUTUBE_UPLOAD_COST = 1600  # Allows ~6 free Shorts uploads per day per project
YOUTUBE_GAMING_CATEGORY_ID = "20"  # Gaming category
CLIENT_SECRETS_FILE = os.getenv("YOUTUBE_CLIENT_SECRETS", str(BASE_DIR / "client_secrets.json"))
TOKEN_FILE = os.getenv("YOUTUBE_TOKEN_FILE", str(BASE_DIR / "token.json"))

# Optional Telegram Gate
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
