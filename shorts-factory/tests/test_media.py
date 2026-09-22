from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from shorts_factory.config import CaptionCfg, RenderCfg, ScheduleCfg
from shorts_factory.media.captions import Word, ass_color, ass_time, build_ass, chunk_words
from shorts_factory.media.render import audio_graph
from shorts_factory.media.wordmap import map_words
from shorts_factory.publish.schedule import daily_slots

from .conftest import needs_ffmpeg


def test_ass_helpers():
    assert ass_color("#FFE14D") == "&H004DE1FF"
    assert ass_time(61.234) == "0:01:01.23"


def test_chunking_breaks_on_limits_and_punctuation():
    words = [Word(w, i * 0.3, i * 0.3 + 0.25) for i, w in enumerate("In 1908, a whole village vanished overnight. Nobody".split())]
    chunks = [[w.text for w in c] for c in chunk_words(words, 3, 18)]
    # "vanished overnight." is 19 chars: too wide for one caption line at the default size
    assert chunks == [["In", "1908,"], ["a", "whole", "village"], ["vanished"], ["overnight."], ["Nobody"]]
    assert [[w.text for w in c] for c in chunk_words(words, 3, 24)][2] == ["vanished", "overnight."]


def test_build_ass_highlights_each_word_once():
    words = [Word("hello", 0.0, 0.4), Word("big", 0.4, 0.8, emphasis=True), Word("world.", 0.8, 1.2)]
    ass = build_ass(words, CaptionCfg(), 1080, 1920, headline="Hook", total=2.0)
    events = [l for l in ass.splitlines() if l.startswith("Dialogue: 1")]
    assert len(events) == 3
    assert "&H004DE1FF" in events[0] and "&H007AFF3D" in events[1]  # highlight, then emphasis colour
    assert "Headline" in ass and "HOOK" in ass


def test_map_words_keeps_script_spelling_and_interpolates():
    script = "In 1908 Dr. Smyth vanished".split()
    asr = [("in", 0.0, 0.2), ("nineteen", 0.2, 0.5), ("oh", 0.5, 0.6), ("eight", 0.6, 0.9),
           ("doctor", 0.9, 1.2), ("smith", 1.2, 1.5), ("vanished", 1.5, 2.0)]
    out = map_words(script, asr, 2.2)
    assert [w["w"] for w in out] == script
    assert out[0]["s"] == 0.0 and out[-1]["e"] == 2.0
    assert all(a["s"] <= b["s"] for a, b in zip(out, out[1:]))
    assert 0.2 <= out[1]["s"] < out[2]["s"] < out[4]["s"]


def test_slots_fall_inside_windows_and_respect_gap():
    sched = ScheduleCfg(timezone="America/New_York", windows=["07:00-09:00", "17:00-22:00"], min_gap_minutes=40)
    slots = daily_slots(date(2026, 10, 1), sched, 6, seed="x")
    assert len(slots) == 6
    local = [s.astimezone(ZoneInfo("America/New_York")) for s in slots]
    for t in local:
        assert 7 <= t.hour < 9 or 17 <= t.hour <= 22
    assert all((b - a).total_seconds() >= 40 * 60 for a, b in zip(slots, slots[1:]))
    cutoff = datetime(2026, 10, 1, 20, 0, tzinfo=timezone.utc)
    assert all(s >= cutoff for s in daily_slots(date(2026, 10, 1), sched, 6, "x", not_before=cutoff))


def test_audio_graph_pads_every_branch():
    g = audio_graph(40.0, True, [3.1, 7.2], RenderCfg())
    assert "sidechaincompress" in g and "amix=inputs=4" in g and "loudnorm=I=-14.0" in g
    assert g.count("apad=whole_dur=40.000") == 4
    assert "amix" not in audio_graph(40.0, False, [], RenderCfg())


@needs_ffmpeg
def test_render_mixes_video_clip_and_still(tmp_path):
    import subprocess

    import cv2
    import numpy as np

    from shorts_factory.media.audio import probe, write_wav
    from shorts_factory.media.render import Scene, render

    clip = tmp_path / "clip.mp4"  # stands in for a Wan clip: 16 fps, small, shorter than its scene
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=s=464x832:r=16:d=1.5",
                    "-pix_fmt", "yuv420p", str(clip)], check=True)
    still = tmp_path / "still.png"
    cv2.imwrite(str(still), np.full((1920, 1088, 3), 90, np.uint8))
    write_wav(tmp_path / "voice.wav", np.zeros(24000 * 4, np.float32), 24000)
    (tmp_path / "c.ass").write_text(build_ass([Word("hi", 0.1, 0.5)], CaptionCfg(), 1080, 1920))
    cfg = RenderCfg(preset="ultrafast", encoder="libx264")
    scenes = [Scene(str(clip), "video", "zoom_in", 0.0, 2.2), Scene(str(still), "image", "pan_left", 2.2, 4.0)]
    render(scenes, tmp_path / "voice.wav", tmp_path / "c.ass", tmp_path / "out.mp4", cfg, tmp_path, 4.0)
    info = probe(tmp_path / "out.mp4")
    assert abs(float(info["format"]["duration"]) - 4.0) < 0.15
