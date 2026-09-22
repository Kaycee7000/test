"""SQLite state store. Every stage is idempotent: it only picks up jobs in its input state."""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

# Linear job lifecycle. A stage moves jobs from STATES[i-1] to STATES[i].
STATES = [
    "planned", "scripted", "voiced", "aligned", "imaged", "animated", "rendered", "ready", "uploaded",
]
TERMINAL = {"failed", "rejected"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS topics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel TEXT NOT NULL,
    title TEXT NOT NULL,
    angle TEXT NOT NULL DEFAULT '',
    format TEXT NOT NULL,
    keywords TEXT NOT NULL DEFAULT '[]',
    priority REAL NOT NULL DEFAULT 5,
    status TEXT NOT NULL DEFAULT 'new',   -- new | used | rejected
    created_at TEXT NOT NULL,
    used_at TEXT
);
CREATE INDEX IF NOT EXISTS topics_channel ON topics(channel, status);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    channel TEXT NOT NULL,
    topic_id INTEGER,
    publish_date TEXT NOT NULL,
    state TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    format TEXT,
    hook_type TEXT,
    title TEXT,
    dir TEXT NOT NULL,
    slot_utc TEXT,
    video_id TEXT,
    project TEXT,
    uploaded_at TEXT,
    data TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_state ON jobs(state);
CREATE INDEX IF NOT EXISTS jobs_channel_date ON jobs(channel, publish_date);

CREATE TABLE IF NOT EXISTS trend_briefs (
    channel TEXT NOT NULL,
    day TEXT NOT NULL,              -- publish date the brief was made for
    brief TEXT NOT NULL,
    topics_added INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    PRIMARY KEY (channel, day)
);

CREATE TABLE IF NOT EXISTS metrics (
    video_id TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    age_hours REAL,
    views INTEGER, likes INTEGER, comments INTEGER, shares INTEGER,
    subscribers_gained INTEGER,
    avg_view_pct REAL, avg_view_duration REAL,
    PRIMARY KEY (video_id, fetched_at)
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DB:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(topics)")}
        if "trend_ref" not in cols:
            with self.conn:
                self.conn.execute("ALTER TABLE topics ADD COLUMN trend_ref TEXT")
        with self.conn:  # the retired research stage used a 'researched' state
            self.conn.execute("UPDATE jobs SET state='planned' WHERE state='researched'")

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self.conn:
            yield self.conn

    # ------------------------------------------------------------ topics

    def add_topics(self, channel: str, ideas: list[dict[str, Any]]) -> int:
        with self.tx() as c:
            c.executemany(
                "INSERT INTO topics(channel,title,angle,format,keywords,priority,trend_ref,created_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                [(channel, i["title"], i.get("angle", ""), i["format"], json.dumps(i.get("keywords", [])),
                  float(i.get("priority", 5)), i.get("trend_ref"), now_iso()) for i in ideas],
            )
        return len(ideas)

    def topics(self, channel: str, status: str | None = None, limit: int = 10_000) -> list[sqlite3.Row]:
        q, args = "SELECT * FROM topics WHERE channel=?", [channel]
        if status:
            q += " AND status=?"
            args.append(status)
        q += " ORDER BY priority DESC, id ASC LIMIT ?"
        args.append(limit)
        return self.conn.execute(q, args).fetchall()

    def mark_topic(self, topic_id: int, status: str) -> None:
        with self.tx() as c:
            c.execute("UPDATE topics SET status=?, used_at=? WHERE id=?", (status, now_iso(), topic_id))

    # ------------------------------------------------------------ jobs

    def create_job(self, channel: str, topic_id: int | None, publish_date: str, fmt: str, title: str,
                   workdir: Path, data: dict[str, Any] | None = None) -> str:
        jid = f"{publish_date.replace('-', '')}-{channel}-{uuid.uuid4().hex[:6]}"
        jdir = workdir / "jobs" / publish_date / channel / jid
        jdir.mkdir(parents=True, exist_ok=True)
        ts = now_iso()
        with self.tx() as c:
            c.execute(
                "INSERT INTO jobs(id,channel,topic_id,publish_date,state,format,title,dir,data,created_at,updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (jid, channel, topic_id, publish_date, "planned", fmt, title, str(jdir), json.dumps(data or {}), ts, ts),
            )
        return jid

    def jobs(self, state: str | None = None, channel: str | None = None, publish_date: str | None = None,
             ids: list[str] | None = None) -> list[sqlite3.Row]:
        q, args = "SELECT * FROM jobs WHERE 1=1", []
        if state:
            q += " AND state=?"
            args.append(state)
        if channel:
            q += " AND channel=?"
            args.append(channel)
        if publish_date:
            q += " AND publish_date=?"
            args.append(publish_date)
        if ids:
            q += f" AND id IN ({','.join('?' * len(ids))})"
            args.extend(ids)
        return self.conn.execute(q + " ORDER BY created_at, id", args).fetchall()

    def job(self, jid: str) -> sqlite3.Row:
        row = self.conn.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
        if row is None:
            raise KeyError(jid)
        return row

    def update_job(self, jid: str, **fields: Any) -> None:
        if "data" in fields and not isinstance(fields["data"], str):
            fields["data"] = json.dumps(fields["data"])
        fields["updated_at"] = now_iso()
        cols = ", ".join(f"{k}=?" for k in fields)
        with self.tx() as c:
            c.execute(f"UPDATE jobs SET {cols} WHERE id=?", (*fields.values(), jid))

    def merge_data(self, jid: str, **extra: Any) -> dict[str, Any]:
        data = json.loads(self.job(jid)["data"])
        data.update(extra)
        self.update_job(jid, data=data)
        return data

    def fail(self, jid: str, error: str, max_attempts: int = 3) -> None:
        """Record an error; the job stays in its state for retry until attempts run out."""
        row = self.job(jid)
        attempts = row["attempts"] + 1
        fields: dict[str, Any] = {"attempts": attempts, "error": error[-2000:]}
        if attempts >= max_attempts:
            fields["state"] = "failed"
        self.update_job(jid, **fields)

    def count_scheduled(self, channel: str, publish_date: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE channel=? AND publish_date=? AND state NOT IN ('failed','rejected')",
            (channel, publish_date),
        ).fetchone()
        return int(row[0])

    def upload_times(self, project: str, since_iso: str) -> list[str]:
        rows = self.conn.execute(
            "SELECT uploaded_at FROM jobs WHERE project=? AND uploaded_at >= ?", (project, since_iso)
        ).fetchall()
        return [r[0] for r in rows]

    def recent_titles(self, channel: str, limit: int = 300) -> list[str]:
        rows = self.conn.execute(
            "SELECT title FROM jobs WHERE channel=? AND title IS NOT NULL ORDER BY created_at DESC LIMIT ?",
            (channel, limit),
        ).fetchall()
        return [r[0] for r in rows]

    # ------------------------------------------------------------ trends

    def trend_brief(self, channel: str, day: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM trend_briefs WHERE channel=? AND day=?", (channel, day)).fetchone()

    def save_trend_brief(self, channel: str, day: str, brief: str) -> None:
        with self.tx() as c:
            c.execute("INSERT OR REPLACE INTO trend_briefs(channel, day, brief, topics_added, created_at) "
                      "VALUES (?,?,?,0,?)", (channel, day, brief, now_iso()))

    def mark_trend_topics(self, channel: str, day: str, n: int) -> None:
        with self.tx() as c:
            c.execute("UPDATE trend_briefs SET topics_added=? WHERE channel=? AND day=?", (n, channel, day))

    # ------------------------------------------------------------ metrics

    def add_metrics(self, rows: list[dict[str, Any]]) -> None:
        cols = ["video_id", "fetched_at", "age_hours", "views", "likes", "comments", "shares",
                "subscribers_gained", "avg_view_pct", "avg_view_duration"]
        with self.tx() as c:
            c.executemany(
                f"INSERT OR REPLACE INTO metrics({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                [tuple(r.get(k) for k in cols) for r in rows],
            )

    def performance(self, channel: str) -> list[dict[str, Any]]:
        """Latest metrics snapshot per uploaded video of a channel, joined with job metadata."""
        q = """
        SELECT j.id, j.title, j.format, j.hook_type, j.video_id, j.data, m.views, m.avg_view_pct,
               m.likes, m.subscribers_gained, m.age_hours
        FROM jobs j JOIN metrics m ON m.video_id = j.video_id
        WHERE j.channel=? AND m.fetched_at = (SELECT MAX(fetched_at) FROM metrics WHERE video_id=j.video_id)
        ORDER BY j.uploaded_at DESC
        """
        return [dict(r) for r in self.conn.execute(q, (channel,)).fetchall()]
