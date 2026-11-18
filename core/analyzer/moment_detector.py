import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List

import numpy as np
import yaml

from config.settings import DEFAULT_CLIP_DURATION, PRESETS_PATH
from core.ingest.youtube_downloader import IngestedVideo


@dataclass
class CrazyMoment:
    rank: int
    start_time: float
    end_time: float
    viral_score: float  # 0.0 to 100.0
    category: str       # 'action_peak' | 'detail_discovery' | 'hype_reaction'
    reason: str
    matched_keywords: List[str] = field(default_factory=list)
    transcript_excerpt: str = ""
    signal_breakdown: Dict[str, float] = field(default_factory=dict)

    @property
    def duration(self) -> float:
        return round(self.end_time - self.start_time, 2)


class CrazyMomentDetector:
    """
    Multi-signal highlight detector that locates the craziest, highest-retention
    moments in a GTA 6 video by combining:
      1. YouTube 'Most Replayed' retention heatmap peaks
      2. Audio RMS excitement / explosion spikes (FFmpeg PCM -> NumPy)
      3. Visual action & motion intensity (FFmpeg raw grayscale -> NumPy frame diff)
      4. Transcript hype keywords ('insane', 'look at this', 'physics', 'police chase', etc.)
    """

    def __init__(self, presets_path: Path = PRESETS_PATH):
        if presets_path.exists():
            data = yaml.safe_load(presets_path.read_text()) or {}
        else:
            data = {}
        kw = data.get("hype_keywords", {})
        self.high_keywords = [k.lower() for k in kw.get("high_impact", [])]
        self.med_keywords = [k.lower() for k in kw.get("medium_impact", [])]

    def detect_top_moments(
        self,
        video: IngestedVideo,
        top_n: int = 3,
        clip_duration: float = DEFAULT_CLIP_DURATION,
    ) -> List[CrazyMoment]:
        total_sec = max(int(round(video.duration)), 1)
        # Adapt clip duration if source video is short (e.g., 36s demo or short teaser)
        if total_sec < clip_duration * 1.5:
            clip_win = max(8, int(total_sec // max(top_n + 1, 3)))
        else:
            clip_win = int(clip_duration)

        heatmap_arr = self._compute_heatmap_timeline(video, total_sec)
        audio_arr = self._compute_audio_rms_timeline(video.video_path, total_sec)
        motion_arr = self._compute_visual_motion_timeline(video.video_path, total_sec)
        kw_arr, kw_hits_per_sec = self._compute_keyword_timeline(video, total_sec)

        has_heatmap = bool(video.heatmap and np.max(heatmap_arr) > 0)
        if has_heatmap:
            weights = {"heatmap": 0.38, "audio": 0.22, "motion": 0.22, "keywords": 0.18}
        else:
            weights = {"heatmap": 0.0, "audio": 0.36, "motion": 0.38, "keywords": 0.26}

        composite = (
            weights["heatmap"] * heatmap_arr
            + weights["audio"] * audio_arr
            + weights["motion"] * motion_arr
            + weights["keywords"] * kw_arr
        )

        # Slide window of size `clip_win` across the timeline
        max_start = max(1, total_sec - clip_win + 1)
        window_scores = np.zeros(max_start, dtype=np.float32)
        for s in range(max_start):
            window_scores[s] = float(np.mean(composite[s : s + clip_win]))

        selected: List[CrazyMoment] = []
        used_mask = np.zeros(total_sec, dtype=bool)
        min_gap = max(2, clip_win // 2)

        for _ in range(top_n * 3):
            if len(selected) >= top_n:
                break
            best_s = int(np.argmax(window_scores))
            best_val = float(window_scores[best_s])
            if best_val <= -1.0:
                break

            end_s = min(total_sec, best_s + clip_win)
            # Suppress overlapping windows
            suppress_start = max(0, best_s - min_gap)
            suppress_end = min(max_start, end_s)
            window_scores[suppress_start:suppress_end] = -999.0

            if np.mean(used_mask[best_s:end_s]) > 0.35:
                continue
            used_mask[best_s:end_s] = True

            h_val = float(np.mean(heatmap_arr[best_s:end_s]))
            a_val = float(np.mean(audio_arr[best_s:end_s]))
            m_val = float(np.mean(motion_arr[best_s:end_s]))
            k_val = float(np.mean(kw_arr[best_s:end_s]))

            matched_kws: List[str] = []
            for sec_i in range(best_s, end_s):
                for kw in kw_hits_per_sec.get(sec_i, []):
                    if kw not in matched_kws:
                        matched_kws.append(kw)

            excerpt = self._extract_transcript_excerpt(video, float(best_s), float(end_s))
            category = self._classify_moment_category(m_val, k_val, matched_kws)
            reason = self._build_reason_summary(h_val, a_val, m_val, matched_kws)

            viral_score = round(min(99.8, max(55.0, best_val * 100.0)), 1)
            selected.append(
                CrazyMoment(
                    rank=len(selected) + 1,
                    start_time=float(best_s),
                    end_time=float(end_s),
                    viral_score=viral_score,
                    category=category,
                    reason=reason,
                    matched_keywords=matched_kws,
                    transcript_excerpt=excerpt,
                    signal_breakdown={
                        "heatmap": round(h_val, 3),
                        "audio_energy": round(a_val, 3),
                        "visual_motion": round(m_val, 3),
                        "keyword_hype": round(k_val, 3),
                    },
                )
            )

        return selected

    def _compute_heatmap_timeline(self, video: IngestedVideo, total_sec: int) -> np.ndarray:
        arr = np.zeros(total_sec, dtype=np.float32)
        if not video.heatmap:
            return arr
        for pt in video.heatmap:
            s = max(0, int(pt.start_time))
            e = min(total_sec, max(s + 1, int(round(pt.end_time))))
            arr[s:e] = np.maximum(arr[s:e], float(pt.value))
        if np.max(arr) > 0:
            arr = arr / np.max(arr)
        return arr

    def _compute_audio_rms_timeline(self, video_path: Path, total_sec: int) -> np.ndarray:
        arr = np.zeros(total_sec, dtype=np.float32)
        sample_rate = 8000
        cmd = [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(video_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(sample_rate),
            "-f",
            "s16le",
            "-",
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, check=False)
            if not res.stdout:
                return arr
            pcm = np.frombuffer(res.stdout, dtype=np.int16).astype(np.float32) / 32768.0
            for sec in range(total_sec):
                chunk = pcm[sec * sample_rate : (sec + 1) * sample_rate]
                if len(chunk) > 0:
                    arr[sec] = float(np.sqrt(np.mean(chunk * chunk)))
            if np.max(arr) > 1e-5:
                arr = arr / np.max(arr)
        except Exception:
            pass
        return arr

    def _compute_visual_motion_timeline(self, video_path: Path, total_sec: int) -> np.ndarray:
        arr = np.zeros(total_sec, dtype=np.float32)
        w, h, fps = 160, 90, 4
        frame_bytes = w * h
        cmd = [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(video_path),
            "-vf",
            f"fps={fps},scale={w}:{h}",
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
                return arr
            frames = np.frombuffer(raw[: n_frames * frame_bytes], dtype=np.uint8).reshape((n_frames, h, w)).astype(
                np.float32
            )
            diffs = np.mean(np.abs(frames[1:] - frames[:-1]), axis=(1, 2))
            for f_idx, d_val in enumerate(diffs):
                sec = min(total_sec - 1, f_idx // fps)
                arr[sec] = max(arr[sec], float(d_val))
            if np.max(arr) > 1e-5:
                arr = arr / np.max(arr)
        except Exception:
            pass
        return arr

    def _compute_keyword_timeline(
        self, video: IngestedVideo, total_sec: int
    ) -> tuple[np.ndarray, Dict[int, List[str]]]:
        arr = np.zeros(total_sec, dtype=np.float32)
        hits: Dict[int, List[str]] = {}
        for seg in video.transcript:
            lower_text = seg.text.lower()
            score = 0.0
            matched = []
            for kw in self.high_keywords:
                if kw in lower_text:
                    score += 0.45
                    matched.append(kw)
            for kw in self.med_keywords:
                if kw in lower_text:
                    score += 0.20
                    matched.append(kw)
            if score > 0:
                s = max(0, int(seg.start))
                e = min(total_sec, max(s + 1, int(round(seg.end))))
                arr[s:e] = np.maximum(arr[s:e], min(1.0, score))
                for sec_i in range(s, e):
                    hits.setdefault(sec_i, []).extend(matched)
        return arr, hits

    def _extract_transcript_excerpt(self, video: IngestedVideo, start_s: float, end_s: float) -> str:
        parts = []
        for seg in video.transcript:
            if seg.end >= start_s and seg.start <= end_s:
                parts.append(seg.text.strip())
        return " ".join(parts).strip()

    def _classify_moment_category(self, motion_val: float, kw_val: float, matched_kws: List[str]) -> str:
        detail_terms = {"detail", "secret", "hidden detail", "physics", "graphics", "realistic", "npc", "alligator"}
        if any(k in detail_terms for k in matched_kws):
            return "detail_discovery"
        if motion_val >= 0.65:
            return "action_peak"
        if kw_val >= 0.5:
            return "hype_reaction"
        return "action_peak"

    def _build_reason_summary(
        self, heatmap_val: float, audio_val: float, motion_val: float, matched_kws: List[str]
    ) -> str:
        reasons = []
        if heatmap_val >= 0.7:
            reasons.append(f"YouTube Most-Replayed Peak ({int(heatmap_val * 100)}%)")
        if motion_val >= 0.55:
            reasons.append(f"High Visual Action ({int(motion_val * 100)}%)")
        if audio_val >= 0.55:
            reasons.append(f"Audio Excitement Spike ({int(audio_val * 100)}%)")
        if matched_kws:
            top_kws = ", ".join(f"'{k}'" for k in matched_kws[:3])
            reasons.append(f"Hype Triggers ({top_kws})")
        return " + ".join(reasons) if reasons else "Peak Composite Retention Window"
