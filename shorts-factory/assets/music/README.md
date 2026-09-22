# Music library

One folder per mood. The folder names must match the `music_moods` in each channel YAML:

```
assets/music/
  dark_ambient/   mystery/   epic/          # history channels
  tension/        corporate_dark/  cinematic/  # money channel
  space_ambient/  wonder/                    # space channel
```

Any `.mp3 .wav .m4a .ogg .flac .aac` works. The renderer picks a track per video using the job id as the seed,
starts at a random offset so the same track never sounds identical twice, loops it if it is short,
ducks it under the narration with a sidechain compressor and fades it out at the end.

## Licensing: this is how channels get demonetized

Use only tracks you are licensed to monetize on YouTube:

- **YouTube Audio Library** (Studio → Audio Library). Free and safe for monetization. Filter by "No attribution required".
- A subscription library that whitelists your channel IDs (Epidemic Sound, Artlist, Musicbed...). Register every channel ID with them.
- Tracks you commissioned or generated yourself with a commercially licensed model.

Never use popular songs, "no copyright" uploads from random YouTube channels, or TikTok sounds. A Content ID
claim on a Short routes its revenue to the claimant, and repeated claims hurt the channel.

Aim for 15-30 tracks per mood. Variety matters: the same 3 tracks on 300 videos reads as templated content.
