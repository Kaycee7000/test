"""What the system knows about each destination platform. Fed to the strategist, researcher, writer and critic."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Platform:
    id: str
    name: str
    hashtags: int
    rules: list[str] = field(default_factory=list)


PLATFORMS: dict[str, Platform] = {
    "youtube_shorts": Platform("youtube_shorts", "YouTube Shorts", 3, [
        "Viewers arrive by swiping the Shorts feed or from search. The first 1-2 seconds decide the swipe.",
        "The title shows under the video and is indexed by search: name the subject plainly and add curiosity; "
        "don't repeat the on-screen headline.",
        "A seamless loop (last line flows into the first) raises average viewed %, a core ranking signal.",
        "Sweet spot 35-50 seconds. No 'link in bio', no mentions of other apps, no outro screen.",
        "Evergreen search value matters: Shorts keep getting views from search for months.",
    ]),
    "tiktok": Platform("tiktok", "TikTok", 4, [
        "The For You feed judges the first second and the rewatch rate; start mid-action.",
        "Spoken, casual, second-person narration performs best; on-screen text carries the hook.",
        "Sweet spot 30-60 seconds. Trend-aware angles win, but stay factual.",
    ]),
    "instagram_reels": Platform("instagram_reels", "Instagram Reels", 5, [
        "Viewers often watch muted first: the burned-in captions and headline must carry the story.",
        "Shareability to DMs drives reach: end on a line people want to send to a friend.",
        "Sweet spot 20-45 seconds.",
    ]),
}


def platform(pid: str) -> Platform:
    return PLATFORMS.get(pid, PLATFORMS["youtube_shorts"])


def platform_block(pid: str) -> str:
    p = platform(pid)
    return f"PLATFORM: {p.name}\n" + "\n".join(f"- {r}" for r in p.rules)
