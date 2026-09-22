"""Public-data collectors. None of these touch a channel account.

- YouTube Data API v3 with an API KEY only (public search + public video statistics), no OAuth.
- Wikimedia: top pageviews (what people are curious about today), "on this day" events, article text.
- RSS/Atom feeds you list per channel.

Every collector takes a `fetch` callable so tests can inject canned responses, and every failure
is soft: a broken source logs a warning and the rest of the research still runs.
"""
from __future__ import annotations

import json
import logging
import math
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

log = logging.getLogger(__name__)

Fetch = Callable[[str, dict[str, Any] | None], bytes]
YT = "https://www.googleapis.com/youtube/v3/"
NON_ARTICLE = re.compile(r"^(Main_Page|Special:|Wikipedia:|File:|Portal:|Talk:|Help:|Category:|Template:|"
                         r"Especial:|Portada|Wikipedia:Portada|Página_principal|Spezial:|Hauptseite|Accueil|-$)")


def make_fetch(user_agent: str, timeout: int = 20, retries: int = 3) -> Fetch:
    def fetch(url: str, params: dict[str, Any] | None = None) -> bytes:
        if params:
            url = f"{url}?{urllib.parse.urlencode(params, doseq=True)}"
        req = urllib.request.Request(url, headers={"User-Agent": user_agent, "Accept": "application/json"})
        for attempt in range(retries):
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    return r.read()
            except urllib.error.HTTPError as e:
                if e.code not in (429, 500, 502, 503, 504) or attempt == retries - 1:
                    raise
            except (urllib.error.URLError, TimeoutError):
                if attempt == retries - 1:
                    raise
            time.sleep(2 ** attempt)
        raise RuntimeError("unreachable")
    return fetch


def fetch_json(fetch: Fetch, url: str, params: dict[str, Any] | None = None) -> Any:
    return json.loads(fetch(url, params))


def iso_duration_seconds(d: str) -> int:
    m = re.fullmatch(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", d or "")
    if not m:
        return 0
    days, h, mi, s = (int(x or 0) for x in m.groups())
    return days * 86400 + h * 3600 + mi * 60 + s


# ---------------------------------------------------------------- YouTube (public data, API key)


def youtube_trends(fetch: Fetch, api_key: str, *, queries: list[str], language: str, region: str,
                   lookback_days: int, per_query: int, now: datetime | None = None) -> list[dict[str, Any]]:
    """Most-viewed recent Shorts (<=180 s) for each query, with views-per-hour velocity."""
    now = now or datetime.now(timezone.utc)
    after = (now - timedelta(days=lookback_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    found: dict[str, str] = {}
    for q in queries:
        data = fetch_json(fetch, YT + "search", {
            "part": "snippet", "q": q, "type": "video", "videoDuration": "short", "order": "viewCount",
            "publishedAfter": after, "maxResults": per_query, "relevanceLanguage": language,
            "regionCode": region, "key": api_key,
        })
        for it in data.get("items", []):
            vid = (it.get("id") or {}).get("videoId")
            if vid and vid not in found:
                found[vid] = q
    out = []
    ids = list(found)
    for i in range(0, len(ids), 50):
        data = fetch_json(fetch, YT + "videos", {"part": "snippet,statistics,contentDetails",
                                                 "id": ",".join(ids[i:i + 50]), "key": api_key})
        for v in data.get("items", []):
            dur = iso_duration_seconds(v.get("contentDetails", {}).get("duration", ""))
            if not 0 < dur <= 180:
                continue  # not a Short
            sn, st = v.get("snippet", {}), v.get("statistics", {})
            published = datetime.fromisoformat(sn.get("publishedAt", now.isoformat()).replace("Z", "+00:00"))
            hours = max(1.0, (now - published).total_seconds() / 3600)
            views = int(st.get("viewCount", 0))
            vph = views / hours
            out.append({
                "kind": "trend_video", "title": sn.get("title", ""),
                "url": f"https://www.youtube.com/shorts/{v['id']}", "source": "youtube",
                "body": f"{sn.get('title', '')}\n{(sn.get('description') or '')[:400]}\n"
                        f"Tags: {', '.join((sn.get('tags') or [])[:10])}",
                "key": f"yt:{v['id']}",
                "meta": {"views": views, "likes": int(st.get("likeCount", 0)),
                         "comments": int(st.get("commentCount", 0)), "published_at": sn.get("publishedAt"),
                         "channel_title": sn.get("channelTitle"), "duration_s": dur,
                         "views_per_hour": round(vph, 1), "query": found[v["id"]],
                         "score": round(math.log10(vph + 1), 3)},
            })
    return out


# ---------------------------------------------------------------- Wikipedia (public)


def wikipedia_trending(fetch: Fetch, lang: str, day: date, top: int = 60) -> list[dict[str, Any]]:
    """Yesterday's most-read articles: a daily read on what people are curious about."""
    d = day - timedelta(days=1)
    data = fetch_json(fetch, f"https://wikimedia.org/api/rest_v1/metrics/pageviews/top/{lang}.wikipedia/all-access/"
                             f"{d.year}/{d.month:02d}/{d.day:02d}")
    arts = (data.get("items") or [{}])[0].get("articles", [])
    out = []
    for a in arts:
        name = a.get("article", "")
        if NON_ARTICLE.match(name) or ":" in name:
            continue
        title = name.replace("_", " ")
        out.append({"kind": "trend_wiki", "title": title, "body": title, "source": "wikipedia",
                    "url": f"https://{lang}.wikipedia.org/wiki/{urllib.parse.quote(name)}",
                    "key": f"wiki-top:{lang}:{name}",
                    "meta": {"views": a.get("views", 0), "rank": a.get("rank"), "day": d.isoformat(),
                             "score": round(math.log10(a.get("views", 0) + 1), 3)}})
        if len(out) >= top:
            break
    return out


def on_this_day(fetch: Fetch, lang: str, day: date, limit: int = 25) -> list[dict[str, Any]]:
    """Historical events on the publish date's month/day (anniversary hooks)."""
    data = fetch_json(fetch, f"https://{lang}.wikipedia.org/api/rest_v1/feed/onthisday/events/{day.month:02d}/{day.day:02d}")
    out = []
    for ev in data.get("events", [])[:limit]:
        pages = ev.get("pages") or [{}]
        page = pages[0]
        url = ((page.get("content_urls") or {}).get("desktop") or {}).get("page", "")
        text = ev.get("text", "")
        out.append({"kind": "on_this_day", "title": f"{ev.get('year')}: {text[:90]}", "source": "wikipedia",
                    "body": f"{ev.get('year')}: {text}\n{page.get('extract', '')}", "url": url,
                    "key": f"otd:{lang}:{day.month:02d}{day.day:02d}:{ev.get('year')}:{text[:40]}",
                    "meta": {"year": ev.get("year"), "date": f"{day.month:02d}-{day.day:02d}", "score": 1.0}})
    return out


def wikipedia_article(fetch: Fetch, lang: str, query: str, max_chars: int = 30000) -> dict[str, Any] | None:
    """Best-matching article's plain text via the core action API (stable long-term)."""
    api = f"https://{lang}.wikipedia.org/w/api.php"
    hits = fetch_json(fetch, api, {"action": "query", "list": "search", "srsearch": query, "srlimit": 1,
                                   "format": "json", "formatversion": 2}).get("query", {}).get("search", [])
    if not hits:
        return None
    title = hits[0]["title"]
    pages = fetch_json(fetch, api, {"action": "query", "prop": "extracts", "explaintext": 1, "redirects": 1,
                                    "titles": title, "format": "json", "formatversion": 2}).get("query", {}).get("pages", [])
    text = (pages[0].get("extract") if pages else "") or ""
    if not text:
        return None
    return {"kind": "wiki_article", "title": title, "body": text[:max_chars], "source": "wikipedia",
            "url": f"https://{lang}.wikipedia.org/wiki/{urllib.parse.quote(title.replace(' ', '_'))}",
            "key": f"wiki:{lang}:{title}", "meta": {"score": 1.0}}


# ---------------------------------------------------------------- RSS / Atom


def rss_items(fetch: Fetch, url: str, limit: int = 20) -> list[dict[str, Any]]:
    root = ET.fromstring(fetch(url, None))
    atom = "{http://www.w3.org/2005/Atom}"
    out = []
    items = root.findall(".//item")
    if items:
        for it in items[:limit]:
            title = (it.findtext("title") or "").strip()
            link = (it.findtext("link") or "").strip()
            desc = re.sub(r"<[^>]+>", " ", it.findtext("description") or "")
            out.append((title, link, desc, it.findtext("pubDate") or ""))
    else:
        for e in root.findall(f"{atom}entry")[:limit]:
            link_el = e.find(f"{atom}link")
            desc = re.sub(r"<[^>]+>", " ", e.findtext(f"{atom}summary") or e.findtext(f"{atom}content") or "")
            out.append(((e.findtext(f"{atom}title") or "").strip(), link_el.get("href", "") if link_el is not None else "",
                        desc, e.findtext(f"{atom}updated") or ""))
    return [{"kind": "rss", "title": t, "url": l, "source": url, "body": f"{t}\n{' '.join(d.split())[:800]}",
             "key": f"rss:{l or t}", "meta": {"published": p, "score": 0.5}} for t, l, d, p in out if t]


def mock_trends(niche: str, language: str) -> list[dict[str, Any]]:
    return [{"kind": "trend_video", "title": f"Mock trending short #{i} about {niche}", "source": "mock",
             "url": f"https://www.youtube.com/shorts/mock{i}", "body": f"A viral {niche} short", "key": f"mock:{niche}:{i}",
             "meta": {"views": 100000 * (3 - i), "views_per_hour": 2000 / (i + 1), "duration_s": 40,
                      "channel_title": "Mock Channel", "score": 3 - i * 0.2}} for i in range(3)]
