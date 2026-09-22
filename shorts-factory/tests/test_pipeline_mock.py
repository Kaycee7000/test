"""End-to-end with every model mocked: real workers, real renderer, real ffmpeg mix, dry-run upload."""
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
    info = probe(jdir / "final.mp4")
    v = next(st for st in info["streams"] if st["codec_type"] == "video")
    assert (v["width"], v["height"]) == (1080, 1920)
    assert any(st["codec_type"] == "audio" for st in info["streams"])
    assert 15 <= float(info["format"]["duration"]) <= 59.5
    receipt = json.loads((jdir / "upload_receipt.json").read_text())
    assert receipt["contains_synthetic_media"] is True
    assert "#" in receipt["meta"]["description"]
    assert (jdir / "preview.jpg").exists()

    # idempotent: a second run plans nothing new and changes nothing
    p.run(day, count=1)
    assert len(p.db.jobs(publish_date=day.isoformat())) == 1
