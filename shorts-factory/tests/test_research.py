import json
from datetime import date, datetime, timedelta, timezone

from shorts_factory.config import LLMCfg, load_channels, load_settings
from shorts_factory.content.llm import MockLLM
from shorts_factory.content.topics import semantic_dedupe
from shorts_factory.content.writer import ScriptWriter
from shorts_factory.research import collectors as C
from shorts_factory.research.dossier import build_dossier, source_urls, writing_context
from shorts_factory.research.embed import HashEmbedder
from shorts_factory.research.kb import KnowledgeBase
from shorts_factory.research.scout import scout_channel, trend_brief

NOW = datetime(2026, 9, 22, 12, tzinfo=timezone.utc)


class FakeWeb:
    """Canned public-API responses keyed by URL fragment; records every request."""

    def __init__(self):
        self.calls = []

    def __call__(self, url, params=None):
        self.calls.append((url, params or {}))
        p = params or {}
        if url.endswith("/youtube/v3/search"):
            return json.dumps({"items": [{"id": {"videoId": "AAAAAAAAAAA"}}, {"id": {"videoId": "BBBBBBBBBBB"}}]}).encode()
        if url.endswith("/youtube/v3/videos"):
            return json.dumps({"items": [
                {"id": "AAAAAAAAAAA", "contentDetails": {"duration": "PT45S"},
                 "snippet": {"title": "The ship that sailed itself", "publishedAt": "2026-09-20T12:00:00Z",
                             "channelTitle": "Rival History", "description": "wow", "tags": ["ship"]},
                 "statistics": {"viewCount": "480000", "likeCount": "20000", "commentCount": "900"}},
                {"id": "BBBBBBBBBBB", "contentDetails": {"duration": "PT21M3S"},  # long-form: filtered out
                 "snippet": {"title": "Full documentary", "publishedAt": "2026-09-19T12:00:00Z"},
                 "statistics": {"viewCount": "9000000"}},
            ]}).encode()
        if "pageviews/top" in url:
            return json.dumps({"items": [{"articles": [
                {"article": "Main_Page", "views": 9e6, "rank": 1}, {"article": "Special:Search", "views": 1e6, "rank": 2},
                {"article": "Mary_Celeste", "views": 250000, "rank": 3}]}]}).encode()
        if "onthisday" in url:
            return json.dumps({"events": [{"year": 1846, "text": "Neptune is observed for the first time.",
                                           "pages": [{"extract": "Neptune is the eighth planet.",
                                                      "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Neptune"}}}]}]}).encode()
        if url.endswith("/w/api.php") and p.get("list") == "search":
            return json.dumps({"query": {"search": [{"title": "Dancing plague of 1518"}]}}).encode()
        if url.endswith("/w/api.php") and p.get("prop") == "extracts":
            return json.dumps({"query": {"pages": [{"extract": "In July 1518 hundreds of people in Strasbourg danced "
                                                               "for days. Frau Troffea began it. " * 10}]}}).encode()
        if url.endswith(".rss"):
            return (b"<rss><channel><item><title>Probe finds ice</title><link>https://n.example/1</link>"
                    b"<description>&lt;p&gt;Big news&lt;/p&gt;</description></item></channel></rss>")
        if url.endswith(".atom"):
            return (b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Atom item</title>'
                    b'<link href="https://n.example/2"/><summary>text</summary></entry></feed>')
        raise AssertionError(f"unexpected request {url}")


def _setup(project, channel="history_en"):
    s = load_settings(project / "config" / "settings.yaml").mock()
    s.research.scout_sources = ["youtube", "wikipedia_trending", "on_this_day", "rss"]
    s.research.wikipedia = True
    ch = next(c for c in load_channels(s) if c.id == channel)
    kb = KnowledgeBase(project / "kb.db", HashEmbedder())
    return s, ch, kb


def test_youtube_collector_uses_only_public_api_key_and_keeps_shorts():
    web = FakeWeb()
    docs = C.youtube_trends(web, "KEY", queries=["history shorts"], language="en", region="US",
                            lookback_days=14, per_query=25, now=NOW)
    assert [d["title"] for d in docs] == ["The ship that sailed itself"]
    m = docs[0]["meta"]
    assert m["duration_s"] == 45 and m["views_per_hour"] == 480000 / 48 and m["channel_title"] == "Rival History"
    search = web.calls[0][1]
    assert search["key"] == "KEY" and search["videoDuration"] == "short" and search["order"] == "viewCount"
    assert all("access_token" not in p for _, p in web.calls)  # no OAuth, no channel access


def test_wikipedia_and_rss_collectors():
    web = FakeWeb()
    assert [d["title"] for d in C.wikipedia_trending(web, "en", date(2026, 9, 23))] == ["Mary Celeste"]
    otd = C.on_this_day(web, "en", date(2026, 9, 23))
    assert otd[0]["title"].startswith("1846") and "eighth planet" in otd[0]["body"]
    art = C.wikipedia_article(web, "en", "dancing plague")
    assert art["title"] == "Dancing plague of 1518" and "Strasbourg" in art["body"]
    assert C.rss_items(web, "https://n.example/feed.rss")[0]["body"].startswith("Probe finds ice\nBig news")
    assert C.rss_items(web, "https://n.example/feed.atom")[0]["url"] == "https://n.example/2"
    assert C.iso_duration_seconds("PT1M5S") == 65 and C.iso_duration_seconds("P0D") == 0


def test_scout_fills_kb_once_per_day_with_retention(project, monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "KEY")
    s, ch, kb = _setup(project)
    ch.research.rss = ["https://n.example/feed.rss"]
    web = FakeWeb()
    counts = scout_channel(kb, s, ch, date(2026, 9, 23), fetch=web)
    assert counts == {"trend_video": 1, "trend_wiki": 1, "on_this_day": 1, "rss": 1}
    assert scout_channel(kb, s, ch, date(2026, 9, 23), fetch=web) == {}  # already scouted today
    yt = kb.recent(["trend_video"], niche="history", language="en")[0]
    expires = datetime.fromisoformat(yt["expires_at"])
    assert timedelta(days=29) < expires - datetime.now(timezone.utc) <= timedelta(days=30)  # API data policy
    brief = trend_brief(kb, s, ch)
    for part in ("TOP RECENT SHORTS", "The ship that sailed itself", "MOST-READ WIKIPEDIA", "Mary Celeste",
                 "ANNIVERSARIES", "1846", "NICHE NEWS", "Probe finds ice"):
        assert part in brief


def test_scout_survives_a_broken_source(project, monkeypatch):
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    s, ch, kb = _setup(project)

    def broken(url, params=None):
        raise OSError("network down")

    assert scout_channel(kb, s, ch, date(2026, 9, 23), fetch=broken) == {}  # logged, not raised


def test_dossier_and_writing_context(project):
    s, ch, kb = _setup(project)
    kb.add("script", ch.folder, ch.language, "The Dancing Plague (our old video)",
           "In 1518 Strasbourg people danced for days until some collapsed.", key="script:old")
    topic = {"title": "The dancing plague of 1518", "angle": "why authorities made it worse",
             "format": "untold_story", "keywords": ["strasbourg", "1518"]}
    text, doc_id = build_dossier(MockLLM(), kb, s, ch, "job1", topic, fetch=FakeWeb())
    assert "https://example.org/record" in source_urls(text)
    assert kb.stats()["docs"]["wiki_article"] == 1 and kb.doc(doc_id)["kind"] == "dossier"
    ctx = writing_context(kb, s, ch, topic, text, doc_id)
    assert "RESEARCH DOSSIER" in ctx and "RELATED NOTES" in ctx and "Dancing plague of 1518" in ctx
    assert "OUR PAST VIDEOS" in ctx and "our old video" in ctx


class RecordingLLM(MockLLM):
    def __init__(self):
        super().__init__()
        self.prompts = []

    def structured(self, system, user, schema, effort=None, context=None):
        self.prompts.append(user)
        return super().structured(system, user, schema, effort, context)

    def research(self, system, user, context=None):
        self.prompts.append(user)
        return super().research(system, user, context)


def test_writer_and_fact_checker_see_the_research(project):
    s, ch, _ = _setup(project)
    llm = RecordingLLM()
    res = ScriptWriter(llm, LLMCfg(provider="mock")).write(
        ch, "untold_story", {"title": "A village vanished"}, "", [], research="RESEARCH DOSSIER: the bread was warm")
    assert res.ok
    writer_prompt, factcheck_prompt = llm.prompts[0], llm.prompts[1]
    assert "the bread was warm" in writer_prompt and "the bread was warm" in factcheck_prompt
    assert "PLATFORM: YouTube Shorts" not in writer_prompt  # platform rules live in the stable system prompt


def test_semantic_dedupe_drops_paraphrases(project):
    s, ch, _ = _setup(project)

    class Semantic(HashEmbedder):
        semantic = True

    kb = KnowledgeBase(project / "sem.db", Semantic())
    kb.add("script", ch.folder, ch.language, "The Great Emu War", "The Great Emu War. Australia fought emus in 1932",
           key="script:emu")
    ideas = [{"title": "The Great Emu War", "angle": "Australia fought emus in 1932", "format": "untold_story"},
             {"title": "The lighthouse keepers who vanished", "angle": "Flannan Isles 1900", "format": "unsolved"},
             {"title": "The lighthouse keepers who vanished", "angle": "Flannan Isles, 1900", "format": "unsolved"}]
    # the first is a repeat of a past script; the third repeats an idea from this same batch
    assert [i["title"] for i in semantic_dedupe(kb, ch, ideas, 0.86)] == ["The lighthouse keepers who vanished"]


def test_regenerated_docs_replace_old_content(project):
    _, _, kb = _setup(project)
    a = kb.add("dossier", "history", "en", "t", "old findings about the ship", key="dossier:j1")
    b = kb.add("dossier", "history", "en", "t", "new findings about the lighthouse", key="dossier:j1", replace=True)
    assert a != b and kb.doc(a) is None and kb.stats()["docs"] == {"dossier": 1}
    assert kb.search("lighthouse", kinds=["dossier"])[0].doc_id == b and not kb.search("ship", kinds=["dossier"])
