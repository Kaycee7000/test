"""Per-topic research: a sourced dossier for every video, plus the retrieval context the writer sees."""
from __future__ import annotations

import logging
import re
from typing import Any

from ..config import ChannelCfg, Settings
from ..content.llm import LLM
from ..content.platforms import platform_block
from . import collectors as C
from .kb import KnowledgeBase

log = logging.getLogger(__name__)

DOSSIER_SECTIONS = ["Core story", "Verified facts", "Surprising details", "Timeline", "Common misconceptions",
                    "Visual reference", "Uncertain or disputed", "Sources"]


def dossier_system(ch: ChannelCfg, s: Settings) -> str:
    return f"""You are the lead researcher for "{ch.name}", a short-form video channel ({ch.niche}).
{platform_block(ch.platform)}

Build a research dossier a scriptwriter will turn into a {ch.target_seconds}-second video. Use web search.
Prefer primary sources, encyclopedias, museums, universities, space agencies and reputable outlets; include
non-English sources when they are the best ones. Hunt for the specific, surprising, verifiable details that make
a viewer say "no way": names, numbers, dates, quotes that are actually documented. Flag anything disputed.
Write the dossier in English, with exactly these sections:

## Core story
2-4 sentences.
## Verified facts
Numbered, one fact per line, each ending with [source: URL].
## Surprising details
Bullets, each ending with [source: URL].
## Timeline
Bullets "YEAR: event".
## Common misconceptions
Bullets.
## Visual reference
Bullets describing what the people, places, clothing, architecture, technology, weather and light looked like,
so images can be period- and fact-accurate.
## Uncertain or disputed
Bullets; say what is legend or estimate.
## Sources
Bullets "URL: publisher, title"."""


def dossier_user(topic: dict[str, Any], fmt_name: str, wiki: dict[str, Any] | None, past: list[str]) -> str:
    parts = [f"TOPIC: {topic['title']}", f"ANGLE: {topic.get('angle', '')}", f"FORMAT: {fmt_name}",
             f"KEYWORDS: {', '.join(topic.get('keywords', []))}"]
    if wiki:
        parts.append(f"\nWIKIPEDIA STARTING POINT ({wiki['url']}); verify and go beyond it:\n{wiki['body'][:6000]}")
    if past:
        parts.append("\nOUR PAST VIDEOS ON RELATED SUBJECTS (find facts and angles we have NOT used):\n"
                     + "\n".join(f"- {p}" for p in past))
    return "\n".join(parts)


def mock_dossier(topic: dict[str, Any]) -> str:
    return (f"## Core story\n{topic['title']}: a mock dossier for offline runs.\n"
            "## Verified facts\n1. The event is documented in local records. [source: https://example.org/record]\n"
            "## Sources\n- https://example.org/record: Example Archive")


def source_urls(dossier: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"https?://[^\s\]\)>,]+", dossier)))


def build_dossier(llm: LLM, kb: KnowledgeBase, s: Settings, ch: ChannelCfg, job_id: str, topic: dict[str, Any],
                  fetch: C.Fetch | None = None) -> tuple[str, int]:
    """Research one topic; returns (dossier text, knowledge-base doc id)."""
    wiki = None
    if s.research.wikipedia:
        fetch = fetch or C.make_fetch(s.research.user_agent)
        lang = ch.research.wikipedia_lang or ch.language
        try:
            wiki = C.wikipedia_article(fetch, lang, topic["title"]) or (
                C.wikipedia_article(fetch, "en", topic["title"]) if lang != "en" else None)
        except Exception as e:
            log.warning("%s: wikipedia lookup failed: %s", job_id, e)
        if wiki:
            kb.add("wiki_article", ch.folder, ch.language, wiki["title"], wiki["body"], url=wiki["url"],
                   source="wikipedia", platform=ch.platform, key=wiki["key"])
    past = [f"{h.title}: {h.text[:160]}" for h in kb.search(
        f"{topic['title']} {topic.get('angle', '')}", k=4, niche=ch.folder, language=ch.language, kinds=["script"])]
    fmt = ch.format(topic.get("format", ""))
    if s.research.dossier:
        text = llm.research(dossier_system(ch, s), dossier_user(topic, fmt.name, wiki, past),
                            context={"kind": "dossier", "topic": topic})
    else:
        text = f"## Core story\n{topic['title']}\n" + (f"## Wikipedia\n{wiki['body'][:5000]}\n## Sources\n- {wiki['url']}"
                                                       if wiki else "")
    doc_id = kb.add("dossier", ch.folder, ch.language, topic["title"], text, platform=ch.platform,
                    meta={"job_id": job_id, "sources": source_urls(text)}, key=f"dossier:{job_id}", replace=True)
    return text, doc_id


def writing_context(kb: KnowledgeBase, s: Settings, ch: ChannelCfg, topic: dict[str, Any], dossier: str,
                    dossier_doc: int | None) -> str:
    """What the writer gets on top of its brief: the dossier, related notes, and what we already covered."""
    q = f"{topic['title']} {topic.get('angle', '')} {' '.join(topic.get('keywords', []))}"
    related = kb.search(q, k=s.research.retrieve_k, niche=ch.folder, language=ch.language,
                        kinds=["wiki_article", "dossier", "rss", "on_this_day"],
                        exclude_docs=[dossier_doc] if dossier_doc else [])
    past = kb.search(q, k=3, niche=ch.folder, language=ch.language, kinds=["script"])
    parts = []
    if dossier:
        parts.append("RESEARCH DOSSIER (build the story from these facts; never invent beyond them):\n" + dossier[:9000])
    if related:
        parts.append("RELATED NOTES FROM OUR KNOWLEDGE BASE:\n" + "\n".join(
            f"[{h.kind}] {h.title}{f' ({h.url})' if h.url else ''}: {h.text[:500]}" for h in related))
    if past:
        parts.append("OUR PAST VIDEOS ON RELATED SUBJECTS (do not repeat their angle or key facts):\n" + "\n".join(
            f"- {h.title}: {h.text[:200]}" for h in past))
    return "\n\n".join(parts)
