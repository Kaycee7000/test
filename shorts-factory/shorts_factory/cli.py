"""`shorts` command-line interface."""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from .config import Settings, load_channels, load_settings
from .db import DB
from .pipeline import STAGES, Pipeline

app = typer.Typer(add_completion=False, help="Quality-gated YouTube Shorts factory.")
console = Console()


class _State:
    config: str = "config/settings.yaml"
    mock: bool = False


state = _State()


@app.callback()
def main(
    config: str = typer.Option("config/settings.yaml", "--config", "-c", help="Path to settings.yaml"),
    mock: bool = typer.Option(False, "--mock", help="No GPU/API: deterministic stand-ins for every model"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    state.config, state.mock = config, mock
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, format="%(message)s",
                        handlers=[RichHandler(console=console, show_path=False, rich_tracebacks=True)])
    for noisy in ("httpx", "httpx2", "googleapiclient", "urllib3", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _settings() -> Settings:
    s = load_settings(state.config)
    if state.mock:
        s = s.mock()
        s.workdir = str(Path(s.workdir) / "mock")
    return s


def _pipeline(channels: Optional[str] = None) -> Pipeline:
    s = _settings()
    only = [c.strip() for c in channels.split(",")] if channels else None
    chs = load_channels(s, only)
    if not chs:
        raise typer.BadParameter("no enabled channels match")
    return Pipeline(s, chs)


def _day(value: str) -> date:
    if value == "today":
        return date.today()
    if value == "tomorrow":
        return date.today() + timedelta(days=1)
    return date.fromisoformat(value)


@app.command()
def init() -> None:
    """Create data/asset folders and the database."""
    s = _settings()
    for d in [s.work, s.assets / "fonts", s.assets / "voices", s.assets / "sfx" / "whoosh",
              s.path(s.publish.secrets_dir)]:
        d.mkdir(parents=True, exist_ok=True)
    for ch in load_channels(s):
        for mood in ch.music_moods:
            (s.assets / "music" / mood).mkdir(parents=True, exist_ok=True)
    DB(s.work / "shorts.db")
    console.print(f"[green]initialised[/] {s.work}")


@app.command()
def doctor() -> None:
    """Check that the environment can run the full pipeline."""
    s = _settings()
    rows: list[tuple[str, bool, str]] = []
    ff = shutil.which("ffmpeg")
    rows.append(("ffmpeg", bool(ff), ff or "apt-get install -y ffmpeg"))
    if ff:
        filters = subprocess.run([ff, "-hide_banner", "-filters"], capture_output=True, text=True).stdout
        rows.append(("libass (subtitles filter)", " subtitles " in filters, "needed for captions"))
        from .media.render import pick_encoder
        rows.append(("video encoder", True, pick_encoder(s.render.encoder)))
    fonts = list((s.assets / "fonts").glob("*.ttf"))
    rows.append((f"font {s.captions.font}", any(s.captions.font.lower() in f.name.lower() for f in fonts),
                 "scripts/fetch_fonts.sh"))
    smi = shutil.which("nvidia-smi")
    gpu = subprocess.run([smi, "--query-gpu=name,memory.total", "--format=csv,noheader"], capture_output=True,
                         text=True).stdout.strip() if smi else ""
    rows.append(("GPU", bool(gpu), gpu or "no NVIDIA GPU visible"))
    if s.tts.backend == "chatterbox":
        ok = bool(s.tts.python and Path(s.tts.python).exists())
        rows.append(("chatterbox venv", ok, s.tts.python or "unset"))
    if s.llm.provider == "anthropic":
        rows.append(("ANTHROPIC_API_KEY", bool(os.environ.get("ANTHROPIC_API_KEY")), "export it or use `ant auth login`"))
    for ch in load_channels(s):
        if ch.voice.chatterbox_ref and s.tts.backend == "chatterbox":
            p = s.path(ch.voice.chatterbox_ref)
            rows.append((f"{ch.id} voice ref", p.exists(), str(p) if p.exists() else f"missing {p} (built-in voice used)"))
        music = [m for m in ch.music_moods if any((s.assets / "music" / m).glob("*.*"))]
        rows.append((f"{ch.id} music", bool(music), f"{len(music)}/{len(ch.music_moods)} moods stocked"))
        if s.publish.mode == "youtube":
            from .publish.youtube import token_path
            tp = token_path(s, ch)
            rows.append((f"{ch.id} OAuth token", tp.exists(), str(tp)))
    t = Table("check", "ok", "detail")
    for name, ok, detail in rows:
        t.add_row(name, "[green]yes[/]" if ok else "[red]no[/]", detail)
    console.print(t)


@app.command()
def topics(
    channel: str = typer.Option(..., "--channel"),
    count: int = typer.Option(60, "--count"),
    show: bool = typer.Option(False, "--show", help="List the backlog instead of generating"),
) -> None:
    """Generate (or --show) the topic backlog for a channel."""
    from .content.topics import generate_topics

    p = _pipeline(channel)
    ch = p.channels[channel]
    if not show:
        n = generate_topics(p.llm, p.db, ch, count)
        console.print(f"[green]{n}[/] new topics for {channel}")
    t = Table("id", "format", "priority", "title", "status")
    for r in p.db.topics(channel)[:80]:
        t.add_row(str(r["id"]), r["format"], f"{r['priority']:.0f}", r["title"], r["status"])
    console.print(t)


@app.command()
def run(
    day: str = typer.Option("tomorrow", "--date", help="Publish date: today | tomorrow | YYYY-MM-DD"),
    channels: Optional[str] = typer.Option(None, "--channels", help="Comma-separated channel ids"),
    stages: Optional[str] = typer.Option(None, "--stages", help=f"Subset of: {','.join(STAGES)}"),
    count: Optional[int] = typer.Option(None, "--count", help="Override videos per channel for this run"),
) -> None:
    """Produce (and schedule) a full day of Shorts."""
    p = _pipeline(channels)
    chosen = [x.strip() for x in stages.split(",")] if stages else None
    p.run(_day(day), stages=chosen, count=count)
    _print_status(p, _day(day).isoformat())
    _print_usage(p)


@app.command()
def make(
    channel: str = typer.Option(..., "--channel"),
    topic: str = typer.Option(..., "--topic", help="What the Short is about"),
    fmt: Optional[str] = typer.Option(None, "--format", help="Format id (default: channel's first)"),
    angle: str = typer.Option("", "--angle"),
    upload: bool = typer.Option(False, "--upload/--no-upload"),
) -> None:
    """Make ONE Short now for a given topic (great for tuning prompts and styles)."""
    p = _pipeline(channel)
    ch = p.channels[channel]
    f = fmt or ch.formats[0].id
    today = date.today().isoformat()
    p.db.add_topics(ch.id, [{"title": topic, "angle": angle, "format": f, "priority": 10}])
    tid = p.db.conn.execute("SELECT MAX(id) FROM topics WHERE channel=? AND title=?", (ch.id, topic)).fetchone()[0]
    p.db.mark_topic(tid, "used")
    jid = p.db.create_job(ch.id, tid, today, f, topic, p.s.work,
                          data={"topic": {"title": topic, "angle": angle, "format": f, "keywords": []}})
    stages = [s for s in STAGES if s != "plan" and (upload or s != "upload")]
    p.run(date.today(), stages=stages, ids=[jid])
    row = p.db.job(jid)
    console.print(f"job {jid}: [bold]{row['state']}[/] {row['error'] or ''}")
    if row["state"] in ("ready", "uploaded"):
        console.print(f"video: {Path(row['dir']) / 'final.mp4'}")
    _print_usage(p)


@app.command()
def auth(channel: str = typer.Option(..., "--channel"), port: int = typer.Option(8765, "--port")) -> None:
    """OAuth a channel (run on a machine with a browser; copy secrets/ to the pod afterwards)."""
    from .publish.youtube import authorize

    s = load_settings(state.config)
    ch = next(c for c in load_channels(s, [channel]))
    info = authorize(s, ch, port)
    console.print(f"[green]authorized[/] {info}. Put this id in config/channels/{channel}.yaml -> youtube.channel_id")


@app.command()
def sync() -> None:
    """Pull views / retention for uploaded videos (feeds format selection and writer learnings)."""
    p = _pipeline()
    console.print(f"stored metrics for {p.sync_metrics()} videos")


@app.command()
def report(channel: str = typer.Option(..., "--channel")) -> None:
    """Performance by format and hook, plus the learnings the writer currently sees."""
    from .content.strategy import learnings_text, outcomes

    p = _pipeline(channel)
    perf = p.db.performance(channel)
    for key in ("format", "hook_type"):
        t = Table(key, "wins", "losses", "win rate", title=f"{channel}: {key}")
        for k, (w, l) in sorted(outcomes(perf, key).items(), key=lambda kv: -kv[1][0] / max(1, sum(kv[1]))):
            t.add_row(k, str(w), str(l), f"{w / max(1, w + l):.0%}")
        console.print(t)
    console.print(learnings_text(perf) or "[dim]not enough mature videos yet (need 4+ older than 48h)[/]")


@app.command()
def review(day: str = typer.Option("tomorrow", "--date")) -> None:
    """Write an HTML review page (videos + scripts + scores) for a publish date."""
    from .review import build_review_page

    p = _pipeline()
    d = _day(day).isoformat()
    out = build_review_page(p.db, d, p.s.work / "jobs" / d / "review.html")
    console.print(f"review page: {out}")
    for r in p.db.jobs(state="ready", publish_date=d):
        console.print(f"  {r['id']}  {r['title']}")


@app.command()
def approve(
    job_ids: Optional[list[str]] = typer.Argument(None),
    day: Optional[str] = typer.Option(None, "--date", help="Approve every ready video for this date"),
) -> None:
    """Mark ready videos as approved for upload (used when publish.require_approval is true)."""
    p = _pipeline()
    ids = list(job_ids or [])
    if day:
        ids += [r["id"] for r in p.db.jobs(state="ready", publish_date=_day(day).isoformat())]
    for jid in ids:
        p.db.merge_data(jid, approved=True)
    console.print(f"approved {len(ids)} video(s); they upload on the next `shorts run --stages upload`")


@app.command()
def reject(job_id: str, reason: str = typer.Option("rejected in review", "--reason")) -> None:
    """Pull a video from the schedule (the next run plans a replacement)."""
    p = _pipeline()
    row = p.db.job(job_id)
    p.db.update_job(job_id, state="rejected", error=f"review: {reason}")
    if row["topic_id"]:
        p.db.mark_topic(row["topic_id"], "rejected")
    console.print(f"rejected {job_id}")


@app.command()
def status(day: Optional[str] = typer.Option(None, "--date")) -> None:
    """Job counts per channel and state."""
    p = _pipeline()
    _print_status(p, _day(day).isoformat() if day else None)
    failed = [r for r in p.db.jobs(state="failed") if not day or r["publish_date"] == _day(day).isoformat()]
    for r in failed[:20]:
        console.print(f"[red]{r['id']}[/] {r['error']}")


def _print_status(p: Pipeline, day: str | None) -> None:
    from .db import STATES

    cols = STATES + ["rejected", "failed"]
    t = Table("channel", *cols, title=f"jobs {day or '(all dates)'}")
    for ch, counts in sorted(p.status(day).items()):
        t.add_row(ch, *[str(counts.get(c, "")) for c in cols])
    console.print(t)


# $/1M tokens (input, output), list prices; web search is $10 per 1,000 searches.
PRICES = {"claude-opus-5": (5.0, 25.0), "claude-sonnet-5": (2.0, 10.0), "claude-fable-5-1": (10.0, 50.0),
          "claude-haiku-4-5": (1.0, 5.0)}


def _print_usage(p: Pipeline) -> None:
    usage = getattr(p._llm, "usage", None)
    if not usage:
        return
    line = f"LLM usage: {json.dumps(dict(usage))}"
    price = PRICES.get(p.s.llm.model)
    if price and p.s.llm.provider == "anthropic":
        uncached = usage.get("input_tokens", 0)
        cost = (uncached * price[0] + usage.get("cache_read_input_tokens", 0) * price[0] * 0.1
                + usage.get("output_tokens", 0) * price[1]) / 1e6 + usage.get("web_search_requests", 0) * 0.01
        line += f"  ≈ ${cost:.2f}"
    console.print(f"[dim]{line}[/]")


if __name__ == "__main__":
    app()
