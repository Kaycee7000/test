# Channel playbook (YouTube account specialist)

Everything outside the code that decides whether these channels grow, get monetized and *stay* monetized.

> **Current setup:** the pipeline delivers finished videos to Backblaze B2 (`publish.mode: storage`) and does not
> post to YouTube. Each video's `.json` post kit and the daily `_manifest.csv` hold the title, description,
> hashtags, pinned-comment question and suggested posting time. When you post, **turn on "Altered or synthetic
> content"** in the upload flow. The posting team's day-to-day contract (what they get, how they send
> performance back) is in [TEAM_HANDOFF.md](TEAM_HANDOFF.md). Section 2 (API/OAuth) and `shorts sync` only apply
> if you later switch to `publish.mode: youtube`; with B2 delivery, the team's feedback CSVs feed section 5's
> learning loop instead.

Items marked **(verify)** are YouTube/Google rules that change. Re-check them on the official pages before launch.

## 1. Account structure

- **One Google account you control, with 2-step verification (security key or authenticator app).** Each channel is a separate **Brand Account** under it, so channels can have managers, be transferred, and don't share a personal identity.
- Add a second owner or manager you trust to every Brand Account (recovery path if you get locked out).
- **Phone-verify** every channel (Studio → Settings → Channel → Feature eligibility) to unlock intermediate features.
- Channel-level defaults: Studio → Settings → Upload defaults → category, language, "Not made for kids". Set the audience at the channel level to **No, not made for kids**.
- Branding per channel: a brandable name (not "History Facts 247"), a matching @handle, an avatar and banner in the same art direction as the videos, a 2-3 sentence keyword-rich description, and a link to the sister channel in the same language.

## 2. Google Cloud + OAuth (one-time)

1. Create a Google Cloud project, e.g. `shorts-factory`. Put its name in each channel YAML as `youtube.project` (quotas are per project).
2. Enable **YouTube Data API v3** and **YouTube Analytics API**.
3. OAuth consent screen: *External*. Add your Google account as a test user, then **publish the app ("In production")**.
   While an external app is in *Testing*, Google issues refresh tokens that **expire after 7 days**, so uploads would silently start failing every week. Unverified production apps work fine for your own accounts; you click through one "unverified app" warning.
4. Credentials → Create OAuth client ID → **Desktop app** → download JSON → save as `secrets/client_secret.json`.
5. On your laptop (it needs a browser): `shorts auth --channel history_en`. When Google asks, **pick the correct Brand Account/channel**. Paste the printed channel id into the YAML as `youtube.channel_id`; the uploader refuses to post if a token points at a different channel.
6. Copy `secrets/*.token.json` to the pod (`/workspace/<repo>/shorts-factory/secrets/`). Tokens refresh automatically.

### The API audit gate (plan for it)

Videos uploaded through `videos.insert` by an **unverified API project created after 28 July 2020 are locked to private** until the project passes YouTube's API compliance audit **(verify)**. Scheduled publishing does not work while the lock is on.

- Apply via the *YouTube API Services Audit and Quota Extension* form as soon as the project exists. Describe it honestly: an internal tool that uploads your own original videos to channels you own. Approval typically takes weeks.
- Until approved, run with `publish.mode: dry_run`, then either upload manually through Studio from `data/jobs/<date>/…/final.mp4` (the receipt JSON holds title, description, tags and slot), or keep testing formats while you wait.

### Quota

- **Uploads:** since June 2026, `videos.insert` has its own bucket: **100 uploads/day per project** **(verify** in Cloud Console → Quotas**)**. The pipeline counts uploads per Pacific-time day (when quotas reset) and stops at `publish.max_uploads_per_project_per_day`.
- Everything else (playlist inserts 50 units each, metrics reads 1 unit each) comes from the default 10,000 units/day. Enough for this workload.
- If you ever go past ~90 uploads/day, split channels across two projects (`youtube.project`) with separate OAuth clients, or request a quota extension.

## 3. AI disclosure and policy

- Every upload sets `status.containsSyntheticMedia = true` (the API form of Studio's "altered or synthetic content" toggle). Realistic AI imagery and cloned voices require disclosure; stylized content may not, but disclosing costs nothing and removes the risk. Keep it on.
- **Inauthentic content** (the July 2025 rename of "repetitious content"): mass-produced, templated or near-duplicate content loses monetization, with warnings, suspensions and removal from the Partner Program. Channels have been removed from YPP in sweeps. The defences this system relies on are listed in [NICHE_STRATEGY.md](NICHE_STRATEGY.md#what-makes-this-not-inauthentic-content). Your part:
  - keep the **editorial gate** on for the first two weeks (`publish.require_approval: true`, then `shorts review` / `shorts approve`), and keep watching a daily sample after;
  - never upload the same video to two channels, and never re-upload a deleted video;
  - keep each channel's voice and art direction distinct.
- **Misinformation**: history/science/finance are fact-checked by web search before production, but you are the publisher. Correct mistakes fast: delete and re-make rather than argue in comments.
- **Money channel**: stories and psychology only. No "buy this", no price predictions, no income claims (these trip both advertiser and scam policies).
- **Music**: only tracks licensed for monetized YouTube use (see `assets/music/README.md`).

## 4. Posting cadence

- Follow the ramp in each channel YAML: **3/day for 2 weeks → 6/day → 10-12/day**. Shorts are tested on small seed audiences first; flooding a brand-new channel dilutes those tests.
- Slots spread across the audience's local morning, lunch and evening windows (`schedule.windows`, local timezone), at least 45-50 minutes apart, with jittered minutes. Subscribers are **not** notified by default (`notify_subscribers: false`): at 10/day, notifications train people to mute you.
- Produce the next day's batch once a day; uploads go out as private with `publishAt`, and YouTube publishes them on time even with the pod off.

## 5. Metrics that matter and what to do about them

Shorts analytics in Studio (per video, after ~48 h):

| Metric | Healthy | Great | If low |
|---|---|---|---|
| **Stayed to watch** (vs swiped away) | ≥ 70% | ≥ 80% | The first 1.5 s failed: hook, headline, first image. Look at `hook_type` win rates in `shorts report` |
| **Average % viewed** | ≥ 80% | ≥ 100% (loops) | Middle sags: tighten escalation, shorten to 35-40 s (`target_seconds`) |
| Subscribers per 1k views | ≥ 1 | ≥ 3 | The channel promise is unclear: sharpen branding and series playlists |
| Engaged views vs views **(verify)** | | | Since March 2025 "views" counts every play and replay; YPP and revenue use **engaged views** |

The team's feedback CSVs (or `shorts sync` in `publish.mode: youtube`) bring in views, likes, comments, shares, subscribers gained and average % viewed. The pipeline then:
- marks each video a *win* if it beats the channel median on views **and** average % viewed (once it's 48 h old);
- shifts format selection toward winners (Thompson sampling) and feeds the top and bottom performers to the writer as "what works here";
- shows format and hook win rates in `shorts report --channel <id>`.

## 6. Weekly operator routine (≈ 30 min/day + 1 h/week)

**Daily (10-20 min):** open `data/jobs/<date>/review.html`, skim the batch at 2x, `shorts reject` anything off, approve the rest if the gate is on; pin the suggested question on yesterday's uploads; hide spam comments.

**Weekly (1 h):**
1. `shorts report` per channel: which formats and hook types win?
2. Adjust format `weight`s in the YAMLs; retire formats under 30% win rate after 30 videos; add one new format experiment.
3. Refill topics with your own seeds for timely angles: `shorts make --channel … --topic "…"`.
4. Add 5-10 new music tracks per mood; refresh the narrator reference clip if the voice starts sounding samey.
5. Check Studio for copyright claims, policy notices and the monetization tab.

## 7. Monetization roadmap

- **YouTube Partner Program (verify current thresholds):** full YPP needs 1,000 subscribers plus either 10M valid public Shorts views in 90 days or 4,000 public watch hours in 12 months. The lower tier (fan funding) needs 500 subscribers, 3 uploads in 90 days, and 3M Shorts views in 90 days or 3,000 watch hours.
- **Set expectations:** Shorts pay from a shared pool at low RPMs (typically a few cents per 1,000 views). Shorts are the **growth engine**; the money is in:
  1. **Long-form compilations** of your best Shorts (8-15 min, e.g. "The 40 Strangest Moments in History"), which earn long-form rates and are the natural next module for this pipeline;
  2. **Sponsorships** once a channel passes ~50k subscribers (history and money audiences attract premium sponsors);
  3. **Affiliate/newsletter** on the money channel (books, courses, tools; always disclosed).

## 8. Risk management

- **Strikes:** three Community Guidelines strikes in 90 days terminates a channel, and YouTube can terminate *related* channels for circumvention. Keep channels clean and separate in purpose; never use one to re-upload another's removed content.
- Never buy views, subscribers or engagement, and never use "sub4sub" or view bots. This is the fastest route to termination.
- Back up `data/shorts.db`, `config/`, `secrets/` and `assets/voices/` (the network volume is not a backup).
- Rotate any leaked credentials immediately (`secrets/` is gitignored; keep it that way).

## 9. Launch checklist

- [ ] Brand Accounts created, 2-step verification, phone-verified, branding done, "not made for kids" default
- [ ] Cloud project, both APIs enabled, consent screen **In production**, Desktop OAuth client in `secrets/`
- [ ] API audit form submitted
- [ ] `shorts auth` for every channel; `youtube.channel_id` filled in
- [ ] Narrator clip per channel in `assets/voices/`; music per mood; whoosh SFX
- [ ] `shorts doctor` all green on the pod
- [ ] 5 test videos per channel via `shorts make`, watched in full on a phone
- [ ] `launch_date` set; `publish.require_approval: true` for weeks 1-2
- [ ] `publish.mode: youtube` (after the audit) and the daily schedule running
