"""Script generation with a quality gate: draft -> lint + critic -> fact-check -> rewrite -> ship or reject."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from ..config import ChannelCfg, LLMCfg
from . import prompts
from .llm import LLM, LLMError
from .schemas import Critique, ScriptDraft

log = logging.getLogger(__name__)

BAD_OPENERS = re.compile(
    r"^\s*(did you know|in this video|have you ever|hey|hi\b|hello|welcome|today we|let me tell|"
    r"sabías que|en este video|hola|bienvenid)", re.I)
CTA = re.compile(r"\b(subscribe|like and|follow for|smash that|suscr[ií]bete|dale like)\b", re.I)


@dataclass
class ScriptResult:
    ok: bool
    draft: ScriptDraft | None
    critique: Critique | None
    rounds: int
    notes: list[str] = field(default_factory=list)


def narration_words(d: ScriptDraft) -> int:
    return sum(len(s.narration.split()) for s in d.scenes)


def normalize(d: ScriptDraft, ch: ChannelCfg) -> ScriptDraft:
    """Silently fix mechanical issues the model tends to get slightly wrong."""
    d = d.model_copy(deep=True)
    d.title = d.title.strip().strip('"')
    d.hashtags = [h.lstrip("#").replace(" ", "") for h in d.hashtags if h.strip()][:3]
    tags, total = [], 0
    for t in (t.strip() for t in d.tags):
        if t and total + len(t) + 1 <= 450:  # YouTube caps the combined tag field at 500 chars
            tags.append(t)
            total += len(t) + 1
    d.tags = tags
    if d.music_mood not in ch.music_moods:
        d.music_mood = ch.music_moods[0]
    for s in d.scenes:
        s.narration = " ".join(s.narration.split())
        words = {w.strip(".,!?;:\"'").lower() for w in s.narration.split()}
        s.emphasis = [e for e in s.emphasis if e.strip(".,!?;:\"'").lower() in words][:2]
    return d


def lint(d: ScriptDraft, ch: ChannelCfg, recent_titles: list[str]) -> list[str]:
    issues: list[str] = []
    lo, hi = ch.target_words
    words = narration_words(d)
    if words < lo * 0.9 or words > hi * 1.1:
        issues.append(f"Narration is {words} words; it must be {lo}-{hi} words.")
    if not 6 <= len(d.scenes) <= 18:
        issues.append(f"{len(d.scenes)} scenes; use 8-16 so the visual changes every 2.5-5 seconds.")
    if d.scenes:
        first = d.scenes[0].narration
        if len(first.split()) > 16:
            issues.append("The hook sentence is too long; the first scene must be at most 12-16 words.")
        if BAD_OPENERS.search(first):
            issues.append("The hook opens with a banned generic opener; start with the most shocking concrete detail.")
    if any(CTA.search(s.narration) for s in d.scenes):
        issues.append("Remove the call to action (subscribe/like/follow) from the narration; end on the loop line.")
    if len(d.title) > 70:
        issues.append(f"Title is {len(d.title)} characters; keep it under 60.")
    if d.title.isupper():
        issues.append("Title is ALL CAPS; use normal capitalisation.")
    for t in recent_titles:
        if fuzz.token_set_ratio(d.title.lower(), t.lower()) >= 88:
            issues.append(f'Title is too close to an existing video ("{t}"); this must be a different story/angle.')
            break
    return issues


FACT_LINE = re.compile(r"^\s*\**\s*(\d+)\s*[.)]?\s*\|\s*(OK|WRONG|UNSURE)\b\s*\|?\s*(.*)$", re.I)


def parse_factcheck(text: str, claims: list[str]) -> dict[str, str]:
    """Map each claim to "OK" or a problem line. Claims the checker skipped are marked UNSURE."""
    verdicts: dict[int, str] = {}
    for ln in text.splitlines():
        m = FACT_LINE.match(ln)
        if not m:
            continue
        n, verdict, note = int(m.group(1)), m.group(2).upper(), m.group(3).strip()
        if 1 <= n <= len(claims):
            verdicts[n] = "OK" if verdict == "OK" else f'{verdict}: "{claims[n - 1]}" -> {note}'
    out: dict[str, str] = {}
    for i, c in enumerate(claims, 1):
        out[c] = verdicts.get(i, f'UNSURE: "{c}" -> the fact-checker could not verify it')
    return out


class ScriptWriter:
    def __init__(self, llm: LLM, cfg: LLMCfg):
        self.llm = llm
        self.cfg = cfg

    def fact_check(self, ch: ChannelCfg, claims: list[str], cache: dict[str, str]) -> list[str]:
        """Verify claims not seen before (cache: normalized claim -> verdict); return open problems."""
        todo = list(dict.fromkeys(c.strip() for c in claims if c.strip() and c.strip().lower() not in cache))
        if todo:
            text = self.llm.research(prompts.factcheck_system(), prompts.factcheck_user(todo),
                                     context={"channel": ch.id, "kind": "factcheck"})
            for c, verdict in parse_factcheck(text, todo).items():
                cache[c.lower()] = verdict
        wanted = {c.strip().lower() for c in claims}
        return [v for k, v in cache.items() if k in wanted and v != "OK"]

    def write(self, ch: ChannelCfg, fmt_id: str, topic: dict, learnings: str,
              recent_titles: list[str]) -> ScriptResult:
        fmt = ch.format(fmt_id)
        system = prompts.writer_system(ch)
        lo, hi = ch.target_words
        ctx = {"channel": ch.id, "topic": topic["title"], "words_target": (lo + hi) // 2,
               "music_moods": ch.music_moods}
        draft = normalize(self.llm.structured(
            system, prompts.writer_user(ch, fmt, topic, learnings, recent_titles), ScriptDraft,
            effort=self.cfg.effort, context=ctx | {"step": "script draft"}), ch)

        fc_cache: dict[str, str] = {}
        notes: list[str] = []
        critique: Critique | None = None
        for rnd in range(self.cfg.max_rewrites + 1):
            issues = lint(draft, ch, recent_titles)
            critique = self.llm.structured(
                prompts.critic_system(ch), prompts.critic_user(ch, fmt, draft, issues),
                Critique, effort=self.cfg.critic_effort, context=ctx | {"step": "critic"})
            craft_ok = (
                not issues
                and critique.hook_score >= self.cfg.min_hook_score
                and critique.overall >= self.cfg.min_overall_score
                and critique.accuracy_risk != "high"
                and critique.policy_risk != "high"
                and critique.verdict != "reject"
            )
            # Web fact-checking is the most expensive step, so it runs only on drafts that would otherwise
            # ship. Every published script is still verified; drafts headed for a rewrite are not.
            fact_problems = self.fact_check(ch, draft.fact_claims, fc_cache) if craft_ok and ch.fact_check else []
            wrong = [f for f in fact_problems if f.upper().startswith("WRONG")]
            notes.append(f"round {rnd}: {prompts.critique_summary(critique)}; lint={issues[:3]}; "
                         f"critic={[i[:160] for i in critique.issues[:3]]}; wrong_facts={len(wrong)}")
            if craft_ok and not wrong:
                return ScriptResult(True, draft, critique, rnd + 1, notes)
            if critique.verdict == "reject" or rnd == self.cfg.max_rewrites:
                break
            fixes = issues + fact_problems + critique.issues
            try:
                draft = normalize(self.llm.structured(
                    system, prompts.rewrite_user(ch, fmt, draft, fixes), ScriptDraft,
                    effort=self.cfg.effort, context=ctx | {"step": "rewrite"}), ch)
            except LLMError as e:
                # Keep the last complete draft; it is rejected (and replaced) rather than failing the job.
                notes.append(f"rewrite {rnd + 1} failed: {e}")
                break
        return ScriptResult(False, draft, critique, rnd + 1, notes)
