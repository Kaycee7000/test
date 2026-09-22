"""Animated word-by-word captions as an ASS subtitle file (burned in by ffmpeg/libass)."""
from __future__ import annotations

from dataclasses import dataclass

from ..config import CaptionCfg


@dataclass
class Word:
    text: str
    start: float
    end: float
    emphasis: bool = False


def ass_color(hex_rgb: str, alpha: int = 0) -> str:
    """#RRGGBB -> &HAABBGGRR"""
    h = hex_rgb.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def ass_time(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def ass_escape(text: str) -> str:
    return text.replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", " ")


def chunk_words(words: list[Word], max_words: int, max_chars: int, max_gap: float = 0.45) -> list[list[Word]]:
    chunks: list[list[Word]] = []
    cur: list[Word] = []
    for w in words:
        cur_len = sum(len(x.text) for x in cur) + max(0, len(cur) - 1)
        if cur and (len(cur) >= max_words or cur_len + 1 + len(w.text) > max_chars or w.start - cur[-1].end > max_gap):
            chunks.append(cur)
            cur = []
        cur.append(w)
        if w.text[-1:] in ".!?;:," and (w.text[-1:] != "," or len(cur) > 1):  # close on natural pauses
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    return chunks


def build_ass(words: list[Word], cfg: CaptionCfg, width: int, height: int, headline: str | None = None,
              total: float | None = None) -> str:
    bold = 0 if cfg.font.lower() in {"anton", "bebas neue", "league gothic"} else -1
    white, hl, em = ass_color(cfg.primary), ass_color(cfg.highlight), ass_color(cfg.emphasis)
    black = ass_color("#000000")
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,{cfg.font},{cfg.size},{white},{white},{black},{ass_color('#000000', 0x60)},{bold},0,0,0,100,100,1,0,1,{cfg.outline},{cfg.shadow},5,90,90,0,1
Style: Headline,{cfg.font},{cfg.headline_size},{black},{black},{hl},{hl},{bold},0,0,0,100,100,1,0,3,16,0,8,90,90,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines: list[str] = []
    pos = f"\\an5\\pos({width // 2},{cfg.center_y})"
    chunks = chunk_words(words, cfg.max_words, cfg.max_chars)
    for ci, chunk in enumerate(chunks):
        c_start = chunk[0].start
        nxt = chunks[ci + 1][0].start if ci + 1 < len(chunks) else None
        c_end = chunk[-1].end + 0.25
        if nxt is not None and nxt - chunk[-1].end < 0.6:
            c_end = nxt  # no flicker between chunks that follow each other closely
        elif nxt is not None:
            c_end = min(c_end, nxt)
        if total is not None:
            c_end = min(c_end, total)
        for k, w in enumerate(chunk):
            s = c_start if k == 0 else w.start
            e = chunk[k + 1].start if k + 1 < len(chunk) else c_end
            if e <= s:
                continue
            parts = []
            for j, x in enumerate(chunk):
                txt = ass_escape(x.text.upper() if cfg.uppercase else x.text)
                if j == k:
                    color = em if x.emphasis else hl
                    parts.append(f"{{\\c{color}\\fscx104\\fscy104}}{txt}{{\\c{white}\\fscx100\\fscy100}}")
                elif x.emphasis:
                    parts.append(f"{{\\c{em}}}{txt}{{\\c{white}}}")
                else:
                    parts.append(txt)
            pop = "\\fscx82\\fscy82\\t(0,90,\\fscx100\\fscy100)" if k == 0 else ""
            lines.append(f"Dialogue: 1,{ass_time(s)},{ass_time(e)},Caption,,0,0,0,,{{{pos}{pop}}}{'  '.join(parts)}")
    if headline and cfg.headline:
        txt = ass_escape(headline.upper() if cfg.uppercase else headline)
        lines.append(
            f"Dialogue: 2,{ass_time(0)},{ass_time(cfg.headline_seconds)},Headline,,0,0,0,,"
            f"{{\\an8\\pos({width // 2},{cfg.headline_y})\\fad(0,220)}}{txt}"
        )
    return head + "\n".join(lines) + "\n"
