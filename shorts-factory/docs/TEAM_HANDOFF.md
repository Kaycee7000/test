# Handoff: content system → YouTube team

Two separate operations with one shared bucket between them.

| | Content system (this repo) | YouTube team |
|---|---|---|
| Owns | Trends, scripts, voice, visuals, editing, QC | Channels, posting, thumbnails, comments, community, analytics |
| Access | Anthropic API (incl. web search), write access to the bucket | Channel logins, read access to the bucket, write access to `feedback/` only |
| Never touches | Channel accounts, OAuth, Studio | The pipeline, the GPU pod |

## What the team receives (every morning)

```
<bucket>/<niche>/<language>/<date>/
  <id>_<title-slug>.mp4     1080x1920, 35-50 s, captions burned in, -14 LUFS, ready to post
  <id>_<title-slug>.json    post kit
  <id>_<title-slug>.jpg     preview frame
  _manifest.csv             the day's videos for this niche + language, in suggested posting order
```

The **post kit** (`.json`) has: `title`, `description` (hashtags included), `tags`, `pinned_comment` (a question to
pin), `suggested_post_time_local` + `timezone`, `platform`, `format`, `hook_type`, `trend_ref` (the trend it rides,
or "evergreen"), `music_track` (keep for licence records), and `ai_disclosure`.

`_manifest.csv` opens in Google Sheets or Excel, with a 7-day download link per video.

## Posting checklist (per video)

1. Upload the `.mp4` as a Short to the channel for that niche + language.
2. Paste title, description and tags from the post kit.
3. **Turn on "Altered or synthetic content"** (AI narration and imagery).
4. Audience: not made for kids. Post at the suggested time (±30 min is fine).
5. Pin the `pinned_comment` question once it is live.
6. Something wrong (a factual error, a bad render, off-brand)? Don't post it. Note the `id` and tell the content
   owner; they run `shorts reject <id>` and a replacement is produced in the next run.

## What the team sends back (weekly, or ~48 h after posting)

This feedback is what lets the system learn which formats and hooks work. Two options, whichever is easier:

**A. The manifest.** Fill in the empty columns of `_manifest.csv` (`posted_url`, `posted_at`, `views`,
`avg_view_pct`, `likes`, `comments`, `shares`, `subscribers_gained`), then upload it to `<bucket>/feedback/`
under any name, e.g. `feedback/history-en-2026-09-23.csv`.

**B. A YouTube Studio export.** Studio → Analytics → Advanced mode → Content → include *Views* and *Average
percentage viewed* → Export → CSV. Upload that file to `<bucket>/feedback/`. Rows are matched by video title.

The pipeline imports new files automatically at the start of every run (also `shorts feedback pull`). Each file
is imported once; re-uploading a corrected file under the same name imports it again. After about 4 videos per
channel are 48 h old, `shorts report --channel <id>` shows format and hook win rates, and the writer starts seeing
"what works here" examples.

## Bucket access (Backblaze B2 application keys)

Create two keys for the team in B2 → Application Keys, both restricted to the bucket:
- **Read-only**: to download videos and manifests.
- **Read and write, restricted to the file-name prefix `feedback/`**: to upload performance CSVs.

The pipeline's own key (read and write, whole bucket) stays on the pod in `/workspace/secrets.env`.
