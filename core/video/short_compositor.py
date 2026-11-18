import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from config.settings import (
    RENDERS_DIR,
    TARGET_FPS,
    TARGET_HEIGHT,
    TARGET_WIDTH,
    TEMP_DIR,
    get_ffmpeg_bin,
    get_primary_font,
)
from core.analyzer.moment_detector import CrazyMoment
from core.audio.audio_mixer import AudioMixer
from core.audio.transcriber import AudioTranscriber, WordTiming
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
         (with automatic Pillow + NumPy band-compositor fallback if Windows FFmpeg lacks libass).
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
        ffmpeg_bin = get_ffmpeg_bin()
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
                audio_path_for_whisper=vo_audio_path,
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

        base_render_path = self.temp_dir / f"{clip_id}_base.mp4" if vo_audio_path else final_mp4

        # 5. Try Fast Single-Pass FFmpeg (9:16 Crop + Hook PNG Overlay + libass Subtitles)
        try:
            rel_ass = os.path.relpath(subs_ass, Path.cwd()).replace("\\", "/")
        except ValueError:
            rel_ass = str(subs_ass).replace("\\", "/")
        ass_escaped = rel_ass.replace(":", "\\:").replace("'", "\\'")
        filter_complex = f"[0:v]{crop_vf}[cropped];[cropped][1:v]overlay=0:0,ass='{ass_escaped}'[vout]"

        cmd = [
            ffmpeg_bin,
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
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if res.returncode != 0 or not base_render_path.exists():
            # Fallback for Windows FFmpeg builds without libass compiled in:
            # Render 9:16 crop + hook overlay + Pillow karaoke subtitle band
            self._render_with_pillow_karaoke_fallback(
                ffmpeg_bin=ffmpeg_bin,
                video_path=video.video_path,
                start_time=moment.start_time,
                duration=moment.duration,
                crop_vf=crop_vf,
                hook_png=hook_png,
                word_timings=word_timings,
                output_path=base_render_path,
            )

        # 6. If AI Voiceover mode, mix the narration over the ducked (-16dB) game audio
        if vo_audio_path and vo_audio_path.exists():
            AudioMixer.mix_voiceover_with_game_audio(
                portrait_video_path=base_render_path,
                voiceover_audio_path=vo_audio_path,
                output_video_path=final_mp4,
                game_volume_db=-16.0,
            )
            if base_render_path.exists() and base_render_path != final_mp4:
                base_render_path.unlink()

        return RenderedShort(
            clip_id=clip_id,
            output_path=final_mp4,
            moment=moment,
            hooks=hook_pkg,
            crop_geometry=crop_geom,
            audio_mode=audio_mode,
        )

    def _render_with_pillow_karaoke_fallback(
        self,
        ffmpeg_bin: str,
        video_path: Path,
        start_time: float,
        duration: float,
        crop_vf: str,
        hook_png: Path,
        word_timings: List[WordTiming],
        output_path: Path,
    ) -> None:
        """
        Pure Pillow + NumPy band compositor fallback for Windows FFmpeg binaries
        that do not include libass. Pre-renders the ~25 active-word karaoke bands
        and blends them onto the 1080x1920 video stream.
        """
        hook_rgba = np.array(Image.open(hook_png).convert("RGBA"))
        hook_y0, hook_y1 = 125, 430
        hook_band_rgb = hook_rgba[hook_y0:hook_y1, :, :3].astype(np.float32)
        hook_band_a = (hook_rgba[hook_y0:hook_y1, :, 3:4] / 255.0).astype(np.float32)

        sub_y0, sub_y1 = 1360, 1540
        sub_h = sub_y1 - sub_y0
        font = ImageFont.truetype(get_primary_font(), 58)
        chunks = AudioTranscriber.group_into_karaoke_chunks(word_timings, max_words_per_chunk=3)

        # Pre-render each active word state as a (sub_h, 1080, 4) RGBA band
        karaoke_Lookup = []
        for chunk in chunks:
            clean_words = [
                self.overlay_renderer.strip_unsupported_glyphs(w.word).upper() for w in chunk.words
            ]
            for active_idx, active_w in enumerate(chunk.words):
                band_img = Image.new("RGBA", (TARGET_WIDTH, sub_h), (0, 0, 0, 0))
                draw = ImageDraw.Draw(band_img)
                widths = []
                for cw in clean_words:
                    bb = draw.textbbox((0, 0), cw + " ", font=font, stroke_width=6)
                    widths.append(bb[2] - bb[0])
                total_w = sum(widths)
                cursor_x = (TARGET_WIDTH - total_w) // 2
                for idx, cw in enumerate(clean_words):
                    if not cw:
                        continue
                    fill_col = (255, 230, 0, 255) if idx == active_idx else (255, 255, 255, 255)
                    draw.text(
                        (cursor_x, 45),
                        cw,
                        font=font,
                        fill=fill_col,
                        stroke_width=6,
                        stroke_fill=(0, 0, 0, 255),
                    )
                    cursor_x += widths[idx]
                arr = np.array(band_img)
                karaoke_Lookup.append(
                    (
                        active_w.start,
                        active_w.end,
                        arr[:, :, :3].astype(np.float32),
                        (arr[:, :, 3:4] / 255.0).astype(np.float32),
                    )
                )

        # Decode 9:16 cropped frames from source and pipe blended frames to output MP4
        dec_cmd = [
            ffmpeg_bin,
            "-v",
            "error",
            "-ss",
            f"{start_time:.2f}",
            "-t",
            f"{duration:.2f}",
            "-i",
            str(video_path),
            "-vf",
            crop_vf,
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ]
        enc_cmd = [
            ffmpeg_bin,
            "-y",
            "-v",
            "error",
            "-f",
            "rawvideo",
            "-vcodec",
            "rawvideo",
            "-s",
            f"{TARGET_WIDTH}x{TARGET_HEIGHT}",
            "-pix_fmt",
            "rgb24",
            "-r",
            str(TARGET_FPS),
            "-i",
            "-",
            "-ss",
            f"{start_time:.2f}",
            "-t",
            f"{duration:.2f}",
            "-i",
            str(video_path),
            "-map",
            "0:v:0",
            "-map",
            "1:a?",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "21",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-shortest",
            str(output_path),
        ]

        dec_proc = subprocess.Popen(dec_cmd, stdout=subprocess.PIPE)
        enc_proc = subprocess.Popen(enc_cmd, stdin=subprocess.PIPE)
        assert dec_proc.stdout is not None and enc_proc.stdin is not None

        frame_bytes = TARGET_WIDTH * TARGET_HEIGHT * 3
        f_idx = 0
        while True:
            raw = dec_proc.stdout.read(frame_bytes)
            if len(raw) < frame_bytes:
                break
            frame = np.frombuffer(raw, dtype=np.uint8).reshape((TARGET_HEIGHT, TARGET_WIDTH, 3)).copy()
            t_sec = f_idx / TARGET_FPS

            # Blend Hook Banner band
            roi_h = frame[hook_y0:hook_y1].astype(np.float32)
            frame[hook_y0:hook_y1] = (roi_h * (1.0 - hook_band_a) + hook_band_rgb * hook_band_a).astype(
                np.uint8
            )

            # Blend active Karaoke word band
            for ws, we, sub_rgb, sub_a in karaoke_Lookup:
                if ws <= t_sec <= we:
                    roi_s = frame[sub_y0:sub_y1].astype(np.float32)
                    frame[sub_y0:sub_y1] = (roi_s * (1.0 - sub_a) + sub_rgb * sub_a).astype(np.uint8)
                    break

            enc_proc.stdin.write(frame.tobytes())
            f_idx += 1

        enc_proc.stdin.close()
        dec_proc.stdout.close()
        dec_proc.wait()
        enc_proc.wait()
