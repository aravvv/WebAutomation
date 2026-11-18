#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

if ! command -v yt-dlp >/dev/null 2>&1 && [ ! -f "$HOME/.local/bin/yt-dlp" ]; then
  echo "Installing yt-dlp..."
  pip install -q yt-dlp
fi

PORT="${1:-8080}"
echo "=========================================================="
echo " 🚀 GTA 6 Viral Shorts Clipper & Auto-Publisher Studio"
echo " 🌐 Open http://localhost:${PORT} in your browser"
echo "=========================================================="
python3 cli.py dashboard --port "${PORT}"
