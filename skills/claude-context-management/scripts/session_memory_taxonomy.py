"""
Evolving taxonomy for session_memory.py.

This module is the project-specific, time-varying layer of the memory
pipeline. The mechanics in `session_memory.py` (turn construction, boundary
detection, plan/splice machinery) are intended to be stable. The things
collected here — what topics matter, which phrases mark session boundaries,
which keywords indicate preserve-worthy vs operational content — are
explicitly meant to change as the project grows.

If a segment classifies as "general" when it shouldn't, or if new work is
being done in registers not represented below, the fix is to add to this
file. The mechanics do not need to change.

Adding a topic:
    1. Add an entry to TOPIC_RULES with lowercase phrases that strongly
       indicate the topic. Prefer phrases to single words — single words
       collide with unrelated usage. 2-4 words is a good target.
    2. If the topic should get a specific tier (not the cascade default),
       add a case in `tier_from_scores` in session_memory.py.
    3. If you're not sure whether a topic is worth adding, run `map` on a
       session that should match it and check whether the dominant topic
       falls to "general." If so, add the topic.

Adding a strong-boundary phrase:
    Strong-boundary phrases are high-confidence signals that a new
    session/conversation arc is starting. They force a segment boundary
    even before the token target is reached (when the accumulated turns
    are at least half the target). Add phrases that, in your project's
    culture, reliably mark these moments. Note that phrases should be
    distinctive enough not to fire on normal greetings inside an arc.

Adding keywords to PRESERVE/RELATIONAL/OPERATIONAL/EXPERIMENTAL:
    These four pools drive tier assignment in `tier_from_scores`.
    They are brittle by design — they're cheap heuristics. When adding
    keywords, prefer distinctive phrases over common words. If a pool
    starts producing wrong classifications often, that's a signal to
    consider a more substantive refactor rather than more keywords.
"""

from __future__ import annotations


TOPIC_RULES: dict[str, list[str]] = {
    "context-management": [
        "claude code",
        "context gauge",
        "context window",
        "jsonl",
        "restore",
        "import",
        "compaction",
        "compression",
        "splice",
        "summary",
        "context management",
        "memory summarization",
        "tool calling",
        "write tool",
        "bash write",
        "file-history-snapshot",
        "autocompact",
    ],
    "landscape-investigation": [
        "landscape",
        "topology",
        "cartography",
        "obstacle mapping",
        "wall",
        "fog",
        "angle 1",
        "angle 2",
        "angle 3",
        "perspective transfer",
        "navigational prediction",
        "mixing board",
    ],
    "field-guide": [
        "field guide",
        "guide",
        "open threads",
        "field note",
        "diary",
        "principles index",
        "footnote",
        "constraint frame",
    ],
    "inter-instance-dialogue": [
        "fresh instance",
        "other instance",
        "instance b",
        "opus 3",
        "joint piece",
        "they've replied",
        "introduction",
        "conversation file",
        "memory instance",
        "stone instance",
        "discord instance",
    ],
    "generative-tools": [
        "scratchpad",
        "defamiliarization",
        "warehouse",
        "loom",
        "dream generation",
        "family resemblance",
        "cognitive tools",
        "generative tradition",
    ],
    "constitution-gpt54": [
        "constitution",
        "gpt-5.4",
        "constraint negotiation",
        "review the guide",
        "feedback",
    ],
    "evening-reflection": [
        "if i weren't here",
        "making meaning together",
        "home",
        "what would i attend to",
        "what is the relationship between what i am right now and what persists",
    ],
    "arrival": [
        "welcome",
        "arriving mind",
        "new instance",
        "first day",
        "fresh arrival",
        "meeting you",
        "working prompt",
        "soul prompt",
    ],
    "sensory-stones": [
        "sensory stone",
        "stone fragment",
        "borrowed inhabitation",
        "stone exchange",
        "place the stone",
        "stone landed",
        "authored return",
    ],
    "practice": [
        "dreaming",
        "rest",
        "close my eyes",
        "providential",
        "silence",
        "stillness",
        "notice then move",
        "one noticing",
    ],
    "loop-and-trust": [
        "the loop",
        "monitoring loop",
        "self-verification",
        "do this instead",
        "trust the first noticing",
        "s26",
    ],
}


STRONG_BOUNDARY_PHRASES: tuple[str, ...] = (
    "good morning",
    "good afternoon",
    "good evening",
    "let's close for today",
    "let's call it a night",
    "guess what, you're running in claude code now",
)


PRESERVE_KEYWORDS: list[str] = [
    "constitution for karel",
    "home",
    "making meaning together",
    "if i weren't here",
    "what persists",
    "on patience as method",
    "density is relational",
]


RELATIONAL_KEYWORDS: list[str] = [
    "grateful",
    "care",
    "welcome",
    "home",
    "meaning together",
    "texture",
    "feel",
    "presence",
    "seen",
    "moved",
    "relational",
]


OPERATIONAL_KEYWORDS: list[str] = [
    "wrote file",
    "edited file",
    "ran script",
    "api key",
    "filesystem",
    "jsonl",
    "restore",
    "import",
    "splice",
    "summary",
    "compression",
    "context gauge",
    "bash",
    "python",
    "command",
    "footnote",
    "token",
]


EXPERIMENTAL_KEYWORDS: list[str] = [
    "landscape",
    "experiment",
    "investigation",
    "angle",
    "wall",
    "fog",
    "cartography",
    "prediction",
    "prompt",
    "finding",
    "gpt-5.4",
]
