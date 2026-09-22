"""AI music library: prompt table, top-up logic, licence log, and the `shorts music` command (mock backend)."""
import csv
from pathlib import Path

from typer.testing import CliRunner

from shorts_factory.cli import app
from shorts_factory.config import load_channels, load_settings
from shorts_factory.media.music import pick_music
from shorts_factory.media.musicgen import LOG_NAME, MOOD_PROMPTS, build_library, library_moods, mood_prompt


def test_every_channel_mood_has_a_written_prompt():
    s = load_settings(Path(__file__).resolve().parent.parent / "config" / "settings.yaml")
    moods = library_moods(load_channels(s))
    assert moods and set(moods) <= set(MOOD_PROMPTS)


def test_mood_prompt_variations_overrides_and_fallback(project: Path):
    s = load_settings(project / "config" / "settings.yaml")
    a, bpm_a = mood_prompt(s, "epic", 0)
    b, _ = mood_prompt(s, "epic", 1)
    assert a != b and "instrumental, no vocals" in a and 100 <= bpm_a <= 120
    assert mood_prompt(s, "epic", 0) == (a, bpm_a)  # stable per index
    caption, bpm = mood_prompt(s, "lofi_rain", 0)
    assert caption.startswith("lofi rain") and bpm is None
    s.music.prompts["epic"] = "war drums only"
    assert mood_prompt(s, "epic", 0)[0].startswith("war drums only,")


def test_build_library_tops_up_and_logs(project: Path):
    s = load_settings(project / "config" / "settings.yaml").mock()
    s.music.seconds = 3
    made, errors = build_library(s, ["mystery", "wonder"], 2)
    assert (made, errors) == (4, [])
    root = s.assets / "music"
    assert len(list((root / "mystery").glob("ai-mystery-*.wav"))) == 2
    rows = list(csv.DictReader((root / LOG_NAME).open()))
    assert len(rows) == 4 and {r["mood"] for r in rows} == {"mystery", "wonder"}
    assert all((root / r["file"]).exists() for r in rows)

    assert build_library(s, ["mystery", "wonder"], 2) == (0, [])  # already stocked: nothing to do
    assert build_library(s, ["mystery"], 3)[0] == 1  # raise the target: only the difference is made
    track, _ = pick_music(s.assets, "mystery", [], "job-1", 2.0)
    assert track.parent.name == "mystery"


def test_music_command_mock(project: Path):
    res = CliRunner().invoke(app, ["--config", str(project / "config" / "settings.yaml"), "--mock",
                                   "music", "--moods", "space_ambient", "--count", "1"])
    assert res.exit_code == 0, res.output
    assert len(list((project / "assets" / "music" / "space_ambient").glob("*.wav"))) == 1


def test_music_command_without_acestep_venv_explains_setup(project: Path):
    cfg = project / "config" / "settings.yaml"
    cfg.write_text(cfg.read_text().replace("acestep_dir: /workspace/ACE-Step-1.5",
                                           f"acestep_dir: {project / 'no-acestep'}"))
    res = CliRunner().invoke(app, ["--config", str(cfg), "music", "--moods", "epic", "--count", "1"])
    assert res.exit_code == 1 and "runpod_bootstrap.sh" in res.output
