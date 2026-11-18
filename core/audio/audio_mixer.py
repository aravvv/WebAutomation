import subprocess
from pathlib import Path

from config.settings import get_ffmpeg_bin


class AudioMixer:
    """
    Mixes AI Hype Voiceover narration over the original GTA 6 clip audio,
    automatically ducking the original game audio (-16dB) so explosions and
    ambience remain audible underneath the voiceover.
    """

    @staticmethod
    def mix_voiceover_with_game_audio(
        portrait_video_path: Path,
        voiceover_audio_path: Path,
        output_video_path: Path,
        game_volume_db: float = -16.0,
    ) -> Path:
        ffmpeg_bin = get_ffmpeg_bin()
        volume_linear = 10 ** (game_volume_db / 20.0)
        filter_complex = (
            f"[0:a]volume={volume_linear:.4f}[game];"
            f"[1:a]volume=1.35[vo];"
            f"[game][vo]amix=inputs=2:duration=first:dropout_transition=2[aout]"
        )
        cmd = [
            ffmpeg_bin,
            "-y",
            "-v",
            "error",
            "-i",
            str(portrait_video_path),
            "-i",
            str(voiceover_audio_path),
            "-filter_complex",
            filter_complex,
            "-map",
            "0:v:0",
            "-map",
            "[aout]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            str(output_video_path),
        ]
        res = subprocess.run(cmd, capture_output=True)
        if res.returncode != 0:
            fallback_cmd = [
                ffmpeg_bin,
                "-y",
                "-v",
                "error",
                "-i",
                str(portrait_video_path),
                "-i",
                str(voiceover_audio_path),
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-shortest",
                str(output_video_path),
            ]
            subprocess.run(fallback_cmd, check=True, capture_output=True)
        return output_video_path
