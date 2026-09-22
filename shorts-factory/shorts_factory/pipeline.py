"""Stage-batched orchestrator.

Instead of pushing one video through every model (reloading models 50x a day), each stage processes
the whole day's batch with its model loaded once:

  feedback (team CSVs) -> trends (Claude web search, per channel) -> plan -> script (API, parallel)
       -> voice (GPU) -> align (GPU) -> images (GPU) -> animate (GPU, optional) -> render (CPU, parallel)
       -> qc -> upload (B2 grouped by niche; YouTube optional)

Every stage is idempotent and resumable: it only picks up jobs in its input state, so a crashed or
interrupted run continues where it stopped when re-run.
"""
from __future__ import annotations

import json
import logging
import random
import shutil
import sqlite3
import zlib
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import ChannelCfg, Settings
from .content.llm import LLM, make_llm
from .content.strategy import learnings_text, outcomes, pick_formats, target_per_day
from .content.topics import generate_topics, pick_topic
from .content.trends import daily_brief
from .content.writer import ScriptResult, ScriptWriter
from .db import DB, now_iso
from .media.audio import change_tempo, concat_wavs, duration, mean_volume_db, probe
from .media.workers import WorkerError, run_worker
from .publish.schedule import daily_slots, pacific_day
from .publish.feedback import import_from_store
from .publish.storage import cleanup_local, deliver, make_store, write_manifests
from .publish.youtube import QuotaExhausted, build_metadata, lookback, make_publisher

log = logging.getLogger(__name__)

STAGES = ["feedback", "trends", "plan", "script", "voice", "align", "images", "animate", "render", "qc", "upload"]


def _seed(*parts: Any) -> int:
    """Stable across processes and runs (unlike hash()), so re-renders reproduce the same media."""
    return zlib.crc32("|".join(map(str, parts)).encode()) & 0x7FFFFFFF


class Pipeline:
    def __init__(self, settings: Settings, channels: list[ChannelCfg], db: DB | None = None):
        self.s = settings
        self.channels = {c.id: c for c in channels}
        self.db = db or DB(settings.work / "shorts.db")
        self._llm: LLM | None = None
        self.scratch = settings.work / "manifests"

    @property
    def llm(self) -> LLM:
        if self._llm is None:
            self._llm = make_llm(self.s.llm)
        return self._llm

    def _jobs(self, state: str, day: str | None, ids: list[str] | None) -> list:
        rows = self.db.jobs(state=state, publish_date=day, ids=ids)
        return [r for r in rows if r["channel"] in self.channels]

    @staticmethod
    def _script(row) -> dict[str, Any]:
        return json.loads((Path(row["dir"]) / "script.json").read_text())

    # ------------------------------------------------------------------ plan

    def plan(self, day: date, count: int | None = None, only_channel: str | None = None) -> list[str]:
        created: list[str] = []
        for ch in self.channels.values():
            if only_channel and ch.id != only_channel:
                continue
            target = count if count is not None else target_per_day(ch, day)
            need = target - self.db.count_scheduled(ch.id, day.isoformat())
            if need <= 0:
                continue
            if len(self.db.topics(ch.id, status="new")) < need * 2:
                brief = self.db.trend_brief(ch.id, day.isoformat())
                generate_topics(self.llm, self.db, ch, n=max(30, need * 4), trends=brief["brief"] if brief else "")
            rng = random.Random(_seed(ch.id, day))
            fmts = pick_formats(ch, need, outcomes(self.db.performance(ch.id), "format"), rng)
            live = self.db.jobs(channel=ch.id, publish_date=day.isoformat())
            used = {r["slot_utc"] for r in live if r["state"] not in ("failed", "rejected")}
            free = [s for s in daily_slots(day, ch.schedule, target, seed=ch.id)
                    if s.isoformat() not in used]
            taken: set[int] = set()
            before = len(created)
            for i, f in enumerate(fmts):
                topic = pick_topic(self.db, ch, f, taken)
                if topic is None:
                    log.warning("%s: topic backlog empty; run `shorts topics --channel %s`", ch.id, ch.id)
                    break
                taken.add(topic["id"])
                self.db.mark_topic(topic["id"], "used")
                jid = self.db.create_job(
                    ch.id, topic["id"], day.isoformat(), topic["format"], topic["title"], self.s.work,
                    data={"topic": {k: topic[k] for k in ("title", "angle", "format", "trend_ref")} |
                          {"keywords": json.loads(topic["keywords"])}},
                )
                if i < len(free):
                    self.db.update_job(jid, slot_utc=free[i].isoformat())
                created.append(jid)
            log.info("%s: planned %d new job(s) for %s (target %d)", ch.id, len(created) - before, day, target)
        return created

    # ------------------------------------------------------------------ feedback + trends

    def stage_feedback(self, day: str | None = None, ids: list[str] | None = None) -> None:
        """Import performance CSVs the posting team dropped into storage."""
        if self.s.publish.mode != "storage":
            return
        try:
            import_from_store(make_store(self.s), self.s.storage.feedback_prefix, self.db)
        except Exception as e:
            log.warning("feedback import skipped: %s", e)

    def stage_trends(self, day: date) -> None:
        """Per channel: today's trend brief (Claude + web search, cached), then a few fresh trend-driven ideas
        with boosted priority so they are made while still timely."""
        t = self.s.trends
        if not t.enabled:
            return
        for ch in self.channels.values():
            brief = daily_brief(self.llm, self.db, ch, day, t)
            row = self.db.trend_brief(ch.id, day.isoformat())
            if brief and row is not None and not row["topics_added"] and t.daily_topics > 0:
                n = generate_topics(self.llm, self.db, ch, n=t.daily_topics, trends=brief, timely=True, boost=2.0)
                self.db.mark_trend_topics(ch.id, day.isoformat(), max(1, n))

    # ------------------------------------------------------------------ script

    def stage_script(self, day: str | None, ids: list[str] | None = None) -> None:
        rows = self._jobs("planned", day, ids)
        if not rows:
            return
        writer = ScriptWriter(self.llm, self.s.llm)
        # sqlite connections are single-threaded: gather per-channel context up front
        ctx = {cid: (learnings_text(self.db.performance(cid)), self.db.recent_titles(cid))
               for cid in {r["channel"] for r in rows}}

        def work(row) -> ScriptResult:
            ch = self.channels[row["channel"]]
            topic = json.loads(row["data"])["topic"]
            learn, recent = ctx[ch.id]
            recent = [t for t in recent if t != row["title"]]
            return writer.write(ch, row["format"], topic, learn, recent)

        with ThreadPoolExecutor(max_workers=self.s.llm.concurrency) as ex:
            futs = {ex.submit(work, r): r for r in rows}
            for fut in as_completed(futs):
                row = futs[fut]
                try:
                    res = fut.result()
                except Exception as e:
                    log.exception("script failed for %s", row["id"])
                    self.db.fail(row["id"], f"script: {e}")
                    continue
                jdir = Path(row["dir"])
                payload = {"script": res.draft.model_dump() if res.draft else None,
                           "critique": res.critique.model_dump() if res.critique else None,
                           "rounds": res.rounds, "notes": res.notes}
                (jdir / ("script.json" if res.ok else "script.rejected.json")).write_text(
                    json.dumps(payload["script"] if res.ok else payload, indent=1, ensure_ascii=False))
                self.db.merge_data(row["id"], review={"rounds": res.rounds, "notes": res.notes,
                                                      "critique": payload["critique"]},
                                   hook=res.draft.scenes[0].narration if res.draft and res.draft.scenes else "")
                if res.ok and res.draft:
                    self.db.update_job(row["id"], state="scripted", title=res.draft.title,
                                       hook_type=res.draft.hook_type, error=None)
                    log.info("%s: scripted in %d round(s): %s", row["id"], res.rounds, res.draft.title)
                else:
                    self.db.update_job(row["id"], state="rejected", error="; ".join(res.notes)[-1500:])
                    if row["topic_id"]:
                        self.db.mark_topic(row["topic_id"], "rejected")
                    log.info("%s: rejected by quality gate after %d round(s)", row["id"], res.rounds)

    # ------------------------------------------------------------------ voice

    def stage_voice(self, day: str | None, ids: list[str] | None = None) -> None:
        rows = self._jobs("scripted", day, ids)
        if not rows:
            return
        t = self.s.tts
        tasks = []
        for row in rows:
            ch = self.channels[row["channel"]]
            jdir = Path(row["dir"])
            (jdir / "voice").mkdir(exist_ok=True)
            for i, sc in enumerate(self._script(row)["scenes"]):
                raw = jdir / "voice" / f"scene_{i:02d}.raw.wav"
                if raw.exists():
                    continue
                tasks.append({
                    "id": f"{row['id']}:{i}", "text": sc["narration"], "out": str(raw),
                    "language": ch.language, "seed": _seed(row["id"], i) % 100000,
                    "voice_ref": str(self.s.path(ch.voice.chatterbox_ref)) if ch.voice.chatterbox_ref else None,
                    "kokoro_voice": ch.voice.kokoro_voice, "elevenlabs_voice_id": ch.voice.elevenlabs_voice_id,
                    "exaggeration": ch.voice.exaggeration,
                })
        results: dict[str, dict] = {}
        if tasks:
            params = {"chatterbox": t.chatterbox, "kokoro": t.kokoro, "elevenlabs": t.elevenlabs}.get(t.backend, {})
            results = run_worker("tts", {
                "backend": t.backend, "device": t.device, "params": params, "lead_pad": t.lead_pad,
                "tail_pad": t.tail_pad, "max_retries": t.max_retries, "tasks": tasks,
            }, self.scratch, python=t.python if t.backend == "chatterbox" else None,
                require_python=t.backend == "chatterbox")
        for row in rows:
            self._finish_voice(row, results)

    def _finish_voice(self, row, results: dict[str, dict]) -> None:
        jid, jdir = row["id"], Path(row["dir"])
        scenes = self._script(row)["scenes"]
        errors = [f"scene {i}: {results[f'{jid}:{i}'].get('error')}" for i in range(len(scenes))
                  if f"{jid}:{i}" in results and not results[f"{jid}:{i}"].get("ok")]
        raws = [jdir / "voice" / f"scene_{i:02d}.raw.wav" for i in range(len(scenes))]
        if errors or not all(p.exists() for p in raws):
            self.db.fail(jid, "voice: " + "; ".join(errors or ["missing scene audio"]))
            return
        warns = [f"scene {i}: {res['warn']}" for i in range(len(scenes))
                 if (res := results.get(f"{jid}:{i}", {})).get("warn")]
        raw_total = sum(duration(p) for p in raws)
        r = self.s.render
        speed = self.s.tts.speed
        budget = r.max_duration - r.tail - 0.3
        if raw_total / speed > budget:
            needed = raw_total / budget
            if needed > speed * 1.15:
                self.db.update_job(jid, state="rejected",
                                   error=f"voice too long ({raw_total:.1f}s) even at {needed:.2f}x")
                return
            speed = needed
        if raw_total / speed < r.min_duration:
            self.db.update_job(jid, state="rejected", error=f"voice too short ({raw_total / speed:.1f}s)")
            return
        finals = []
        for i, raw in enumerate(raws):
            final = jdir / "voice" / f"scene_{i:02d}.wav"
            if abs(speed - 1.0) > 1e-3:
                change_tempo(raw, final, speed)
            else:
                shutil.copyfile(raw, final)
            finals.append(final)
        spans = concat_wavs(finals, jdir / "voice.wav")
        self.db.merge_data(jid, scene_spans=spans, voice_duration=spans[-1][1], tempo=round(speed, 3),
                           voice_warnings=warns)
        self.db.update_job(jid, state="voiced", error=None)

    # ------------------------------------------------------------------ align

    def stage_align(self, day: str | None, ids: list[str] | None = None) -> None:
        rows = self._jobs("voiced", day, ids)
        if not rows:
            return
        a, t = self.s.align, self.s.tts
        tasks = []
        for row in rows:
            jdir = Path(row["dir"])
            (jdir / "words").mkdir(exist_ok=True)
            ch = self.channels[row["channel"]]
            for i, sc in enumerate(self._script(row)["scenes"]):
                tasks.append({"id": f"{row['id']}:{i}", "wav": str(jdir / "voice" / f"scene_{i:02d}.wav"),
                              "text": sc["narration"], "language": ch.language,
                              "out": str(jdir / "words" / f"scene_{i:02d}.json"),
                              "lead": t.lead_pad, "tail": t.tail_pad})
        results = run_worker("align", {"backend": a.backend, "device": a.device,
                                       "params": {"model": a.model, "compute_type": a.compute_type},
                                       "tasks": tasks}, self.scratch, python=a.python)
        for row in rows:
            jid, jdir = row["id"], Path(row["dir"])
            scenes = self._script(row)["scenes"]
            bad = [i for i in range(len(scenes)) if not results.get(f"{jid}:{i}", {}).get("ok")]
            if bad:
                self.db.fail(jid, f"align failed for scenes {bad}")
                continue
            spans = json.loads(row["data"])["scene_spans"]
            words = []
            for i, sc in enumerate(scenes):
                emph = {e.lower().strip(".,!?;:\"'") for e in sc.get("emphasis", [])}
                for w in json.loads((jdir / "words" / f"scene_{i:02d}.json").read_text()):
                    words.append({"w": w["w"], "s": round(w["s"] + spans[i][0], 3), "e": round(w["e"] + spans[i][0], 3),
                                  "scene": i, "emph": w["w"].lower().strip(".,!?;:\"'") in emph})
            (jdir / "words.json").write_text(json.dumps(words, ensure_ascii=False))
            self.db.update_job(jid, state="aligned", error=None)

    # ------------------------------------------------------------------ images

    def stage_images(self, day: str | None, ids: list[str] | None = None) -> None:
        rows = self._jobs("aligned", day, ids)
        if not rows:
            return
        im = self.s.images
        tasks = []
        for row in rows:
            ch = self.channels[row["channel"]]
            jdir = Path(row["dir"])
            (jdir / "images").mkdir(exist_ok=True)
            for i, sc in enumerate(self._script(row)["scenes"]):
                out = jdir / "images" / f"scene_{i:02d}.png"
                if out.exists():
                    continue
                tasks.append({"id": f"{row['id']}:{i}", "out": str(out), "seed": _seed(row["id"], "img", i),
                              "prompt": f"{sc['visual'].rstrip('. ')}. {' '.join(ch.visual_style.split())}",
                              "negative": ch.negative_prompt or im.negative_prompt})
        results: dict[str, dict] = {}
        if tasks:
            results = run_worker("image", {"backend": im.backend, "device": im.device, "params": {
                "model": im.model, "width": im.width, "height": im.height, "steps": im.steps,
                "guidance_scale": im.guidance_scale, "extra": im.extra, "cpu_offload": im.cpu_offload,
            }, "tasks": tasks}, self.scratch, python=im.python)
        for row in rows:
            n = len(self._script(row)["scenes"])
            missing = [i for i in range(n) if not (Path(row["dir"]) / "images" / f"scene_{i:02d}.png").exists()]
            if missing:
                errs = [results.get(f"{row['id']}:{i}", {}).get("error", "missing") for i in missing]
                self.db.fail(row["id"], f"images missing for scenes {missing}: {errs[:2]}")
            else:
                self.db.update_job(row["id"], state="imaged", error=None)

    # ------------------------------------------------------------------ animate

    def stage_animate(self, day: str | None, ids: list[str] | None = None) -> None:
        rows = self._jobs("imaged", day, ids)
        if not rows:
            return
        an = self.s.animate
        if an.enabled:
            tasks = []
            for row in rows:
                ch = self.channels[row["channel"]]
                jdir = Path(row["dir"])
                (jdir / "clips").mkdir(exist_ok=True)
                scenes = self._script(row)["scenes"]
                which = range(len(scenes)) if an.scenes == "all" else range(min(an.hook_scenes, len(scenes)))
                for i in which:
                    out = jdir / "clips" / f"scene_{i:02d}.mp4"
                    if out.exists():
                        continue
                    sc = scenes[i]
                    move = sc["motion"].replace("_", " ")
                    tasks.append({"id": f"{row['id']}:{i}", "image": str(jdir / "images" / f"scene_{i:02d}.png"),
                                  "out": str(out), "seed": _seed(row["id"], "vid", i),
                                  "prompt": f"{sc['visual']} Slow cinematic camera {move}, subtle natural motion, "
                                            f"atmospheric, consistent lighting. {' '.join(ch.visual_style.split())}"})
            if tasks:
                try:
                    run_worker("animate", {"device": an.device, "params": an.model_dump(), "tasks": tasks},
                               self.scratch, python=an.python)
                except WorkerError as e:  # animation is an enhancement: fall back to stills
                    log.warning("animate stage failed, continuing with stills: %s", e)
        for row in rows:
            self.db.update_job(row["id"], state="animated", error=None)

    # ------------------------------------------------------------------ render

    def stage_render(self, day: str | None, ids: list[str] | None = None) -> None:
        rows = self._jobs("animated", day, ids)
        if not rows:
            return
        payloads = [{"job": dict(r), "settings": self.s.model_dump(mode="json"),
                     "channel": self.channels[r["channel"]].model_dump(mode="json")} for r in rows]
        workers = max(1, min(self.s.render.workers, len(payloads)))
        if workers == 1:
            outcomes_ = [render_job(p) for p in payloads]
        else:
            with ProcessPoolExecutor(max_workers=workers) as ex:
                outcomes_ = list(ex.map(render_job, payloads))
        for row, res in zip(rows, outcomes_):
            if res.get("ok"):
                self.db.merge_data(row["id"], duration=res["duration"], music=res.get("music"))
                self.db.update_job(row["id"], state="rendered", error=None)
            else:
                self.db.fail(row["id"], f"render: {res.get('error')}")

    # ------------------------------------------------------------------ qc

    def stage_qc(self, day: str | None, ids: list[str] | None = None) -> None:
        from .media.render import extract_preview

        r = self.s.render
        for row in self._jobs("rendered", day, ids):
            jdir = Path(row["dir"])
            video = jdir / "final.mp4"
            problems = []
            try:
                info = probe(video)
                v = next(s for s in info["streams"] if s["codec_type"] == "video")
                if (v["width"], v["height"]) != (r.width, r.height):
                    problems.append(f"resolution {v['width']}x{v['height']}")
                if not any(s["codec_type"] == "audio" for s in info["streams"]):
                    problems.append("no audio stream")
                d = float(info["format"]["duration"])
                if not r.min_duration <= d <= r.max_duration + 0.5:
                    problems.append(f"duration {d:.1f}s")
                vol = mean_volume_db(video)
                if vol < -40:
                    problems.append(f"audio nearly silent ({vol:.1f} dB)")
                extract_preview(video, jdir / "preview.jpg", at=min(1.0, d / 2))
            except Exception as e:
                problems.append(f"probe failed: {e}")
            if problems:
                self.db.fail(row["id"], "qc: " + "; ".join(problems), max_attempts=1)
            else:
                self.db.update_job(row["id"], state="ready", error=None)

    # ------------------------------------------------------------------ upload

    def stage_upload(self, day: str | None, ids: list[str] | None = None) -> None:
        rows = self._jobs("ready", day, ids)
        if self.s.publish.require_approval:
            waiting = [r for r in rows if not json.loads(r["data"]).get("approved")]
            if waiting:
                log.info("%d ready video(s) await `shorts approve`", len(waiting))
            rows = [r for r in rows if json.loads(r["data"]).get("approved")]
        if not rows:
            return
        if self.s.publish.mode == "storage":
            self._deliver_to_storage(rows)
        else:
            self._upload_to_youtube(rows)

    def _deliver_to_storage(self, rows: list) -> None:
        """Push final videos + post kits to B2 (or a local folder), grouped by niche/language/date."""
        try:
            store = make_store(self.s)
        except Exception as e:
            log.error("storage unavailable, %d video(s) stay ready: %s", len(rows), e)
            return
        touched: set[tuple[str, str]] = set()
        for row in rows:
            ch = self.channels[row["channel"]]
            try:
                info = deliver(store, self.s, ch, row, self._script(row), json.loads(row["data"]))
            except Exception as e:
                log.exception("delivery failed for %s", row["id"])
                self.db.fail(row["id"], f"storage: {e}", max_attempts=5)
                continue
            self.db.merge_data(row["id"], delivery=info)
            self.db.update_job(row["id"], state="uploaded", uploaded_at=now_iso(), project=store.name, error=None)
            touched.add((ch.id, row["publish_date"]))
            cleanup_local(Path(row["dir"]), self.s.storage.cleanup_local)
            log.info("%s: delivered %s", row["id"], info["key"])
        for m in write_manifests(store, self.s, self.db, self.channels, touched):
            log.info("manifest %s", m)

    def _upload_to_youtube(self, rows: list) -> None:
        now = datetime.now(timezone.utc)
        earliest = now + timedelta(minutes=self.s.publish.lead_minutes)
        cap = self.s.publish.max_uploads_per_project_per_day
        today_pt = pacific_day(now)
        exhausted: set[str] = set()
        by_channel: dict[str, list] = {}
        for r in rows:
            by_channel.setdefault(r["channel"], []).append(r)
        for cid, jobs in by_channel.items():
            ch = self.channels[cid]
            project = ch.youtube.project
            if project in exhausted:
                continue
            try:
                pub = make_publisher(self.s, ch)
            except Exception as e:
                log.error("%s: cannot create publisher: %s", cid, e)
                continue
            late = 0
            for row in sorted(jobs, key=lambda r: r["slot_utc"] or ""):
                since = (now - timedelta(days=2)).isoformat()
                used = sum(1 for t in self.db.upload_times(project, since)
                           if pacific_day(datetime.fromisoformat(t)) == today_pt)
                if self.s.publish.mode == "youtube" and used >= cap:
                    log.warning("project %s hit %d uploads today; remaining jobs wait for tomorrow", project, cap)
                    exhausted.add(project)
                    break
                slot = datetime.fromisoformat(row["slot_utc"]) if row["slot_utc"] else earliest
                if slot < earliest:  # missed its slot: publish soon, still spaced out
                    slot = earliest + timedelta(minutes=ch.schedule.min_gap_minutes * late)
                    late += 1
                script = self._script(row)
                try:
                    vid = pub.upload(Path(row["dir"]) / "final.mp4", build_metadata(ch, script), slot, row["id"])
                except QuotaExhausted as e:
                    log.warning("project %s quota exhausted (%s)", project, e)
                    exhausted.add(project)
                    break
                except Exception as e:
                    log.exception("upload failed for %s", row["id"])
                    self.db.fail(row["id"], f"upload: {e}", max_attempts=5)
                    continue
                self.db.update_job(row["id"], state="uploaded", video_id=vid, slot_utc=slot.isoformat(),
                                   uploaded_at=now_iso(), project=project, error=None)
                pl = ch.format(row["format"]).playlist_id
                if pl:
                    try:
                        pub.add_to_playlist(vid, pl)
                    except Exception as e:
                        log.warning("playlist insert failed for %s: %s", vid, e)
                self.db.merge_data(row["id"], pinned_comment=script.get("pinned_comment"))

    # ------------------------------------------------------------------ analytics

    def sync_metrics(self) -> int:
        n = 0
        now = datetime.now(timezone.utc)
        for ch in self.channels.values():
            rows = [r for r in self.db.jobs(state="uploaded", channel=ch.id)
                    if r["video_id"] and not r["video_id"].startswith("dryrun")]
            if not rows:
                continue
            pub = make_publisher(self.s, ch)
            data = pub.metrics([r["video_id"] for r in rows], lookback(60))
            batch = []
            for r in rows:
                m = data.get(r["video_id"])
                if not m:
                    continue
                published = datetime.fromisoformat(r["slot_utc"]) if r["slot_utc"] else now
                batch.append({"video_id": r["video_id"], "fetched_at": now_iso(),
                              "age_hours": round((now - published).total_seconds() / 3600, 1), **m})
            self.db.add_metrics(batch)
            n += len(batch)
        return n

    # ------------------------------------------------------------------ run

    def run(self, day: date, stages: list[str] | None = None, ids: list[str] | None = None,
            count: int | None = None, refill_cycles: int = 2) -> None:
        stages = stages or STAGES
        d = day.isoformat()
        if "feedback" in stages and not ids:
            self.stage_feedback()
        if "trends" in stages and not ids:
            self.stage_trends(day)
        if "plan" in stages and not ids:
            self.plan(day, count=count)
        if "script" in stages:
            self.stage_script(d, ids)
            for _ in range(refill_cycles if (not ids and "plan" in stages) else 0):
                if not self.plan(day, count=count):  # replace scripts the quality gate rejected
                    break
                self.stage_script(d, ids)
        for name in ["voice", "align", "images", "animate", "render", "qc", "upload"]:
            if name in stages:
                log.info("== stage %s", name)
                getattr(self, f"stage_{name}")(d, ids)
        if "upload" in stages:
            self.backup()

    def backup(self) -> None:
        """Snapshot the job database into storage (_system/backups/<date>/)."""
        if self.s.publish.mode != "storage" or not self.s.storage.backup_db:
            return
        try:
            store = make_store(self.s)
            tmp = self.s.work / "backup"
            tmp.mkdir(parents=True, exist_ok=True)
            day = datetime.now(timezone.utc).date().isoformat()
            snap = tmp / "shorts.db"
            dst = sqlite3.connect(snap)
            self.db.conn.backup(dst)
            dst.close()
            prefix = "/".join(p for p in (self.s.storage.prefix.strip("/"), "_system/backups", day) if p)
            store.put(snap, f"{prefix}/{snap.name}", "application/vnd.sqlite3")
            snap.unlink()
            log.info("backed up the job database to %s/", prefix)
        except Exception as e:
            log.warning("database backup skipped: %s", e)

    def status(self, day: str | None = None) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {}
        for r in self.db.jobs(publish_date=day):
            out.setdefault(r["channel"], {}).setdefault(r["state"], 0)
            out[r["channel"]][r["state"]] += 1
        return out


# ---------------------------------------------------------------------- render (process pool)


def render_job(payload: dict[str, Any]) -> dict[str, Any]:
    """Runs in a worker process: build captions, choose music, render final.mp4."""
    from .config import ChannelCfg as _Ch, Settings as _S
    from .media.captions import Word, build_ass
    from .media.music import pick_music, pick_sfx
    from .media.render import Scene, render

    try:
        s = _S.model_validate(payload["settings"])
        ch = _Ch.model_validate(payload["channel"])
        job = payload["job"]
        jdir = Path(job["dir"])
        data = json.loads(job["data"])
        script = json.loads((jdir / "script.json").read_text())
        spans = data["scene_spans"]
        total = spans[-1][1] + s.render.tail
        scenes = []
        for i, sc in enumerate(script["scenes"]):
            clip = jdir / "clips" / f"scene_{i:02d}.mp4"
            media, kind = (clip, "video") if clip.exists() else (jdir / "images" / f"scene_{i:02d}.png", "image")
            start = 0.0 if i == 0 else spans[i][0]
            end = spans[i + 1][0] if i + 1 < len(spans) else total
            scenes.append(Scene(str(media), kind, sc["motion"], start, end))
        words = [Word(w["w"], w["s"], w["e"], w.get("emph", False))
                 for w in json.loads((jdir / "words.json").read_text())]
        ass = jdir / "captions.ass"
        ass.write_text(build_ass(words, s.captions, s.render.width, s.render.height,
                                 headline=script.get("headline"), total=total), encoding="utf-8")
        music = pick_music(s.assets, script.get("music_mood", ""), ch.music_moods, job["id"], total)
        sfx = pick_sfx(s.assets, "whoosh", job["id"])
        render(scenes, jdir / "voice.wav", ass, jdir / "final.mp4", s.render, s.assets / "fonts", total,
               music=music, sfx=sfx)
        return {"ok": True, "duration": round(total, 2), "music": str(music[0].name) if music else None}
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
