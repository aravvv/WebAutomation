import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple

import numpy as np

from config.settings import TARGET_FPS, TARGET_HEIGHT, TARGET_WIDTH


@dataclass
class CropGeometry:
    src_width: int
    src_height: int
    crop_width: int
    crop_height: int
    target_width: int = TARGET_WIDTH
    target_height: int = TARGET_HEIGHT
    center_x_trajectory: List[Tuple[float, int]] = None  # [(rel_sec, crop_x), ...]


class SmartPortraitCropper:
    """
    Strictly full-bleed 9:16 portrait cropper (crops landscape 16:9 directly into
    vertical 9:16 portrait without black bars or landscape-in-portrait padding).

    Includes smooth horizontal action-tracking camera pan using visual motion
    & saliency center-of-mass detection with Exponential Moving Average smoothing.
    """

    @staticmethod
    def compute_crop_dimensions(src_width: int, src_height: int) -> Tuple[int, int]:
        """
        Calculates exact 9:16 full-height crop window from a landscape frame.
        Ensures even dimensions for H.264 yuv420p compatibility.
        """
        target_aspect = 9.0 / 16.0
        src_aspect = src_width / max(src_height, 1)

        if src_aspect >= target_aspect:
            # Landscape or square source -> keep 100% of height, crop width to h * 9/16
            crop_h = src_height - (src_height % 2)
            crop_w = int(round(crop_h * target_aspect))
            crop_w -= crop_w % 2
        else:
            # Ultra-tall source -> keep 100% of width, crop height to w * 16/9
            crop_w = src_width - (src_width % 2)
            crop_h = int(round(crop_w / target_aspect))
            crop_h -= crop_h % 2

        return max(2, crop_w), max(2, crop_h)

    def analyze_action_trajectory(
        self,
        video_path: Path,
        start_time: float,
        end_time: float,
        src_width: int,
        src_height: int,
        crop_width: int,
    ) -> List[Tuple[float, int]]:
        """
        Samples the segment at 2 FPS, computes horizontal motion + contrast saliency,
        and returns a smoothed list of (relative_sec, crop_x) anchors.
        """
        duration = max(1.0, end_time - start_time)
        default_x = max(0, (src_width - crop_width) // 2)
        max_x = max(0, src_width - crop_width)
        if max_x == 0:
            return [(0.0, 0), (duration, 0)]

        sample_w, sample_h, sample_fps = 160, 90, 2
        frame_bytes = sample_w * sample_h
        cmd = [
            "ffmpeg",
            "-v",
            "error",
            "-ss",
            f"{start_time:.2f}",
            "-t",
            f"{duration:.2f}",
            "-i",
            str(video_path),
            "-vf",
            f"fps={sample_fps},scale={sample_w}:{sample_h}",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "gray",
            "-",
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, check=False)
            raw = res.stdout
            n_frames = len(raw) // frame_bytes
            if n_frames < 2:
                return [(0.0, default_x), (duration, default_x)]

            frames = (
                np.frombuffer(raw[: n_frames * frame_bytes], dtype=np.uint8)
                .reshape((n_frames, sample_h, sample_w))
                .astype(np.float32)
            )
            diffs = np.abs(frames[1:] - frames[:-1])
            # Also include horizontal gradient contrast so high-detail subjects attract focus
            grad_x = np.abs(frames[:-1, :, 1:] - frames[:-1, :, :-1])
            grad_padded = np.pad(grad_x, ((0, 0), (0, 0), (0, 1)), mode="edge")
            saliency = 0.7 * diffs + 0.3 * grad_padded

            col_coords = np.arange(sample_w, dtype=np.float32)
            smoothed_center = src_width / 2.0
            alpha = 0.28  # EMA smoothing factor to prevent camera jitter
            deadband_px = src_width * 0.03

            trajectory: List[Tuple[float, int]] = []
            for idx in range(saliency.shape[0]):
                col_weights = np.sum(saliency[idx], axis=0)
                total_w = float(np.sum(col_weights))
                if total_w > 1e-3:
                    com_sample = float(np.sum(col_coords * col_weights) / total_w)
                    target_center = (com_sample / sample_w) * src_width
                else:
                    target_center = src_width / 2.0

                if abs(target_center - smoothed_center) > deadband_px:
                    smoothed_center = (1.0 - alpha) * smoothed_center + alpha * target_center

                crop_x = int(round(smoothed_center - crop_width / 2.0))
                crop_x = max(0, min(max_x, crop_x))
                crop_x -= crop_x % 2
                rel_t = round(idx / sample_fps, 2)
                trajectory.append((rel_t, crop_x))

            return trajectory if trajectory else [(0.0, default_x)]
        except Exception:
            return [(0.0, default_x), (duration, default_x)]

    def build_ffmpeg_crop_filter(
        self,
        src_width: int,
        src_height: int,
        trajectory: List[Tuple[float, int]],
    ) -> Tuple[str, CropGeometry]:
        """
        Builds the FFmpeg video filter chain that crops full height to 9:16 aspect ratio
        with smooth horizontal interpolation and scales to 1080x1920 (lanczos).
        """
        crop_w, crop_h = self.compute_crop_dimensions(src_width, src_height)
        default_x = max(0, (src_width - crop_w) // 2)
        crop_y = max(0, (src_height - crop_h) // 2)

        if not trajectory or len(trajectory) == 1:
            x_expr = str(trajectory[0][1] if trajectory else default_x)
        else:
            # Downsample trajectory into key anchors every ~1.5s for a clean FFmpeg expression
            anchors = trajectory[::3]
            if anchors[-1][0] != trajectory[-1][0]:
                anchors.append(trajectory[-1])

            if len(anchors) == 1:
                x_expr = str(anchors[0][1])
            else:
                # Build smooth piecewise linear interpolation across anchors using FFmpeg `t`
                expr = str(anchors[-1][1])
                for i in range(len(anchors) - 2, -1, -1):
                    t0, x0 = anchors[i]
                    t1, x1 = anchors[i + 1]
                    dt = max(0.1, round(t1 - t0, 2))
                    dx = x1 - x0
                    seg_expr = f"({x0}+{dx}*clip((t-{t0})/{dt},0,1))"
                    expr = f"if(lt(t,{t1}),{seg_expr},{expr})"
                x_expr = expr

        vf_chain = (
            f"crop=w={crop_w}:h={crop_h}:x='{x_expr}':y={crop_y},"
            f"scale={TARGET_WIDTH}:{TARGET_HEIGHT}:flags=lanczos,"
            f"setsar=1:1,fps={TARGET_FPS}"
        )
        geom = CropGeometry(
            src_width=src_width,
            src_height=src_height,
            crop_width=crop_w,
            crop_height=crop_h,
            center_x_trajectory=trajectory,
        )
        return vf_chain, geom

    def crop_segment_to_portrait(
        self,
        video_path: Path,
        output_path: Path,
        start_time: float,
        end_time: float,
        src_width: int = 1920,
        src_height: int = 1080,
        track_action: bool = True,
    ) -> CropGeometry:
        """
        Directly crops a landscape video segment to a 1080x1920 (9:16) MP4 file.
        """
        crop_w, _ = self.compute_crop_dimensions(src_width, src_height)
        if track_action:
            trajectory = self.analyze_action_trajectory(
                video_path, start_time, end_time, src_width, src_height, crop_w
            )
        else:
            default_x = max(0, (src_width - crop_w) // 2)
            trajectory = [(0.0, default_x)]

        vf_chain, geom = self.build_ffmpeg_crop_filter(src_width, src_height, trajectory)
        duration = max(0.5, end_time - start_time)

        cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            f"{start_time:.2f}",
            "-t",
            f"{duration:.2f}",
            "-i",
            str(video_path),
            "-vf",
            vf_chain,
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
            str(output_path),
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        return geom

    @staticmethod
    def verify_portrait_video(video_path: Path) -> dict:
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,sample_aspect_ratio,display_aspect_ratio",
            "-of",
            "json",
            str(video_path),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        stream = json.loads(res.stdout).get("streams", [{}])[0]
        w = int(stream.get("width", 0))
        h = int(stream.get("height", 0))
        return {
            "width": w,
            "height": h,
            "is_9_16": (w == TARGET_WIDTH and h == TARGET_HEIGHT),
            "aspect_ratio": round(w / max(h, 1), 4),
        }
