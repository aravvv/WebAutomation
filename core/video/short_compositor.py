import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from config.settings import RENDERS_DIR, TEMP_DIR
from core.analyzer.moment_detector import CrazyMoment
from core.audio.audio_mixer import AudioMixer
from core.audio.transcriber import AudioTranscriber
from core.audio.voice_synthesizer import VoiceSynthesizer
from core.ideation.hook_generator import ViralHookGenerator, ViralHookPackage
from core.ingest.youtube_downloader import IngestedVideo
from core.video.overlay_renderer import OverlayRenderer
from core.video.smart_cropper import CropGeometry, SmartPortraitCropper


@dataclass
class RenderedShort:
    clip_id: str
    output_path: Path
    moment: CrazyMoment
    hooks: ViralHookPackage
    crop_geometry: CropGeometry
    audio_mode: str  # 'original' | 'ai-voiceover'
    width: int = 1080
    height: int = 1920


class ShortCompositor:
    """
    End-to-end 9:16 YouTube Shorts compositor:
      1. True full-bleed 9:16 portrait crop with horizontal action tracking (1080x1920)
      2. Optional AI Hype Voiceover synthesis + game audio ducking (-16dB)
      3. Top Viral Hook Banner + Word-by-Word Karaoke Subtitles burned in via FFmpeg
    """

    def __init__(
        self,
        renders_dir: Path = RENDERS_DIR,
        temp_dir: Path = TEMP_DIR,
    ):
        self.renders_dir = Path(renders_dir)
        self.temp_dir = Path(temp_dir)
        self.renders_dir.mkdir(parents=True, exist_ok=True)
        self.temp_dir.mkdir(parents=True, exist_ok=True)

        self.cropper = SmartPortraitCropper()
        self.hook_gen = ViralHookGenerator()
        self.transcriber = AudioTranscriber()
        self.voice_synth = VoiceSynthesizer()
        self.overlay_renderer = OverlayRenderer()

    def render_short(
        self,
        video: IngestedVideo,
        moment: CrazyMoment,
        audio_mode: str = "original",
        custom_hook_text: Optional[str] = None,
        track_action: bool = True,
    ) -> RenderedShort:
        clip_id = f"{video.video_id}_clip{moment.rank}_{int(moment.start_time)}s_{audio_mode}"
        final_mp4 = self.renders_dir / f"{clip_id}.mp4"
        hook_png = self.temp_dir / f"{clip_id}_hook.png"
        subs_ass = self.temp_dir / f"{clip_id}_subs.ass"

        # 1. Generate viral hooks, titles, hashtags & AI script
        hook_pkg = self.hook_gen.generate_for_moment(moment, source_title=video.title)
        if custom_hook_text:
            hook_pkg.primary_hook = custom_hook_text

        # 2. Compute 9:16 smart crop filter chain
        crop_w, _ = self.cropper.compute_crop_dimensions(video.width, video.height)
        if track_action:
            trajectory = self.cropper.analyze_action_trajectory(
                video.video_path,
                moment.start_time,
                moment.end_time,
                video.width,
                video.height,
                crop_w,
            )
        else:
            trajectory = [(0.0, max(0, (video.width - crop_w) // 2))]

        crop_vf, crop_geom = self.cropper.build_ffmpeg_crop_filter(video.width, video.height, trajectory)

        # 3. Prepare word-level karaoke timings (and optional AI voiceover audio)
        vo_audio_path: Optional[Path] = None
        if audio_mode == "ai-voiceover":
            vo_audio_path = self.temp_dir / f"{clip_id}_vo.mp3"
            self.voice_synth.synthesize(
                hook_pkg.ai_voiceover_script,
                vo_audio_path,
                target_duration=moment.duration,
            )
            word_timings = self.transcriber.extract_clip_word_timings(
                video,
                moment.start_time,
                moment.end_time,
                override_script=hook_pkg.ai_voiceover_script,
            )
        else:
            word_timings = self.transcriber.extract_clip_word_timings(
                video,
                moment.start_time,
                moment.end_time,
            )

        # 4. Render Top Viral Hook PNG + Karaoke ASS Subtitles
        self.overlay_renderer.render_hook_banner_png(hook_pkg.primary_hook, hook_png)
        self.overlay_renderer.generate_karaoke_ass_subtitles(word_timings, subs_ass)

        # 5. Single-pass FFmpeg composite: 9:16 Crop + Scale + Hook Banner Overlay + ASS Subtitles
        import os

        try:
            rel_ass = os.path.relpath(subs_ass, Path.cwd()).replace("\\", "/")
        except ValueError:
            rel_ass = str(subs_ass).replace("\\", "/")
        ass_escaped = rel_ass.replace(":", "\\:").replace("'", "\\'")
        filter_complex = f"[0:v]{crop_vf}[cropped];[cropped][1:v]overlay=0:0,ass='{ass_escaped}'[vout]"

        base_render_path = self.temp_dir / f"{clip_id}_base.mp4" if vo_audio_path else final_mp4

        cmd = [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-ss",
            f"{moment.start_time:.2f}",
            "-t",
            f"{moment.duration:.2f}",
            "-i",
            str(video.video_path),
            "-i",
            str(hook_png),
            "-filter_complex",
            filter_complex,
            "-map",
            "[vout]",
            "-map",
            "0:a?",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            str(base_render_path),
        ]
        subprocess.run(cmd, check=True, capture_output=True)

        # 6. If AI Voiceover mode, mix the narration over the ducked (-16dB) game audio
        if vo_audio_path and vo_audio_path.exists():
            AudioMixer.mix_voiceover_with_game_audio(
                portrait_video_path=base_render_path,
                voiceover_audio_path=vo_audio_path,
                output_video_path=final_mp4,
                game_volume_db=-16.0,
            )
            if base_render_path.exists():
                base_render_path.unlink()

        return RenderedShort(
            clip_id=clip_id,
            output_path=final_mp4,
            moment=moment,
            hooks=hook_pkg,
            crop_geometry=crop_geom,
            audio_mode=audio_mode,
        )
