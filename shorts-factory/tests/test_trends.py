from datetime import date

from shorts_factory.config import TrendsCfg, load_channels, load_settings
from shorts_factory.content import prompts
from shorts_factory.content.llm import MockLLM
from shorts_factory.content.topics import generate_topics
from shorts_factory.content.trends import daily_brief, trend_system, trend_user
from shorts_factory.db import DB


class Counting(MockLLM):
    def __init__(self):
        super().__init__()
        self.calls = []

    def research(self, system, user, context=None, max_uses=None):
        self.calls.append((system, user, max_uses))
        return super().research(system, user, context, max_uses)

    def structured(self, system, user, schema, effort=None, context=None):
        self.calls.append((system, user, None))
        return super().structured(system, user, schema, effort, context)


def _ch(project, cid="history_es"):
    s = load_settings(project / "config" / "settings.yaml")
    return next(c for c in load_channels(s) if c.id == cid)


def test_trend_prompt_knows_channel_platform_language_and_date(project):
    ch = _ch(project)
    system = trend_system(ch)
    assert "YouTube Shorts" in system and "Spanish" in system and ch.trend_focus[0] in system
    assert "[source: URL]" in system and "Graphic gore" in system  # banned topics excluded from trends
    assert "2026-09-23" in trend_user(date(2026, 9, 23), date(2026, 9, 22))


def test_brief_is_searched_once_per_channel_per_day(tmp_path, project):
    db, ch, llm = DB(tmp_path / "s.db"), _ch(project), Counting()
    day = date(2026, 9, 23)
    b1 = daily_brief(llm, db, ch, day, TrendsCfg(max_searches=3))
    b2 = daily_brief(llm, db, ch, day, TrendsCfg())
    assert b1 == b2 and "documentary" in b1 and len(llm.calls) == 1 and llm.calls[0][2] == 3
    daily_brief(llm, db, ch, day, TrendsCfg(), refresh=True)
    assert len(llm.calls) == 2


def test_failed_search_never_blocks_production(tmp_path, project):
    class Broken(MockLLM):
        def research(self, *a, **k):
            raise RuntimeError("search unavailable")

    assert daily_brief(Broken(), DB(tmp_path / "s.db"), _ch(project), date(2026, 9, 23), TrendsCfg()) == ""


def test_timely_topics_ride_the_trends_and_jump_the_queue(tmp_path, project):
    db, ch, llm = DB(tmp_path / "s.db"), _ch(project), Counting()
    generate_topics(llm, db, ch, n=5)  # evergreen backlog, priorities up to 10
    brief = daily_brief(llm, db, ch, date(2026, 9, 23), TrendsCfg())
    generate_topics(llm, db, ch, n=2, trends=brief, timely=True, boost=2.0)
    prompt = llm.calls[-1][1]
    assert "WHAT'S TRENDING" in prompt and "EVERY idea must connect" in prompt and "lost city" in prompt
    top = db.topics(ch.id, status="new")[0]
    assert top["priority"] == 12 and top["trend_ref"] == "A new documentary about a lost city"


def test_topic_prompt_without_trends_stays_evergreen(project):
    ch = _ch(project, "space_en")
    assert "nothing today" in prompts.topics_user(ch, 10, [], "", "")
