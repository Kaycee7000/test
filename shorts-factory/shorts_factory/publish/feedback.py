"""Performance feedback from the posting team, with no access to their channels.

The team drops CSVs into the storage `feedback/` folder (storage.feedback_prefix). Two shapes work:
  1. the daily `_manifest.csv` with its empty columns filled in (id, posted_url, views, avg_view_pct, ...),
  2. a YouTube Studio analytics export ("Content", "Video title", "Views", "Average percentage viewed (%)", ...).
Rows are matched to jobs by id, then by YouTube video id, then by fuzzy title. Matched rows land in the
metrics table, which re-enables the format bandit and the writer's "what works here" learnings.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from rapidfuzz import fuzz

from ..db import DB, now_iso

log = logging.getLogger(__name__)

ALIASES = {
    "id": ["id", "job_id", "job id"],
    "url": ["posted_url", "youtube_url", "url", "video url", "link", "content", "video id", "video_id"],
    "title": ["title", "video title"],
    "posted_at": ["posted_at", "video publish time", "publish time", "published", "published_at"],
    "views": ["views", "views_48h", "engaged views"],
    "avg_view_pct": ["avg_view_pct", "average percentage viewed (%)", "average percentage viewed", "avg viewed %",
                     "stayed to watch (%)"],
    "avg_view_duration": ["avg_view_duration", "average view duration"],
    "likes": ["likes"],
    "comments": ["comments", "comments added"],
    "shares": ["shares"],
    "subscribers_gained": ["subscribers_gained", "subscribers", "subscribers gained"],
}
YT_ID = re.compile(r"(?:shorts/|v=|youtu\.be/|^)([A-Za-z0-9_-]{11})(?:[?&/#]|$)")


def _num(v: Any) -> float | None:
    if v is None:
        return None
    s = str(v).strip().replace(",", "").replace("%", "")
    if not s:
        return None
    if re.fullmatch(r"\d+:\d{2}(:\d{2})?", s):  # h:mm:ss or m:ss durations
        parts = [int(p) for p in s.split(":")]
        return float(sum(p * 60 ** i for i, p in enumerate(reversed(parts))))
    try:
        return float(s)
    except ValueError:
        return None


def _date(v: str | None) -> datetime | None:
    if not v:
        return None
    for fmt in (None, "%b %d, %Y", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            d = datetime.fromisoformat(v) if fmt is None else datetime.strptime(v.strip(), fmt)
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def normalize(row: dict[str, str]) -> dict[str, Any]:
    # hand-edited sheets can have ragged rows: csv puts extra cells in a list under key None; ignore them
    low = {k.strip().lower(): (v or "").strip() for k, v in row.items() if isinstance(k, str) and not isinstance(v, list)}
    out: dict[str, Any] = {}
    for field, names in ALIASES.items():
        for n in names:
            if low.get(n):
                out[field] = low[n]
                break
    m = YT_ID.search(out.get("url", ""))
    out["video_id"] = m.group(1) if m else None
    return out


def parse_csv(data: bytes) -> list[dict[str, Any]]:
    text = data.decode("utf-8-sig", errors="replace")
    rows = [normalize(r) for r in csv.DictReader(io.StringIO(text))]
    return [r for r in rows if (r.get("url") or "").lower() != "total" and (r.get("title") or "").lower() != "total"]


def import_rows(db: DB, rows: list[dict[str, Any]]) -> tuple[int, int]:
    jobs = db.jobs(state="uploaded")
    by_id = {j["id"]: j for j in jobs}
    by_vid = {j["video_id"]: j for j in jobs if j["video_id"]}
    now = datetime.now(timezone.utc)
    matched, unmatched, metrics = 0, 0, []
    for r in rows:
        if _num(r.get("views")) is None:
            continue  # manifest row the team hasn't filled in yet
        job = by_id.get(r.get("id", "")) or (by_vid.get(r["video_id"]) if r.get("video_id") else None)
        if job is None and r.get("title"):
            best = max(jobs, key=lambda j: fuzz.token_set_ratio(r["title"].lower(), (j["title"] or "").lower()),
                       default=None)
            if best is not None and fuzz.token_set_ratio(r["title"].lower(), (best["title"] or "").lower()) >= 90:
                job = best
        if job is None:
            unmatched += 1
            continue
        matched += 1
        vid = r.get("video_id") or job["video_id"] or f"job:{job['id']}"
        if job["video_id"] != vid:
            db.update_job(job["id"], video_id=vid)
            by_vid[vid] = job
        posted = _date(r.get("posted_at")) or _date(job["slot_utc"]) or _date(job["uploaded_at"]) or now
        metrics.append({
            "video_id": vid, "fetched_at": now_iso(), "age_hours": round((now - posted).total_seconds() / 3600, 1),
            "views": int(_num(r.get("views")) or 0), "likes": _int(r.get("likes")), "comments": _int(r.get("comments")),
            "shares": _int(r.get("shares")), "subscribers_gained": _int(r.get("subscribers_gained")),
            "avg_view_pct": _num(r.get("avg_view_pct")), "avg_view_duration": _num(r.get("avg_view_duration")),
        })
    db.add_metrics(metrics)
    return matched, unmatched


def _int(v: Any) -> int | None:
    n = _num(v)
    return int(n) if n is not None else None


FEEDBACK_SCHEMA = """CREATE TABLE IF NOT EXISTS feedback_files (
    key TEXT NOT NULL, etag TEXT NOT NULL, imported_at TEXT NOT NULL, matched INTEGER, unmatched INTEGER,
    PRIMARY KEY (key, etag))"""


def import_from_store(store: Any, prefix: str, db: DB) -> list[dict[str, Any]]:
    """Import every new or changed CSV under `prefix` exactly once."""
    db.conn.execute(FEEDBACK_SCHEMA)
    done = []
    for key, etag in store.list(prefix):
        if not key.lower().endswith(".csv"):
            continue
        if db.conn.execute("SELECT 1 FROM feedback_files WHERE key=? AND etag=?", (key, etag)).fetchone():
            continue
        try:
            matched, unmatched = import_rows(db, parse_csv(store.get(key)))
        except Exception as e:
            log.warning("feedback %s could not be imported: %s", key, e)
            continue
        with db.conn:
            db.conn.execute("INSERT OR REPLACE INTO feedback_files VALUES (?,?,?,?,?)",
                            (key, etag, now_iso(), matched, unmatched))
        log.info("feedback %s: %d rows matched, %d unmatched", key, matched, unmatched)
        done.append({"key": key, "matched": matched, "unmatched": unmatched})
    return done


def summary(db: DB) -> str:
    rows = db.conn.execute("SELECT COUNT(*), MAX(fetched_at) FROM metrics").fetchone()
    return json.dumps({"metric_rows": rows[0], "latest": rows[1]})
