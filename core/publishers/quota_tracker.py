import json
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from config.settings import DB_PATH, YOUTUBE_DAILY_QUOTA_LIMIT, YOUTUBE_UPLOAD_COST
from core.video.short_compositor import RenderedShort


class QuotaAndClipTracker:
    """
    SQLite database tracking:
      1. All clipped 9:16 GTA 6 Shorts, suggested hook captions, titles, hashtags & approval status
      2. Daily YouTube Data API v3 free quota usage (10,000 units/day limit; 1,600 units/upload)
    """

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS clips (
                    clip_id TEXT PRIMARY KEY,
                    source_url TEXT,
                    output_path TEXT,
                    start_time REAL,
                    end_time REAL,
                    viral_score REAL,
                    reason TEXT,
                    primary_hook TEXT,
                    hook_options_json TEXT,
                    primary_title TEXT,
                    title_options_json TEXT,
                    description TEXT,
                    hashtags_json TEXT,
                    youtube_tags_json TEXT,
                    audio_mode TEXT,
                    status TEXT DEFAULT 'pending_review',
                    youtube_video_id TEXT DEFAULT '',
                    created_at TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS quota_usage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    day TEXT,
                    operation TEXT,
                    units INTEGER,
                    clip_id TEXT,
                    created_at TEXT
                )
                """
            )

    def save_rendered_short(self, short: RenderedShort, source_url: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO clips (
                    clip_id, source_url, output_path, start_time, end_time,
                    viral_score, reason, primary_hook, hook_options_json,
                    primary_title, title_options_json, description,
                    hashtags_json, youtube_tags_json, audio_mode, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending_review', ?)
                """,
                (
                    short.clip_id,
                    source_url,
                    str(short.output_path),
                    short.moment.start_time,
                    short.moment.end_time,
                    short.moment.viral_score,
                    short.moment.reason,
                    short.hooks.primary_hook,
                    json.dumps(short.hooks.hook_options),
                    short.hooks.primary_title,
                    json.dumps(short.hooks.title_options),
                    short.hooks.description,
                    json.dumps(short.hooks.hashtags),
                    json.dumps(short.hooks.youtube_tags),
                    short.audio_mode,
                    datetime.utcnow().isoformat(),
                ),
            )

    def list_clips(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM clips ORDER BY created_at DESC, viral_score DESC").fetchall()
            result = []
            for r in rows:
                d = dict(r)
                d["hook_options"] = json.loads(d.get("hook_options_json") or "[]")
                d["title_options"] = json.loads(d.get("title_options_json") or "[]")
                d["hashtags"] = json.loads(d.get("hashtags_json") or "[]")
                d["youtube_tags"] = json.loads(d.get("youtube_tags_json") or "[]")
                result.append(d)
            return result

    def get_clip(self, clip_id: str) -> Optional[Dict[str, Any]]:
        for c in self.list_clips():
            if c["clip_id"] == clip_id:
                return c
        return None

    def update_clip_selection(self, clip_id: str, selected_hook: str, selected_title: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE clips SET primary_hook = ?, primary_title = ? WHERE clip_id = ?",
                (selected_hook, selected_title, clip_id),
            )

    def mark_published(self, clip_id: str, youtube_video_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE clips SET status = 'published', youtube_video_id = ? WHERE clip_id = ?",
                (youtube_video_id, clip_id),
            )

    def get_today_quota_used(self) -> int:
        today_str = date.today().isoformat()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(units), 0) AS used FROM quota_usage WHERE day = ?",
                (today_str,),
            ).fetchone()
            return int(row["used"]) if row else 0

    def get_remaining_quota(self) -> Dict[str, int]:
        used = self.get_today_quota_used()
        remaining = max(0, YOUTUBE_DAILY_QUOTA_LIMIT - used)
        uploads_left = remaining // YOUTUBE_UPLOAD_COST
        return {
            "daily_limit": YOUTUBE_DAILY_QUOTA_LIMIT,
            "used_today": used,
            "remaining_units": remaining,
            "uploads_remaining_today": uploads_left,
        }

    def record_quota_usage(self, clip_id: str, units: int = YOUTUBE_UPLOAD_COST, operation: str = "videos.insert") -> None:
        today_str = date.today().isoformat()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO quota_usage (day, operation, units, clip_id, created_at) VALUES (?, ?, ?, ?, ?)",
                (today_str, operation, units, clip_id, datetime.utcnow().isoformat()),
            )
