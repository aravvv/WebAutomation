#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path

from core.analyzer.moment_detector import CrazyMomentDetector
from core.ingest.youtube_downloader import YouTubeDownloader
from core.publishers.quota_tracker import QuotaAndClipTracker
from core.publishers.youtube_publisher import YouTubeShortsPublisher
from core.video.short_compositor import ShortCompositor
from core.video.smart_cropper import SmartPortraitCropper

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def cmd_clip(args: argparse.Namespace) -> None:
    downloader = YouTubeDownloader()
    detector = CrazyMomentDetector()
    compositor = ShortCompositor()
    tracker = QuotaAndClipTracker()
    publisher = YouTubeShortsPublisher(tracker=tracker)

    source_target = "demo" if args.demo else args.url
    print(f"\n[1/4] Ingesting GTA 6 video source: {source_target} ...")
    video = downloader.ingest(source_target, use_demo_fallback=args.demo)
    print(
        f"      Loaded: '{video.title}' ({video.width}x{video.height} landscape, "
        f"duration={video.duration:.1f}s, heatmap_points={len(video.heatmap)})"
    )

    print(f"\n[2/4] Scanning for Top {args.top} 'Crazy Moments' (Heatmap + Audio RMS + Motion + Keywords)...")
    moments = detector.detect_top_moments(video, top_n=args.top, clip_duration=args.duration)

    for m in moments:
        print(
            f"\n  🔥 Crazy Moment #{m.rank}: [{m.start_time:.1f}s -> {m.end_time:.1f}s] "
            f"| Viral Score: {m.viral_score}/100\n"
            f"     Reason: {m.reason}"
        )

    print(f"\n[3/4] Cropping 16:9 -> True Full-Bleed 9:16 Portrait (1080x1920) & Burning Viral Hooks...")
    for m in moments:
        short = compositor.render_short(
            video=video,
            moment=m,
            audio_mode=args.mode,
            track_action=not args.static_center,
        )
        tracker.save_rendered_short(short, source_url=video.source_url)
        verify = SmartPortraitCropper.verify_portrait_video(short.output_path)

        print(f"\n  ✅ Rendered 9:16 Short #{m.rank}: {short.output_path}")
        print(
            f"     Output Resolution : {verify['width']}x{verify['height']} "
            f"(Full-Bleed 9:16 = {verify['is_9_16']}, Crop Window = {short.crop_geometry.crop_width}x{short.crop_geometry.crop_height})"
        )
        print(f"     Burned Hook Banner: {short.hooks.primary_hook}")
        print("     Suggested Crazy Hook Captions:")
        for idx, h_opt in enumerate(short.hooks.hook_options, 1):
            print(f"       {idx}. {h_opt}")
        print("     Suggested YouTube Shorts Titles:")
        for idx, t_opt in enumerate(short.hooks.title_options[:3], 1):
            print(f"       {idx}. {t_opt}")
        print(f"     Viral Hashtags    : {' '.join(short.hooks.hashtags)}")

        if args.auto_publish:
            pub_res = publisher.publish_short(
                clip_id=short.clip_id,
                video_path=short.output_path,
                title=short.hooks.primary_title,
                description=short.hooks.description,
                tags=short.hooks.youtube_tags,
                privacy_status=args.privacy,
                dry_run=args.dry_run,
            )
            print(f"     🚀 Published Status: {pub_res['shorts_url']} ({pub_res['mode']})")

    quota = tracker.get_remaining_quota()
    print(
        f"\n[4/4] Done! Free YouTube Daily Quota Remaining: "
        f"{quota['remaining_units']}/{quota['daily_limit']} units "
        f"({quota['uploads_remaining_today']} free uploads left today)."
    )


def cmd_publish(args: argparse.Namespace) -> None:
    tracker = QuotaAndClipTracker()
    publisher = YouTubeShortsPublisher(tracker=tracker)
    clip = tracker.get_clip(args.clip_id)
    if not clip:
        raise SystemExit(f"Clip ID '{args.clip_id}' not found. Run `python3 cli.py list` to view clips.")

    res = publisher.publish_short(
        clip_id=clip["clip_id"],
        video_path=Path(clip["output_path"]),
        title=args.title or clip["primary_title"],
        description=clip["description"],
        tags=clip["youtube_tags"],
        privacy_status=args.privacy,
        dry_run=args.dry_run,
    )
    print(json.dumps(res, indent=2))


def cmd_list(_: argparse.Namespace) -> None:
    tracker = QuotaAndClipTracker()
    clips = tracker.list_clips()
    print(f"Total Clipped GTA 6 Shorts: {len(clips)}")
    for c in clips:
        print(
            f" - [{c['status']}] {c['clip_id']} | Score: {c['viral_score']} | "
            f"Hook: {c['primary_hook']} | File: {c['output_path']}"
        )


def cmd_dashboard(args: argparse.Namespace) -> None:
    import uvicorn

    print(f"Launching GTA 6 Viral Shorts Studio Dashboard on http://localhost:{args.port} ...")
    uvicorn.run("core.review.web_dashboard:app", host="0.0.0.0", port=args.port, reload=False)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="GTA 6 Viral YouTube Shorts Clipper & Auto-Publisher (100% Free Stack)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # clip command
    p_clip = sub.add_parser("clip", help="Download/ingest a GTA 6 video, clip crazy moments in 9:16, and suggest hooks")
    p_clip.add_argument("--url", type=str, default="demo", help="YouTube video URL or local MP4 path")
    p_clip.add_argument("--demo", action="store_true", help="Use built-in 1920x1080 GTA 6 demo video")
    p_clip.add_argument("--top", type=int, default=2, help="Number of top crazy moments to clip (default: 2)")
    p_clip.add_argument("--duration", type=float, default=28.0, help="Target Short duration in seconds")
    p_clip.add_argument(
        "--mode",
        choices=["original", "ai-voiceover"],
        default="original",
        help="Keep original audio + captions ('original') or add AI hype commentary ('ai-voiceover')",
    )
    p_clip.add_argument("--static-center", action="store_true", help="Disable dynamic horizontal action tracking")
    p_clip.add_argument("--auto-publish", action="store_true", help="Immediately publish rendered Shorts to YouTube")
    p_clip.add_argument("--privacy", choices=["public", "unlisted", "private"], default="public")
    p_clip.add_argument("--dry-run", action="store_true", help="Simulate YouTube API upload")
    p_clip.set_defaults(func=cmd_clip)

    # publish command
    p_pub = sub.add_parser("publish", help="Publish a rendered 9:16 Short to YouTube Shorts")
    p_pub.add_argument("--clip-id", type=str, required=True, help="Clip ID to publish")
    p_pub.add_argument("--title", type=str, default=None, help="Optional custom YouTube Short title")
    p_pub.add_argument("--privacy", choices=["public", "unlisted", "private"], default="public")
    p_pub.add_argument("--dry-run", action="store_true", help="Simulate upload without consuming quota")
    p_pub.set_defaults(func=cmd_publish)

    # list command
    p_list = sub.add_parser("list", help="List all clipped 9:16 Shorts and suggested hooks")
    p_list.set_defaults(func=cmd_list)

    # dashboard command
    p_dash = sub.add_parser("dashboard", help="Launch interactive Web Review Studio")
    p_dash.add_argument("--port", type=int, default=8080, help="Port to run studio on (default: 8080)")
    p_dash.set_defaults(func=cmd_dashboard)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
