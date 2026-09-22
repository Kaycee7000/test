# Niche strategy (content manager)

## The one-line answer

**Launch "Untold History" as the flagship, clone it into Spanish, and run two sister channels: business/money stories (for ad rates) and space/science (for visual spectacle).** Four channels at 10-12 Shorts/day each gets you to ~42/day without any single channel looking like a spam farm.

## How the niches were scored

An AI Shorts niche has to survive five filters at once:

1. **Retention**: can a 45-second story hold people to the end and make them replay it?
2. **AI fit**: do today's image/voice models make it look *better* than stock footage, not worse?
3. **Monetization upside**: Shorts ad rates plus how well it funnels into long-form, sponsors and affiliates.
4. **Policy safety**: advertiser-friendly, low misinformation risk, and defensible under YouTube's July 2025 **inauthentic content** policy (mass-produced/templated content gets demonetized).
5. **Saturation**: how many faceless AI channels already fight for the same viewers.

| Niche | Retention | AI fit | Money | Policy safety | Saturation (5 = open) | Verdict |
|---|---|---|---|---|---|---|
| **Untold / dark history & mysteries** | 5 | 5 | 3 | 4 | 3 | **Flagship** |
| **Business & money stories** | 4 | 3 | 5 | 3 | 3 | **High-RPM channel** |
| **Space & physics** | 4 | 5 | 3 | 5 | 3 | **Spectacle channel** |
| Same winners in Spanish / Portuguese | 5 | 5 | 2 | 4 | 4 | **Multiplier** |
| Psychology "facts" | 3 | 2 | 3 | 3 | 1 | Skip: oversaturated, abstract visuals |
| AI / tech news | 3 | 3 | 4 | 2 | 2 | Later, needs same-day human editing |
| True crime | 5 | 3 | 2 | 1 | 2 | Skip: limited ads, victims' families, high risk |
| Reddit stories over gameplay | 4 | 1 | 1 | 1 | 1 | **Avoid**: the textbook "inauthentic content" pattern |
| Motivational / stoic quotes | 2 | 3 | 2 | 1 | 1 | **Avoid**: templated, heavily demonetized |
| Kids / animals | 4 | 3 | 1 | 2 | 2 | Skip: "made for kids" kills ads and comments |

### Why history wins

- **Built-in hooks.** "In 1518, 400 people danced until they died" is a scroll-stopper on its own. The story does the work, so AI production quality isn't a handicap.
- **AI visuals are an advantage.** There is no footage of 1518 Strasbourg. Painterly AI images look *intentional* and beat anything a stock-footage channel can do.
- **Infinite, evergreen supply.** Thousands of years of stories, and none of them go stale.
- **Loop-friendly.** Mystery structures ("...and that's why the village vanished") loop naturally, which pushes average viewed % above 100%.
- **Long-form funnel.** History has one of YouTube's strongest long-form audiences. Compilations of your best Shorts ("50 Strangest Moments in History") monetize at long-form rates.
- **Defensible.** Researched, fact-checked, original narration in a consistent voice is the opposite of the "mass-produced" pattern YouTube demonetizes.

### Why the other three

- **Money stories**: finance-adjacent audiences carry some of the highest ad rates on YouTube, and this is the best channel for newsletters and affiliate links. It is framed as stories and psychology, never advice, which keeps it out of "financial advice" risk.
- **Space**: the easiest niche for jaw-dropping AI visuals, globally watchable, and clearly educational.
- **Spanish history**: Spanish-language Shorts have far less high-production competition, and Latin American history is a huge underserved pool. Scripts are written natively in Spanish, not translated, and topics are chosen for that audience. It is a separate show, not duplicate content.

## Portfolio and ramp to 30-50/day

Posting 40 Shorts a day on one channel is the fastest way to look like a content farm and exhaust the audience you're testing on. Volume is spread across channels, and each channel ramps up (configured in each YAML under `schedule.ramp`):

| Phase | history_en | history_es | money_en | space_en | Total/day |
|---|---|---|---|---|---|
| Weeks 1-2: find what sticks | 3 | 3 | 3 | 3 | **12** |
| Weeks 3-5: double down | 6 | 6 | 6 | 6 | **24** |
| Week 6+: steady state | 12 | 10 | 10 | 10 | **42** |

Set `launch_date` in each channel file on the day you publish its first video; the ramp then runs automatically.
To get to 50: add a Portuguese history channel (`history_pt`, copy `history_es.yaml`, `language: pt`) once the Spanish one proves the playbook.

**Reallocation rule:** after 60 days, move slots from the weakest channel to the strongest. If a channel's median views per Short are under 1,000 *and* viewers stay under 60%, change its visual style, voice and formats once. If it still underperforms after another 30 days, retire it and clone the winner into a new language.

## Formats: the unit of experimentation

Each channel has 4-6 **formats** (see the YAMLs): repeatable story structures such as *The Untold Story*, *History's Worst Decisions*, *Still Unsolved*, *Myth vs Reality*, *You Wouldn't Survive*. They are not templates of text; each is a *shape* of story. The pipeline:

- picks formats per day by **Thompson sampling** on real performance (`content/strategy.py`), starting from your configured weights, then letting data take over;
- caps any format at 50% of a day's slots, so the channel never becomes one-note;
- tags every video with format and hook type, so `shorts report` shows what wins.

Kill a format when it has 30+ videos and wins under 30% of the time. Invent a new one to replace it: that's the content manager's main creative lever.

## Script anatomy (what the writer and critic enforce)

| Time | Beat | Rule |
|---|---|---|
| 0-1.5 s | **Hook** | ≤12 words; the most shocking concrete detail; no "Did you know", no greeting, no context |
| 1.5-5 s | Context + open loop | who/where/when in one line; a question the ending will answer |
| 5-35 s | Escalation | a new fact, twist or higher stake every 5-7 s; names, numbers, years |
| 35-45 s | Payoff + loop | answer the loop; the last line flows grammatically into the first line |

Plus: a new visual every 2.5-5 s (8-16 scenes); spoken-rhythm writing; no "like and subscribe"; every factual claim listed and web-verified before production.

## Packaging

- **Title** ≤60 chars, specific and curious, 100% true: *"The Village That Vanished Overnight in 1908"* beats *"CRAZY History Fact!!"*
- **Headline**: 2-6 words burned in during the hook (the "thumbnail" of a Short, since most viewers never see the real one).
- **Pinned-comment question**: generated per video to seed comments (you pin it manually; the API can't pin).
- **Series playlists**: set `playlist_id` per format so binge sessions stay on your channel.

## What makes this *not* "inauthentic content"

YouTube's policy targets content that is mass-produced, templated and low-value, not AI itself. This pipeline is built around that:

1. Every script is a unique, researched story with an original angle, checked by a critic model and a web fact-checker.
2. A consistent, owned narrator voice per channel (your cloned voice or a licensed one), not a default stock TTS voice.
3. A distinct art direction per channel, not random stock footage.
4. Formats rotate; topics are deduplicated against everything the channel has published.
5. Optional human editorial gate (`publish.require_approval: true`) with a daily review page. Use it for the first weeks and keep sampling after.
6. Honest AI disclosure (`containsSyntheticMedia: true`) on every upload.
