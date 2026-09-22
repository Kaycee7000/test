"""Deliver finished videos to Backblaze B2 (S3-compatible API) or a local folder, grouped by niche.

Layout (default key_template "{niche}/{language}/{date}/{stem}"):

    history/en/2026-09-23/20260923-history_en-1a2b3c_the-village-that-vanished-in-1908.mp4
    history/en/2026-09-23/20260923-history_en-1a2b3c_the-village-that-vanished-in-1908.json   <- post kit
    history/en/2026-09-23/20260923-history_en-1a2b3c_the-village-that-vanished-in-1908.jpg    <- preview
    history/en/2026-09-23/_manifest.csv   <- the day's videos for that niche + language, in posting order
"""
from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
import shutil
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from ..config import ChannelCfg, Settings
from ..db import DB

log = logging.getLogger(__name__)

MEDIA = ["images", "voice", "clips", "words", "voice.wav", "final.mp4", "preview.jpg"]
AI_NOTE = ("Contains AI-generated narration and imagery. When posting to YouTube, turn on "
           "'Altered or synthetic content' in the upload flow.")


class StorageError(RuntimeError):
    pass


def slugify(text: str, max_len: int = 60) -> str:
    ascii_ = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_.lower()).strip("-")
    return slug[:max_len].rstrip("-") or "video"


def object_base(s: Settings, ch: ChannelCfg, job: Any, title: str) -> str:
    """Object key without extension for a job's files."""
    key = s.storage.key_template.format(
        niche=ch.folder, language=ch.language, date=job["publish_date"], channel=ch.id,
        format=job["format"] or "", stem=f"{job['id']}_{slugify(title)}",
    )
    return "/".join(p.strip("/") for p in (s.storage.prefix, key) if p.strip("/"))


def manifest_base(s: Settings, ch: ChannelCfg, day: str) -> str:
    key = s.storage.key_template.format(niche=ch.folder, language=ch.language, date=day, channel=ch.id,
                                        format="", stem="_manifest")
    return "/".join(p.strip("/") for p in (s.storage.prefix, key) if p.strip("/"))


def post_kit(ch: ChannelCfg, job: Any, script: dict[str, Any], data: dict[str, Any], base: str) -> dict[str, Any]:
    """Everything a person (or a later uploader) needs to publish the video well."""
    slot = datetime.fromisoformat(job["slot_utc"]) if job["slot_utc"] else None
    tags = " ".join(f"#{h}" for h in script.get("hashtags", []))
    crit = (data.get("review") or {}).get("critique") or {}
    return {
        "id": job["id"],
        "niche": ch.folder,
        "language": ch.language,
        "channel": ch.id,
        "channel_name": ch.name,
        "platform": ch.platform,
        "format": job["format"],
        "hook_type": job["hook_type"],
        "title": script.get("title"),
        "headline": script.get("headline"),
        "description": (script.get("description", "").strip() + (f"\n\n{tags}" if tags else "")).strip(),
        "hashtags": script.get("hashtags", []),
        "tags": script.get("tags", []),
        "pinned_comment": script.get("pinned_comment"),
        "suggested_post_time_utc": slot.isoformat() if slot else None,
        "suggested_post_time_local": slot.astimezone(ZoneInfo(ch.schedule.timezone)).strftime("%Y-%m-%d %H:%M")
        if slot else None,
        "timezone": ch.schedule.timezone,
        "duration_s": data.get("duration"),
        "trend_ref": (data.get("topic") or {}).get("trend_ref"),
        "music_track": data.get("music"),  # keep for licence records
        "ai_disclosure": AI_NOTE,
        "critic_scores": {k: v for k, v in crit.items() if k.endswith("_score")},
        "narration": [sc.get("narration", "") for sc in script.get("scenes", [])],
        "files": {"video": f"{base}.mp4", "preview": f"{base}.jpg", "post_kit": f"{base}.json"},
    }


class Store(Protocol):
    name: str

    def put(self, local: Path, key: str, content_type: str) -> None: ...
    def put_bytes(self, data: bytes, key: str, content_type: str) -> None: ...
    def url(self, key: str) -> str | None: ...
    def check(self) -> str: ...
    def list(self, prefix: str) -> list[tuple[str, str]]: ...
    def get(self, key: str) -> bytes: ...


class B2Store:
    """Backblaze B2 through its S3-compatible API (also works for R2, Wasabi, S3 by changing endpoint)."""

    name = "b2"

    def __init__(self, s: Settings):
        import boto3
        from botocore.config import Config

        key_id, app_key = os.environ.get("B2_KEY_ID"), os.environ.get("B2_APPLICATION_KEY")
        if not (key_id and app_key):
            raise StorageError("set B2_KEY_ID and B2_APPLICATION_KEY (a B2 application key for the bucket)")
        if not s.storage.bucket:
            raise StorageError("set storage.bucket in settings.yaml or B2_BUCKET")
        self.bucket = s.storage.bucket
        self.presign_days = s.storage.presign_days
        endpoint = s.storage.endpoint
        m = re.search(r"s3\.([a-z0-9-]+)\.backblazeb2\.com", endpoint or "")
        self.s3 = boto3.client(
            "s3", endpoint_url=endpoint, region_name=m.group(1) if m else "us-east-1",
            aws_access_key_id=key_id, aws_secret_access_key=app_key,
            # B2 rejects the checksum headers newer boto3 sends by default.
            config=Config(signature_version="s3v4", request_checksum_calculation="when_required",
                          response_checksum_validation="when_required",
                          retries={"max_attempts": 8, "mode": "standard"}),
        )

    def put(self, local: Path, key: str, content_type: str) -> None:
        size = local.stat().st_size
        # put_object rather than upload_file: the managed transfer layer has ignored the checksum
        # setting above in some versions, and a Short is far below the 5 GB single-PUT limit.
        with open(local, "rb") as f:
            self.s3.put_object(Bucket=self.bucket, Key=key, Body=f, ContentType=content_type)
        remote = self.s3.head_object(Bucket=self.bucket, Key=key)["ContentLength"]
        if remote != size:
            raise StorageError(f"{key}: uploaded {remote} bytes, expected {size}")

    def put_bytes(self, data: bytes, key: str, content_type: str) -> None:
        self.s3.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)

    def url(self, key: str) -> str | None:
        if self.presign_days <= 0:
            return None
        return self.s3.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=min(7, self.presign_days) * 86400)

    def check(self) -> str:
        self.s3.head_bucket(Bucket=self.bucket)
        return f"b2://{self.bucket}"

    def list(self, prefix: str) -> list[tuple[str, str]]:
        """(key, etag) for every object under prefix."""
        out = []
        for page in self.s3.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=prefix):
            out += [(o["Key"], o.get("ETag", "").strip('"')) for o in page.get("Contents", [])]
        return out

    def get(self, key: str) -> bytes:
        return self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read()


class LocalStore:
    """Same layout on local disk: offline runs, tests, or syncing with rclone yourself."""

    name = "local"

    def __init__(self, s: Settings):
        self.root = s.path(s.storage.local_dir)

    def put(self, local: Path, key: str, content_type: str) -> None:
        dst = self.root / key
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local, dst)

    def put_bytes(self, data: bytes, key: str, content_type: str) -> None:
        dst = self.root / key
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)

    def url(self, key: str) -> str | None:
        return str(self.root / key)

    def check(self) -> str:
        self.root.mkdir(parents=True, exist_ok=True)
        return str(self.root)

    def list(self, prefix: str) -> list[tuple[str, str]]:
        base = self.root / prefix
        files = [p for p in base.rglob("*") if p.is_file()] if base.exists() else []
        return [(str(p.relative_to(self.root)), f"{p.stat().st_size}-{int(p.stat().st_mtime)}") for p in sorted(files)]

    def get(self, key: str) -> bytes:
        return (self.root / key).read_bytes()


def make_store(s: Settings) -> Store:
    return B2Store(s) if s.storage.provider == "b2" else LocalStore(s)


def deliver(store: Store, s: Settings, ch: ChannelCfg, job: Any, script: dict[str, Any],
            data: dict[str, Any]) -> dict[str, Any]:
    jdir = Path(job["dir"])
    base = object_base(s, ch, job, script.get("title") or job["title"] or "video")
    kit = post_kit(ch, job, script, data, base)
    store.put(jdir / "final.mp4", f"{base}.mp4", "video/mp4")
    if (jdir / "preview.jpg").exists():
        store.put(jdir / "preview.jpg", f"{base}.jpg", "image/jpeg")
    store.put_bytes(json.dumps(kit, indent=1, ensure_ascii=False).encode(), f"{base}.json", "application/json")
    return {"provider": store.name, "bucket": getattr(store, "bucket", None), "key": f"{base}.mp4", "kit": kit}


def cleanup_local(jdir: Path, mode: str) -> None:
    if mode == "all":
        shutil.rmtree(jdir, ignore_errors=True)
    elif mode == "media":
        for name in MEDIA:
            p = jdir / name
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            elif p.exists():
                p.unlink()


MANIFEST_COLUMNS = ["suggested_post_time_local", "title", "video", "download_url", "description",
                    "pinned_comment", "duration_s", "format", "hook_type", "channel", "id",
                    # filled in by the posting team ~48 h after posting, then dropped into feedback/
                    "posted_url", "posted_at", "views", "avg_view_pct", "likes", "comments", "shares",
                    "subscribers_gained"]


def write_manifests(store: Store, s: Settings, db: DB, channels: dict[str, ChannelCfg],
                    touched: set[tuple[str, str]]) -> list[str]:
    """One manifest per (niche, language, date): the day's videos in suggested posting order."""
    groups: dict[str, tuple[ChannelCfg, str, list[str]]] = {}
    for cid, day in touched:
        ch = channels[cid]
        mbase = manifest_base(s, ch, day)
        members = [c.id for c in channels.values() if c.folder == ch.folder and c.language == ch.language]
        groups[mbase] = (ch, day, members)
    written = []
    for mbase, (ch, day, members) in groups.items():
        rows = []
        for cid in members:
            for job in db.jobs(state="uploaded", channel=cid, publish_date=day):
                kit = (json.loads(job["data"]).get("delivery") or {}).get("kit")
                if kit:
                    rows.append(kit)
        rows.sort(key=lambda k: k.get("suggested_post_time_utc") or "")
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=MANIFEST_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for k in rows:
            w.writerow({**k, "video": k["files"]["video"], "download_url": store.url(k["files"]["video"]) or ""})
        store.put_bytes(buf.getvalue().encode("utf-8-sig"), f"{mbase}.csv", "text/csv")
        store.put_bytes(json.dumps(rows, indent=1, ensure_ascii=False).encode(), f"{mbase}.json", "application/json")
        written.append(f"{mbase}.csv")
    return written
