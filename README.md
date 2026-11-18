# GTA 6 Viral YouTube Shorts Clipper & Auto-Publisher

An automated, **100% Free & Open-Source** pipeline that clips the craziest moments from GTA 6 videos into **true full-bleed 9:16 portrait YouTube Shorts (`1080x1920`)**, burns in **viral hook banners & word-by-word karaoke subtitles**, suggests **crazy hook captions & GTA 6 hashtags**, and auto-publishes to your YouTube channel.

---

## Key Features

1. **Multi-Signal "Crazy Moment" Detector (`core/analyzer/moment_detector.py`)**:
   - **Signal 1**: YouTube "Most Replayed" viewer retention heatmap peaks (`yt-dlp`).
   - **Signal 2**: Audio RMS energy & excitement spikes (`FFmpeg` + `NumPy`).
   - **Signal 3**: Visual action & horizontal motion intensity (`FFmpeg` + `NumPy`).
   - **Signal 4**: Transcript hype keywords (*"insane"*, *"wait for it"*, *"police chase"*, *"physics"*, *"Lucia"*, *"Vice City"*).
2. **True Full-Bleed 9:16 Portrait Cropper (`core/video/smart_cropper.py`)**:
   - Strictly crops `16:9` landscape (`1920x1080`) into full vertical `9:16` (`608x1080` scaled via `lanczos` to `1080x1920`) — **never** landscape-inside-portrait with black/blurred bars.
   - Smooth **Action-Tracking Horizontal Camera Pan** (follows the subject/action using Exponential Moving Average saliency tracking).
3. **Dual Audio Modes (`core/audio/`)**:
   - `--mode original`: Keeps original game/creator audio with synced word-by-word karaoke captions.
   - `--mode ai-voiceover`: Generates an energetic AI hype voiceover (`edge-tts` free Microsoft neural voice) mixed over ducked (`-16dB`) game audio.
4. **Crazy Hook Captions, Titles & GTA 6 Hashtags (`core/ideation/hook_generator.py`)**:
   - Automatically burns a high-contrast viral hook header at the top of the video AND generates 5 suggested hook captions, 5 YouTube Shorts titles, and 12 high-velocity GTA 6 hashtags (`#gta6 #gta #grandtheftauto6 #rockstargames #gtashorts #shorts`).
5. **Interactive Web Review Studio & YouTube Shorts Auto-Publisher (`core/review/web_dashboard.py` & `core/publishers/youtube_publisher.py`)**:
   - Preview your rendered 9:16 Shorts in your browser, pick your favorite hook caption, and publish in 1 click.

---

## Quick Start Commands

### 1. Clip a GTA 6 YouTube Video via CLI
```bash
# Clip the top 2 crazy moments (Original Audio + Synced Karaoke Subtitles)
python3 cli.py clip --url "https://www.youtube.com/watch?v=QdBZY2fkU-0" --top 2 --mode original

# Clip with AI Hype Voiceover narration over ducked game audio
python3 cli.py clip --url "https://www.youtube.com/watch?v=QdBZY2fkU-0" --top 1 --mode ai-voiceover

# Test immediately offline using the built-in 1920x1080 GTA 6 demo video
python3 cli.py clip --demo --top 2 --mode original
```

### 2. Launch the Interactive Web Studio Dashboard
```bash
python3 cli.py dashboard --port 8080
```

### 3. Connect Your New GTA 6 YouTube Channel (100% Free API)
1. Go to the [Google Cloud Console](https://console.cloud.google.com/) (free, no billing card required).
2. Create a new project and enable **YouTube Data API v3** (comes with **10,000 free quota units/day** = ~6 free video uploads every day).
3. Create an **OAuth 2.0 Client ID** (Application type: **Desktop app**), download the JSON file, and save it as `client_secrets.json` in this folder.
4. Run `python3 cli.py publish --clip-id <CLIP_ID>` (or click **Approve & Post** in the Web Dashboard) — a browser window will open once to authorize your new GTA 6 channel and save `token.json` for automatic future uploads.
