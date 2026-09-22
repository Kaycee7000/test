"""YouTube Data API v3 + Analytics API v2: OAuth per channel, scheduled uploads, playlists, metrics."""
from __future__ import annotations

import json
import logging
import random
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..config import ChannelCfg, Settings

log = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]
RETRIABLE_STATUS = {500, 502, 503, 504}


class QuotaExhausted(RuntimeError):
    pass


class WrongChannel(RuntimeError):
    pass


def token_path(s: Settings, ch: ChannelCfg) -> Path:
    return s.path(ch.youtube.token_file or Path(s.publish.secrets_dir) / f"{ch.id}.token.json")


def authorize(s: Settings, ch: ChannelCfg, port: int = 8765) -> dict[str, str]:
    """Interactive OAuth. Run on a machine with a browser, then copy the token file to the pod."""
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    secrets = s.path(Path(s.publish.secrets_dir) / s.publish.client_secrets)
    flow = InstalledAppFlow.from_client_secrets_file(str(secrets), SCOPES)
    creds = flow.run_local_server(
        host="localhost", port=port, open_browser=True, prompt="consent",
        authorization_prompt_message=(
            f"Authorize the channel for '{ch.id}'. Sign in and PICK THE RIGHT CHANNEL/BRAND ACCOUNT:\n{{url}}"),
    )
    tp = token_path(s, ch)
    tp.parent.mkdir(parents=True, exist_ok=True)
    tp.write_text(creds.to_json())
    tp.chmod(0o600)
    items = build("youtube", "v3", credentials=creds, cache_discovery=False).channels().list(
        part="snippet", mine=True).execute().get("items", [])
    return {"id": items[0]["id"], "title": items[0]["snippet"]["title"]} if items else {}


def load_credentials(s: Settings, ch: ChannelCfg):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    tp = token_path(s, ch)
    if not tp.exists():
        raise FileNotFoundError(f"no OAuth token for {ch.id} at {tp}; run `shorts auth --channel {ch.id}`")
    creds = Credentials.from_authorized_user_file(str(tp), SCOPES)
    if not creds.valid and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        tp.write_text(creds.to_json())
    return creds


def build_metadata(ch: ChannelCfg, script: dict[str, Any]) -> dict[str, Any]:
    tags = [f"#{h}" for h in script.get("hashtags", [])]
    desc = script.get("description", "").strip()
    if tags:
        desc += "\n\n" + " ".join(tags)
    return {
        "title": script["title"][:100],
        "description": desc[:4900],
        "tags": script.get("tags", []),
        "language": ch.language,
    }


class YouTubePublisher:
    def __init__(self, s: Settings, ch: ChannelCfg):
        from googleapiclient.discovery import build

        self.s, self.ch = s, ch
        self.creds = load_credentials(s, ch)
        self.yt = build("youtube", "v3", credentials=self.creds, cache_discovery=False)
        self._verified = False

    def verify_channel(self) -> str:
        items = self.yt.channels().list(part="id", mine=True).execute().get("items", [])
        if not items:
            raise WrongChannel(f"token for {self.ch.id} has no YouTube channel")
        cid = items[0]["id"]
        if self.ch.youtube.channel_id and cid != self.ch.youtube.channel_id:
            raise WrongChannel(f"token for {self.ch.id} belongs to {cid}, expected {self.ch.youtube.channel_id}")
        self._verified = True
        return cid

    def upload(self, video: Path, meta: dict[str, Any], publish_at: datetime, job_id: str) -> str:
        from googleapiclient.errors import HttpError
        from googleapiclient.http import MediaFileUpload

        if not self._verified:
            self.verify_channel()
        body = {
            "snippet": {
                "title": meta["title"], "description": meta["description"], "tags": meta["tags"],
                "categoryId": self.ch.youtube.category_id,
                "defaultLanguage": meta["language"], "defaultAudioLanguage": meta["language"],
            },
            "status": {
                "privacyStatus": "private",  # becomes public at publishAt
                "publishAt": publish_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "selfDeclaredMadeForKids": self.ch.youtube.made_for_kids,
                "containsSyntheticMedia": self.ch.youtube.contains_synthetic_media,
                "embeddable": True,
                "license": "youtube",
            },
        }
        media = MediaFileUpload(str(video), mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True)
        req = self.yt.videos().insert(part="snippet,status", body=body, media_body=media,
                                      notifySubscribers=self.s.publish.notify_subscribers)
        response, attempt = None, 0
        while response is None:
            try:
                _, response = req.next_chunk()
            except HttpError as e:
                reason = _reason(e)
                if reason in {"quotaExceeded", "uploadLimitExceeded", "dailyLimitExceeded"}:
                    raise QuotaExhausted(reason) from e
                retriable = e.resp.status in RETRIABLE_STATUS or reason == "rateLimitExceeded"
                if not retriable or attempt >= 6:
                    raise
                attempt += 1
                time.sleep(min(64, 2 ** attempt) + random.random())
            except (OSError, TimeoutError):
                if attempt >= 6:
                    raise
                attempt += 1
                time.sleep(min(64, 2 ** attempt) + random.random())
        vid = response["id"]
        log.info("%s: uploaded %s as %s, publishes %s", self.ch.id, job_id, vid, body["status"]["publishAt"])
        return vid

    def add_to_playlist(self, video_id: str, playlist_id: str) -> None:
        self.yt.playlistItems().insert(part="snippet", body={"snippet": {
            "playlistId": playlist_id, "resourceId": {"kind": "youtube#video", "videoId": video_id}}}).execute()

    def metrics(self, video_ids: list[str], since: date) -> dict[str, dict[str, Any]]:
        """Public counters (Data API, near real-time) merged with retention (Analytics API, ~48h delay)."""
        from googleapiclient.discovery import build

        out: dict[str, dict[str, Any]] = {}
        for i in range(0, len(video_ids), 50):
            chunk = video_ids[i:i + 50]
            for it in self.yt.videos().list(part="statistics", id=",".join(chunk)).execute().get("items", []):
                st = it.get("statistics", {})
                out[it["id"]] = {"views": int(st.get("viewCount", 0)), "likes": int(st.get("likeCount", 0)),
                                 "comments": int(st.get("commentCount", 0))}
        ya = build("youtubeAnalytics", "v2", credentials=self.creds, cache_discovery=False)
        for i in range(0, len(video_ids), 50):
            chunk = video_ids[i:i + 50]
            try:
                rep = ya.reports().query(
                    ids="channel==MINE", startDate=since.isoformat(), endDate=date.today().isoformat(),
                    metrics="views,averageViewPercentage,averageViewDuration,shares,subscribersGained",
                    dimensions="video", filters="video==" + ",".join(chunk), maxResults=200,
                ).execute()
            except Exception as e:  # analytics lag/permissions should not block the counters
                log.warning("analytics query failed: %s", e)
                continue
            for row in rep.get("rows", []):
                vid, _views, avp, avd, shares, subs = row
                out.setdefault(vid, {}).update({"avg_view_pct": float(avp), "avg_view_duration": float(avd),
                                                "shares": int(shares), "subscribers_gained": int(subs)})
        return out


class DryRunPublisher:
    """Writes the exact upload request next to the video instead of calling YouTube."""

    def __init__(self, s: Settings, ch: ChannelCfg):
        self.s, self.ch = s, ch

    def verify_channel(self) -> str:
        return "dry-run"

    def upload(self, video: Path, meta: dict[str, Any], publish_at: datetime, job_id: str) -> str:
        receipt = {"video": str(video), "meta": meta, "publish_at": publish_at.isoformat(),
                   "category_id": self.ch.youtube.category_id,
                   "contains_synthetic_media": self.ch.youtube.contains_synthetic_media}
        (video.parent / "upload_receipt.json").write_text(json.dumps(receipt, indent=1))
        return f"dryrun-{job_id}"

    def add_to_playlist(self, video_id: str, playlist_id: str) -> None:
        pass

    def metrics(self, video_ids: list[str], since: date) -> dict[str, dict[str, Any]]:
        return {}


def make_publisher(s: Settings, ch: ChannelCfg):
    return YouTubePublisher(s, ch) if s.publish.mode == "youtube" else DryRunPublisher(s, ch)


def _reason(e: Any) -> str:
    try:
        return json.loads(e.content)["error"]["errors"][0]["reason"]
    except Exception:
        return ""


def lookback(days: int = 60) -> date:
    return (datetime.now(timezone.utc) - timedelta(days=days)).date()
