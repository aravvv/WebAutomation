import unittest
from pathlib import Path

from core.analyzer.moment_detector import CrazyMomentDetector
from core.ideation.hook_generator import ViralHookGenerator
from core.ingest.youtube_downloader import YouTubeDownloader
from core.publishers.quota_tracker import QuotaAndClipTracker
from core.publishers.youtube_publisher import YouTubeShortsPublisher
from core.video.short_compositor import ShortCompositor
from core.video.smart_cropper import SmartPortraitCropper


class TestGTA6ShortsPipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.downloader = YouTubeDownloader()
        cls.video = cls.downloader.create_demo_gta6_landscape_video(duration=36.0)
        cls.detector = CrazyMomentDetector()
        cls.cropper = SmartPortraitCropper()
        cls.hook_gen = ViralHookGenerator()
        cls.compositor = ShortCompositor()

    def test_01_source_is_landscape_1080p(self) -> None:
        self.assertEqual(self.video.width, 1920)
        self.assertEqual(self.video.height, 1080)
        self.assertTrue(self.video.video_path.exists())

    def test_02_crazy_moment_detector_finds_action_peaks(self) -> None:
        moments = self.detector.detect_top_moments(self.video, top_n=2, clip_duration=10.0)
        self.assertEqual(len(moments), 2)
        # Our synthetic GTA 6 video has action peaks at [6s..16s] and [21s..31s]
        first = moments[0]
        self.assertGreaterEqual(first.viral_score, 75.0)
        self.assertTrue(4.0 <= first.start_time <= 24.0)
        self.assertIn("YouTube Most-Replayed Peak", first.reason)

    def test_03_smart_cropper_strict_9_16_portrait_dimensions(self) -> None:
        crop_w, crop_h = self.cropper.compute_crop_dimensions(1920, 1080)
        # 1080 * 9 / 16 = 607.5 -> 608x1080 (full height cropped to 9:16, NOT letterboxed)
        self.assertEqual(crop_h, 1080)
        self.assertEqual(crop_w, 608)

    def test_04_hook_generator_outputs_crazy_hooks_and_hashtags(self) -> None:
        moments = self.detector.detect_top_moments(self.video, top_n=1, clip_duration=10.0)
        pkg = self.hook_gen.generate_for_moment(moments[0], source_title=self.video.title)
        self.assertGreaterEqual(len(pkg.hook_options), 5)
        self.assertGreaterEqual(len(pkg.title_options), 5)
        self.assertIn("#gta6", pkg.hashtags)
        self.assertIn("#shorts", pkg.hashtags)

    def test_05_end_to_end_render_original_and_ai_voiceover(self) -> None:
        moments = self.detector.detect_top_moments(self.video, top_n=1, clip_duration=10.0)
        m = moments[0]

        # Test Mode A: Original Audio + Karaoke Subtitles
        short_orig = self.compositor.render_short(self.video, m, audio_mode="original")
        self.assertTrue(short_orig.output_path.exists())
        verify_orig = SmartPortraitCropper.verify_portrait_video(short_orig.output_path)
        self.assertEqual(verify_orig["width"], 1080)
        self.assertEqual(verify_orig["height"], 1920)
        self.assertTrue(verify_orig["is_9_16"])

        # Test Mode B: AI Hype Voiceover + Ducked Game Audio
        short_vo = self.compositor.render_short(self.video, m, audio_mode="ai-voiceover")
        self.assertTrue(short_vo.output_path.exists())
        verify_vo = SmartPortraitCropper.verify_portrait_video(short_vo.output_path)
        self.assertEqual(verify_vo["width"], 1080)
        self.assertEqual(verify_vo["height"], 1920)
        self.assertTrue(verify_vo["is_9_16"])

        # Test Quota Tracker & Dry-Run Publisher
        tracker = QuotaAndClipTracker()
        tracker.save_rendered_short(short_orig, source_url=self.video.source_url)
        pub = YouTubeShortsPublisher(tracker=tracker)
        res = pub.publish_short(
            clip_id=short_orig.clip_id,
            video_path=short_orig.output_path,
            title=short_orig.hooks.primary_title,
            description=short_orig.hooks.description,
            tags=short_orig.hooks.youtube_tags,
            dry_run=True,
        )
        self.assertIn("https://www.youtube.com/shorts/", res["shorts_url"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
