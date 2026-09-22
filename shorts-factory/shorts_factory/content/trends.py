"""Daily trend brief: one Claude call with live web search per channel per day.

The brief lands in the topic prompt ("DEMAND SIGNALS"), and each day a few fresh trend-driven ideas are
generated and prioritised so they get made while they are still timely.
"""
from __future__ import annotations

import logging
from datetime import date

from ..config import ChannelCfg, TrendsCfg
from ..db import DB
from .llm import LLM
from .platforms import platform, platform_block
from .prompts import LANGUAGES

log = logging.getLogger(__name__)


def trend_system(ch: ChannelCfg) -> str:
    focus = "\n".join(f"- {f}" for f in ch.trend_focus) or "- anything this audience is talking about"
    banned = "; ".join(ch.banned) or "none"
    return f"""You are the trend researcher for "{ch.name}", a {platform(ch.platform).name} channel.
NICHE: {ch.niche}
AUDIENCE: {" ".join(ch.audience.split())}
{platform_block(ch.platform)}

Use web search to find what this audience is curious about right now. Search in
{LANGUAGES.get(ch.language, ch.language)} and English. Look for:
- news, discoveries and releases (films, series, documentaries, games, books) tied to the niche from the last 14 days;
- anniversaries and dates in the next 7 days;
- stories and questions going viral on YouTube Shorts, TikTok, Reddit and X in this niche (use articles, roundups
  and search results; you cannot see view counts directly);
- this channel's own focus areas:
{focus}
Skip anything off-niche, tragedies with recent victims, partisan politics, and the channel's banned topics ({banned}).

Reply in English with 6-12 bullets and nothing else, each exactly:
- <trend in a few words> | why it's hot now | a short-video story angle for this channel | [source: URL]"""


def trend_user(day: date, today: date) -> str:
    return (f"Today is {today.isoformat()}. The videos this informs will be published on {day.isoformat()}. "
            "Find what's trending for this channel.")


def daily_brief(llm: LLM, db: DB, ch: ChannelCfg, day: date, cfg: TrendsCfg, refresh: bool = False) -> str:
    """The channel's trend brief for a publish date, fetched once and cached in the database."""
    row = db.trend_brief(ch.id, day.isoformat())
    if row is not None and not refresh:
        return row["brief"]
    try:
        brief = llm.research(trend_system(ch), trend_user(day, date.today()),
                             context={"kind": "trends", "channel": ch.id}, max_uses=cfg.max_searches).strip()
    except Exception as e:  # trends are an enhancement; never block production on them
        log.warning("%s: trend search failed, continuing with evergreen topics: %s", ch.id, e)
        return ""
    db.save_trend_brief(ch.id, day.isoformat(), brief)
    log.info("%s: trend brief for %s (%d bullets)", ch.id, day, brief.count("\n- ") + brief.startswith("- "))
    return brief
