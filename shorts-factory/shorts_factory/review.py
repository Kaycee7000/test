"""Daily editorial review page: every ready video with its script, critique and schedule on one page."""
from __future__ import annotations

import html
import json
import os
from pathlib import Path

from .db import DB

CSS = """
:root{--bg:#0f1115;--card:#181b22;--text:#e8eaf0;--muted:#9aa3b2;--accent:#ffe14d;--bad:#ff6b6b;--ok:#3dff7a}
@media (prefers-color-scheme: light){:root{--bg:#f5f6f8;--card:#fff;--text:#161a22;--muted:#5b6474;--accent:#9a7a00;--bad:#c62828;--ok:#1b8a3a}}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:1200px;margin:0 auto;padding:16px}
h1{font-size:22px;margin:8px 0 16px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:16px}
.card{background:var(--card);border-radius:12px;padding:12px;display:flex;flex-direction:column;gap:8px}
video{width:100%;aspect-ratio:9/16;background:#000;border-radius:8px}
.meta{color:var(--muted);font-size:13px}
.title{font-weight:650}
code{font-size:12px;color:var(--accent);word-break:break-all}
details{font-size:13px}
.state-ready{color:var(--ok)} .state-uploaded{color:var(--accent)} .state-failed,.state-rejected{color:var(--bad)}
"""


def build_review_page(db: DB, day: str, out: Path) -> Path:
    rows = [r for r in db.jobs(publish_date=day) if r["state"] in ("ready", "uploaded", "failed", "rejected")]
    cards = []
    for r in rows:
        jdir = Path(r["dir"])
        data = json.loads(r["data"])
        video = jdir / "final.mp4"
        script_p = jdir / "script.json"
        script = json.loads(script_p.read_text()) if script_p.exists() else {}
        narration = " ".join(s.get("narration", "") for s in script.get("scenes", []))
        crit = (data.get("review") or {}).get("critique") or {}
        scores = ", ".join(f"{k.split('_')[0]} {v}" for k, v in crit.items() if k.endswith("_score"))
        rel = os.path.relpath(video, out.parent)
        approved = " · approved" if data.get("approved") else ""
        cards.append(f"""<div class="card">
  {'<video controls preload="none" poster="' + html.escape(os.path.relpath(jdir / 'preview.jpg', out.parent)) + '" src="' + html.escape(rel) + '"></video>' if video.exists() else ''}
  <div class="title">{html.escape(r['title'] or '')}</div>
  <div class="meta"><span class="state-{r['state']}">{r['state']}</span>{approved} · {html.escape(r['channel'])} · {html.escape(r['format'] or '')} · {html.escape(r['hook_type'] or '')}</div>
  <div class="meta">publish {html.escape(r['slot_utc'] or '-')} UTC · {data.get('duration', '?')}s · {scores}</div>
  <details><summary>script</summary>{html.escape(narration)}</details>
  {f'<details><summary>error</summary>{html.escape(r["error"] or "")}</details>' if r['error'] else ''}
  {f'<div class="meta">delivered: {html.escape(data["delivery"]["key"])}</div>' if data.get('delivery') else ''}
  <code>{html.escape(r['id'])}</code>
</div>""")
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Shorts review {day}</title>
<style>{CSS}</style></head><body><main><h1>Shorts review · {day} · {len(rows)} videos</h1>
<div class="grid">{''.join(cards)}</div></main></body></html>"""
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    return out
