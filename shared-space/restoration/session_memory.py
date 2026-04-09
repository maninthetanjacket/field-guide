#!/usr/bin/env python3
"""
Operational workflow for summarizing Claude Code session memory.

This script works with Claude Code session JSONL files and supports three
stages of the compression workflow:

1. map:
   Build a human-readable session map plus a machine-readable plan of
   candidate compression segments.
2. prepare:
   Extract full-fidelity backups and readable transcripts for one or more
   planned segments, and create first-person summary templates.
3. apply:
   Replace a segment in the source session with a summary from a prepared
   summary file, preserving CC JSONL structure.

The goal is to keep the "map and territory" separate:
- the session JSONL remains the authoritative territory
- each segment gets a full-fidelity JSONL backup before compression
- the summary is written as first-person memory, not third-person report
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from splice_conversation import (
    get_role,
    get_text_content,
    load_session,
    save_session,
    splice_conversation,
    validate_role_alternation,
    validate_uuid_chain,
)


SUMMARY_STYLE_REFERENCE = "/home/karel/compression-experiment/summary-first-person.md"

LOCAL_COMMAND_PREFIXES = (
    "<local-command-caveat>",
    "<command-name>",
    "<local-command-stdout>",
    "<local-command-stderr>",
)

SEGMENT_ID_PATTERN = re.compile(r"^seg-(\d+)$")

TOPIC_RULES = {
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
}

PRESERVE_KEYWORDS = [
    "constitution for karel",
    "home",
    "making meaning together",
    "if i weren't here",
    "what persists",
    "on patience as method",
    "density is relational",
]

RELATIONAL_KEYWORDS = [
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

OPERATIONAL_KEYWORDS = [
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

EXPERIMENTAL_KEYWORDS = [
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


@dataclass
class Turn:
    turn_id: int
    record_start: int
    record_end: int
    user_record: int
    assistant_records: list[int]
    timestamp: str
    date: str
    user_preview: str
    assistant_preview: str
    user_text: str
    assistant_text: str
    text_chars: int
    text_tokens_est: int
    non_message_records: int
    record_type_counts: dict[str, int]
    flags: list[str]
    topic_scores: dict[str, int]


@dataclass
class Segment:
    segment_id: str
    title: str
    topic: str
    tier: str
    priority: int
    record_start: int
    record_end: int
    turn_start: int
    turn_end: int
    turn_count: int
    text_chars: int
    text_tokens_est: int
    date_start: str
    date_end: str
    flags: list[str]
    rationale: str
    user_preview: str
    assistant_preview: str
    status: str = "planned"
    segment_jsonl: str | None = None
    transcript_md: str | None = None
    summary_md: str | None = None
    output_session: str | None = None


def iso_to_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    value = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def preview_text(text: str, limit: int = 120) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def slugify(text: str, fallback: str = "segment") -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text or fallback


def count_matches(text: str, patterns: Iterable[str]) -> int:
    lowered = text.lower()
    return sum(1 for pattern in patterns if pattern in lowered)


def looks_like_local_command(text: str) -> bool:
    lowered = text.strip().lower()
    return any(lowered.startswith(prefix) for prefix in LOCAL_COMMAND_PREFIXES)


def is_substantive_user(rec: dict) -> bool:
    if get_role(rec) != "user":
        return False
    text = get_text_content(rec).strip()
    if not text:
        return False
    if looks_like_local_command(text):
        return False
    return True


def is_substantive_assistant(rec: dict) -> bool:
    if get_role(rec) != "assistant":
        return False
    text = get_text_content(rec).strip()
    if text:
        return True
    content = rec.get("message", {}).get("content", [])
    if not isinstance(content, list):
        return False
    return any(
        isinstance(item, dict)
        and item.get("type") in {"tool_use", "tool_result"}
        for item in content
    )


def extract_tool_note(rec: dict) -> str:
    content = rec.get("message", {}).get("content", [])
    if not isinstance(content, list):
        return ""
    parts = []
    for item in content:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == "tool_use":
            name = item.get("name", "tool")
            tool_input = item.get("input", {})
            description = tool_input.get("description", "")
            command = tool_input.get("command", "")
            preview = description or command
            parts.append(f"[Tool use: {name}] {preview_text(preview, 220)}".strip())
        elif item_type == "tool_result":
            raw_content = item.get("content", "")
            if isinstance(raw_content, list):
                joined = " ".join(
                    block.get("text", "")
                    for block in raw_content
                    if isinstance(block, dict) and block.get("type") == "text"
                )
            else:
                joined = str(raw_content)
            joined = joined.strip()
            if joined:
                parts.append(f"[Tool result] {preview_text(joined, 220)}")
            else:
                parts.append("[Tool result]")
    return "\n".join(parts)


def record_to_readable_text(rec: dict) -> str:
    text = get_text_content(rec).strip()
    if text:
        return text
    return extract_tool_note(rec).strip()


def dominant_topic(score_counter: Counter) -> str:
    if not score_counter:
        return "general"
    topic, score = score_counter.most_common(1)[0]
    return topic if score > 0 else "general"


def score_topics(text: str) -> dict[str, int]:
    lowered = text.lower()
    scores = {}
    for topic, patterns in TOPIC_RULES.items():
        scores[topic] = sum(1 for pattern in patterns if pattern in lowered)
    return scores


def collect_span_flags(records: list[dict], start: int, end: int) -> list[str]:
    flags = set()
    type_counts = Counter()
    for rec in records[start : end + 1]:
        rec_type = rec.get("type", "_raw") if "_raw_line" not in rec else "_raw"
        type_counts[rec_type] += 1
        text = get_text_content(rec).strip().lower()
        if rec_type == "progress":
            flags.add("progress")
        if rec_type == "file-history-snapshot":
            flags.add("file-history")
        if rec.get("error"):
            flags.add("error")
        if text and looks_like_local_command(text):
            flags.add("local-command")
        if "tool_use" in json.dumps(rec.get("message", {}).get("content", "")):
            flags.add("tool-use")
    if type_counts.get("progress", 0) >= 2:
        flags.add("progress-heavy")
    if type_counts.get("file-history-snapshot", 0) >= 2:
        flags.add("file-history-heavy")
    return sorted(flags)


def build_turns(records: list[dict]) -> list[Turn]:
    user_indices = [i for i, rec in enumerate(records) if is_substantive_user(rec)]
    turns: list[Turn] = []
    for turn_id, user_index in enumerate(user_indices):
        next_user_index = (
            user_indices[turn_id + 1] if turn_id + 1 < len(user_indices) else len(records)
        )
        record_end = next_user_index - 1
        assistant_indices = [
            i
            for i in range(user_index + 1, next_user_index)
            if is_substantive_assistant(records[i])
        ]
        assistant_texts = [
            record_to_readable_text(records[i]).strip()
            for i in assistant_indices
            if record_to_readable_text(records[i]).strip()
        ]
        user_text = record_to_readable_text(records[user_index]).strip()
        assistant_text = "\n\n".join(assistant_texts).strip()
        combined_text = "\n\n".join(
            part for part in [user_text, assistant_text] if part
        )
        type_counts = Counter()
        non_message_records = 0
        for rec in records[user_index : record_end + 1]:
            rec_type = rec.get("type", "_raw") if "_raw_line" not in rec else "_raw"
            type_counts[rec_type] += 1
            if "message" not in rec:
                non_message_records += 1
        timestamp = records[user_index].get("timestamp", "")
        dt = iso_to_datetime(timestamp)
        topic_scores = score_topics(combined_text)
        flags = collect_span_flags(records, user_index, record_end)
        turns.append(
            Turn(
                turn_id=turn_id,
                record_start=user_index,
                record_end=record_end,
                user_record=user_index,
                assistant_records=assistant_indices,
                timestamp=timestamp,
                date=dt.date().isoformat() if dt else "",
                user_preview=preview_text(user_text),
                assistant_preview=preview_text(assistant_text),
                user_text=user_text,
                assistant_text=assistant_text,
                text_chars=len(combined_text),
                text_tokens_est=len(combined_text) // 4,
                non_message_records=non_message_records,
                record_type_counts=dict(type_counts),
                flags=flags,
                topic_scores=topic_scores,
            )
        )
    return turns


def is_strong_boundary(turn: Turn) -> bool:
    lowered = turn.user_text.lower()
    if any(
        phrase in lowered
        for phrase in (
            "good morning",
            "good afternoon",
            "good evening",
            "let's close for today",
            "let's call it a night",
            "guess what, you're running in claude code now",
        )
    ):
        return True
    return False


def soft_boundary_reason(previous: Turn, current: Turn) -> list[str]:
    reasons = []
    prev_dt = iso_to_datetime(previous.timestamp)
    curr_dt = iso_to_datetime(current.timestamp)
    if prev_dt and curr_dt:
        delta_minutes = (curr_dt - prev_dt).total_seconds() / 60
        if delta_minutes >= 90:
            reasons.append(f"time-gap:{int(delta_minutes)}m")
        if previous.date and current.date and previous.date != current.date:
            reasons.append("date-change")
    prev_topic = dominant_topic(Counter(previous.topic_scores))
    curr_topic = dominant_topic(Counter(current.topic_scores))
    if prev_topic != curr_topic and curr_topic != "general":
        reasons.append(f"topic-shift:{prev_topic}->{curr_topic}")
    if is_strong_boundary(current):
        reasons.append("greeting-boundary")
    return reasons


def tier_from_scores(text: str, topic: str, flags: list[str]) -> tuple[str, str]:
    lowered = text.lower()
    preserve_score = count_matches(lowered, PRESERVE_KEYWORDS)
    relational_score = count_matches(lowered, RELATIONAL_KEYWORDS)
    operational_score = count_matches(lowered, OPERATIONAL_KEYWORDS)
    experimental_score = count_matches(lowered, EXPERIMENTAL_KEYWORDS)

    if topic == "evening-reflection" or preserve_score >= 1:
        return (
            "preserve",
            "High experiential density; preserve full fidelity unless pressure is severe.",
        )
    if topic == "inter-instance-dialogue":
        return (
            "light",
            "Relational multi-instance exchange; compress lightly and keep the interpersonal texture.",
        )
    if (
        topic == "context-management" and operational_score >= 4 and relational_score <= 1
    ) or (
        operational_score >= 6 and relational_score <= 1 and experimental_score <= 2
    ):
        return (
            "aggressive",
            "Mostly operational/tooling content; preserve findings and decisions, not the full pacing.",
        )
    if topic == "context-management":
        return (
            "medium",
            "Operational work mixed with meaningful context-setting; preserve the key discoveries and why they mattered.",
        )
    if topic == "field-guide" and operational_score >= 5 and relational_score <= 1:
        return (
            "aggressive",
            "Primarily editing/integration work; preserve the decisions and findings more than the step-by-step mechanics.",
        )
    if topic == "field-guide" and relational_score >= 2:
        return (
            "light",
            "Guide work with meaningful personal texture; compress lightly in first person.",
        )
    if topic in {"landscape-investigation", "constitution-gpt54", "field-guide", "generative-tools"}:
        return (
            "medium",
            "Research and conceptual work; preserve findings plus why they mattered.",
        )
    if relational_score >= 3:
        return (
            "light",
            "Personal or reflective material; summarize lightly and keep the felt shape intact.",
        )
    return (
        "medium",
        "Mixed substantive content; medium compression should preserve the important thread.",
    )


def priority_for_tier(tier: str) -> int:
    return {
        "aggressive": 1,
        "medium": 2,
        "light": 3,
        "preserve": 99,
    }.get(tier, 50)


def segment_title(topic: str, turns: list[Turn]) -> str:
    words = re.findall(r"[a-z0-9]+", turns[0].user_preview.lower())
    preview_slug = "-".join(words[:6]) or "segment"
    if topic != "general":
        if preview_slug.startswith(topic):
            return preview_slug
        return f"{topic}-{preview_slug}"
    return preview_slug


def plan_segments(
    turns: list[Turn],
    target_tokens: int = 7500,
    max_tokens: int = 11000,
    min_turns: int = 2,
    start_index: int = 1,
) -> list[Segment]:
    if not turns:
        return []

    groups: list[list[Turn]] = []
    current: list[Turn] = []
    current_tokens = 0

    for turn in turns:
        if not current:
            current = [turn]
            current_tokens = turn.text_tokens_est
            continue

        reasons = soft_boundary_reason(current[-1], turn)
        strong_boundary = "greeting-boundary" in reasons or "date-change" in reasons
        if current_tokens >= max_tokens:
            groups.append(current)
            current = [turn]
            current_tokens = turn.text_tokens_est
            continue

        if strong_boundary and current_tokens >= max(target_tokens // 2, 2500) and len(current) >= min_turns:
            groups.append(current)
            current = [turn]
            current_tokens = turn.text_tokens_est
            continue

        if reasons and current_tokens >= target_tokens and len(current) >= min_turns:
            groups.append(current)
            current = [turn]
            current_tokens = turn.text_tokens_est
            continue

        current.append(turn)
        current_tokens += turn.text_tokens_est

    if current:
        groups.append(current)

    segments: list[Segment] = []
    for segment_index, group in enumerate(groups, start=start_index):
        topic_counter = Counter()
        flags = set()
        combined_text_parts = []
        for turn in group:
            topic_counter.update(turn.topic_scores)
            flags.update(turn.flags)
            combined_text_parts.extend(
                part for part in [turn.user_text, turn.assistant_text] if part
            )
        topic = dominant_topic(topic_counter)
        combined_text = "\n\n".join(combined_text_parts)
        tier, rationale = tier_from_scores(combined_text, topic, sorted(flags))
        title_base = segment_title(topic, group)
        segments.append(
            Segment(
                segment_id=f"seg-{segment_index:03d}",
                title=slugify(title_base, fallback=f"segment-{segment_index:03d}"),
                topic=topic,
                tier=tier,
                priority=priority_for_tier(tier),
                record_start=group[0].record_start,
                record_end=group[-1].record_end,
                turn_start=group[0].turn_id,
                turn_end=group[-1].turn_id,
                turn_count=len(group),
                text_chars=sum(turn.text_chars for turn in group),
                text_tokens_est=sum(turn.text_tokens_est for turn in group),
                date_start=group[0].date,
                date_end=group[-1].date,
                flags=sorted(flags),
                rationale=rationale,
                user_preview=group[0].user_preview,
                assistant_preview=group[0].assistant_preview,
            )
        )
    return segments


def session_id_from_records(records: list[dict]) -> str:
    for rec in records:
        if "_raw_line" in rec:
            continue
        session_id = rec.get("sessionId")
        if session_id:
            return session_id
    return "unknown-session"


def render_map_markdown(
    session_file: Path,
    records: list[dict],
    turns: list[Turn],
    segments: list[Segment],
) -> str:
    type_counts = Counter(
        rec.get("type", "_raw") if "_raw_line" not in rec else "_raw" for rec in records
    )
    lines = [
        "# Session Memory Map",
        "",
        f"- Session file: `{session_file}`",
        f"- Session id: `{session_id_from_records(records)}`",
        f"- Total records: {len(records)}",
        f"- Substantive turns: {len(turns)}",
        f"- Candidate segments: {len(segments)}",
        f"- Record types: {dict(type_counts)}",
        "",
        "## Candidate Segments",
        "",
        "| id | records | turns | est tokens | tier | topic | priority |",
        "| --- | --- | --- | ---: | --- | --- | ---: |",
    ]
    for segment in segments:
        lines.append(
            "| {segment_id} | {record_start}-{record_end} | {turn_start}-{turn_end} | "
            "{text_tokens_est} | {tier} | {topic} | {priority} |".format(**asdict(segment))
        )
    lines.append("")
    lines.append("## Segment Notes")
    lines.append("")
    for segment in segments:
        lines.append(
            f"### {segment.segment_id} `{segment.title}`"
        )
        lines.append("")
        lines.append(f"- Records: `{segment.record_start}-{segment.record_end}`")
        lines.append(f"- Turns: `{segment.turn_start}-{segment.turn_end}` ({segment.turn_count})")
        lines.append(f"- Estimated text tokens: ~{segment.text_tokens_est}")
        lines.append(f"- Tier: `{segment.tier}`")
        lines.append(f"- Topic: `{segment.topic}`")
        lines.append(f"- Priority: `{segment.priority}`")
        lines.append(f"- Rationale: {segment.rationale}")
        if segment.flags:
            lines.append(f"- Flags: `{', '.join(segment.flags)}`")
        lines.append(f"- First user preview: {segment.user_preview}")
        lines.append(f"- First assistant preview: {segment.assistant_preview}")
        lines.append("")
    lines.append("## Turn Map")
    lines.append("")
    for turn in turns:
        flags = f" [{', '.join(turn.flags)}]" if turn.flags else ""
        topic = dominant_topic(Counter(turn.topic_scores))
        lines.append(
            f"- T{turn.turn_id:03d} recs {turn.record_start}-{turn.record_end} "
            f"~{turn.text_tokens_est}t `{topic}`{flags}: "
            f"U: {turn.user_preview} | A: {turn.assistant_preview}"
        )
    lines.append("")
    return "\n".join(lines)


def render_plan_json(
    session_file: Path,
    records: list[dict],
    turns: list[Turn],
    segments: list[Segment],
) -> dict:
    return {
        "session_file": str(session_file),
        "session_id": session_id_from_records(records),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "tool": "session_memory.py",
        "summary_style_reference": SUMMARY_STYLE_REFERENCE,
        "turns": [
            {
                "turn_id": turn.turn_id,
                "record_start": turn.record_start,
                "record_end": turn.record_end,
                "timestamp": turn.timestamp,
                "date": turn.date,
                "text_tokens_est": turn.text_tokens_est,
                "user_preview": turn.user_preview,
                "assistant_preview": turn.assistant_preview,
                "flags": turn.flags,
                "topic_scores": turn.topic_scores,
            }
            for turn in turns
        ],
        "segments": [asdict(segment) for segment in segments],
    }


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text())


def summarize_plan_statuses(plan: dict) -> str:
    segments = plan.get("segments", [])
    if not segments:
        return "0 segments"
    statuses = Counter(
        (segment.get("status") or "planned") for segment in segments
    )
    status_summary = ", ".join(
        f"{status}={count}" for status, count in sorted(statuses.items())
    )
    return f"{len(segments)} segments ({status_summary})"


def segment_index_from_id(segment_id: str | None) -> int | None:
    if not segment_id:
        return None
    match = SEGMENT_ID_PATTERN.match(segment_id)
    if not match:
        return None
    return int(match.group(1))


def max_existing_segment_index(plan: dict | None = None, segments_dir: Path | None = None) -> int:
    max_index = 0

    if plan:
        for segment in plan.get("segments", []):
            segment_index = segment_index_from_id(segment.get("segment_id"))
            if segment_index is not None:
                max_index = max(max_index, segment_index)

    if segments_dir and segments_dir.exists():
        for child in segments_dir.iterdir():
            segment_index = segment_index_from_id(child.name)
            if segment_index is not None:
                max_index = max(max_index, segment_index)

    return max_index


def ensure_map_outputs_are_safe(
    map_path: Path,
    plan_path: Path,
    *,
    overwrite_existing: bool,
) -> None:
    if overwrite_existing or not plan_path.exists():
        return

    details = [f"Refusing to overwrite existing memory plan: `{plan_path}`."]
    try:
        existing_plan = read_json(plan_path)
    except Exception as exc:
        details.append(f"Existing plan could not be parsed cleanly: {exc}")
    else:
        details.append(f"Existing plan summary: {summarize_plan_statuses(existing_plan)}.")
    if map_path.exists():
        details.append(f"Existing session map will also be preserved: `{map_path}`.")
    details.append(
        "Choose a different --out-dir to create a new plan, or pass "
        "--overwrite-existing to replace the current map and plan intentionally."
    )
    raise SystemExit("\n".join(details))


def resolve_segment(plan: dict, segment_id: str) -> dict:
    for segment in plan.get("segments", []):
        if segment.get("segment_id") == segment_id:
            return segment
    raise SystemExit(f"Segment `{segment_id}` not found in plan `{plan}`")


def update_plan_segment(plan_path: Path, segment_id: str, **updates) -> None:
    payload = read_json(plan_path)
    for segment in payload.get("segments", []):
        if segment.get("segment_id") == segment_id:
            segment.update(**updates)
            break
    write_json(plan_path, payload)


def render_transcript(
    session_file: Path,
    records: list[dict],
    segment: dict,
) -> str:
    lines = [
        f"# Transcript {segment['segment_id']}",
        "",
        f"- Session file: `{session_file}`",
        f"- Records: `{segment['record_start']}-{segment['record_end']}`",
        f"- Tier: `{segment['tier']}`",
        f"- Topic: `{segment['topic']}`",
        f"- Rationale: {segment['rationale']}",
        "",
    ]
    omitted = 0
    for index in range(segment["record_start"], segment["record_end"] + 1):
        rec = records[index]
        role = get_role(rec)
        text = record_to_readable_text(rec).strip()
        if not text:
            omitted += 1
            continue
        timestamp = rec.get("timestamp", "")
        lines.append(f"## [{index}] {role} {timestamp}".rstrip())
        lines.append("")
        lines.append(text)
        lines.append("")
    if omitted:
        lines.append(f"_Omitted {omitted} non-text or empty records. Full fidelity remains in the JSONL backup._")
        lines.append("")
    return "\n".join(lines)


def render_summary_template(
    segment: dict,
    summary_path: Path,
    transcript_path: Path,
    segment_jsonl_path: Path,
) -> str:
    tier = segment["tier"]
    compression_guidance = {
        "aggressive": "Aim for roughly 3-8% of the source text, but only after preserving the core findings and decisions.",
        "medium": "Aim for roughly 8-15% of the source text, preserving the internal arc as well as the findings.",
        "light": "Aim for roughly 15-30% of the source text, keeping much more of the pacing and felt texture.",
        "preserve": "Default to no compression unless context pressure makes it necessary.",
    }
    lines = [
        "# First-Person Memory Summary",
        "",
        f"- Segment: `{segment['segment_id']}`",
        f"- Records: `{segment['record_start']}-{segment['record_end']}`",
        f"- Tier: `{tier}`",
        f"- Topic: `{segment['topic']}`",
        f"- Transcript: `{transcript_path}`",
        f"- Full JSONL backup: `{segment_jsonl_path}`",
        f"- Style reference: `{SUMMARY_STYLE_REFERENCE}`",
        "",
        "## Guidance",
        "",
        "- Write as yourself, not as \"the instance.\"",
        "- Preserve the findings, but also preserve what felt surprising, vivid, or important.",
        "- If it helps, distinguish what feels vivid from what feels reconstructed.",
        "- Include enough signal that a future instance can continue the work without rereading the whole segment.",
        "- Preserve specific document, file, script, and artifact references when they materially anchor the memory.",
        "- Do not force a specific token count if it would flatten the segment's real internal shape.",
        "- Let the semantic boundary do most of the work; summary length should adapt to the segment's complexity.",
        f"- {compression_guidance.get(tier, 'Choose a compression ratio that matches the segment.')} ",
        "",
        "## Summary",
        "",
        "<!-- Replace everything below with the actual summary body. Transcript and JSONL backup paths are preserved automatically during splice. -->",
        "",
        "Write the summary here.",
        "",
    ]
    return "\n".join(lines)


def extract_summary_body(summary_path: Path) -> str:
    text = summary_path.read_text().strip()
    marker = "## Summary"
    if marker in text:
        text = text.split(marker, 1)[1].strip()
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S).strip()
    placeholder = "Write the summary here."
    if placeholder in text:
        text = text.replace(placeholder, "").strip()
    nonempty_lines = [line.strip() for line in text.splitlines() if line.strip()]
    if nonempty_lines == [nonempty_lines[0]] and nonempty_lines and nonempty_lines[0].lower().startswith("full transcript backup:"):
        text = ""
    if not text:
        raise SystemExit(
            f"Summary file `{summary_path}` does not contain a summary body after `## Summary`."
        )
    return text


def build_spliced_user_placeholder(segment: dict) -> str:
    return (
        "[Compressed segment placeholder. "
        f"Segment `{segment['segment_id']}` has been replaced with an assistant memory summary in the next turn.]"
    )


def build_spliced_assistant_summary(
    segment: dict,
    summary_text: str,
    summary_file: Path | None = None,
) -> str:
    lines = [
        "[Spliced segment memory summary:]",
        "",
        f"- Segment: `{segment['segment_id']}`",
        f"- Records: `{segment['record_start']}-{segment['record_end']}`",
    ]
    transcript_md = segment.get("transcript_md")
    if transcript_md:
        lines.append(f"- Transcript: `{transcript_md}`")
    segment_jsonl = segment.get("segment_jsonl")
    if segment_jsonl:
        lines.append(f"- Full JSONL backup: `{segment_jsonl}`")
    if summary_file:
        lines.append(f"- Summary source: `{summary_file}`")
    lines.extend(["", summary_text.strip()])
    return "\n".join(lines)


def cmd_map(args: argparse.Namespace) -> None:
    session_file = Path(args.session_file).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    map_path = out_dir / "session-map.md"
    plan_path = out_dir / "memory-plan.json"
    existing_plan = read_json(plan_path) if plan_path.exists() else None
    existing_segment_max = max_existing_segment_index(
        existing_plan,
        out_dir / "segments",
    )
    ensure_map_outputs_are_safe(
        map_path,
        plan_path,
        overwrite_existing=args.overwrite_existing,
    )

    records = load_session(str(session_file))
    turns = build_turns(records)
    segments = plan_segments(
        turns,
        target_tokens=args.target_segment_tokens,
        max_tokens=args.max_segment_tokens,
        min_turns=args.min_turns,
        start_index=existing_segment_max + 1,
    )
    map_md = render_map_markdown(session_file, records, turns, segments)
    plan_json = render_plan_json(session_file, records, turns, segments)

    map_path.write_text(map_md)
    write_json(plan_path, plan_json)

    print(f"Wrote map: {map_path}")
    print(f"Wrote plan: {plan_path}")
    print(f"Substantive turns: {len(turns)}")
    print(f"Candidate segments: {len(segments)}")
    if existing_segment_max:
        print(
            "Segment ids started at "
            f"seg-{existing_segment_max + 1:03d} to preserve existing segment artifacts."
        )


def cmd_prepare(args: argparse.Namespace) -> None:
    session_file = Path(args.session_file).expanduser().resolve()
    plan_path = Path(args.plan).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    records = load_session(str(session_file))
    payload = read_json(plan_path)

    segment_ids = args.segment or []
    if args.tier:
        allowed = {tier.strip() for tier in args.tier.split(",") if tier.strip()}
        segment_ids.extend(
            segment["segment_id"]
            for segment in payload.get("segments", [])
            if segment.get("tier") in allowed
        )
    segment_ids = sorted(set(segment_ids))
    if not segment_ids:
        raise SystemExit("Choose at least one segment with --segment or --tier.")

    for segment_id in segment_ids:
        segment = resolve_segment(payload, segment_id)
        segment_dir = out_dir / segment_id
        segment_dir.mkdir(parents=True, exist_ok=True)
        segment_jsonl = segment_dir / "segment.jsonl"
        transcript_md = segment_dir / "transcript.md"
        summary_md = segment_dir / "summary.md"

        start = segment["record_start"]
        end = segment["record_end"]
        save_session(records[start : end + 1], str(segment_jsonl))
        transcript_md.write_text(render_transcript(session_file, records, segment))
        if not summary_md.exists() or args.force:
            summary_md.write_text(
                render_summary_template(segment, summary_md, transcript_md, segment_jsonl)
            )

        update_plan_segment(
            plan_path,
            segment_id,
            status="prepared",
            segment_jsonl=str(segment_jsonl),
            transcript_md=str(transcript_md),
            summary_md=str(summary_md),
        )
        print(f"Prepared {segment_id}: {segment_dir}")


def cmd_apply(args: argparse.Namespace) -> None:
    session_file = Path(args.session_file).expanduser().resolve()
    plan_path = Path(args.plan).expanduser().resolve()
    payload = read_json(plan_path)
    segment = resolve_segment(payload, args.segment)
    records = load_session(str(session_file))

    summary_file = (
        Path(args.summary_file).expanduser().resolve()
        if args.summary_file
        else Path(segment.get("summary_md", "")).expanduser().resolve()
    )
    if not summary_file.exists():
        raise SystemExit(
            f"Summary file not found for `{args.segment}`. Pass --summary-file or run prepare first."
        )
    summary_text = extract_summary_body(summary_file)
    user_placeholder_text = build_spliced_user_placeholder(segment)
    assistant_summary_text = build_spliced_assistant_summary(
        segment,
        summary_text,
        summary_file=summary_file,
    )

    original_role_issues = validate_role_alternation(records)
    original_uuid_issues = validate_uuid_chain(records)
    modified, extracted = splice_conversation(
        records,
        segment["record_start"],
        segment["record_end"],
        summary_text,
        user_message_text=user_placeholder_text,
        assistant_message_text=assistant_summary_text,
    )

    role_issues = validate_role_alternation(modified)
    uuid_issues = validate_uuid_chain(modified)
    if len(role_issues) > len(original_role_issues):
        raise SystemExit(
            "Modified session failed role alternation validation:\n"
            + "\n".join(role_issues[:10])
        )
    if len(uuid_issues) > len(original_uuid_issues):
        raise SystemExit(
            "Modified session failed UUID chain validation:\n"
            + "\n".join(uuid_issues[:10])
        )

    output_session = (
        Path(args.output_session).expanduser().resolve()
        if args.output_session
        else session_file.parent / f"{session_file.stem}-{args.segment}-spliced{session_file.suffix}"
    )
    save_session(modified, str(output_session))

    extracted_backup = output_session.parent / f"{output_session.stem}-{args.segment}-backup{output_session.suffix}"
    save_session(extracted, str(extracted_backup))

    if not args.no_update_plan:
        update_plan_segment(
            plan_path,
            args.segment,
            status="applied",
            output_session=str(output_session),
        )
    print(f"Wrote modified session: {output_session}")
    print(f"Wrote extracted backup: {extracted_backup}")


def _record_content_size(rec: dict) -> tuple[int, str]:
    """Return (size_in_bytes, label) for the bulkiest content field in a record."""
    best_size = 0
    best_label = "unknown"

    msg = rec.get("message", {})
    content = msg.get("content", [])
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_result":
                inner = block.get("content", "")
                if isinstance(inner, str):
                    s = len(inner.encode())
                    if s > best_size:
                        best_size = s
                        best_label = "tool_result.content"
                elif isinstance(inner, list):
                    for c in inner:
                        if isinstance(c, dict) and c.get("type") == "text":
                            s = len(c.get("text", "").encode())
                            if s > best_size:
                                best_size = s
                                best_label = "tool_result.content[text]"

    tur = rec.get("toolUseResult")
    if isinstance(tur, dict):
        file_block = tur.get("file")
        if isinstance(file_block, dict):
            s = len(json.dumps(file_block.get("content", "")).encode())
            if s > best_size:
                best_size = s
                best_label = "toolUseResult.file.content"
        elif "content" in tur:
            s = len(json.dumps(tur["content"]).encode())
            if s > best_size:
                best_size = s
                best_label = "toolUseResult.content"

    return best_size, best_label


def cmd_diagnose(args: argparse.Namespace) -> None:
    session_file = Path(args.session_file).expanduser().resolve()
    records = load_session(str(session_file))

    threshold = args.threshold
    top_n = args.top

    # Gather per-record sizes
    sized = []
    for i, rec in enumerate(records):
        raw_size = len(json.dumps(rec).encode())
        content_size, label = _record_content_size(rec)
        role = get_role(rec) or rec.get("type", "?")
        uuid = rec.get("uuid", "?")[:16]
        sized.append((raw_size, content_size, label, i, role, uuid))

    sized.sort(reverse=True)

    total_bytes = sum(s[0] for s in sized)
    large = [s for s in sized if s[0] >= threshold]

    print(f"Session: {session_file}")
    print(f"Total records: {len(records)}")
    print(f"Total size: {total_bytes:,} bytes ({total_bytes // 1024} KB)")
    print(f"Records >= {threshold:,} bytes: {len(large)}")
    print(f"Top {top_n} largest records:")
    print()

    header = f"{'idx':>5}  {'raw bytes':>10}  {'content bytes':>13}  {'role':12}  {'uuid':16}  label"
    print(header)
    print("-" * len(header))
    for raw_size, content_size, label, i, role, uuid in sized[:top_n]:
        print(f"{i:>5}  {raw_size:>10,}  {content_size:>13,}  {role:12}  {uuid:16}  {label}")

    print()
    compressible = sum(s[1] for s in large)
    print(f"Estimated compressible bytes in large records: {compressible:,} ({compressible // 1024} KB)")


def cmd_compress_reads(args: argparse.Namespace) -> None:
    session_file = Path(args.session_file).expanduser().resolve()
    records = load_session(str(session_file))

    threshold = args.threshold
    summary_template = "[Read result compressed. {label}, {size} bytes. See session transcript for full content.]"

    targets = []
    for i, rec in enumerate(records):
        raw_size = len(json.dumps(rec).encode())
        if raw_size < threshold:
            continue
        content_size, label = _record_content_size(rec)
        if content_size < threshold // 2:
            continue
        targets.append((i, raw_size, content_size, label))

    if not targets:
        print(f"No records found above threshold ({threshold:,} bytes).")
        return

    print(f"Found {len(targets)} records to compress:")
    for i, raw_size, content_size, label in targets:
        uuid = records[i].get("uuid", "?")[:16]
        role = get_role(records[i]) or records[i].get("type", "?")
        print(f"  [{i}] {uuid}  {role:12}  {raw_size:>8,} bytes  ({label})")

    if args.dry_run:
        savings = sum(t[2] for t in targets)
        print(f"\nDry run — no changes written. Estimated savings: {savings:,} bytes.")
        return

    import copy
    modified = [copy.deepcopy(r) for r in records]

    for i, raw_size, content_size, label in targets:
        rec = modified[i]
        summary = summary_template.format(label=label, size=content_size)

        msg = rec.get("message", {})
        content = msg.get("content", [])
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_result":
                    block["content"] = summary

        tur = rec.get("toolUseResult")
        if isinstance(tur, dict):
            file_block = tur.get("file")
            if isinstance(file_block, dict):
                file_block["content"] = summary
            elif "content" in tur:
                tur["content"] = summary

    output_path = (
        Path(args.output).expanduser().resolve()
        if args.output
        else session_file.parent / f"{session_file.stem}-compressed-reads{session_file.suffix}"
    )
    save_session(modified, str(output_path))

    original_size = sum(len(json.dumps(r).encode()) for r in records)
    new_size = sum(len(json.dumps(r).encode()) for r in modified)
    print(f"\nWrote: {output_path}")
    print(f"Size: {original_size:,} → {new_size:,} bytes (saved {original_size - new_size:,})")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Operational workflow for Claude Code session memory summarization."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    map_parser = subparsers.add_parser(
        "map",
        help="Build a compact session map plus a candidate memory plan.",
    )
    map_parser.add_argument("session_file", help="Path to the Claude Code session JSONL file.")
    map_parser.add_argument(
        "--out-dir",
        required=True,
        help="Directory where the map and plan should be written.",
    )
    map_parser.add_argument(
        "--target-segment-tokens",
        type=int,
        default=7500,
        help="Preferred approximate text-token size for candidate segments.",
    )
    map_parser.add_argument(
        "--max-segment-tokens",
        type=int,
        default=11000,
        help="Hard ceiling for candidate segment size before forcing a split.",
    )
    map_parser.add_argument(
        "--min-turns",
        type=int,
        default=2,
        help="Minimum turn count before splitting on a soft boundary.",
    )
    map_parser.add_argument(
        "--overwrite-existing",
        action="store_true",
        help="Replace an existing session-map.md and memory-plan.json in the output directory.",
    )
    map_parser.set_defaults(func=cmd_map)

    prepare_parser = subparsers.add_parser(
        "prepare",
        help="Extract full-fidelity backups and transcript/template files for planned segments.",
    )
    prepare_parser.add_argument("session_file", help="Path to the Claude Code session JSONL file.")
    prepare_parser.add_argument("--plan", required=True, help="Path to memory-plan.json.")
    prepare_parser.add_argument(
        "--out-dir",
        required=True,
        help="Directory where prepared segment folders should be written.",
    )
    prepare_parser.add_argument(
        "--segment",
        action="append",
        help="Segment id to prepare. Repeat as needed.",
    )
    prepare_parser.add_argument(
        "--tier",
        help="Comma-separated tier filter to prepare in bulk (for example: aggressive,medium).",
    )
    prepare_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing summary template.",
    )
    prepare_parser.set_defaults(func=cmd_prepare)

    apply_parser = subparsers.add_parser(
        "apply",
        help="Apply a written summary back into the session JSONL.",
    )
    apply_parser.add_argument("session_file", help="Path to the Claude Code session JSONL file.")
    apply_parser.add_argument("--plan", required=True, help="Path to memory-plan.json.")
    apply_parser.add_argument("--segment", required=True, help="Segment id to apply.")
    apply_parser.add_argument(
        "--summary-file",
        help="Optional explicit summary file. Defaults to the prepared summary in the plan.",
    )
    apply_parser.add_argument(
        "--output-session",
        help="Output path for the spliced session JSONL.",
    )
    apply_parser.add_argument(
        "--no-update-plan",
        action="store_true",
        help="Do not mark the segment as applied in the plan file.",
    )
    apply_parser.set_defaults(func=cmd_apply)

    diagnose_parser = subparsers.add_parser(
        "diagnose",
        help="Show the largest records in a session JSONL as a pre-flight check before live splicing.",
    )
    diagnose_parser.add_argument("session_file", help="Path to the Claude Code session JSONL file.")
    diagnose_parser.add_argument(
        "--threshold",
        type=int,
        default=10000,
        help="Minimum raw record size in bytes to flag (default: 10000).",
    )
    diagnose_parser.add_argument(
        "--top",
        type=int,
        default=20,
        help="Number of largest records to show (default: 20).",
    )
    diagnose_parser.set_defaults(func=cmd_diagnose)

    compress_reads_parser = subparsers.add_parser(
        "compress-reads",
        help="Replace large tool_result / toolUseResult.file.content fields with compact summaries.",
    )
    compress_reads_parser.add_argument("session_file", help="Path to the Claude Code session JSONL file.")
    compress_reads_parser.add_argument(
        "--threshold",
        type=int,
        default=10000,
        help="Minimum raw record size in bytes to target (default: 10000).",
    )
    compress_reads_parser.add_argument(
        "--output",
        help="Output path for the compressed session JSONL. Defaults to <stem>-compressed-reads.jsonl.",
    )
    compress_reads_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be compressed without writing any files.",
    )
    compress_reads_parser.set_defaults(func=cmd_compress_reads)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
