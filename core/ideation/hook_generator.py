from dataclasses import dataclass, field
from pathlib import Path
from typing import List

import yaml

from config.settings import PRESETS_PATH
from core.analyzer.moment_detector import CrazyMoment


@dataclass
class ViralHookPackage:
    primary_hook: str
    hook_options: List[str]
    primary_title: str
    title_options: List[str]
    description: str
    hashtags: List[str]
    youtube_tags: List[str]
    ai_voiceover_script: str
    detected_topic: str = "GTA 6 Crazy Detail"


class ViralHookGenerator:
    """
    100% Free GTA 6 Viral Hook, Title, Hashtag & AI Hype Script Generator.
    Uses contextual keyword analysis from the clipped moment to craft:
      - 5 Crazy On-Screen Viral Hook Overlays
      - 5 High-CTR YouTube Shorts Titles
      - Full SEO Description + High-Velocity GTA 6 Hashtags & Tags
      - Custom AI Hype Commentary Script
    """

    def __init__(self, presets_path: Path = PRESETS_PATH):
        if presets_path.exists():
            self.presets = yaml.safe_load(presets_path.read_text()) or {}
        else:
            self.presets = {}

    def generate_for_moment(self, moment: CrazyMoment, source_title: str = "GTA 6") -> ViralHookPackage:
        topic_upper, topic_title = self._infer_specific_topic(moment)
        cat_hooks = self.presets.get("viral_hook_templates", {}).get(moment.category, [])

        # Dynamic context-aware hooks tailored to the exact moment
        custom_hooks = [
            f"WAIT TIL YOU SEE THIS GTA 6 {topic_upper} 💀",
            f"99% OF PLAYERS MISSED THIS {topic_upper} 🤯",
            f"GTA 6 {topic_upper} IS ACTUALLY UNREAL 🔥",
            f"ROCKSTAR WENT CRAZY WITH THIS {topic_upper} 😱",
            f"NOBODY NOTICED THIS IN GTA 6... 👀",
        ]

        # Blend custom topic hooks with preset category templates
        hook_options: List[str] = []
        for h in custom_hooks[:3] + cat_hooks[:3]:
            if h not in hook_options:
                hook_options.append(h)
        hook_options = hook_options[:5]
        primary_hook = hook_options[0]

        # Build 5 high-CTR YouTube Shorts titles
        title_options = [
            f"{primary_hook} #gta6 #shorts",
            f"Nobody noticed this crazy {topic_title} in GTA 6... 🤯 #gta6 #shorts",
            f"Rockstar went INSANE with {topic_title} in GTA 6 💀 #gta6 #gaming #shorts",
            f"Wait until the end of this GTA 6 clip... 🔥 ({topic_title}) #gta6 #shorts",
            f"GTA 5 vs GTA 6 {topic_title} is NOT even close 🏆 #gta6 #shorts",
        ]
        primary_title = title_options[0]

        # Build Hashtag Pack
        hp = self.presets.get("hashtag_packs", {})
        hashtags: List[str] = []
        for group in ("core_gta6", "viral_gaming", "detail_focus"):
            for tag in hp.get(group, []):
                if tag not in hashtags:
                    hashtags.append(tag)
        if not hashtags:
            hashtags = [
                "#gta6",
                "#gta",
                "#grandtheftauto6",
                "#rockstargames",
                "#gtashorts",
                "#vicecity",
                "#gaming",
                "#shorts",
            ]

        hashtag_line = " ".join(hashtags[:12])
        description = (
            f"{primary_hook}\n\n"
            f"Did you catch this crazy {topic_title} moment in GTA 6? "
            f"Clipped from timestamp {int(moment.start_time)}s–{int(moment.end_time)}s "
            f"(Viral Score: {moment.viral_score}/100 — {moment.reason}).\n\n"
            f"Drop a comment if you noticed this detail and SUBSCRIBE for daily GTA 6 Shorts! 🔥\n\n"
            f"Source Context: {source_title}\n\n"
            f"{hashtag_line}"
        )

        youtube_tags = list(self.presets.get("default_tags", []))
        for kw in moment.matched_keywords[:5]:
            tag_candidate = f"GTA 6 {kw.title()}"
            if tag_candidate not in youtube_tags:
                youtube_tags.append(tag_candidate)

        ai_script = self._build_ai_commentary(moment, topic_title)

        return ViralHookPackage(
            primary_hook=primary_hook,
            hook_options=hook_options,
            primary_title=primary_title,
            title_options=title_options,
            description=description,
            hashtags=hashtags[:12],
            youtube_tags=youtube_tags,
            ai_voiceover_script=ai_script,
            detected_topic=topic_title,
        )

    def _infer_specific_topic(self, moment: CrazyMoment) -> tuple[str, str]:
        kws = [k.lower() for k in moment.matched_keywords]
        excerpt_lower = moment.transcript_excerpt.lower()
        combined = " ".join(kws) + " " + excerpt_lower

        topic_map = [
            (["police chase", "chase", "drift", "highway"], ("POLICE CHASE", "Police Chase Physics")),
            (["alligator", "gator", "swamp", "leonida"], ("LEONIDA GATOR SCENE", "Leonida Alligator Scene")),
            (["water physics", "water", "ocean", "beach"], ("WATER PHYSICS", "Water Physics")),
            (["lucia", "jason"], ("LUCIA & JASON MOMENT", "Lucia & Jason Scene")),
            (["npc", "ai", "reaction"], ("NPC AI DETAIL", "NPC AI Reaction")),
            (["explosion", "crash", "car"], ("EXPLOSION & PHYSICS", "Explosion Physics")),
            (["physics", "realistic", "graphics"], ("NEXT-GEN PHYSICS", "Next-Gen Physics")),
            (["vice city"], ("VICE CITY SECRET", "Vice City Secret")),
        ]
        for triggers, (upper_lbl, title_lbl) in topic_map:
            if any(t in combined for t in triggers):
                return upper_lbl, title_lbl

        if moment.category == "detail_discovery":
            return "HIDDEN DETAIL", "Hidden Detail"
        return "CRAZY MOMENT", "Crazy Moment"

    def _build_ai_commentary(self, moment: CrazyMoment, topic_title: str) -> str:
        # Target roughly 2.6 words per second so the voiceover fits the clip duration naturally
        target_words = max(20, min(75, int(moment.duration * 2.5)))
        base_script = (
            f"Stop scrolling! Wait until you see this crazy {topic_title} right here in GTA 6! "
            f"Look closely at how realistic the physics and background reactions are in Vice City. "
            f"Rockstar Games spent years building tiny details like this, and 99 percent of players "
            f"completely missed it on their first watch. Drop a comment if you caught this and subscribe "
            f"for daily GTA 6 shorts!"
        )
        words = base_script.split()
        if len(words) > target_words:
            trimmed = " ".join(words[:target_words]).rstrip(",.") + "!"
            return trimmed
        return base_script
