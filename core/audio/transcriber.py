import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import numpy as np

from config.settings import get_ffmpeg_bin
from core.ingest.youtube_downloader import IngestedVideo


@dataclass
class WordTiming:
    word: str
    start: float  # seconds relative to clip start (0.0 .. clip_duration)
    end: float


@dataclass
class KaraokeChunk:
    start: float
    end: float
    words: List[WordTiming] = field(default_factory=list)


class AudioTranscriber:
    """
    High-Precision Subtitle Engine:
      1. Extracts millisecond word-level timings from YouTube WebVTT `<c>` tags or faster-whisper.
      2. Refines word onset/offset against the 100Hz (10ms) Audio RMS Speech Energy (VAD) envelope
         so words never linger during silent pauses.
      3. Groups words into natural phrase & punctuation-aware Karaoke Chunks.
    """

    def __init__(self, model_size: str = "tiny.en"):
        self.model_size = model_size
        self._whisper_model = None

    def extract_clip_word_timings(
        self,
        video: IngestedVideo,
        start_time: float,
        end_time: float,
        override_script: Optional[str] = None,
        audio_path_for_whisper: Optional[Path] = None,
    ) -> List[WordTiming]:
        clip_dur = max(1.0, end_time - start_time)

        # 1. If AI voiceover script is provided, align words to the voiceover audio envelope
        if override_script:
            words = self.align_text_to_duration(override_script, clip_dur)
            if audio_path_for_whisper and audio_path_for_whisper.exists():
                return self.refine_with_audio_vad(words, audio_path_for_whisper, 0.0, clip_dur)
            return words

        # 2. Try local faster-whisper if installed and audio_path provided
        if audio_path_for_whisper and audio_path_for_whisper.exists():
            whisper_words = self._try_faster_whisper(audio_path_for_whisper)
            if whisper_words:
                return self.refine_with_audio_vad(whisper_words, audio_path_for_whisper, 0.0, clip_dur)

        # 3. Extract millisecond word timings from IngestedVideo transcript/VTT
        words_out: List[WordTiming] = []
        for seg in video.transcript:
            if seg.end < start_time or seg.start > end_time:
                continue
            if seg.words:
                for w_obj in seg.words:
                    ws = float(w_obj["start"]) - start_time
                    we = float(w_obj["end"]) - start_time
                    if we > 0.02 and ws < clip_dur - 0.02:
                        w_start = max(0.0, round(ws, 3))
                        # Clamp max single-word hold to 0.70s so captions don't freeze across pauses
                        w_end = min(clip_dur, round(min(we, ws + 0.70), 3))
                        if w_end > w_start:
                            words_out.append(WordTiming(word=str(w_obj["word"]), start=w_start, end=w_end))
            else:
                seg_words = seg.text.split()
                if not seg_words:
                    continue
                s_clamped = max(start_time, seg.start) - start_time
                e_clamped = min(end_time, seg.end) - start_time
                span = max(0.3, e_clamped - s_clamped)
                step = span / len(seg_words)
                for idx, w_str in enumerate(seg_words):
                    ws = round(s_clamped + idx * step, 3)
                    we = round(min(s_clamped + (idx + 1) * step, ws + 0.65), 3)
                    words_out.append(WordTiming(word=w_str, start=ws, end=we))

        if words_out:
            return self.refine_with_audio_vad(words_out, video.video_path, start_time, clip_dur)

        fallback_text = "WAIT FOR IT! LOOK AT THIS INSANE GTA 6 DETAIL IN VICE CITY!"
        return self.align_text_to_duration(fallback_text, min(clip_dur, 8.0))

    def refine_with_audio_vad(
        self,
        words: List[WordTiming],
        media_path: Path,
        start_time: float,
        clip_dur: float,
    ) -> List[WordTiming]:
        """
        Extracts 100Hz (10ms) RMS audio envelope of the clip and snaps word start/end
        timings so words don't appear during silence or overlap into the next word.
        """
        if not words:
            return words

        env_100hz = self._extract_100hz_rms_envelope(media_path, start_time, clip_dur)
        if env_100hz is None or len(env_100hz) < 10:
            return self._enforce_monotonic_non_overlapping(words, clip_dur)

        # Voice activity threshold (15% of 90th percentile energy)
        p90 = float(np.percentile(env_100hz, 90)) if len(env_100hz) > 0 else 0.0
        vad_thresh = max(0.015, p90 * 0.14)

        refined: List[WordTiming] = []
        for idx, w in enumerate(words):
            s_idx = max(0, min(len(env_100hz) - 1, int(round(w.start * 100))))
            e_idx = max(s_idx + 1, min(len(env_100hz), int(round(w.end * 100))))

            # Trim trailing silence at the end of a phrase (when gap to next word > 0.25s)
            next_start = words[idx + 1].start if (idx + 1 < len(words)) else clip_dur
            if next_start - w.end >= 0.20 and (e_idx - s_idx) > 18:
                while e_idx > s_idx + 12 and env_100hz[e_idx - 1] < vad_thresh:
                    e_idx -= 1

            new_s = round(s_idx / 100.0, 3)
            new_e = round(max(new_s + 0.08, min(clip_dur, e_idx / 100.0)), 3)
            refined.append(WordTiming(word=w.word, start=new_s, end=new_e))

        return self._enforce_monotonic_non_overlapping(refined, clip_dur)

    @staticmethod
    def _enforce_monotonic_non_overlapping(words: List[WordTiming], clip_dur: float) -> List[WordTiming]:
        cleaned: List[WordTiming] = []
        for idx, w in enumerate(words):
            s = max(0.0, w.start)
            if cleaned and s < cleaned[-1].end:
                s = cleaned[-1].end
            next_s = words[idx + 1].start if (idx + 1 < len(words)) else clip_dur
            e = min(clip_dur, max(s + 0.06, min(w.end, max(s + 0.06, next_s))))
            # Never hold a single word longer than 0.75s
            e = min(e, s + 0.75)
            cleaned.append(WordTiming(word=w.word, start=round(s, 3), end=round(e, 3)))
        return cleaned

    @staticmethod
    def _extract_100hz_rms_envelope(
        media_path: Path, start_time: float, duration: float
    ) -> Optional[np.ndarray]:
        sample_rate = 16000
        hop = sample_rate // 100  # 160 samples = 10ms
        cmd = [
            get_ffmpeg_bin(),
            "-v",
            "error",
            "-ss",
            f"{start_time:.3f}",
            "-t",
            f"{duration:.3f}",
            "-i",
            str(media_path),
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
                return None
            pcm = np.frombuffer(res.stdout, dtype=np.int16).astype(np.float32) / 32768.0
            n_frames = len(pcm) // hop
            if n_frames < 2:
                return None
            frames = pcm[: n_frames * hop].reshape((n_frames, hop))
            rms = np.sqrt(np.mean(frames * frames, axis=1))
            return rms
        except Exception:
            return None

    @staticmethod
    def align_text_to_duration(text: str, duration: float) -> List[WordTiming]:
        words = [w.strip() for w in text.split() if w.strip()]
        if not words:
            return []
        usable_dur = max(1.0, duration * 0.92)
        # Add pause weight for words ending with punctuation
        weights = []
        for w in words:
            base = max(2, len(w))
            if w.endswith((".", "!", "?")):
                base += 3
            elif w.endswith((",", ";", ":")):
                base += 1.5
            weights.append(base)
        total_weight = sum(weights)
        cursor = 0.12
        result: List[WordTiming] = []
        for w, weight in zip(words, weights):
            slot_dur = usable_dur * (weight / total_weight)
            speak_dur = min(slot_dur, 0.60) if w.endswith((".", "!", "?")) else slot_dur
            result.append(
                WordTiming(
                    word=w,
                    start=round(cursor, 3),
                    end=round(min(duration, cursor + speak_dur), 3),
                )
            )
            cursor += slot_dur
        return result

    @staticmethod
    def group_into_karaoke_chunks(
        words: List[WordTiming],
        max_words_per_chunk: int = 3,
        max_chars_per_chunk: int = 20,
        pause_threshold_sec: float = 0.28,
    ) -> List[KaraokeChunk]:
        """
        Natural phrase-aware chunker:
        Splits chunks whenever:
          - There is a speech pause >= 0.28s between words
          - A word ends with sentence/clause punctuation (., !, ?, ;, :)
          - Adding the next word exceeds `max_words_per_chunk` (3) or `max_chars_per_chunk` (20)
        """
        chunks: List[KaraokeChunk] = []
        current_group: List[WordTiming] = []
        current_chars = 0

        for idx, w in enumerate(words):
            if current_group:
                prev_w = current_group[-1]
                gap = w.start - prev_w.end
                prev_ends_sentence = prev_w.word.endswith((".", "!", "?", ";", ":"))
                prev_ends_comma = prev_w.word.endswith(",") and len(current_group) >= 2
                char_overflow = (current_chars + 1 + len(w.word)) > max_chars_per_chunk
                word_overflow = len(current_group) >= max_words_per_chunk

                if gap >= pause_threshold_sec or prev_ends_sentence or prev_ends_comma or char_overflow or word_overflow:
                    chunks.append(
                        KaraokeChunk(
                            start=current_group[0].start,
                            end=current_group[-1].end,
                            words=current_group,
                        )
                    )
                    current_group = []
                    current_chars = 0

            current_group.append(w)
            current_chars += len(w.word) + (1 if current_chars > 0 else 0)

        if current_group:
            chunks.append(
                KaraokeChunk(
                    start=current_group[0].start,
                    end=current_group[-1].end,
                    words=current_group,
                )
            )

        return chunks

    def _try_faster_whisper(self, audio_path: Path) -> List[WordTiming]:
        try:
            from faster_whisper import WhisperModel

            if self._whisper_model is None:
                self._whisper_model = WhisperModel(self.model_size, device="cpu", compute_type="int8")
            segments, _ = self._whisper_model.transcribe(
                str(audio_path),
                word_timestamps=True,
                vad_filter=True,
            )
            words: List[WordTiming] = []
            for seg in segments:
                for w in seg.words or []:
                    words.append(
                        WordTiming(
                            word=w.word.strip(),
                            start=round(float(w.start), 3),
                            end=round(float(w.end), 3),
                        )
                    )
            return words
        except Exception:
            return []
