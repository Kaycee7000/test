"""End-to-end with every model mocked: real workers, real renderer, real ffmpeg mix, local-storage delivery."""
import json
from datetime import date, timedelta
from pathlib import Path

from shorts_factory.config import load_channels, load_settings
from shorts_factory.media.audio import probe
from shorts_factory.pipeline import Pipeline
from shorts_factory.review import build_review_page

from .conftest import needs_ffmpeg


@needs_ffmpeg
def test_full_day_mock_run(project: Path):
    s = load_settings(project / "config" / "settings.yaml").mock()
    s.render.workers = 1
    s.render.preset = "ultrafast"
    s.publish.require_approval = True
    p = Pipeline(s, load_channels(s, ["history_en"]))
    day = date.today() + timedelta(days=1)
    p.run(day, count=1)

    # editorial gate: rendered + QC'd, but held until a human approves it
    jobs = p.db.jobs(publish_date=day.isoformat())
    assert [j["state"] for j in jobs] == ["ready"], [j["error"] for j in jobs]
    page = build_review_page(p.db, day.isoformat(), s.work / "review.html")
    assert "final.mp4" in page.read_text() and jobs[0]["id"] in page.read_text()
    p.db.merge_data(jobs[0]["id"], approved=True)
    p.run(day, stages=["upload"])

    jobs = p.db.jobs(publish_date=day.isoformat())
    assert [j["state"] for j in jobs] == ["uploaded"], [j["error"] for j in jobs]
    job = jobs[0]
    jdir = Path(job["dir"])
    delivery = json.loads(job["data"])["delivery"]
    out = s.path(s.storage.local_dir)
    group = out / "history" / "en" / day.isoformat()
    video = out / delivery["key"]
    assert video.parent == group and video.name.startswith(job["id"])
    kit = json.loads(video.with_suffix(".json").read_text())
    assert kit["title"] and kit["suggested_post_time_local"] and "synthetic" in kit["ai_disclosure"]
    assert video.with_suffix(".jpg").exists()
    manifest = (group / "_manifest.csv").read_text(encoding="utf-8-sig")
    assert kit["title"] in manifest and delivery["key"] in manifest
    assert not (jdir / "final.mp4").exists() and (jdir / "script.json").exists()  # local media cleaned up
    info = probe(video)
    v = next(st for st in info["streams"] if st["codec_type"] == "video")
    assert (v["width"], v["height"]) == (1080, 1920)
    assert any(st["codec_type"] == "audio" for st in info["streams"])
    assert 15 <= float(info["format"]["duration"]) <= 59.5
    assert "#" in kit["description"]

    # trends: one web-searched brief per channel per day, fresh trend ideas jump the queue
    brief = p.db.trend_brief("history_en", day.isoformat())
    assert brief and "documentary" in brief["brief"] and brief["topics_added"]
    assert kit["trend_ref"] == "A new documentary about a lost city" and kit["platform"] == "youtube_shorts"
    assert list((out / "_system" / "backups").rglob("shorts.db"))
    header = manifest.splitlines()[0]
    assert "posted_url" in header and "avg_view_pct" in header  # columns the posting team fills in

    # the team fills in the manifest and drops it in feedback/; the next run learns from it
    filled = manifest.replace(",,,,,,,,", ",https://www.youtube.com/shorts/ABCDEFGHIJK,,4200,81,,,,")
    (out / "feedback").mkdir()
    (out / "feedback" / "day1.csv").write_text(filled, encoding="utf-8-sig")
    p.run(day, count=1)
    perf = p.db.performance("history_en")
    assert perf and perf[0]["views"] == 4200 and perf[0]["video_id"] == "ABCDEFGHIJK"

    # idempotent: a second run plans nothing new and changes nothing
    p.run(day, count=1)
    assert len(p.db.jobs(publish_date=day.isoformat())) == 1
