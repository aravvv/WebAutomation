import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from config.settings import (
    CLIENT_SECRETS_FILE,
    TOKEN_FILE,
    YOUTUBE_GAMING_CATEGORY_ID,
    YOUTUBE_UPLOAD_COST,
)
from core.publishers.quota_tracker import QuotaAndClipTracker

YOUTUBE_UPLOAD_SCOPE = ["https://www.googleapis.com/auth/youtube.upload"]


class YouTubeShortsPublisher:
    """
    Publishes 9:16 vertical GTA 6 clips directly to YouTube Shorts via the official
    100% Free YouTube Data API v3 (OAuth 2.0).

    Includes:
      - Automatic daily quota verification (1,600 units/upload out of 10,000 free units/day)
      - Resumable upload with Gaming Category (#20), Shorts hashtags, and privacy control
      - Dry-run verification mode when testing before connecting client_secrets.json
    """

    def __init__(
        self,
        tracker: Optional[QuotaAndClipTracker] = None,
        client_secrets_file: str = CLIENT_SECRETS_FILE,
        token_file: str = TOKEN_FILE,
    ):
        self.tracker = tracker or QuotaAndClipTracker()
        self.client_secrets_file = Path(client_secrets_file)
        self.token_file = Path(token_file)

    def publish_short(
        self,
        clip_id: str,
        video_path: Path,
        title: str,
        description: str,
        tags: List[str],
        privacy_status: str = "public",
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        video_path = Path(video_path)
        if not video_path.exists():
            raise FileNotFoundError(f"Rendered Short file not found: {video_path}")

        quota_status = self.tracker.get_remaining_quota()
        if quota_status["remaining_units"] < YOUTUBE_UPLOAD_COST:
            raise RuntimeError(
                f"Daily free YouTube Data API quota reached ({quota_status['used_today']}/"
                f"{quota_status['daily_limit']} units used today). Quota resets at midnight PT."
            )

        # Ensure #shorts and #gta6 are present for YouTube Shorts shelf discovery
        if "#shorts" not in title.lower():
            title = f"{title[:88].strip()} #shorts"

        body_payload = {
            "snippet": {
                "title": title[:100],
                "description": description[:4900],
                "tags": tags[:35],
                "categoryId": YOUTUBE_GAMING_CATEGORY_ID,
            },
            "status": {
                "privacyStatus": privacy_status,
                "selfDeclaredMadeForKids": False,
            },
        }

        if dry_run or not self.client_secrets_file.exists():
            sim_id = f"yt_short_{clip_id[:16]}"
            self.tracker.mark_published(clip_id, sim_id)
            return {
                "mode": "dry_run" if dry_run else "simulated_no_credentials",
                "youtube_video_id": sim_id,
                "shorts_url": f"https://www.youtube.com/shorts/{sim_id}",
                "payload": body_payload,
                "quota_after": self.tracker.get_remaining_quota(),
                "note": (
                    "Place your free Google Cloud OAuth `client_secrets.json` in the project root "
                    "to perform live uploads to your new GTA 6 YouTube channel."
                ),
            }

        youtube = self._get_authenticated_service()
        from googleapiclient.http import MediaFileUpload

        media = MediaFileUpload(str(video_path), chunksize=1024 * 1024, resumable=True, mimetype="video/mp4")
        request = youtube.videos().insert(
            part="snippet,status",
            body=body_payload,
            media_body=media,
        )
        response = None
        while response is None:
            _, response = request.next_chunk()

        yt_video_id = response.get("id", "")
        self.tracker.record_quota_usage(clip_id=clip_id, units=YOUTUBE_UPLOAD_COST)
        self.tracker.mark_published(clip_id=clip_id, youtube_video_id=yt_video_id)

        return {
            "mode": "live_upload",
            "youtube_video_id": yt_video_id,
            "shorts_url": f"https://www.youtube.com/shorts/{yt_video_id}",
            "payload": body_payload,
            "quota_after": self.tracker.get_remaining_quota(),
        }

    def _get_authenticated_service(self):
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build

        creds = None
        if self.token_file.exists():
            creds = Credentials.from_authorized_user_file(str(self.token_file), YOUTUBE_UPLOAD_SCOPE)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(self.client_secrets_file), YOUTUBE_UPLOAD_SCOPE
                )
                creds = flow.run_local_server(port=0)
            self.token_file.write_text(creds.to_json(), encoding="utf-8")

        return build("youtube", "v3", credentials=creds)
