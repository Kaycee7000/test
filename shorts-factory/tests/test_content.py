import random
from datetime import date

from shorts_factory.config import load_channels, load_settings
from shorts_factory.content.schemas import SceneDraft, ScriptDraft
from shorts_factory.content.strategy import learnings_text, outcomes, pick_formats, target_per_day
from shorts_factory.content.topics import dedupe, is_duplicate
from shorts_factory.content.writer import lint, normalize, parse_factcheck


def _channel(project, cid="history_en"):
    s = load_settings(project / "config" / "settings.yaml")
    return next(c for c in load_channels(s) if c.id == cid)


def _draft(lines, title="The ship that sailed itself", moods="dark_ambient"):
    return ScriptDraft(
        title=title, headline="It sailed alone", hook_type="shocking_fact",
        scenes=[SceneDraft(narration=l, visual="v", motion="zoom_in", emphasis=[]) for l in lines],
        description="d", hashtags=["#history", "ships", "x", "y"], tags=["a"] * 5, pinned_comment="?",
        music_mood=moods, fact_claims=["c"], sources=[],
    )


def test_ramp_follows_launch_date(project):
    ch = _channel(project)
    assert target_per_day(ch, date(2026, 1, 1)) == 3  # no launch date -> first step
    ch.schedule.launch_date = date(2026, 1, 1)
    assert target_per_day(ch, date(2025, 12, 31)) == 0
    assert target_per_day(ch, date(2026, 1, 5)) == 3
    assert target_per_day(ch, date(2026, 1, 15)) == 6
    assert target_per_day(ch, date(2026, 3, 1)) == 12


def test_dedupe_catches_paraphrases_and_keeps_new_stories():
    existing = [("The Dancing Plague of 1518", ["dancing plague", "1518", "strasbourg", "mass hysteria"])]
    assert is_duplicate("Why people danced to death in Strasbourg", ["strasbourg", "1518", "dancing plague"], existing)
    assert is_duplicate("The Dancing Plague of 1518: explained", [], existing)
    assert not is_duplicate("The Great Emu War", ["emu war", "australia", "1932"], existing)
    ideas = [{"title": "The Great Emu War", "format": "untold_story", "keywords": ["emu war", "1932"]},
             {"title": "The Great Emu War of 1932", "format": "untold_story", "keywords": ["emu war", "1932"]},
             {"title": "Bad format", "format": "nope", "keywords": ["z"]}]
    assert [i["title"] for i in dedupe(ideas, existing, {"untold_story"})] == ["The Great Emu War"]


def test_thompson_sampling_respects_cap_and_learns(project):
    ch = _channel(project)
    picks = pick_formats(ch, 10, {}, random.Random(1))
    assert len(picks) == 10 and max(picks.count(f) for f in set(picks)) <= 5
    stats = {"unsolved": (40, 2)} | {f.id: (1, 30) for f in ch.formats if f.id != "unsolved"}
    picks = pick_formats(ch, 10, stats, random.Random(2))
    assert picks.count("unsolved") == 5  # strongly winning format hits the diversity cap


def test_outcomes_and_learnings_need_mature_data():
    perf = [{"format": "a", "hook_type": "question", "views": v, "avg_view_pct": p, "age_hours": 72,
             "title": f"t{v}", "data": '{"hook": "h"}'} for v, p in [(100, 50), (5000, 90), (300, 70), (20, 30)]]
    res = outcomes(perf, "format")
    assert sum(res["a"]) == 4
    text = learnings_text(perf)
    assert "WHAT IS WORKING" in text and "t5000" in text.split("UNDERPERFORMED")[0]
    assert learnings_text(perf[:2]) == ""


def test_lint_flags_retention_killers(project):
    ch = _channel(project)
    draft = normalize(_draft(["Did you know that ships can sail alone?"] + ["Filler words here now."] * 3), ch)
    issues = lint(draft, ch, recent_titles=["The ship that sailed itself!"])
    joined = " ".join(issues)
    assert "words" in joined and "opener" in joined and "too close" in joined and "scenes" in joined
    assert draft.hashtags == ["history", "ships", "x"]


def test_parse_factcheck_maps_by_claim_number():
    claims = ["A happened in 1900", "B is 3 km tall", "C exists"]
    text = "1 | OK\n2 | WRONG | it is 3 m tall (Britannica)\nnoise line"
    out = parse_factcheck(text, claims)
    assert out[claims[0]] == "OK"
    assert out[claims[1]].startswith("WRONG") and "3 m" in out[claims[1]]
    assert out[claims[2]].startswith("UNSURE")
