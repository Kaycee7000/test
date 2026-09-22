"""Word-timing worker: faster-whisper word timestamps mapped onto the exact script words.

usage: python align_worker.py manifest.json results.json
"""
from __future__ import annotations

import json
import sys
import traceback
import wave

from shorts_factory.media.wordmap import even_timings, map_words


def wav_duration(path: str) -> float:
    with wave.open(path, "rb") as w:
        return w.getnframes() / w.getframerate()


def main(manifest_path: str, out_path: str) -> int:
    m = json.load(open(manifest_path))
    model = None
    if m["backend"] == "faster_whisper":
        from faster_whisper import WhisperModel

        p = m.get("params", {})
        model = WhisperModel(p.get("model", "large-v3"), device=m.get("device", "cuda"),
                             compute_type=p.get("compute_type", "float16"))
    results: dict = {}
    for task in m["tasks"]:
        try:
            words = task["text"].split()
            total = wav_duration(task["wav"])
            if model is None:
                timed = even_timings(words, task.get("lead", 0.0), max(0.1, total - task.get("tail", 0.0)))
            else:
                segments, _ = model.transcribe(
                    task["wav"], language=task.get("language") or None, word_timestamps=True,
                    beam_size=5, vad_filter=False, condition_on_previous_text=False,
                    initial_prompt=task["text"][:220],  # biases spelling of names toward the script
                )
                asr = [(w.word.strip(), float(w.start), float(w.end)) for s in segments for w in (s.words or [])]
                timed = map_words(words, asr, total)
            json.dump(timed, open(task["out"], "w"))
            results[task["id"]] = {"ok": True, "words": len(timed)}
        except Exception as e:
            traceback.print_exc()
            results[task["id"]] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    json.dump({"results": results}, open(out_path, "w"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
