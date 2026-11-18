import re
import textwrap
from pathlib import Path
from typing import List

from PIL import Image, ImageDraw, ImageFont

from config.settings import TARGET_HEIGHT, TARGET_WIDTH, get_primary_font
from core.audio.transcriber import AudioTranscriber, WordTiming


class OverlayRenderer:
    """
    Renders:
      1. High-contrast Top Viral Hook Header PNG (1080x1920 RGBA) in the upper Shorts safe zone.
      2. Dynamic Word-by-Word Karaoke ASS Subtitle script (.ass) with active-word yellow/green
         highlight, thick black outline, and pop scaling in the lower Shorts safe zone.
    """

    def __init__(self, font_path: str = ""):
        self.font_path = font_path or get_primary_font()

    @staticmethod
    def strip_unsupported_glyphs(text: str) -> str:
        """
        Unescapes HTML entities (e.g. &gt;&gt;, &amp;), strips speaker markers (>>),
        and keeps clean ASCII/Latin punctuation so vector fonts render cleanly.
        """
        import html

        unescaped = html.unescape(text)
        unescaped = re.sub(r">>+|--+", " ", unescaped)
        cleaned = re.sub(r"[^\x20-\x7E]", "", unescaped).strip()
        return re.sub(r"\s+", " ", cleaned)

    def render_hook_banner_png(
        self,
        hook_text: str,
        output_png_path: Path,
        badge_label: str = "GTA VI VIRAL CLIP",
    ) -> Path:
        """
        Creates a 1080x1920 transparent RGBA overlay with a viral TikTok/Shorts hook header
        positioned in the top safe zone (y = 160..390).
        """
        img = Image.new("RGBA", (TARGET_WIDTH, TARGET_HEIGHT), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        clean_hook = self.strip_unsupported_glyphs(hook_text).upper()
        if not clean_hook:
            clean_hook = "WAIT TIL YOU SEE THIS GTA 6 DETAIL!"

        hook_font = ImageFont.truetype(self.font_path, 52)
        badge_font = ImageFont.truetype(self.font_path, 28)

        wrapped_lines = textwrap.wrap(clean_hook, width=22)[:3]
        line_height = 64
        box_h = 85 + len(wrapped_lines) * line_height
        box_y0 = 155
        box_y1 = box_y0 + box_h
        box_x0 = 64
        box_x1 = TARGET_WIDTH - 64

        # Rounded dark translucent card with Vice City neon pink/gold border
        draw.rounded_rectangle(
            [box_x0, box_y0, box_x1, box_y1],
            radius=28,
            fill=(12, 12, 20, 215),
            outline=(255, 45, 145, 255),
            width=5,
        )

        # Top pill badge ("GTA VI VIRAL CLIP")
        badge_w = 340
        badge_x0 = (TARGET_WIDTH - badge_w) // 2
        draw.rounded_rectangle(
            [badge_x0, box_y0 - 22, badge_x0 + badge_w, box_y0 + 26],
            radius=16,
            fill=(255, 215, 0, 255),
        )
        b_bbox = draw.textbbox((0, 0), badge_label, font=badge_font)
        b_tw = b_bbox[2] - b_bbox[0]
        draw.text(
            ((TARGET_WIDTH - b_tw) // 2, box_y0 - 14),
            badge_label,
            font=badge_font,
            fill=(15, 15, 20, 255),
        )

        # Draw wrapped hook lines with thick black stroke & alternating yellow highlight
        curr_y = box_y0 + 44
        for idx, line in enumerate(wrapped_lines):
            l_bbox = draw.textbbox((0, 0), line, font=hook_font, stroke_width=6)
            l_w = l_bbox[2] - l_bbox[0]
            x_pos = (TARGET_WIDTH - l_w) // 2
            text_color = (255, 255, 255, 255) if idx == 0 else (255, 230, 0, 255)
            draw.text(
                (x_pos, curr_y),
                line,
                font=hook_font,
                fill=text_color,
                stroke_width=6,
                stroke_fill=(0, 0, 0, 255),
            )
            curr_y += line_height

        output_png_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(output_png_path, "PNG")
        return output_png_path

    def generate_karaoke_ass_subtitles(
        self,
        words: List[WordTiming],
        output_ass_path: Path,
        max_words_per_chunk: int = 3,
    ) -> Path:
        """
        Generates an Advanced SubStation Alpha (.ass) file at 1080x1920 resolution
        where each word in a chunk lights up in bright Viral Yellow (&H0000E6FF&)
        during its spoken timestamp.
        """
        import os

        ass_font_name = "Arial Black" if os.name == "nt" else "Roboto Black"
        chunks = AudioTranscriber.group_into_karaoke_chunks(words, max_words_per_chunk=max_words_per_chunk)
        ass_header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {TARGET_WIDTH}
PlayResY: {TARGET_HEIGHT}
WrapStyle: 1
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Karaoke,{ass_font_name},68,&H00FFFFFF,&H0000E6FF,&H00000000,&H80000000,-1,0,0,0,100,100,1,0,1,6,3,2,70,70,440,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
        events = []
        for chunk in chunks:
            for active_idx, active_word in enumerate(chunk.words):
                start_ts = self._format_ass_time(active_word.start)
                end_ts = self._format_ass_time(active_word.end)

                styled_tokens = []
                for idx, w in enumerate(chunk.words):
                    clean_w = self.strip_unsupported_glyphs(w.word).upper()
                    if not clean_w:
                        continue
                    if idx == active_idx:
                        # Active spoken word: Bright Yellow (&H0000E6FF& in BGR) + 112% scale pop
                        styled_tokens.append(r"{\c&H0000E6FF&\fscx112\fscy112}" + clean_w + r"{\rKaraoke}")
                    else:
                        styled_tokens.append(clean_w)

                if styled_tokens:
                    line_text = " ".join(styled_tokens)
                    events.append(f"Dialogue: 0,{start_ts},{end_ts},Karaoke,,0,0,0,,{line_text}")

        output_ass_path.parent.mkdir(parents=True, exist_ok=True)
        output_ass_path.write_text(ass_header + "\n".join(events) + "\n", encoding="utf-8")
        return output_ass_path

    @staticmethod
    def _format_ass_time(seconds: float) -> str:
        seconds = max(0.0, seconds)
        hrs = int(seconds // 3600)
        mins = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        cs = int(round((seconds - int(seconds)) * 100))
        if cs >= 100:
            cs = 99
        return f"{hrs}:{mins:02d}:{secs:02d}.{cs:02d}"
