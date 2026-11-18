import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict

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

# Font paths (supports both Windows and Linux system fonts)
FONT_CANDIDATES = [
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/impact.ttf",
    "C:/Windows/Fonts/segoeuib.ttf",
    "C:/Windows/Fonts/calibrib.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/roboto/unhinted/RobotoTTF/Roboto-Black.ttf",
    "/usr/share/fonts/truetype/roboto/unhinted/RobotoCondensed-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]

_CACHED_FFMPEG_BIN: str = ""


def get_primary_font() -> str:
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            return path
    return FONT_CANDIDATES[-1]


def get_ffmpeg_bin() -> str:
    """
    Returns path to ffmpeg executable. On Windows/Mac/Linux, uses system ffmpeg
    if on PATH; otherwise uses (and auto-installs if needed) imageio-ffmpeg's
    standalone ffmpeg binary so the user never has to install FFmpeg manually.
    """
    global _CACHED_FFMPEG_BIN
    if _CACHED_FFMPEG_BIN and os.path.exists(_CACHED_FFMPEG_BIN):
        return _CACHED_FFMPEG_BIN

    sys_ffmpeg = shutil.which("ffmpeg")
    if sys_ffmpeg:
        _CACHED_FFMPEG_BIN = sys_ffmpeg
        return sys_ffmpeg

    try:
        import imageio_ffmpeg

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.exists(exe):
            target_name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
            std_exe = TEMP_DIR / target_name
            if not std_exe.exists():
                try:
                    shutil.copy2(exe, std_exe)
                except Exception:
                    _CACHED_FFMPEG_BIN = exe
                    return exe
            _CACHED_FFMPEG_BIN = str(std_exe)
            return _CACHED_FFMPEG_BIN
    except ImportError:
        try:
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "-q", "imageio-ffmpeg"],
                check=False,
            )
            import imageio_ffmpeg

            exe = imageio_ffmpeg.get_ffmpeg_exe()
            if exe and os.path.exists(exe):
                target_name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
                std_exe = TEMP_DIR / target_name
                if not std_exe.exists():
                    try:
                        shutil.copy2(exe, std_exe)
                    except Exception:
                        _CACHED_FFMPEG_BIN = exe
                        return exe
                _CACHED_FFMPEG_BIN = str(std_exe)
                return _CACHED_FFMPEG_BIN
        except Exception:
            pass

    return "ffmpeg"


def probe_media_with_ffmpeg(media_path: Path) -> Dict[str, Any]:
    """
    Probes video width, height, and duration using `ffmpeg -i` stderr output.
    Works across Windows, macOS, and Linux without requiring a separate ffprobe binary.
    """
    ffmpeg_bin = get_ffmpeg_bin()
    cmd = [ffmpeg_bin, "-hide_banner", "-i", str(media_path)]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    info_text = (res.stderr or "") + "\n" + (res.stdout or "")

    duration = 0.0
    dur_m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", info_text)
    if dur_m:
        duration = (
            int(dur_m.group(1)) * 3600.0
            + int(dur_m.group(2)) * 60.0
            + float(dur_m.group(3))
        )

    width, height = 1920, 1080
    for m in re.finditer(r"Video:.*?\s(\d{3,5})x(\d{3,5})(?:[\s,\[]|$)", info_text):
        width = int(m.group(1))
        height = int(m.group(2))
        break

    return {
        "width": width,
        "height": height,
        "duration": duration,
        "start_time": 0.0,
    }


# YouTube Data API v3 Quota Settings (Optional Auto-Uploader)
YOUTUBE_DAILY_QUOTA_LIMIT = 10000
YOUTUBE_UPLOAD_COST = 1600
YOUTUBE_GAMING_CATEGORY_ID = "20"
CLIENT_SECRETS_FILE = os.getenv("YOUTUBE_CLIENT_SECRETS", str(BASE_DIR / "client_secrets.json"))
TOKEN_FILE = os.getenv("YOUTUBE_TOKEN_FILE", str(BASE_DIR / "token.json"))

# Optional Telegram Gate
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
