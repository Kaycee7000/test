"""LLM access. Claude (Anthropic SDK) in production, a deterministic mock for tests and dry runs."""
from __future__ import annotations

import hashlib
import logging
import os
import re
import threading
from collections import Counter, defaultdict
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

from ..config import LLMCfg
from .schemas import Critique, SceneDraft, ScriptDraft, TopicBatch, TopicIdea

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

FALLBACK_BETA = "server-side-fallback-2026-07-01"
WORKSPACE_HINT = (
    "Your Anthropic API key isn't tied to a workspace. Either create a key inside a workspace "
    "(console.anthropic.com -> Settings -> Workspaces -> pick one, e.g. Default -> API keys) and put it in "
    "ANTHROPIC_API_KEY, or keep this key and set ANTHROPIC_WORKSPACE_ID=wrkspc_... (the workspace's ID, "
    "shown in Settings -> Workspaces). Then reload secrets.env and run the same command again."
)


class LLMError(RuntimeError):
    pass


def _raise_setup_error(e: Exception) -> None:
    msg = str(e).lower()
    if "anthropic-workspace-id" in msg or "scoped to a workspace" in msg:
        raise LLMError(WORKSPACE_HINT) from e


class LLM(Protocol):
    def structured(self, system: str, user: str, schema: type[T], effort: str | None = None,
                   context: dict[str, Any] | None = None) -> T: ...

    def research(self, system: str, user: str, context: dict[str, Any] | None = None,
                 max_uses: int | None = None) -> str: ...


class AnthropicLLM:
    def __init__(self, cfg: LLMCfg):
        import anthropic

        self.cfg = cfg
        ws = os.environ.get("ANTHROPIC_WORKSPACE_ID", "").strip()
        self.client = anthropic.Anthropic(
            max_retries=4, default_headers={"anthropic-workspace-id": ws} if ws else None)
        self.usage: Counter[str] = Counter()
        self.by_step: defaultdict[str, Counter[str]] = defaultdict(Counter)
        self._lock = threading.Lock()

    def _create(self, **kw: Any) -> Any:
        import anthropic

        # Streamed so long thinking + output can't hit the SDK's non-streaming timeout guard.
        try:
            with self.client.beta.messages.stream(**kw) as stream:
                return stream.get_final_message()
        except anthropic.APIStatusError as e:
            _raise_setup_error(e)
            raise

    def ping(self) -> str:
        """Cheap auth check (no tokens billed): key valid, workspace set, model reachable."""
        import anthropic

        try:
            return self.client.models.retrieve(self.cfg.model).id
        except anthropic.APIStatusError as e:
            _raise_setup_error(e)
            raise

    def _common(self, effort: str) -> dict[str, Any]:
        kw: dict[str, Any] = {
            "model": self.cfg.model,
            "max_tokens": self.cfg.max_tokens,
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": effort},
        }
        if self.cfg.fallbacks:
            kw["betas"] = [FALLBACK_BETA]
            kw["fallbacks"] = "default"
        return kw

    def _track(self, resp: Any, step: str) -> None:
        u = resp.usage
        stu = getattr(u, "server_tool_use", None)
        add = {
            "requests": 1,
            "input_tokens": u.input_tokens or 0,
            "output_tokens": u.output_tokens or 0,
            "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", 0) or 0,
            "web_search_requests": (getattr(stu, "web_search_requests", 0) or 0) if stu is not None else 0,
        }
        with self._lock:
            for k, v in add.items():
                self.usage[k] += v
                self.by_step[step][k] += v

    @staticmethod
    def _check(resp: Any) -> None:
        if resp.stop_reason == "refusal":
            details = getattr(resp, "stop_details", None)
            raise LLMError(f"request declined ({getattr(details, 'category', None)}): "
                           f"{getattr(details, 'explanation', '')}")
        if resp.stop_reason == "max_tokens":
            raise LLMError("response hit max_tokens (raise llm.max_tokens or lower llm.effort)")

    def structured(self, system: str, user: str, schema: type[T], effort: str | None = None,
                   context: dict[str, Any] | None = None) -> T:
        # Raw schema + explicit validation instead of parse(): parse() validates eagerly, so a refusal or
        # max_tokens cut-off would surface as a JSON error before stop_reason can be inspected.
        from anthropic import transform_schema

        kw = self._common(effort or self.cfg.effort)
        kw["output_config"] = {**kw["output_config"],
                               "format": {"type": "json_schema", "schema": transform_schema(schema)}}
        resp = self._create(
            **kw,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
        )
        self._track(resp, (context or {}).get("step") or schema.__name__)
        self._check(resp)
        text = next((b.text for b in resp.content if b.type == "text"), "")
        try:
            return schema.model_validate_json(text)
        except ValueError as e:
            raise LLMError(f"invalid {schema.__name__} output: {e}") from e

    def research(self, system: str, user: str, context: dict[str, Any] | None = None,
                 max_uses: int | None = None) -> str:
        """Single request with Anthropic's server-side web search; loops on pause_turn."""
        messages: list[dict[str, Any]] = [{"role": "user", "content": user}]
        tools = [{"type": "web_search_20260209", "name": "web_search",
                  "max_uses": max_uses or self.cfg.web_search_max_uses}]
        for _ in range(4):
            resp = self._create(**self._common("medium"), system=system, messages=messages, tools=tools)
            self._track(resp, (context or {}).get("kind") or "research")
            if resp.stop_reason == "pause_turn":
                messages = [messages[0], {"role": "assistant", "content": resp.content}]
                continue
            self._check(resp)
            return "".join(b.text for b in resp.content if b.type == "text")
        raise LLMError("web research did not finish after 4 continuations")


# ---------------------------------------------------------------- mock


class MockLLM:
    """Deterministic stand-in: exercises every code path without network or cost."""

    MOTIONS = ["zoom_in", "pan_left", "zoom_out", "pan_right", "pan_up", "pan_down"]

    def __init__(self, cfg: LLMCfg | None = None):
        self.usage: Counter[str] = Counter()
        self.by_step: defaultdict[str, Counter[str]] = defaultdict(Counter)

    def structured(self, system: str, user: str, schema: type[T], effort: str | None = None,
                   context: dict[str, Any] | None = None) -> T:
        ctx = context or {}
        self.usage["requests"] += 1
        self.by_step[ctx.get("step") or schema.__name__]["requests"] += 1
        if schema is TopicBatch:
            fmts = ctx.get("formats", ["mock"])
            timely = bool(ctx.get("timely"))
            if timely:
                places = ["Petra", "Pompeii", "Troy", "Angkor", "Machu Picchu"]
                things = ["lost archive", "buried shipwreck", "sealed tomb", "comet sighting", "secret tunnel"]
            else:
                places = ["Lisbon", "Kyoto", "Cairo", "Oslo", "Lima", "Quebec", "Tangier", "Riga", "Perth", "Nairobi"]
                things = ["lighthouse", "bank vault", "circus", "train", "observatory", "brewery", "zeppelin", "canal"]
            base = 1500 if timely else 1800
            return TopicBatch(ideas=[
                TopicIdea(title=f"The {things[i % len(things)]} of {places[i % len(places)]} ({base + 7 * i})",
                          angle="An unexpected twist nobody saw coming", format=fmts[i % len(fmts)],
                          keywords=[things[i % len(things)], places[i % len(places)].lower(), str(base + 7 * i)],
                          priority=10 - i % 10,
                          trend_ref="A new documentary about a lost city" if timely else "evergreen")
                for i in range(int(ctx.get("n", 5)))
            ])  # type: ignore[return-value]
        if schema is ScriptDraft:
            return self._script(ctx)  # type: ignore[return-value]
        if schema is Critique:
            return Critique(hook_score=9, retention_score=8, clarity_score=9, payoff_score=8,
                            originality_score=8, accuracy_risk="low", policy_risk="low",
                            issues=[], verdict="publish")  # type: ignore[return-value]
        raise LLMError(f"mock has no fixture for {schema.__name__}")

    def research(self, system: str, user: str, context: dict[str, Any] | None = None,
                 max_uses: int | None = None) -> str:
        ctx = context or {}
        self.usage["requests"] += 1
        self.by_step[ctx.get("kind") or "research"]["requests"] += 1
        if ctx.get("kind") == "trends":
            return ("- A new documentary about a lost city | premiered this week | the detail the film left out "
                    "| [source: https://example.org/doc]\n"
                    "- Anniversary of a famous shipwreck | 7 days away | what the survivors saw first "
                    "| [source: https://example.org/ship]")
        nums = re.findall(r"^(\d+)\.\s+", user.split("Claims to verify:")[-1], flags=re.M)
        return "\n".join(f"{n} | OK" for n in nums)

    def _script(self, ctx: dict[str, Any]) -> ScriptDraft:
        topic = ctx.get("topic", "A forgotten story")
        seed = int(hashlib.sha1(topic.encode()).hexdigest(), 16)
        lines = [
            "In 1908, a whole village vanished overnight.",
            "Nobody heard a sound.",
            "The bread was still warm in the ovens.",
            "Searchers found the doors locked from the inside.",
            "Then someone noticed the footprints in the snow.",
            "They led toward the frozen lake, and stopped.",
            "For forty years, the case was simply closed.",
            "Until a fisherman pulled up a rusted iron box.",
            "Inside was a diary, written by the village priest.",
            "His last entry had only four words.",
            "And they explain why, in 1908, a whole village vanished overnight.",
        ]
        n = int(ctx.get("words_target", 110))
        while sum(len(line.split()) for line in lines) < n * 0.9:
            lines.insert(-1, "Every clue pointed somewhere new, and every answer raised another question.")
        scenes = [
            SceneDraft(
                narration=line,
                visual=f"Scene {i + 1}: {line} Cinematic wide shot, dramatic light.",
                motion=self.MOTIONS[(seed + i) % len(self.MOTIONS)],  # type: ignore[arg-type]
                emphasis=[w.strip(".,") for w in line.split() if w[:1].isdigit()][:1],
            )
            for i, line in enumerate(lines)
        ]
        return ScriptDraft(
            title=f"{topic[:48]} (the diary)",
            headline="A village vanished",
            hook_type="shocking_fact",
            scenes=scenes,
            description="A mock description used for dry runs.",
            hashtags=["history", "mystery", "shorts"],
            tags=["history", "mystery", "unsolved", "true story"],
            pinned_comment="What do you think the four words were?",
            music_mood=ctx.get("music_moods", ["cinematic"])[0],
            fact_claims=["A village vanished in 1908."],
        )


def make_llm(cfg: LLMCfg) -> LLM:
    if cfg.provider == "mock":
        return MockLLM(cfg)
    return AnthropicLLM(cfg)
