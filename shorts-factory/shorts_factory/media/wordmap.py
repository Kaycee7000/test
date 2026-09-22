"""Map ASR word timings onto the exact script words, so captions show our spelling (names, numbers)."""
from __future__ import annotations

import difflib
import re

_NON_WORD = re.compile(r"[^\w]+", re.UNICODE)


def norm(w: str) -> str:
    return _NON_WORD.sub("", w.lower())


def _distribute(words: list[str], start: float, end: float) -> list[tuple[float, float]]:
    weights = [len(norm(w)) + 1 for w in words]
    total = sum(weights) or 1
    out, t = [], start
    for wgt in weights:
        d = (end - start) * wgt / total
        out.append((t, t + d))
        t += d
    return out


def even_timings(words: list[str], start: float, end: float) -> list[dict]:
    return [{"w": w, "s": round(s, 3), "e": round(e, 3)} for w, (s, e) in zip(words, _distribute(words, start, end))]


def map_words(script_words: list[str], asr: list[tuple[str, float, float]], total: float) -> list[dict]:
    """Align script words to ASR words (text, start, end); unmatched words are interpolated."""
    if not script_words:
        return []
    if not asr:
        return even_timings(script_words, 0.0, total)
    a = [norm(w) for w in script_words]
    b = [norm(w) for w, _, _ in asr]
    times: list[tuple[float, float] | None] = [None] * len(a)
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                times[i1 + k] = (asr[j1 + k][1], asr[j1 + k][2])
        elif tag == "replace":
            # e.g. script "1908" vs ASR "nineteen oh eight": spread the ASR span over the script words
            span = _distribute(script_words[i1:i2], asr[j1][1], asr[j2 - 1][2])
            for k, st in enumerate(span):
                times[i1 + k] = st
    # interpolate script words the ASR missed entirely
    i = 0
    while i < len(times):
        if times[i] is not None:
            i += 1
            continue
        j = i
        while j < len(times) and times[j] is None:
            j += 1
        lo = times[i - 1][1] if i > 0 else 0.0  # type: ignore[index]
        hi = times[j][0] if j < len(times) else max(lo, total)  # type: ignore[index]
        if hi <= lo:
            hi = lo + 0.18 * (j - i)
        for k, st in enumerate(_distribute(script_words[i:j], lo, hi)):
            times[i + k] = st
        i = j
    out, prev = [], 0.0
    for w, (s, e) in zip(script_words, times):  # type: ignore[misc]
        s = max(s, prev)
        e = max(e, s + 0.05)
        out.append({"w": w, "s": round(s, 3), "e": round(e, 3)})
        prev = s
    return out
