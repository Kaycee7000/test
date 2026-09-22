"""Publish-slot planning: spread a day's uploads across the audience's peak windows."""
from __future__ import annotations

import random
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from ..config import ScheduleCfg


def _parse(hhmm: str) -> time:
    h, m = hhmm.strip().split(":")
    return time(int(h), int(m))


def daily_slots(day: date, sched: ScheduleCfg, n: int, seed: str, not_before: datetime | None = None) -> list[datetime]:
    """n publish times (UTC) on `day` in the channel's timezone.

    Slots are evenly spaced over the concatenated windows, jittered so the channel does not post
    at robotic :00 times, and kept at least `min_gap_minutes` apart. Slots earlier than
    `not_before` are dropped (a scheduled publish time must be in the future).
    """
    if n <= 0:
        return []
    tz = ZoneInfo(sched.timezone)
    windows = []
    for w in sched.windows:
        a, b = (_parse(x) for x in w.split("-"))
        windows.append((datetime.combine(day, a, tz), datetime.combine(day, b, tz)))
    total = sum((b - a).total_seconds() for a, b in windows)
    rng = random.Random(f"{seed}-{day.isoformat()}")
    gap = timedelta(minutes=sched.min_gap_minutes)
    step = total / n
    jitter = min(step / 4, 12 * 60)
    out: list[datetime] = []
    for i in range(n):
        off = step * (i + 0.5) + rng.uniform(-jitter, jitter)
        for a, b in windows:
            span = (b - a).total_seconds()
            if off <= span:
                t = a + timedelta(seconds=max(0.0, min(off, span)))
                break
            off -= span
        else:
            t = windows[-1][1]
        t = t.replace(second=0, microsecond=0)
        if out and t - out[-1] < gap:
            t = out[-1] + gap
        out.append(t)
    utc = [t.astimezone(timezone.utc) for t in out]
    if not_before is not None:
        utc = [t for t in utc if t >= not_before]
    return utc


def pacific_day(ts: datetime) -> str:
    """YouTube API quotas reset at midnight Pacific time."""
    return ts.astimezone(ZoneInfo("America/Los_Angeles")).date().isoformat()
