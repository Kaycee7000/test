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

## Generate it with AI (one command)

```bash
shorts music                          # every mood your channels use, 20 tracks each (music.tracks_per_mood)
shorts music --moods epic --count 5   # top up one mood
```

Tracks are made on the pod's GPU by ACE-Step 1.5 (MIT licence; its authors permit commercial use of the output
and say it was trained on licensed, royalty-free and synthetic music), saved here as `ai-<mood>-<seed>.flac`,
and recorded in `ai_tracks.csv` (file, model, prompt, seed, date) for your licence records. Running it again
only fills folders that are short. Delete any track you don't like and run it again to replace it. Each mood's
style is in `shorts_factory/media/musicgen.py`; override it with `music.prompts` in `config/settings.yaml`.
One-time setup: `bash scripts/setup_music.sh` (already part of `runpod_bootstrap.sh`).

Listen to a few before a full batch. Generated tracks can't be registered with Content ID, which is fine for
background music, and nothing stops you mixing them with licensed tracks in the same folder.

## Licensing: this is how channels get demonetized

Use only tracks you are licensed to monetize on YouTube:

- **YouTube Audio Library** (Studio → Audio Library). Free and safe for monetization. Filter by "No attribution required".
- A subscription library that whitelists your channel IDs (Epidemic Sound, Artlist, Musicbed...). Register every channel ID with them.
- Tracks you commissioned or generated yourself with a commercially licensed model (`shorts music` above).

Never use popular songs, "no copyright" uploads from random YouTube channels, or TikTok sounds. A Content ID
claim on a Short routes its revenue to the claimant, and repeated claims hurt the channel.

Aim for 15-30 tracks per mood. Variety matters: the same 3 tracks on 300 videos reads as templated content.
