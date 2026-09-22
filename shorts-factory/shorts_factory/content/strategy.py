"""The feedback loop: learn which formats and hooks win on each channel and steer production toward them."""
from __future__ import annotations

import json
import random
import statistics
from collections import Counter, defaultdict
from datetime import date
from typing import Any

from ..config import ChannelCfg

MIN_AGE_HOURS = 48  # Shorts distribution takes ~2 days to settle; judge nothing younger


def target_per_day(ch: ChannelCfg, day: date) -> int:
    """Uploads planned for `day`, following the channel's warm-up ramp from its launch date."""
    ramp = ch.schedule.ramp
    if not ramp:
        return 0
    if ch.schedule.launch_date is None:
        return ramp[0].per_day
    elapsed = (day - ch.schedule.launch_date).days
    if elapsed < 0:
        return 0
    for step in ramp:
        if step.days is None or elapsed < step.days:
            return step.per_day
        elapsed -= step.days
    return ramp[-1].per_day


def outcomes(perf: list[dict[str, Any]], key: str) -> dict[str, tuple[int, int]]:
    """Win/loss per value of `key` (format or hook_type).

    A video "wins" when it beats the channel median on views AND on average percentage viewed
    (Shorts can exceed 100% thanks to loops). Both matter: views = the feed chose to push it,
    viewed % = people actually stayed.
    """
    mature = [p for p in perf if (p.get("age_hours") or 0) >= MIN_AGE_HOURS and p.get("views") is not None]
    if len(mature) < 4:
        return {}
    med_views = statistics.median(p["views"] for p in mature)
    pcts = [p["avg_view_pct"] for p in mature if p.get("avg_view_pct") is not None]
    med_pct = statistics.median(pcts) if pcts else None
    res: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for p in mature:
        k = p.get(key)
        if not k:
            continue
        win = p["views"] >= med_views and (med_pct is None or (p.get("avg_view_pct") or 0) >= med_pct)
        res[k][0 if win else 1] += 1
    return {k: (w, l) for k, (w, l) in res.items()}


def pick_formats(ch: ChannelCfg, n: int, stats: dict[str, tuple[int, int]], rng: random.Random,
                 max_share: float = 0.5) -> list[str]:
    """Thompson sampling over formats with a diversity cap.

    Prior strength comes from the configured weight, so strategy intent drives early days and data
    takes over as results come in. The cap keeps some exploration and avoids a samey channel.
    """
    cap = max(1, int(round(n * max_share))) if len(ch.formats) > 1 else n
    counts: Counter[str] = Counter()
    picks: list[str] = []
    for _ in range(n):
        best, best_score = None, -1.0
        for f in ch.formats:
            if counts[f.id] >= cap:
                continue
            w, l = stats.get(f.id, (0, 0))
            prior = 2.0 * f.weight
            score = rng.betavariate(prior + w, 2.0 + l)
            if score > best_score:
                best, best_score = f.id, score
        best = best or ch.formats[0].id
        counts[best] += 1
        picks.append(best)
    return picks


def learnings_text(perf: list[dict[str, Any]], top: int = 6, bottom: int = 3) -> str:
    """Compact, prompt-ready summary of what works on this channel right now."""
    mature = [p for p in perf if (p.get("age_hours") or 0) >= MIN_AGE_HOURS and p.get("views") is not None]
    if len(mature) < 4:
        return ""

    def score(p: dict[str, Any]) -> float:
        return (p["views"] or 0) * (0.5 + (p.get("avg_view_pct") or 50) / 100)

    ranked = sorted(mature, key=score, reverse=True)

    def line(p: dict[str, Any]) -> str:
        hook = ""
        try:
            hook = json.loads(p.get("data") or "{}").get("hook", "")
        except json.JSONDecodeError:
            pass
        pct = p.get("avg_view_pct")
        pct_s = f"{pct:.0f}% viewed" if pct is not None else "viewed % n/a"
        return f'- [{p.get("format")}/{p.get("hook_type")}] "{p.get("title")}" | hook: "{hook}" | {p["views"]:,} views, {pct_s}'

    out = ["WHAT IS WORKING ON THIS CHANNEL (learn the pattern, never copy the story):"]
    out += [line(p) for p in ranked[:top]]
    out.append("WHAT UNDERPERFORMED (avoid these patterns):")
    out += [line(p) for p in ranked[-bottom:]]
    fmt = outcomes(mature, "format")
    if fmt:
        rates = sorted(((w / (w + l), k) for k, (w, l) in fmt.items() if w + l), reverse=True)
        out.append("FORMAT WIN RATES: " + ", ".join(f"{k} {r:.0%}" for r, k in rates))
    return "\n".join(out)
