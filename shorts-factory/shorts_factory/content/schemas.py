"""Structured-output schemas the LLM must fill. Kept flat and explicit so they validate reliably."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Motion = Literal["zoom_in", "zoom_out", "pan_left", "pan_right", "pan_up", "pan_down"]
HookType = Literal[
    "shocking_fact", "question", "contradiction", "in_medias_res", "stakes", "second_person", "countdown",
]


class TopicIdea(BaseModel):
    title: str = Field(description="Working title of the story, specific (names/places/years), max 70 chars")
    angle: str = Field(description="The single surprising angle that makes this worth 45 seconds")
    format: str = Field(description="One of the channel's format ids")
    keywords: list[str] = Field(
        description="3-6 SPECIFIC keywords naming the story's people, places, events, objects and years, "
                    "lowercase; never generic words like history, mystery, facts, money or space")
    priority: int = Field(description="Estimated viral potential 1-10 for this audience, be harsh")


class TopicBatch(BaseModel):
    ideas: list[TopicIdea]


class SceneDraft(BaseModel):
    narration: str = Field(
        description="Exact words spoken in this scene: 1-2 short spoken sentences, 5-20 words. "
                    "Scene 1 is the hook. No stage directions, no emojis.")
    visual: str = Field(
        description="Self-contained image prompt for this scene: subject, action, setting, era, camera "
                    "angle, lighting, mood. Never refer to other scenes ('he', 'the same man'). No text in image.")
    motion: Motion = Field(description="Camera move that best fits the beat")
    emphasis: list[str] = Field(description="0-2 single words copied from this scene's narration to highlight")


class ScriptDraft(BaseModel):
    title: str = Field(description="YouTube title, max 60 chars, curiosity-driven, true, not identical to the hook")
    headline: str = Field(description="On-screen hook text for the first seconds, 2-6 words")
    hook_type: HookType
    scenes: list[SceneDraft] = Field(description="8-16 scenes; a new visual every 2.5-5 seconds")
    description: str = Field(description="2-3 sentence YouTube description in the channel language, no hashtags")
    hashtags: list[str] = Field(description="3 relevant hashtags without the # sign")
    tags: list[str] = Field(description="8-15 search tags")
    pinned_comment: str = Field(description="A question that sparks debate in the comments, max 140 chars")
    music_mood: str = Field(description="One of the channel's allowed music moods")
    fact_claims: list[str] = Field(description="Every checkable factual claim in the narration, one per item")


class Critique(BaseModel):
    hook_score: int = Field(description="1-10: would a scrolling viewer stop within 1.5 seconds?")
    retention_score: int = Field(description="1-10: does every 5-7 seconds add a new reason to keep watching?")
    clarity_score: int = Field(description="1-10: instantly understandable when heard once at speed?")
    payoff_score: int = Field(description="1-10: is the ending satisfying and does the last line loop into the first?")
    originality_score: int = Field(description="1-10: fresh angle vs generic/overdone content?")
    accuracy_risk: Literal["low", "medium", "high"]
    policy_risk: Literal["low", "medium", "high"] = Field(
        description="Risk under YouTube advertiser-friendly, misinformation or inauthentic-content policies")
    issues: list[str] = Field(description="Concrete, actionable fixes, most important first")
    verdict: Literal["publish", "revise", "reject"]

    @property
    def overall(self) -> float:
        return (self.hook_score * 1.5 + self.retention_score * 1.25 + self.clarity_score
                + self.payoff_score + self.originality_score) / 5.75
