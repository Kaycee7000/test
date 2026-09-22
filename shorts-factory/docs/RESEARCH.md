# Research and knowledge base (RAG)

The content system researches on its own, from public sources only. It never logs into, reads from or
posts to the channels the posting team runs. Everything it learns goes into one knowledge base
(`data/knowledge.db`) that the strategist, researcher, writer and fact-checker all draw from.

```
             ┌──────────── daily: shorts scout ────────────┐
public data  │ YouTube search (API key) · Wikipedia most-read │──► knowledge base ──► topic strategist
             │ on-this-day · your RSS feeds                  │     (SQLite: FTS5      (demand signals)
             └───────────────────────────────────────────────┘      + embeddings)
per video:   Wikipedia article + Claude web research ─► dossier ──►      │ ──► writer + fact-checker
                                                                         │     (dossier, related notes,
our output:  every approved script ─────────────────────────────────────►│      past videos to avoid)
team:        feedback CSVs (views, retention) ─► metrics ─► format bandit + "what works here"
```

## 1. Scouting: what is in demand right now

`shorts scout` (runs automatically at the start of `shorts run`, once per channel per day):

| Source | What it gives | Access |
|---|---|---|
| **YouTube Data API v3, public data** | The most-viewed Shorts (≤180 s) of the last 14 days for each channel's search terms, with views-per-hour velocity | An **API key** (`YOUTUBE_API_KEY`), not OAuth: no channel is involved |
| **Wikipedia most-read** | Yesterday's top articles in the channel's language: what people are curious about today | Free, public |
| **On this day** | Events on the publish date (history channels): anniversary hooks | Free, public |
| **RSS/Atom** | Headlines from feeds you list per channel (`research.rss`) | Free, public |

Search terms, region and anniversaries are set per channel in `config/channels/*.yaml` under `research:`.
`shorts trends --channel history_en` shows exactly what the strategist will see.

The strategist is told to **ride demand, never remake a competitor's video**: about one third of ideas tie to a
signal (recorded as `trend_ref` in the post kit), the rest are evergreen.

**YouTube quota:** each search costs 100 units. The default (4 searches × 4 channels = 1,600 units/day) fits the
free 10,000 units/day. Create the key in Google Cloud Console → enable *YouTube Data API v3* → Credentials →
API key, and restrict it to that API.

**Data policy (verify):** YouTube's API Services Developer Policies limit how long API data may be kept without
refreshing (30 days at the time of writing). Trend documents from YouTube expire after
`research.youtube_retention_days` and are purged automatically. Wikimedia asks for a descriptive User-Agent:
set `research.user_agent` to include a contact URL.

## 2. Per-video research dossier

For every planned video (`research` stage, between `plan` and `script`):

1. The best-matching **Wikipedia article** (in the channel language, English fallback) is fetched through the
   core action API and stored.
2. **Claude with web search** writes a dossier in fixed sections: core story, numbered verified facts each with a
   source URL, surprising details, timeline, common misconceptions, **visual reference** (what people, places,
   clothing and light looked like, for accurate images), disputed points, sources. It is told which angles our
   past videos already used.
3. The dossier is saved to the job folder (`research.md`) and the knowledge base.

The writer then gets the dossier, the most relevant related notes from the knowledge base, and our past videos on
related subjects ("do not repeat these angles"). The fact-checker gets the same notes and only searches the web
for claims they don't settle. Scripts return `sources`, which ship in the post kit so the team can cite them.

## 3. The knowledge base

- **Store:** SQLite (`data/knowledge.db`) with an FTS5 full-text index and per-chunk embeddings.
- **Retrieval:** hybrid. BM25 full-text + cosine similarity, merged with reciprocal-rank fusion, filtered by niche,
  language, kind and age.
- **Embeddings:** `BAAI/bge-m3` (multilingual, so English and Spanish channels share one index) via
  sentence-transformers, loaded once per run and freed before the GPU stages. Without it, retrieval falls back to
  full-text only. After changing `research.embed_model`, run `shorts kb reindex`.
- **Semantic dedupe:** new topic ideas too close in meaning to a past topic or script (`research.topic_similarity`)
  are dropped, which catches paraphrases that keyword matching misses.
- **Backups:** after each run, `shorts.db` and `knowledge.db` are copied to `_system/backups/<date>/` in the bucket.

Inspect it:
```bash
shorts kb stats
shorts kb search "dancing plague strasbourg" --niche history
shorts kb search "black hole" --kind dossier -k 5
```

## 4. Cost

The dossier adds one Claude call with web search per video: roughly **$0.10-0.20** on `claude-opus-5`, partly offset
by fewer fact-check searches. Budget about **$0.40-0.90 per finished video** for all Claude work; each run prints the
real figure. Levers: `research.dossier: false` keeps Wikipedia-only dossiers (free), and `web_search_max_uses`
caps searches per call.
