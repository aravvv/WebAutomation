import json
import math
import os
import re
import shutil
import subprocess
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from config.settings import DOWNLOADS_DIR, get_primary_font


@dataclass
class HeatmapPoint:
    start_time: float
    end_time: float
    value: float  # 0.0 to 1.0 YouTube Most Replayed intensity


@dataclass
class TranscriptSegment:
    start: float
    end: float
    text: str
    words: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class IngestedVideo:
    video_id: str
    title: str
    source_url: str
    video_path: Path
    duration: float
    width: int
    height: int
    heatmap: List[HeatmapPoint] = field(default_factory=list)
    chapters: List[Dict[str, Any]] = field(default_factory=list)
    transcript: List[TranscriptSegment] = field(default_factory=list)
    description: str = ""


class YouTubeDownloader:
    """
    Downloads GTA 6 videos from YouTube via yt-dlp (100% free) along with:
      - Exact millisecond word-level captions from YouTube's WebVTT `<c>` stream
      - YouTube's 'Most Replayed' retention heatmap & chapters
      - Zero-drift section downloading for long videos
    """

    def __init__(self, download_dir: Path = DOWNLOADS_DIR):
        self.download_dir = Path(download_dir)
        self.download_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def find_yt_dlp_cmd() -> Optional[List[str]]:
        found = shutil.which("yt-dlp")
        if found:
            return [found]
        local_bin = Path.home() / ".local" / "bin" / "yt-dlp"
        if local_bin.exists():
            return [str(local_bin)]
        try:
            import sys
            import yt_dlp  # noqa: F401
            return [sys.executable, "-m", "yt_dlp"]
        except ImportError:
            return None

    @staticmethod
    def extract_video_id(url_or_path: str) -> str:
        patterns = [
            r"(?:v=|\/shorts\/|youtu\.be\/)([A-Za-z0-9_-]{11})",
        ]
        for pattern in patterns:
            match = re.search(pattern, url_or_path)
            if match:
                return match.group(1)
        clean = re.sub(r"[^A-Za-z0-9_-]", "_", Path(url_or_path).stem)
        return clean[:24] or "gta6_source"

    def probe_video_stream(self, video_path: Path) -> Dict[str, Any]:
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,r_frame_rate:format=duration,start_time",
            "-of",
            "json",
            str(video_path),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)
        data = json.loads(res.stdout)
        stream = data.get("streams", [{}])[0]
        fmt = data.get("format", {})
        return {
            "width": int(stream.get("width", 1920)),
            "height": int(stream.get("height", 1080)),
            "duration": float(fmt.get("duration", 0.0)),
            "start_time": float(fmt.get("start_time", 0.0)),
        }

    def ingest(self, url_or_path: str, use_demo_fallback: bool = False) -> IngestedVideo:
        if url_or_path.lower() in ("demo", "sample") or (
            use_demo_fallback and not os.path.exists(url_or_path) and not self._is_yt_dlp_available()
        ):
            return self.create_demo_gta6_landscape_video()

        local_candidate = Path(url_or_path)
        if local_candidate.exists() and local_candidate.is_file():
            return self._ingest_local_file(local_candidate)

        return self._download_from_youtube(url_or_path, use_demo_fallback=use_demo_fallback)

    def _is_yt_dlp_available(self) -> bool:
        return self.find_yt_dlp_cmd() is not None

    def _ingest_local_file(self, file_path: Path) -> IngestedVideo:
        info = self.probe_video_stream(file_path)
        meta_path = file_path.with_suffix(".info.json")
        heatmap: List[HeatmapPoint] = []
        transcript: List[TranscriptSegment] = []
        title = file_path.stem.replace("_", " ")

        if meta_path.exists():
            raw_meta = json.loads(meta_path.read_text(encoding="utf-8", errors="ignore"))
            title = raw_meta.get("title", title)
            for h in raw_meta.get("heatmap", []) or []:
                heatmap.append(
                    HeatmapPoint(
                        start_time=float(h.get("start_time", 0.0)),
                        end_time=float(h.get("end_time", 0.0)),
                        value=float(h.get("value", 0.0)),
                    )
                )
            for seg in raw_meta.get("transcript", []) or []:
                transcript.append(
                    TranscriptSegment(
                        start=float(seg["start"]),
                        end=float(seg["end"]),
                        text=str(seg["text"]),
                        words=seg.get("words", []),
                    )
                )

        if not transcript:
            transcript = self.parse_vtt_for_video(file_path.stem, offset=0.0, max_dur=info["duration"])

        return IngestedVideo(
            video_id=file_path.stem,
            title=title,
            source_url=str(file_path),
            video_path=file_path,
            duration=info["duration"],
            width=info["width"],
            height=info["height"],
            heatmap=heatmap,
            transcript=transcript,
        )

    def _download_from_youtube(self, url: str, use_demo_fallback: bool = False) -> IngestedVideo:
        video_id = self.extract_video_id(url)
        output_mp4 = self.download_dir / f"{video_id}.mp4"
        info_json_path = self.download_dir / f"{video_id}.info.json"
        offset_meta_path = self.download_dir / f"{video_id}.offset.json"
        yt_dlp_cmd = self.find_yt_dlp_cmd()

        if not yt_dlp_cmd:
            if use_demo_fallback:
                return self.create_demo_gta6_landscape_video(video_id=video_id, source_url=url)
            raise RuntimeError("yt-dlp not found. Run `pip install yt-dlp` to download YouTube URLs.")

        js_args = ["--js-runtimes", "node"] if shutil.which("node") else []

        # 1. Fetch metadata JSON if not cached
        if not info_json_path.exists():
            meta_cmd = yt_dlp_cmd + js_args + ["--dump-single-json", "--no-warnings", url]
            meta_res = subprocess.run(
                meta_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace"
            )
            if meta_res.returncode == 0 and meta_res.stdout.strip():
                info_json_path.write_text(meta_res.stdout, encoding="utf-8")

        info_dict = (
            json.loads(info_json_path.read_text(encoding="utf-8", errors="ignore"))
            if info_json_path.exists()
            else {}
        )
        full_duration = float(info_dict.get("duration") or 0.0)
        raw_heatmap = info_dict.get("heatmap") or []

        # 2. Determine section offset (start at 0.0 by default so VTT timestamps have zero keyframe drift!)
        section_offset = 0.0
        if offset_meta_path.exists():
            try:
                section_offset = float(
                    json.loads(offset_meta_path.read_text(encoding="utf-8")).get("section_offset", 0.0)
                )
            except Exception:
                section_offset = 0.0
        elif full_duration > 180.0 and raw_heatmap:
            best_pt = max(raw_heatmap, key=lambda x: float(x.get("value", 0.0)))
            peak_t = float(best_pt.get("start_time", 0.0))
            if peak_t > 90.0:
                section_offset = max(0.0, peak_t - 25.0)

        # 3. Download video (for >180s videos, grab 120s window starting at section_offset)
        if not output_mp4.exists():
            dl_cmd = (
                yt_dlp_cmd
                + js_args
                + [
                    "--no-warnings",
                    "-f",
                    "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/18/best",
                    "--merge-output-format",
                    "mp4",
                    "--write-subs",
                    "--write-auto-subs",
                    "--sub-langs",
                    "en.*",
                ]
            )
            if full_duration > 180.0:
                sec_end = min(full_duration, section_offset + 120.0)
                dl_cmd.extend(["--download-sections", f"*{int(section_offset)}-{int(sec_end)}"])

            dl_cmd.extend(["-o", str(self.download_dir / f"{video_id}.%(ext)s"), url])
            res = subprocess.run(dl_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
            if res.returncode != 0 and not output_mp4.exists():
                if use_demo_fallback:
                    return self.create_demo_gta6_landscape_video(video_id=video_id, source_url=url)
                raise RuntimeError(f"yt-dlp failed to download {url}: {res.stderr}")
            offset_meta_path.write_text(json.dumps({"section_offset": section_offset}), encoding="utf-8")

        stream_info = self.probe_video_stream(output_mp4)
        clip_dur = stream_info["duration"]

        heatmap_points: List[HeatmapPoint] = []
        for h in raw_heatmap:
            hs = float(h.get("start_time", 0.0)) - section_offset
            he = float(h.get("end_time", 0.0)) - section_offset
            if he > 0 and hs < clip_dur:
                heatmap_points.append(
                    HeatmapPoint(
                        start_time=max(0.0, hs),
                        end_time=min(clip_dur, he),
                        value=float(h.get("value", 0.0)),
                    )
                )

        transcript_segments = self.parse_vtt_for_video(video_id, offset=section_offset, max_dur=clip_dur)

        return IngestedVideo(
            video_id=video_id,
            title=info_dict.get("title", f"GTA 6 Video ({video_id})"),
            source_url=url,
            video_path=output_mp4,
            duration=clip_dur,
            width=stream_info["width"],
            height=stream_info["height"],
            heatmap=heatmap_points,
            chapters=info_dict.get("chapters") or [],
            transcript=transcript_segments,
            description=info_dict.get("description", ""),
        )

    def parse_vtt_for_video(
        self, video_id: str, offset: float = 0.0, max_dur: float = 99999.0
    ) -> List[TranscriptSegment]:
        """
        High-precision WebVTT parser that extracts YouTube's inline `<HH:MM:SS.mmm><c> word</c>`
        millisecond word-level timestamps and eliminates rollover duplicate lines.
        """
        vtt_candidates = sorted(self.download_dir.glob(f"{video_id}*.vtt"))
        if not vtt_candidates:
            return []
        vtt_text = vtt_candidates[0].read_text(encoding="utf-8", errors="ignore")

        def to_sec(ts: str) -> float:
            parts = [float(p) for p in ts.split(":")]
            if len(parts) == 3:
                return parts[0] * 3600.0 + parts[1] * 60.0 + parts[2]
            return parts[0] * 60.0 + parts[1]

        cue_re = re.compile(
            r"(\d{2}:\d{2}:\d{2}\.\d{3}|\d{2}:\d{2}\.\d{3})\s*-->\s*"
            r"(\d{2}:\d{2}:\d{2}\.\d{3}|\d{2}:\d{2}\.\d{3})[^\n]*\n([\s\S]*?)(?=\n\n|\Z)"
        )
        tag_re = re.compile(r"<(\d{2}:\d{2}:\d{2}\.\d{3}|\d{2}:\d{2}\.\d{3})><c>(.*?)</c>")

        has_inline_word_tags = "<c>" in vtt_text
        segments: List[TranscriptSegment] = []

        for m in cue_re.finditer(vtt_text):
            c_start = to_sec(m.group(1)) - offset
            c_end = to_sec(m.group(2)) - offset
            body = m.group(3)

            # Skip 10ms YouTube rollover freeze cues
            if c_end - c_start <= 0.025:
                continue
            if c_end <= 0.0 or c_start >= max_dur:
                continue

            if has_inline_word_tags:
                for line in body.splitlines():
                    if "<c>" not in line:
                        # If a cue line in an auto-caption file has no <c> tags,
                        # check if it's a single-word or non-repeated line not seen in the previous segment
                        continue

                    import html

                    first_raw = html.unescape(re.sub(r"<[^>]+>", "", line.split("<")[0]))
                    first_raw = re.sub(r">>+|--+", "", first_raw).strip()
                    tags = list(tag_re.finditer(line))
                    line_words: List[Dict[str, Any]] = []

                    if first_raw and tags:
                        w0_end = to_sec(tags[0].group(1)) - offset
                        if w0_end > 0 and c_start < max_dur:
                            line_words.append(
                                {
                                    "word": first_raw,
                                    "start": round(max(0.0, c_start), 3),
                                    "end": round(min(max_dur, max(c_start + 0.05, w0_end)), 3),
                                }
                            )

                    for idx, tm in enumerate(tags):
                        w_txt = html.unescape(re.sub(r"<[^>]+>", "", tm.group(2)))
                        w_txt = re.sub(r">>+|--+", "", w_txt).strip()
                        if not w_txt:
                            continue
                        w_s = to_sec(tm.group(1)) - offset
                        w_e = (
                            (to_sec(tags[idx + 1].group(1)) - offset)
                            if (idx + 1 < len(tags))
                            else c_end
                        )
                        if w_e <= 0.0 or w_s >= max_dur:
                            continue
                        line_words.append(
                            {
                                "word": w_txt,
                                "start": round(max(0.0, w_s), 3),
                                "end": round(min(max_dur, max(w_s + 0.05, w_e)), 3),
                            }
                        )

                    if line_words:
                        seg_text = " ".join(w["word"] for w in line_words)
                        segments.append(
                            TranscriptSegment(
                                start=line_words[0]["start"],
                                end=line_words[-1]["end"],
                                text=seg_text,
                                words=line_words,
                            )
                        )
            else:
                # Standard manual VTT without <c> tags
                clean_lines = []
                for line in body.splitlines():
                    cl = re.sub(r"<[^>]+>", "", line).strip()
                    if cl and (not segments or cl not in segments[-1].text):
                        clean_lines.append(cl)
                full_text = " ".join(clean_lines).strip()
                if full_text:
                    s_clamp = max(0.0, c_start)
                    e_clamp = min(max_dur, c_end)
                    raw_tokens = full_text.split()
                    char_weights = [max(2, len(t)) for t in raw_tokens]
                    tot_w = max(1, sum(char_weights))
                    span = max(0.2, e_clamp - s_clamp)
                    cursor = s_clamp
                    line_words = []
                    for tok, cw in zip(raw_tokens, char_weights):
                        dur = span * (cw / tot_w)
                        line_words.append(
                            {
                                "word": tok,
                                "start": round(cursor, 3),
                                "end": round(min(max_dur, cursor + dur), 3),
                            }
                        )
                        cursor += dur
                    segments.append(
                        TranscriptSegment(
                            start=s_clamp,
                            end=e_clamp,
                            text=full_text,
                            words=line_words,
                        )
                    )

        return segments

    def create_demo_gta6_landscape_video(
        self,
        video_id: str = "gta6_trailer_demo",
        source_url: str = "https://www.youtube.com/watch?v=QdBZY2fkU-0",
        duration: float = 36.0,
    ) -> IngestedVideo:
        video_path = self.download_dir / f"{video_id}.mp4"
        meta_path = self.download_dir / f"{video_id}.info.json"

        heatmap = [
            HeatmapPoint(0.0, 6.0, 0.18),
            HeatmapPoint(6.0, 11.0, 0.96),
            HeatmapPoint(11.0, 16.0, 0.89),
            HeatmapPoint(16.0, 21.0, 0.22),
            HeatmapPoint(21.0, 26.0, 0.94),
            HeatmapPoint(26.0, 31.0, 0.85),
            HeatmapPoint(31.0, 36.0, 0.20),
        ]

        transcript = [
            TranscriptSegment(
                start=0.5,
                end=5.8,
                text="Welcome back to our full GTA 6 trailer breakdown in Vice City.",
            ),
            TranscriptSegment(
                start=6.0,
                end=11.0,
                text="Wait for it, look at this insane GTA 6 police chase physics detail right here!",
            ),
            TranscriptSegment(
                start=11.0,
                end=15.8,
                text="Lucia drifts across the highway, and the realistic water and tire smoke are crazy!",
            ),
            TranscriptSegment(
                start=16.2,
                end=20.8,
                text="Now let us check out the Leonida swamp area in slow motion.",
            ),
            TranscriptSegment(
                start=21.0,
                end=26.2,
                text="No way, look at that alligator entering the store and the insane NPC reaction!",
            ),
            TranscriptSegment(
                start=26.2,
                end=31.0,
                text="Rockstar Games made every single GTA 6 reflection and explosion look unbelievable!",
            ),
            TranscriptSegment(
                start=31.2,
                end=35.5,
                text="Let me know in the comments if you noticed these hidden details.",
            ),
        ]

        for seg in transcript:
            words = seg.text.split()
            if words:
                w_dur = (seg.end - seg.start) / len(words)
                seg.words = [
                    {
                        "word": w,
                        "start": round(seg.start + idx * w_dur, 3),
                        "end": round(seg.start + (idx + 1) * w_dur, 3),
                    }
                    for idx, w in enumerate(words)
                ]

        if not video_path.exists():
            self._render_synthetic_landscape_mp4(video_path, duration=duration)
            meta_payload = {
                "title": "Grand Theft Auto VI - Official Trailer & Crazy Hidden Details Breakdown",
                "heatmap": [
                    {"start_time": h.start_time, "end_time": h.end_time, "value": h.value}
                    for h in heatmap
                ],
                "transcript": [
                    {"start": s.start, "end": s.end, "text": s.text, "words": s.words}
                    for s in transcript
                ],
            }
            meta_path.write_text(json.dumps(meta_payload, indent=2), encoding="utf-8")

        return IngestedVideo(
            video_id=video_id,
            title="Grand Theft Auto VI - Official Trailer & Crazy Hidden Details Breakdown",
            source_url=source_url,
            video_path=video_path,
            duration=duration,
            width=1920,
            height=1080,
            heatmap=heatmap,
            transcript=transcript,
            description="Full GTA 6 breakdown of Vice City physics, Lucia & Jason chase, and Leonida secrets.",
        )

    def _render_synthetic_landscape_mp4(self, output_path: Path, duration: float = 36.0) -> None:
        fps = 15
        width, height = 1920, 1080
        total_frames = int(duration * fps)
        wav_path = output_path.with_suffix(".wav")

        sample_rate = 16000
        t = np.linspace(0, duration, int(sample_rate * duration), endpoint=False)
        env = np.full_like(t, 0.12)
        crazy_mask_1 = (t >= 6.0) & (t <= 16.0)
        crazy_mask_2 = (t >= 21.0) & (t <= 31.0)
        env[crazy_mask_1] = 0.88
        env[crazy_mask_2] = 0.92

        carrier = (
            0.5 * np.sin(2 * np.pi * 180 * t)
            + 0.3 * np.sin(2 * np.pi * 440 * t * (1 + 0.2 * np.sin(2 * np.pi * 3 * t)))
            + 0.2 * np.sin(2 * np.pi * 880 * t)
        )
        pcm = np.clip(carrier * env * 32000, -32767, 32767).astype(np.int16)
        with wave.open(str(wav_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(pcm.tobytes())

        font = ImageFont.truetype(get_primary_font(), 54)
        sub_font = ImageFont.truetype(get_primary_font(), 36)

        cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "rawvideo",
            "-vcodec",
            "rawvideo",
            "-s",
            f"{width}x{height}",
            "-pix_fmt",
            "rgb24",
            "-r",
            str(fps),
            "-i",
            "-",
            "-i",
            str(wav_path),
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-r",
            "30",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-shortest",
            str(output_path),
        ]

        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
        assert proc.stdin is not None

        for f_idx in range(total_frames):
            sec = f_idx / fps
            is_crazy_1 = 6.0 <= sec <= 16.0
            is_crazy_2 = 21.0 <= sec <= 31.0

            img = Image.new("RGB", (width, height), (18, 12, 36))
            draw = ImageDraw.Draw(img)

            for gx in range(0, width, 160):
                draw.line([(gx, 0), (gx, height)], fill=(40, 28, 68), width=2)
            for gy in range(0, height, 120):
                draw.line([(0, gy), (width, gy)], fill=(40, 28, 68), width=2)

            if is_crazy_1 or is_crazy_2:
                phase = sec * 2.4
                hotspot_x = int(width * 0.5 + math.sin(phase) * 420)
                hotspot_y = int(height * 0.52 + math.cos(phase * 1.5) * 90)
                pulse_r = 210 + int(45 * math.sin(f_idx * 1.3))
                color_main = (255, 50, 140) if is_crazy_1 else (0, 230, 180)
                color_ring = (255, 220, 0) if (f_idx % 2 == 0) else (255, 90, 0)

                draw.ellipse(
                    [hotspot_x - pulse_r, hotspot_y - pulse_r, hotspot_x + pulse_r, hotspot_y + pulse_r],
                    fill=color_main,
                    outline=color_ring,
                    width=14,
                )
                draw.rectangle(
                    [hotspot_x - 130, hotspot_y - 80, hotspot_x + 130, hotspot_y + 80],
                    fill=(255, 255, 255),
                    outline=(20, 20, 20),
                    width=6,
                )
                scene_label = (
                    "VICE CITY POLICE CHASE [ACTION PEAK]"
                    if is_crazy_1
                    else "LEONIDA GATOR & PHYSICS [CRAZY DETAIL]"
                )
                draw.text((hotspot_x - 260, hotspot_y + 110), scene_label, fill=(255, 240, 100), font=sub_font)
            else:
                draw.ellipse([860, 440, 1060, 640], fill=(70, 90, 140), outline=(120, 150, 210), width=4)
                draw.text((690, 680), "GTA VI - VICE CITY INTRO SCENE", fill=(180, 190, 220), font=sub_font)

            draw.text((60, 45), f"GTA VI 16:9 SOURCE | t={sec:04.1f}s", fill=(255, 120, 210), font=font)
            proc.stdin.write(img.tobytes())

        proc.stdin.close()
        proc.wait()
        if wav_path.exists():
            wav_path.unlink()
