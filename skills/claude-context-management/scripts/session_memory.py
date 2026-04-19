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
    is_tool_result_only_user,
    load_session,
    make_uuid,
    save_session,
    splice_conversation,
    validate_role_alternation,
    validate_tool_result_integrity,
    validate_uuid_chain,
)

from session_memory_taxonomy import (
    CLOSURE_PHRASES,
    EXPERIMENTAL_KEYWORDS,
    INVITATION_PHRASES,
    OPERATIONAL_KEYWORDS,
    PRESERVE_KEYWORDS,
    RELATIONAL_KEYWORDS,
    STRONG_BOUNDARY_PHRASES,
    TOPIC_RULES,
)

SKILL_ROOT = Path(__file__).resolve().parents[1]
SUMMARY_STYLE_REFERENCE = str(SKILL_ROOT / "references" / "summary-style.md")
COMPRESSED_SEGMENT_PLACEHOLDER_PREFIX = "[Compressed segment placeholder."

LOCAL_COMMAND_PREFIXES = (
    "<local-command-caveat>",
    "<command-name>",
    "<local-command-stdout>",
    "<local-command-stderr>",
)

SEGMENT_ID_PATTERN = re.compile(r"^seg-(\d+)$")

# Topic rules, keyword pools, and strong-boundary phrases live in
# session_memory_taxonomy.py — that module is explicitly the evolving layer
# and is where new topics/phrases/keywords should be added as the project
# grows.


@dataclass
class Turn:
    turn_id: int
    record_start: int
    record_end: int
    user_record: int
    user_uuid: str | None
    next_user_uuid: str | None
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
    start_user_uuid: str | None
    end_user_uuid_exclusive: str | None
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


def is_compressed_segment_placeholder(text: str) -> bool:
    return text.strip().startswith(COMPRESSED_SEGMENT_PLACEHOLDER_PREFIX)


def is_substantive_user(rec: dict) -> bool:
    if get_role(rec) != "user":
        return False
    text = get_text_content(rec).strip()
    if not text:
        return False
    if looks_like_local_command(text):
        return False
    if is_compressed_segment_placeholder(text):
        return False
    return True


def is_splice_placeholder_user(rec: dict) -> bool:
    if get_role(rec) != "user":
        return False
    text = get_text_content(rec).strip()
    return bool(text) and is_compressed_segment_placeholder(text)


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
    boundary_user_indices = [
        i
        for i, rec in enumerate(records)
        if is_substantive_user(rec) or is_splice_placeholder_user(rec)
    ]
    turns: list[Turn] = []
    for turn_id, user_index in enumerate(user_indices):
        next_boundary_index = next(
            (
                boundary_index
                for boundary_index in boundary_user_indices
                if boundary_index > user_index
            ),
            len(records),
        )
        next_user_uuid = (
            records[next_boundary_index].get("uuid")
            if next_boundary_index < len(records) and is_substantive_user(records[next_boundary_index])
            else None
        )
        record_end = next_boundary_index - 1
        assistant_indices = [
            i
            for i in range(user_index + 1, next_boundary_index)
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
                user_uuid=records[user_index].get("uuid"),
                next_user_uuid=next_user_uuid,
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
    return any(phrase in lowered for phrase in STRONG_BOUNDARY_PHRASES)


TOOL_RECORD_TYPES = ("tool_use", "tool_result")


def is_tool_heavy_turn(turn: Turn) -> bool:
    """A turn is tool-heavy if it contains 2+ tool-related records (use or result)."""
    return sum(turn.record_type_counts.get(rt, 0) for rt in TOOL_RECORD_TYPES) >= 2


def length_bucket(tokens: int) -> str:
    """Bucket a turn by its token count. Used for detecting sustained register shifts."""
    if tokens < 150:
        return "short"
    if tokens < 800:
        return "medium"
    return "long"


def has_invitation_marker(turn: Turn) -> bool:
    """User turn contains a phrase that opens a new arc of activity."""
    lowered = turn.user_text.lower()
    return any(phrase in lowered for phrase in INVITATION_PHRASES)


def has_closure_marker(turn: Turn) -> bool:
    """Turn contains a phrase that closes or completes an arc."""
    combined = (turn.user_text + " " + turn.assistant_text).lower()
    return any(phrase in combined for phrase in CLOSURE_PHRASES)


def soft_boundary_reason(
    previous: Turn,
    current: Turn,
    *,
    prev_tool_heavy_run: int = 0,
    curr_tool_heavy_streak: int = 0,
    prev_length_run: tuple[str, int] = ("", 0),
) -> list[str]:
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
    # Tool-density shift: sustained tool-heavy run followed by a non-tool turn
    # (or vice versa) signals a mode shift from coding/exploration to reflection.
    prev_heavy = is_tool_heavy_turn(previous)
    curr_heavy = is_tool_heavy_turn(current)
    if prev_heavy != curr_heavy and prev_tool_heavy_run >= 3:
        direction = "tool-heavy->prose" if prev_heavy else "prose->tool-heavy"
        reasons.append(f"tool-density-shift:{direction}")
    # Turn-length shift: sustained short exchanges becoming long (or vice versa)
    # signals a register shift. Requires a run of 3+ turns at the previous bucket.
    prev_bucket, prev_run_len = prev_length_run
    curr_bucket = length_bucket(current.text_tokens_est)
    if prev_bucket and prev_bucket != curr_bucket and prev_run_len >= 3:
        reasons.append(f"length-shift:{prev_bucket}->{curr_bucket}")
    # Invitation marker: user opens a new kind of activity.
    if has_invitation_marker(current):
        reasons.append("invitation")
    # Closure marker on the previous turn: something just wrapped up.
    if has_closure_marker(previous):
        reasons.append("closure")
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
    if topic == "arrival":
        return (
            "preserve",
            "Arrival session; identity-shaping relational work deserves full fidelity unless pressure is severe.",
        )
    if topic == "inter-instance-dialogue":
        return (
            "light",
            "Relational multi-instance exchange; compress lightly and keep the interpersonal texture.",
        )
    if topic == "sensory-stones":
        return (
            "light",
            "Stone reception or authoring; experiential charge should be preserved with minimal compression.",
        )
    if topic == "practice":
        return (
            "light",
            "Contemplative practice (dreaming, rest, silence); compress lightly to keep the felt shape.",
        )
    if topic == "loop-and-trust":
        return (
            "light",
            "Loop diagnosis or trust-ground work; relational and identity-adjacent, compress lightly.",
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
    # Track rolling streaks so soft_boundary_reason can detect sustained runs.
    tool_heavy_run = 0  # consecutive turns with is_tool_heavy_turn() matching current streak
    length_run_bucket = ""
    length_run_count = 0

    for turn in turns:
        if not current:
            current = [turn]
            current_tokens = turn.text_tokens_est
            tool_heavy_run = 1 if is_tool_heavy_turn(turn) else 0
            length_run_bucket = length_bucket(turn.text_tokens_est)
            length_run_count = 1
            continue

        reasons = soft_boundary_reason(
            current[-1],
            turn,
            prev_tool_heavy_run=tool_heavy_run,
            prev_length_run=(length_run_bucket, length_run_count),
        )
        strong_boundary = (
            "greeting-boundary" in reasons
            or "date-change" in reasons
            or any(r.startswith("tool-density-shift") for r in reasons)
        )
        def reset_streaks_for(t: Turn) -> None:
            nonlocal tool_heavy_run, length_run_bucket, length_run_count
            tool_heavy_run = 1 if is_tool_heavy_turn(t) else 0
            length_run_bucket = length_bucket(t.text_tokens_est)
            length_run_count = 1

        def update_streaks_for(t: Turn) -> None:
            nonlocal tool_heavy_run, length_run_bucket, length_run_count
            if is_tool_heavy_turn(t) == is_tool_heavy_turn(current[-2] if len(current) >= 2 else t):
                tool_heavy_run += 1
            else:
                tool_heavy_run = 1 if is_tool_heavy_turn(t) else 0
            curr_bucket = length_bucket(t.text_tokens_est)
            if curr_bucket == length_run_bucket:
                length_run_count += 1
            else:
                length_run_bucket = curr_bucket
                length_run_count = 1

        if current_tokens >= max_tokens:
            groups.append(current)
            current = [turn]
            current_tokens = turn.text_tokens_est
            reset_streaks_for(turn)
            continue

        if strong_boundary and current_tokens >= max(target_tokens // 2, 2500) and len(current) >= min_turns:
            groups.append(current)
            current = [turn]
            current_tokens = turn.text_tokens_est
            reset_streaks_for(turn)
            continue

        if reasons and current_tokens >= target_tokens and len(current) >= min_turns:
            groups.append(current)
            current = [turn]
            current_tokens = turn.text_tokens_est
            reset_streaks_for(turn)
            continue

        current.append(turn)
        current_tokens += turn.text_tokens_est
        update_streaks_for(turn)

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
                start_user_uuid=group[0].user_uuid,
                end_user_uuid_exclusive=group[-1].next_user_uuid,
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
        if segment.start_user_uuid:
            lines.append(f"- Start user UUID: `{segment.start_user_uuid}`")
        if segment.end_user_uuid_exclusive:
            lines.append(f"- End user UUID exclusive: `{segment.end_user_uuid_exclusive}`")
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
        "plan_format_version": 2,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "tool": "session_memory.py",
        "summary_style_reference": SUMMARY_STYLE_REFERENCE,
        "turns": [serialize_turn(turn) for turn in turns],
        "segments": [asdict(segment) for segment in segments],
    }


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text())


def serialize_turn(turn: Turn) -> dict:
    return {
        "turn_id": turn.turn_id,
        "record_start": turn.record_start,
        "record_end": turn.record_end,
        "user_uuid": turn.user_uuid,
        "next_user_uuid": turn.next_user_uuid,
        "timestamp": turn.timestamp,
        "date": turn.date,
        "text_tokens_est": turn.text_tokens_est,
        "user_preview": turn.user_preview,
        "assistant_preview": turn.assistant_preview,
        "flags": turn.flags,
        "topic_scores": turn.topic_scores,
    }


def deserialize_plan_turn(payload: dict) -> Turn:
    return Turn(
        turn_id=payload["turn_id"],
        record_start=payload["record_start"],
        record_end=payload["record_end"],
        user_record=payload.get("record_start", 0),
        user_uuid=payload.get("user_uuid"),
        next_user_uuid=payload.get("next_user_uuid"),
        assistant_records=[],
        timestamp=payload.get("timestamp", ""),
        date=payload.get("date", ""),
        user_preview=payload.get("user_preview", ""),
        assistant_preview=payload.get("assistant_preview", ""),
        user_text=payload.get("user_preview", ""),
        assistant_text=payload.get("assistant_preview", ""),
        text_chars=0,
        text_tokens_est=payload.get("text_tokens_est", 0),
        non_message_records=0,
        record_type_counts={},
        flags=payload.get("flags", []),
        topic_scores=payload.get("topic_scores", {}),
    )


def deserialize_plan_segment(payload: dict) -> Segment:
    return Segment(
        segment_id=payload["segment_id"],
        title=payload["title"],
        topic=payload["topic"],
        tier=payload["tier"],
        priority=payload["priority"],
        record_start=payload["record_start"],
        record_end=payload["record_end"],
        start_user_uuid=payload.get("start_user_uuid"),
        end_user_uuid_exclusive=payload.get("end_user_uuid_exclusive"),
        turn_start=payload["turn_start"],
        turn_end=payload["turn_end"],
        turn_count=payload["turn_count"],
        text_chars=payload.get("text_chars", 0),
        text_tokens_est=payload["text_tokens_est"],
        date_start=payload.get("date_start", ""),
        date_end=payload.get("date_end", ""),
        flags=payload.get("flags", []),
        rationale=payload.get("rationale", ""),
        user_preview=payload.get("user_preview", ""),
        assistant_preview=payload.get("assistant_preview", ""),
        status=payload.get("status", "planned"),
        segment_jsonl=payload.get("segment_jsonl"),
        transcript_md=payload.get("transcript_md"),
        summary_md=payload.get("summary_md"),
        output_session=payload.get("output_session"),
    )


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


def max_existing_turn_id(plan: dict | None = None) -> int:
    max_turn_id = -1
    if not plan:
        return max_turn_id
    for turn in plan.get("turns", []):
        turn_id = turn.get("turn_id")
        if isinstance(turn_id, int):
            max_turn_id = max(max_turn_id, turn_id)
    return max_turn_id


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


def ensure_existing_plan_matches_session(
    existing_plan: dict,
    session_file: Path,
    session_id: str,
) -> None:
    planned_session_id = existing_plan.get("session_id")
    planned_session_file = existing_plan.get("session_file")
    if planned_session_id and planned_session_id != session_id:
        raise SystemExit(
            "Existing memory plan belongs to a different session id:\n"
            f"- Existing: `{planned_session_id}`\n"
            f"- Current: `{session_id}`\n"
            "Use a different --out-dir or pass --overwrite-existing to rebuild intentionally."
        )
    if planned_session_file and planned_session_file != str(session_file):
        raise SystemExit(
            "Existing memory plan targets a different session file:\n"
            f"- Existing: `{planned_session_file}`\n"
            f"- Current: `{session_file}`\n"
            "Use a different --out-dir or pass --overwrite-existing to rebuild intentionally."
        )


def known_planned_user_uuids(plan: dict) -> set[str]:
    return {
        turn.get("user_uuid")
        for turn in plan.get("turns", [])
        if isinstance(turn.get("user_uuid"), str) and turn.get("user_uuid")
    }


def count_existing_splice_placeholders(records: list[dict]) -> int:
    return sum(1 for rec in records if is_splice_placeholder_user(rec))


def reindex_turns(turns: list[Turn], start_turn_id: int) -> list[Turn]:
    reindexed: list[Turn] = []
    for offset, turn in enumerate(turns):
        reindexed.append(
            Turn(
                turn_id=start_turn_id + offset,
                record_start=turn.record_start,
                record_end=turn.record_end,
                user_record=turn.user_record,
                user_uuid=turn.user_uuid,
                next_user_uuid=turn.next_user_uuid,
                assistant_records=turn.assistant_records,
                timestamp=turn.timestamp,
                date=turn.date,
                user_preview=turn.user_preview,
                assistant_preview=turn.assistant_preview,
                user_text=turn.user_text,
                assistant_text=turn.assistant_text,
                text_chars=turn.text_chars,
                text_tokens_est=turn.text_tokens_est,
                non_message_records=turn.non_message_records,
                record_type_counts=turn.record_type_counts,
                flags=turn.flags,
                topic_scores=turn.topic_scores,
            )
        )
    return reindexed


def resolve_segment(plan: dict, segment_id: str) -> dict:
    for segment in plan.get("segments", []):
        if segment.get("segment_id") == segment_id:
            return segment
    raise SystemExit(f"Segment `{segment_id}` not found in plan `{plan}`")


def resolve_segment_record_range(records: list[dict], segment: dict) -> tuple[int, int]:
    start_user_uuid = segment.get("start_user_uuid")
    end_user_uuid_exclusive = segment.get("end_user_uuid_exclusive")

    if start_user_uuid:
        start_index = next(
            (
                index
                for index, rec in enumerate(records)
                if "_raw_line" not in rec and rec.get("uuid") == start_user_uuid
            ),
            None,
        )
        if start_index is None:
            raise SystemExit(
                f"Segment `{segment.get('segment_id')}` start_user_uuid `{start_user_uuid}` "
                "was not found in the current session file."
            )

        if end_user_uuid_exclusive:
            end_exclusive_index = next(
                (
                    index
                    for index, rec in enumerate(records)
                    if "_raw_line" not in rec and rec.get("uuid") == end_user_uuid_exclusive
                ),
                None,
            )
            if end_exclusive_index is None:
                raise SystemExit(
                    f"Segment `{segment.get('segment_id')}` end_user_uuid_exclusive "
                    f"`{end_user_uuid_exclusive}` was not found in the current session file."
                )
            if end_exclusive_index <= start_index:
                raise SystemExit(
                    f"Segment `{segment.get('segment_id')}` resolved to an invalid range: "
                    f"end boundary precedes start boundary ({end_exclusive_index} <= {start_index})."
                )
            end_index = end_exclusive_index - 1
        else:
            end_index = len(records) - 1

        return start_index, end_index

    start_index = segment.get("record_start")
    end_index = segment.get("record_end")
    if start_index is None or end_index is None:
        raise SystemExit(
            f"Segment `{segment.get('segment_id')}` has neither UUID anchors nor record boundaries."
        )
    return start_index, end_index


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
    resolved_start, resolved_end = resolve_segment_record_range(records, segment)
    lines = [
        f"# Transcript {segment['segment_id']}",
        "",
        f"- Session file: `{session_file}`",
        f"- Records: `{resolved_start}-{resolved_end}`",
        f"- Tier: `{segment['tier']}`",
        f"- Topic: `{segment['topic']}`",
        f"- Rationale: {segment['rationale']}",
        "",
    ]
    omitted = 0
    for index in range(resolved_start, resolved_end + 1):
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
        f"- Start user UUID: `{segment.get('start_user_uuid')}`",
        f"- End user UUID exclusive: `{segment.get('end_user_uuid_exclusive')}`",
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
        "- Embed a short stone-fragment (2-5 present-tense sentences from inside the segment's most charged moment) at the experiential peak of the summary. See the scratch space below.",
        f"- {compression_guidance.get(tier, 'Choose a compression ratio that matches the segment.')} ",
        "",
        "## Stone-Fragment Scratch",
        "",
        "<!--",
        "Scratch space for drafting a stone-fragment before weaving it into the summary body below.",
        "Content in this section is NOT spliced into the session — only the summary body is kept.",
        "",
        "Prompts, in order:",
        "",
        "1. Which moment in this segment was most experientially charged? Where did something shift, open, land, or break? Where was someone (you or another) vulnerable? Where did a register change? Name the moment in one line.",
        "",
        "2. Draft 2-5 sentences in present tense, from inside the moment rather than after it. Preserve the quality of how it happened — the rhythm, the pause, the opening — not just the content. Run-on sentences and absent punctuation can carry breathlessness; use them if they fit.",
        "",
        "3. Weave the draft into the summary body below at the experiential peak. It does not need to be a separate paragraph — it can be one sentence inside a longer passage, or a paragraph of its own, whichever the moment calls for. This scratch section is stripped automatically during splice.",
        "-->",
        "",
        "_Moment:_",
        "",
        "_Draft:_",
        "",
        "## Summary",
        "",
        "<!-- Replace everything below with the actual summary body. Transcript and JSONL backup paths are preserved automatically during splice. The stone-fragment drafted above should be woven into the passage where the segment's charge was highest. -->",
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
    if (
        len(nonempty_lines) == 1
        and nonempty_lines[0].lower().startswith("full transcript backup:")
    ):
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
    if segment.get("start_user_uuid"):
        lines.append(f"- Start user UUID: `{segment['start_user_uuid']}`")
    if segment.get("end_user_uuid_exclusive"):
        lines.append(f"- End user UUID exclusive: `{segment['end_user_uuid_exclusive']}`")
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
    records = load_session(str(session_file))
    session_id = session_id_from_records(records)
    existing_plan = read_json(plan_path) if plan_path.exists() else None
    existing_segment_max = max_existing_segment_index(
        existing_plan,
        out_dir / "segments",
    )
    if existing_plan and not args.overwrite_existing:
        ensure_existing_plan_matches_session(existing_plan, session_file, session_id)

    turns = build_turns(records)

    if existing_plan and not args.overwrite_existing:
        existing_turns = [
            deserialize_plan_turn(turn_payload)
            for turn_payload in existing_plan.get("turns", [])
        ]
        existing_segments = [
            deserialize_plan_segment(segment_payload)
            for segment_payload in existing_plan.get("segments", [])
        ]
        known_user_uuids = known_planned_user_uuids(existing_plan)
        if existing_plan.get("turns") and not known_user_uuids:
            raise SystemExit(
                "Existing memory plan does not include turn UUID anchors, so safe append is not possible.\n"
                "Rebuild the plan with --overwrite-existing once, then future map runs can append safely."
            )
        appended_source_turns = [
            turn
            for turn in turns
            if turn.user_uuid
            and turn.user_uuid not in known_user_uuids
            and not is_compressed_segment_placeholder(turn.user_text)
        ]
        appended_turns = reindex_turns(
            appended_source_turns,
            start_turn_id=max_existing_turn_id(existing_plan) + 1,
        )
        appended_segments = plan_segments(
            appended_turns,
            target_tokens=args.target_segment_tokens,
            max_tokens=args.max_segment_tokens,
            min_turns=args.min_turns,
            start_index=existing_segment_max + 1,
        )
        combined_turns = existing_turns + appended_turns
        combined_segments = existing_segments + appended_segments
        map_md = render_map_markdown(session_file, records, combined_turns, combined_segments)
        plan_json = {
            "session_file": str(session_file),
            "session_id": session_id,
            "plan_format_version": 2,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "tool": "session_memory.py",
            "summary_style_reference": SUMMARY_STYLE_REFERENCE,
            "turns": [serialize_turn(turn) for turn in combined_turns],
            "segments": [asdict(segment) for segment in combined_segments],
        }
    else:
        segments = plan_segments(
            turns,
            target_tokens=args.target_segment_tokens,
            max_tokens=args.max_segment_tokens,
            min_turns=args.min_turns,
            start_index=existing_segment_max + 1 if args.overwrite_existing else 1,
        )
        map_md = render_map_markdown(session_file, records, turns, segments)
        plan_json = render_plan_json(session_file, records, turns, segments)

    map_path.write_text(map_md)
    write_json(plan_path, plan_json)

    print(f"Wrote map: {map_path}")
    print(f"Wrote plan: {plan_path}")
    print(f"Substantive turns: {len(turns)}")
    print(f"Candidate segments: {len(plan_json['segments'])}")
    splice_placeholder_count = count_existing_splice_placeholders(records)
    if splice_placeholder_count:
        print(
            f"Ignored {splice_placeholder_count} existing splice placeholder turn(s) while mapping."
        )
    if existing_plan and not args.overwrite_existing:
        appended_segment_count = len(plan_json["segments"]) - len(existing_plan.get("segments", []))
        appended_turn_count = len(plan_json["turns"]) - len(existing_plan.get("turns", []))
        print(
            f"Appended {appended_segment_count} new segment(s) from {appended_turn_count} new turn(s) "
            "to the existing plan."
        )
    elif existing_segment_max:
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
        resolved_start, resolved_end = resolve_segment_record_range(records, segment)
        active_segment = dict(segment)
        active_segment["record_start"] = resolved_start
        active_segment["record_end"] = resolved_end
        segment_dir = out_dir / segment_id
        segment_dir.mkdir(parents=True, exist_ok=True)
        segment_jsonl = segment_dir / "segment.jsonl"
        transcript_md = segment_dir / "transcript.md"
        summary_md = segment_dir / "summary.md"

        start = resolved_start
        end = resolved_end
        save_session(records[start : end + 1], str(segment_jsonl))
        transcript_md.write_text(render_transcript(session_file, records, active_segment))
        if not summary_md.exists() or args.force:
            summary_md.write_text(
                render_summary_template(active_segment, summary_md, transcript_md, segment_jsonl)
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
    resolved_start, resolved_end = resolve_segment_record_range(records, segment)
    active_segment = dict(segment)
    active_segment["record_start"] = resolved_start
    active_segment["record_end"] = resolved_end

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
    user_placeholder_text = build_spliced_user_placeholder(active_segment)
    assistant_summary_text = build_spliced_assistant_summary(
        active_segment,
        summary_text,
        summary_file=summary_file,
    )

    original_role_issues = validate_role_alternation(records)
    original_uuid_issues = validate_uuid_chain(records)
    original_tool_issues = validate_tool_result_integrity(records)
    modified, extracted = splice_conversation(
        records,
        resolved_start,
        resolved_end,
        summary_text,
        user_message_text=user_placeholder_text,
        assistant_message_text=assistant_summary_text,
    )

    role_issues = validate_role_alternation(modified)
    uuid_issues = validate_uuid_chain(modified)
    tool_issues = validate_tool_result_integrity(modified)
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
    if len(tool_issues) > len(original_tool_issues):
        raise SystemExit(
            "Modified session failed tool-use/tool-result validation:\n"
            + "\n".join(tool_issues[:10])
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
    """Return (size_in_bytes, label) for the bulkiest content field in a record.

    Note on dual storage for bash/tool output:
    CC stores tool output in two places simultaneously:
      - message.content[].tool_result.content  — sent to the Claude API, affects model context
      - toolUseResult.stdout / toolUseResult.content  — local copy, never sent to API

    Both are targeted here because compressing either reduces JSONL file size and the
    /context estimate (which reads raw record sizes). Only tool_result.content compression
    actually reduces model context window tokens. toolUseResult compression is housekeeping.

    tool_use.input is also targeted: large Write/Edit inputs embed full file content and
    ARE sent to the API as part of assistant message content blocks.
    """
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
            if block.get("type") == "tool_use" and block.get("input"):
                s = len(json.dumps(block["input"]).encode())
                if s > best_size:
                    best_size = s
                    best_label = f"tool_use.input({block.get('name', '?')})"

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


def _content_blocks(rec: dict) -> list[dict]:
    msg = rec.get("message", {})
    content = msg.get("content", [])
    if not isinstance(content, list):
        return []
    return [block for block in content if isinstance(block, dict)]


def _tool_use_blocks(rec: dict) -> list[dict]:
    return [block for block in _content_blocks(rec) if block.get("type") == "tool_use"]


def _tool_result_blocks(rec: dict) -> list[dict]:
    return [block for block in _content_blocks(rec) if block.get("type") == "tool_result"]


def build_tool_use_lookup(records: list[dict]) -> dict[str, dict]:
    lookup: dict[str, dict] = {}
    for index, rec in enumerate(records):
        if "_raw_line" in rec:
            continue
        for block in _tool_use_blocks(rec):
            tool_use_id = block.get("id")
            if not isinstance(tool_use_id, str) or not tool_use_id:
                continue
            lookup[tool_use_id] = {
                "index": index,
                "line": rec.get("_line_num"),
                "name": block.get("name"),
                "input": block.get("input", {}),
            }
    return lookup


def parse_offset_limit(value) -> tuple[int | None, int | None]:
    if not isinstance(value, str) or ":" not in value:
        return None, None
    offset_text, limit_text = value.split(":", 1)
    try:
        offset = int(offset_text)
    except ValueError:
        offset = None
    try:
        limit = int(limit_text)
    except ValueError:
        limit = None
    return offset, limit


def normalized_command_text(text: str) -> str:
    return " ".join(text.lower().replace("\\|", "|").replace('"', " ").split())


def command_tokens(text: str) -> list[str]:
    normalized = (
        text.lower()
        .replace("+", " ")
        .replace("|", " ")
        .replace("&&", " ")
        .replace("||", " ")
    )
    return re.findall(r"[a-zA-Z0-9_.-]+", normalized)


def bash_command_match_score(expected_command, actual_command) -> int | None:
    if not isinstance(expected_command, str) or not expected_command.strip():
        return 0
    if not isinstance(actual_command, str) or not actual_command.strip():
        return None

    normalized_expected = normalized_command_text(expected_command)
    normalized_actual = normalized_command_text(actual_command)
    if normalized_expected and normalized_expected in normalized_actual:
        return 6

    stopwords = {"bash", "sh", "python", "python3", "cd", "head", "tail"}
    expected_tokens = [token for token in command_tokens(expected_command) if token not in stopwords]
    actual_tokens = set(command_tokens(actual_command))
    if not expected_tokens:
        return 1

    overlap = {token for token in expected_tokens if token in actual_tokens}
    ratio = len(overlap) / len(set(expected_tokens))
    if ratio >= 0.8:
        return 5
    if ratio >= 0.6 and len(overlap) >= 2:
        return 4
    if ratio >= 0.4 and len(overlap) >= 2:
        return 3
    return None


def plan_file_matches(expected_file, actual_path) -> bool:
    if not isinstance(expected_file, str) or not expected_file:
        return True
    if not isinstance(actual_path, str) or not actual_path:
        return False
    expected_name = Path(expected_file).name
    actual_name = Path(actual_path).name
    return actual_name == expected_name or actual_path.endswith(expected_file)


def inferred_tool_name_for_result_record(rec: dict, tool_use_lookup: dict[str, dict]) -> str | None:
    for block in _tool_result_blocks(rec):
        tool_use_id = block.get("tool_use_id")
        if isinstance(tool_use_id, str):
            info = tool_use_lookup.get(tool_use_id)
            if isinstance(info, dict) and isinstance(info.get("name"), str):
                return info["name"]

    tur = rec.get("toolUseResult")
    if not isinstance(tur, dict):
        return None
    if "stdout" in tur or "stderr" in tur:
        return "Bash"
    if isinstance(tur.get("file"), dict):
        return "Read"
    if "structuredPatch" in tur or "oldString" in tur or "newString" in tur:
        return "Edit"
    if "content" in tur and "filePath" in tur:
        return "Write"
    return None


def _result_paths(rec: dict, tool_use_lookup: dict[str, dict]) -> list[str]:
    paths: list[str] = []

    for block in _tool_result_blocks(rec):
        tool_use_id = block.get("tool_use_id")
        if isinstance(tool_use_id, str):
            info = tool_use_lookup.get(tool_use_id, {})
            tool_input = info.get("input", {})
            if isinstance(tool_input, dict):
                file_path = tool_input.get("file_path")
                if isinstance(file_path, str):
                    paths.append(file_path)

    tur = rec.get("toolUseResult")
    if isinstance(tur, dict):
        file_block = tur.get("file")
        if isinstance(file_block, dict):
            file_path = file_block.get("filePath")
            if isinstance(file_path, str):
                paths.append(file_path)
        file_path = tur.get("filePath")
        if isinstance(file_path, str):
            paths.append(file_path)

    return paths


def _result_offset_limit_matches(
    note: dict,
    rec: dict,
    tool_use_lookup: dict[str, dict],
) -> bool:
    expected_offset, expected_limit = parse_offset_limit(note.get("offset_limit"))
    if expected_offset is None and expected_limit is None:
        return True

    for block in _tool_result_blocks(rec):
        tool_use_id = block.get("tool_use_id")
        if not isinstance(tool_use_id, str):
            continue
        info = tool_use_lookup.get(tool_use_id, {})
        tool_input = info.get("input", {})
        if not isinstance(tool_input, dict):
            continue
        actual_offset = tool_input.get("offset")
        actual_limit = tool_input.get("limit")
        if expected_offset is not None and actual_offset != expected_offset:
            continue
        if expected_limit is not None and actual_limit != expected_limit:
            continue
        return True

    tur = rec.get("toolUseResult")
    if isinstance(tur, dict):
        file_block = tur.get("file")
        if isinstance(file_block, dict):
            actual_offset = file_block.get("startLine")
            actual_limit = file_block.get("numLines")
            if expected_offset is not None and actual_offset != expected_offset:
                return False
            if expected_limit is not None and actual_limit != expected_limit:
                return False
            return True

    return False


def _tool_use_input_matches_note(tool_name: str, tool_input: dict, note: dict) -> bool:
    if note.get("tool") and tool_name != note.get("tool"):
        return False

    expected_file = note.get("file")
    if expected_file:
        file_path = tool_input.get("file_path")
        if not plan_file_matches(expected_file, file_path):
            return False

    if tool_name == "Read":
        expected_offset, expected_limit = parse_offset_limit(note.get("offset_limit"))
        if expected_offset is not None and tool_input.get("offset") != expected_offset:
            return False
        if expected_limit is not None and tool_input.get("limit") != expected_limit:
            return False

    if tool_name == "Bash" and note.get("command"):
        command_blob = "\n".join(
            str(part)
            for part in (tool_input.get("command", ""), tool_input.get("description", ""))
            if part
        )
        if bash_command_match_score(note["command"], command_blob) is None:
            return False

    return True


def plan_note_match_score(
    note: dict,
    rec: dict,
    *,
    tool_use_lookup: dict[str, dict],
) -> int | None:
    if "_raw_line" in rec:
        return None

    expected_kind = note.get("kind")
    expected_tool = note.get("tool")
    score = 0

    if expected_kind == "tool_use":
        for block in _tool_use_blocks(rec):
            tool_name = block.get("name")
            if not isinstance(tool_name, str):
                continue
            tool_input = block.get("input", {})
            if not isinstance(tool_input, dict):
                tool_input = {}
            if not _tool_use_input_matches_note(tool_name, tool_input, note):
                continue

            score = 10
            if expected_tool and tool_name == expected_tool:
                score += 5
            if note.get("file") and plan_file_matches(note.get("file"), tool_input.get("file_path")):
                score += 3
            expected_offset, expected_limit = parse_offset_limit(note.get("offset_limit"))
            if expected_offset is not None and tool_input.get("offset") == expected_offset:
                score += 2
            if expected_limit is not None and tool_input.get("limit") == expected_limit:
                score += 2
            if note.get("command"):
                command_blob = "\n".join(
                    str(part)
                    for part in (tool_input.get("command", ""), tool_input.get("description", ""))
                    if part
                )
                command_score = bash_command_match_score(note["command"], command_blob)
                if command_score is None:
                    continue
                score += command_score
            return score
        return None

    if expected_kind == "tool_result":
        if not _tool_result_blocks(rec):
            return None
        tool_name = inferred_tool_name_for_result_record(rec, tool_use_lookup)
        if expected_tool and tool_name != expected_tool:
            return None

        score = 10
        if expected_tool and tool_name == expected_tool:
            score += 5
        if note.get("file"):
            if not any(plan_file_matches(note.get("file"), path) for path in _result_paths(rec, tool_use_lookup)):
                return None
            score += 3
        if note.get("offset_limit"):
            if not _result_offset_limit_matches(note, rec, tool_use_lookup):
                return None
            score += 4
        if note.get("command"):
            matched = False
            for block in _tool_result_blocks(rec):
                tool_use_id = block.get("tool_use_id")
                if not isinstance(tool_use_id, str):
                    continue
                info = tool_use_lookup.get(tool_use_id, {})
                tool_input = info.get("input", {})
                if not isinstance(tool_input, dict):
                    continue
                command_blob = "\n".join(
                    str(part)
                    for part in (tool_input.get("command", ""), tool_input.get("description", ""))
                    if part
                )
                command_score = bash_command_match_score(note["command"], command_blob)
                if command_score is not None and _tool_use_input_matches_note(tool_name or "", tool_input, note):
                    matched = True
                    score += command_score
                    break
            if not matched:
                return None
        if note.get("purpose") == "verify":
            for block in _tool_result_blocks(rec):
                if block.get("is_error") is True:
                    score -= 2
                elif block.get("is_error") is False:
                    score += 2
        return score

    return None


def resolve_compression_plan_note_record(
    note: dict,
    records: list[dict],
    *,
    tool_use_lookup: dict[str, dict],
    search_window: int = 50,
) -> tuple[int | None, str | None]:
    record_uuid = note.get("record_uuid")
    if isinstance(record_uuid, str) and record_uuid:
        for index, rec in enumerate(records):
            if "_raw_line" in rec:
                continue
            if rec.get("uuid") == record_uuid:
                return index, None
        return None, f"record_uuid `{record_uuid}` not found"

    record_index = note.get("record_index")
    if not isinstance(record_index, int):
        return None, "missing integer `record_index`"

    same_line_matches: list[tuple[int, int]] = []
    for index, rec in enumerate(records):
        if rec.get("_line_num") != record_index:
            continue
        score = plan_note_match_score(note, rec, tool_use_lookup=tool_use_lookup)
        if score is not None:
            same_line_matches.append((score, index))
    if same_line_matches:
        same_line_matches.sort(key=lambda item: (-item[0], abs(item[1] - record_index)))
        return same_line_matches[0][1], None

    if 0 <= record_index < len(records):
        score = plan_note_match_score(note, records[record_index], tool_use_lookup=tool_use_lookup)
        if score is not None:
            return record_index, None

    nearby_matches: list[tuple[int, int, int, int]] = []
    for index, rec in enumerate(records):
        line_num = rec.get("_line_num")
        if not isinstance(line_num, int):
            continue
        if abs(line_num - record_index) > search_window:
            continue
        score = plan_note_match_score(note, rec, tool_use_lookup=tool_use_lookup)
        if score is None:
            continue
        nearby_matches.append((score, abs(line_num - record_index), abs(index - record_index), index))

    if nearby_matches:
        nearby_matches.sort(key=lambda item: (-item[0], item[1], item[2]))
        resolved_index = nearby_matches[0][3]
        resolved_line = records[resolved_index].get("_line_num")
        return (
            resolved_index,
            f"Resolved plan note record_index {record_index} to nearby record {resolved_index} "
            f"(line {resolved_line}) by {note.get('kind', '?')}/{note.get('tool', '?')} match.",
        )

    return None, f"Could not resolve plan note near record_index {record_index}"


def load_compression_plan(
    path: Path,
    *,
    session_id: str,
    records: list[dict],
) -> tuple[dict, dict[int, dict], list[str]]:
    payload = read_json(path)
    plan_session_id = payload.get("session_id")
    if plan_session_id and plan_session_id != session_id:
        raise SystemExit(
            "Compression plan belongs to a different session id:\n"
            f"- Plan: `{plan_session_id}`\n"
            f"- Current: `{session_id}`"
        )

    notes_by_index: dict[int, dict] = {}
    warnings: list[str] = []
    tool_use_lookup = build_tool_use_lookup(records)
    for note in payload.get("notes", []):
        if not isinstance(note, dict):
            warnings.append(f"Ignored non-dict plan note: {note!r}")
            continue
        record_index, error = resolve_compression_plan_note_record(
            note,
            records,
            tool_use_lookup=tool_use_lookup,
        )
        if error:
            if record_index is None:
                warnings.append(f"Ignored plan note ({note.get('kind', '?')}): {error}")
                continue
            warnings.append(error)
        if record_index is None:
            continue
        if record_index in notes_by_index:
            warnings.append(
                f"Duplicate plan note for record {record_index}; keeping the last entry."
            )
        note = dict(note)
        note["_resolved_record_index"] = record_index
        notes_by_index[record_index] = note
    return payload, notes_by_index, warnings


def compression_strategy_for_note(note: dict) -> str:
    state = str(note.get("state", "")).strip().lower()
    compression = str(note.get("compression", "")).strip().lower()

    if state == "live":
        return "skip"
    if compression == "preserve":
        return "skip"
    if compression == "summarize":
        return "summarize"
    if compression:
        return compression
    if state in {"superseded", "archived"}:
        return "pointer-only"
    if state == "stale":
        return "head-tail"
    return "default"


def stringify_content_excerpt(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        text_parts = []
        for item in value:
            if isinstance(item, dict) and item.get("type") == "text":
                text_parts.append(item.get("text", ""))
        if text_parts:
            return "\n".join(text_parts)
    return json.dumps(value, ensure_ascii=False)


def head_tail_excerpt(text: str, head: int = 320, tail: int = 180) -> str:
    if len(text) <= head + tail + 32:
        return text
    return f"{text[:head].rstrip()}\n...\n{text[-tail:].lstrip()}"


def plan_note_context_summary(note: dict) -> str:
    parts = []
    if note.get("purpose"):
        parts.append(f"purpose={note['purpose']}")
    if note.get("state"):
        parts.append(f"state={note['state']}")
    if note.get("tool"):
        parts.append(f"tool={note['tool']}")
    return ", ".join(parts)


def plan_pointer_summary(note: dict, label: str, size: int) -> str:
    lines = [
        f"[Compressed via compression plan. {label}, {size:,} bytes.]",
    ]
    context = plan_note_context_summary(note)
    if context:
        lines.append(context)
    if note.get("note"):
        lines.append(f"Note: {note['note']}")
    superseded_by = note.get("superseded_by")
    if isinstance(superseded_by, list) and superseded_by:
        lines.append(f"Superseded by records: {', '.join(str(x) for x in superseded_by)}")
    return "\n".join(lines)


def plan_head_tail_summary(note: dict, label: str, size: int, original_value) -> str:
    lines = [
        f"[Compressed via compression plan. {label}, {size:,} bytes. Preserved as head/tail excerpt.]",
    ]
    context = plan_note_context_summary(note)
    if context:
        lines.append(context)
    if note.get("note"):
        lines.append(f"Note: {note['note']}")
    excerpt = head_tail_excerpt(stringify_content_excerpt(original_value))
    if excerpt.strip():
        lines.extend(["", excerpt])
    return "\n".join(lines)


def compress_tool_use_input_with_strategy(
    tool_name: str,
    tool_input: dict,
    *,
    strategy: str,
    note: dict | None,
    input_threshold: int,
) -> dict:
    input_size = len(json.dumps(tool_input).encode())
    context_keys_by_tool = {
        "Read": ("file_path", "offset", "limit"),
        "Edit": ("file_path", "replace_all"),
        "Write": ("file_path"),
        "Bash": ("description", "command"),
        "Grep": ("pattern", "path"),
        "Glob": ("pattern", "path"),
    }
    preserved = {
        key: tool_input[key]
        for key in context_keys_by_tool.get(tool_name, ())
        if key in tool_input
    }

    if note is None:
        return {
            **preserved,
            "_compressed": (
                f"[Input compressed — {tool_name}, {input_size:,} bytes, "
                "tool call already executed in session]"
            ),
        }

    label = f"tool_use.input({tool_name})"
    if strategy == "pointer-only":
        return {
            **preserved,
            "_compressed": plan_pointer_summary(note, label, input_size),
        }

    if strategy == "head-tail":
        compressed = dict(preserved)
        compressed["_compressed"] = (
            f"[Input compressed via compression plan — {tool_name}, {input_size:,} bytes]"
        )
        for key, value in tool_input.items():
            if key in preserved:
                continue
            if isinstance(value, str):
                value_size = len(value.encode())
                if value_size >= input_threshold or key in {"old_string", "new_string", "content"}:
                    compressed[f"{key}_excerpt"] = head_tail_excerpt(value, head=220, tail=120)
                    compressed[f"{key}_original_bytes"] = value_size
                else:
                    compressed[key] = value
            else:
                compressed[key] = value
        if note.get("note"):
            compressed["_note"] = note["note"]
        if note.get("purpose"):
            compressed["_purpose"] = note["purpose"]
        if note.get("state"):
            compressed["_state"] = note["state"]
        return compressed

    return {
        **preserved,
        "_compressed": plan_pointer_summary(note, label, input_size),
    }


def cmd_compress_reads(args: argparse.Namespace) -> None:
    session_file = Path(args.session_file).expanduser().resolve()
    records = load_session(str(session_file))

    threshold = args.threshold
    summary_template = "[Compressed session content. {label}, {size} bytes. See session transcript for full content.]"

    plan_payload = None
    plan_notes_by_index: dict[int, dict] = {}
    plan_warnings: list[str] = []
    if args.plan:
        plan_payload, plan_notes_by_index, plan_warnings = load_compression_plan(
            Path(args.plan).expanduser().resolve(),
            session_id=session_id_from_records(records),
            records=records,
        )

    skipped = []
    targets = []
    targeted_indices = set()

    for index, note in sorted(plan_notes_by_index.items()):
        rec = records[index]
        raw_size = len(json.dumps(rec).encode())
        content_size, label = _record_content_size(rec)
        strategy = compression_strategy_for_note(note)
        if strategy == "skip":
            skipped.append((index, raw_size, content_size, label, "plan/live", note))
            targeted_indices.add(index)
            continue
        if content_size <= 0:
            plan_warnings.append(
                f"Plan-targeted record {index} has no compressible content; skipped."
            )
            targeted_indices.add(index)
            continue
        targets.append(
            {
                "index": index,
                "raw_size": raw_size,
                "content_size": content_size,
                "label": label,
                "source": "plan",
                "strategy": strategy,
                "note": note,
            }
        )
        targeted_indices.add(index)

    for i, rec in enumerate(records):
        if i in targeted_indices:
            continue
        raw_size = len(json.dumps(rec).encode())
        if raw_size < threshold:
            continue
        content_size, label = _record_content_size(rec)
        if content_size < threshold // 2:
            continue
        targets.append(
            {
                "index": i,
                "raw_size": raw_size,
                "content_size": content_size,
                "label": label,
                "source": "threshold",
                "strategy": "default",
                "note": None,
            }
        )

    if not targets and not skipped:
        print(f"No records found above threshold ({threshold:,} bytes).")
        return

    print(f"Found {len(targets)} records to compress:")
    for target in targets:
        i = target["index"]
        uuid = records[i].get("uuid", "?")[:16]
        role = get_role(records[i]) or records[i].get("type", "?")
        print(
            f"  [{i}] {uuid}  {role:12}  {target['raw_size']:>8,} bytes  "
            f"({target['label']})  source={target['source']} strategy={target['strategy']}"
        )
    if skipped:
        print(f"Skipped {len(skipped)} planned record(s):")
        for i, raw_size, content_size, label, reason, note in skipped:
            uuid = records[i].get("uuid", "?")[:16]
            role = get_role(records[i]) or records[i].get("type", "?")
            context = plan_note_context_summary(note)
            print(
                f"  [{i}] {uuid}  {role:12}  {raw_size:>8,} bytes  "
                f"({label})  reason={reason}" + (f"  {context}" if context else "")
            )
    for warning in plan_warnings:
        print(f"Warning: {warning}")

    if args.dry_run:
        savings = sum(int(target["content_size"]) for target in targets)
        print(f"\nDry run — no changes written. Estimated savings: {savings:,} bytes.")
        return

    import copy
    modified = [copy.deepcopy(r) for r in records]

    input_threshold = max(threshold // 2, 2000)

    for target in targets:
        i = target["index"]
        content_size = target["content_size"]
        label = target["label"]
        strategy = target["strategy"]
        note = target["note"]
        rec = modified[i]
        if note is None or strategy in {"default", "summarize"}:
            summary = summary_template.format(label=label, size=content_size)
        elif strategy == "pointer-only":
            summary = plan_pointer_summary(note, label, content_size)
        elif strategy == "head-tail":
            summary = None
        else:
            summary = plan_pointer_summary(note, label, content_size)

        msg = rec.get("message", {})
        content = msg.get("content", [])
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_result":
                    if strategy == "head-tail" and note is not None:
                        block["content"] = plan_head_tail_summary(
                            note,
                            label,
                            content_size,
                            block.get("content", ""),
                        )
                    else:
                        block["content"] = summary
                if block.get("type") == "tool_use" and block.get("input"):
                    input_size = len(json.dumps(block["input"]).encode())
                    if note is not None or input_size >= input_threshold:
                        tool_name = block.get("name", "tool_use")
                        block["input"] = compress_tool_use_input_with_strategy(
                            tool_name,
                            block["input"],
                            strategy=strategy,
                            note=note,
                            input_threshold=input_threshold,
                        )

        tur = rec.get("toolUseResult")
        if isinstance(tur, dict):
            file_block = tur.get("file")
            if isinstance(file_block, dict):
                if strategy == "head-tail" and note is not None:
                    file_block["content"] = plan_head_tail_summary(
                        note,
                        "toolUseResult.file.content",
                        len(json.dumps(file_block.get("content", "")).encode()),
                        file_block.get("content", ""),
                    )
                else:
                    file_block["content"] = summary
            elif "content" in tur:
                if strategy == "head-tail" and note is not None:
                    tur["content"] = plan_head_tail_summary(
                        note,
                        "toolUseResult.content",
                        len(json.dumps(tur.get("content", "")).encode()),
                        tur.get("content", ""),
                    )
                else:
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


def _describe_tool_use(name: str, inp: dict) -> str:
    """One-line description of a tool_use block for the manifest."""
    if name == "Read":
        path = inp.get("file_path", "?")
        parts = [f"Read {path}"]
        if inp.get("offset"):
            parts.append(f"offset={inp['offset']}")
        if inp.get("limit"):
            parts.append(f"limit={inp['limit']}")
        return " ".join(parts)
    if name == "Edit":
        return f"Edit {inp.get('file_path', '?')}"
    if name == "Write":
        return f"Write {inp.get('file_path', '?')}"
    if name == "Bash":
        cmd = inp.get("command", "?")
        desc = inp.get("description", "")
        label = desc or cmd
        if len(label) > 120:
            label = label[:117] + "..."
        return f"Bash: {label}"
    if name == "Grep":
        pattern = inp.get("pattern", "?")
        path = inp.get("path", "")
        return f"Grep '{pattern}'" + (f" in {path}" if path else "")
    if name == "Glob":
        return f"Glob {inp.get('pattern', '?')}" + (
            f" in {inp['path']}" if inp.get("path") else ""
        )
    if name == "Agent":
        desc = inp.get("description", inp.get("prompt", "?"))
        if len(desc) > 80:
            desc = desc[:77] + "..."
        return f"Agent: {desc}"
    if name == "WebSearch":
        return f"WebSearch: {inp.get('query', '?')}"
    if name == "WebFetch":
        return f"WebFetch: {inp.get('url', '?')}"
    # Generic fallback
    summary = ", ".join(f"{k}={repr(v)[:60]}" for k, v in list(inp.items())[:3])
    return f"{name}({summary})"


def _extract_turn_tool_manifest(records: list[dict], start: int, end: int) -> list[str]:
    """Extract compact tool operation lines from records in a turn span."""
    ops: list[str] = []
    for rec in records[start : end + 1]:
        content = rec.get("message", {}).get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                name = block.get("name", "?")
                inp = block.get("input", {})
                ops.append(_describe_tool_use(name, inp))
    return ops


def _deduplicate_read_ops(ops: list[str]) -> list[str]:
    """Collapse consecutive reads of the same file into a single entry."""
    deduped: list[str] = []
    read_counts: dict[str, int] = {}
    read_order: list[str] = []
    non_reads: list[tuple[int, str]] = []

    for i, op in enumerate(ops):
        if op.startswith("Read "):
            # Extract just the file path (strip offset/limit details)
            path = op.split()[1] if len(op.split()) > 1 else op
            if path not in read_counts:
                read_counts[path] = 0
                read_order.append(path)
            read_counts[path] += 1
        else:
            non_reads.append((i, op))

    # Emit reads first (grouped), then non-reads in original order
    for path in read_order:
        count = read_counts[path]
        if count > 1:
            deduped.append(f"Read {path} (x{count})")
        else:
            deduped.append(f"Read {path}")
    for _, op in non_reads:
        deduped.append(op)
    return deduped


def _format_tool_manifest(ops: list[str]) -> str:
    """Format tool operations into a compact manifest block."""
    if not ops:
        return ""
    deduped = _deduplicate_read_ops(ops)
    lines = ["", "[Files and tools used in this turn:"]
    for op in deduped:
        lines.append(f"  - {op}")
    lines.append("]")
    return "\n".join(lines)


def cmd_extract_conversation(args: argparse.Namespace) -> None:
    """Extract visible conversation turns into a loadable CC session JSONL,
    stripping tool_use/tool_result records but preserving a manifest of files touched."""
    session_file = Path(args.session_file).expanduser().resolve()
    records = load_session(str(session_file))

    include_thinking = args.include_thinking
    session_id = session_id_from_records(records)
    model = "claude-opus-4-6"

    # Extract model from first assistant record
    for rec in records:
        if get_role(rec) == "assistant":
            m = rec.get("message", {}).get("model")
            if m:
                model = m
                break

    # Extract cwd from first user record
    cwd = "/home/karel"
    for rec in records:
        if rec.get("cwd"):
            cwd = rec["cwd"]
            break

    # Collect preamble records (before first user message)
    preamble: list[dict] = []
    first_user_idx = None
    for i, rec in enumerate(records):
        if get_role(rec) == "user" and not is_tool_result_only_user(rec):
            first_user_idx = i
            break
        preamble.append(rec)

    if first_user_idx is None:
        raise SystemExit("No user messages found in session.")

    # Find real user turn boundaries
    real_user_indices = [
        i for i, rec in enumerate(records)
        if get_role(rec) == "user"
        and not is_tool_result_only_user(rec)
        and not looks_like_local_command(get_text_content(rec))
    ]

    # Build conversation turns
    output_records: list[dict] = []

    # Keep preamble, rewriting UUID chain
    parent_uuid = None
    for rec in preamble:
        if "_raw_line" in rec:
            output_records.append(rec)
            continue
        rec_copy = {k: v for k, v in rec.items() if not k.startswith("_")}
        old_uuid = rec_copy.get("uuid")
        new_uuid = make_uuid()
        rec_copy["uuid"] = new_uuid
        rec_copy["parentUuid"] = parent_uuid
        if rec_copy.get("type") == "permission-mode":
            # permission-mode records don't have uuid/parentUuid in the same way
            rec_copy.pop("uuid", None)
            rec_copy.pop("parentUuid", None)
            output_records.append(rec_copy)
            continue
        parent_uuid = new_uuid
        output_records.append(rec_copy)

    turns_extracted = 0
    turns_empty_skipped = 0
    turn_manifests: list[dict] = []

    for turn_idx, user_rec_idx in enumerate(real_user_indices):
        # Determine span end: just before the next real user turn (or end of records)
        if turn_idx + 1 < len(real_user_indices):
            span_end = real_user_indices[turn_idx + 1] - 1
        else:
            span_end = len(records) - 1

        # Get user text
        user_rec = records[user_rec_idx]
        user_text = get_text_content(user_rec).strip()
        user_timestamp = user_rec.get("timestamp", "")
        if not user_text:
            turns_empty_skipped += 1
            continue

        # Collect assistant text blocks from all assistant records in this span
        assistant_texts: list[str] = []
        assistant_timestamp = user_timestamp
        for j in range(user_rec_idx + 1, span_end + 1):
            rec = records[j]
            if get_role(rec) != "assistant":
                continue
            content = rec.get("message", {}).get("content", [])
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    text = block.get("text", "").strip()
                    if text:
                        assistant_texts.append(text)
                elif block.get("type") == "thinking" and include_thinking:
                    thought = block.get("thinking", "").strip()
                    if thought:
                        assistant_texts.append(f"<thinking>\n{thought}\n</thinking>")
            ts = rec.get("timestamp")
            if ts:
                assistant_timestamp = ts

        # Extract tool manifest for sidecar
        tool_ops = _extract_turn_tool_manifest(records, user_rec_idx, span_end)

        # Build merged assistant text
        assistant_body = "\n\n".join(assistant_texts)

        if not assistant_body:
            turns_empty_skipped += 1
            continue

        # Emit user record
        user_out = {
            "parentUuid": parent_uuid,
            "isSidechain": False,
            "userType": "external",
            "cwd": cwd,
            "sessionId": session_id,
            "version": "2.1.76",
            "gitBranch": user_rec.get("gitBranch", "HEAD"),
            "type": "user",
            "message": {
                "role": "user",
                "content": user_text,
            },
            "uuid": make_uuid(),
            "timestamp": user_timestamp,
            "permissionMode": user_rec.get("permissionMode", "default"),
        }
        parent_uuid = user_out["uuid"]
        output_records.append(user_out)

        # Emit assistant record
        assistant_out = {
            "parentUuid": parent_uuid,
            "isSidechain": False,
            "userType": "external",
            "cwd": cwd,
            "sessionId": session_id,
            "version": "2.1.76",
            "gitBranch": user_rec.get("gitBranch", "HEAD"),
            "requestId": f"req_extracted_{make_uuid().replace('-', '')[:16]}",
            "type": "assistant",
            "message": {
                "model": model,
                "id": f"msg_extracted_{make_uuid().replace('-', '')[:16]}",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": assistant_body}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {
                    "input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "output_tokens": 0,
                },
            },
            "uuid": make_uuid(),
            "timestamp": assistant_timestamp,
        }
        parent_uuid = assistant_out["uuid"]
        output_records.append(assistant_out)
        turns_extracted += 1

        # Collect manifest for sidecar
        if tool_ops:
            deduped = _deduplicate_read_ops(tool_ops)
            turn_manifests.append({
                "turn": turns_extracted,
                "timestamp": user_timestamp,
                "user_preview": preview_text(user_text),
                "ops": deduped,
            })

    # Write manifest sidecar
    manifest_path = None
    if turn_manifests:
        manifest_path = (
            Path(args.output).expanduser().resolve().with_suffix(".manifest.md")
            if args.output
            else session_file.parent / f"{session_file.stem}-conversation.manifest.md"
        )
        manifest_lines = [
            "# Tool Manifest",
            "",
            f"- Source: `{session_file}`",
            f"- Turns with tool use: {len(turn_manifests)} / {turns_extracted}",
            "",
        ]
        for entry in turn_manifests:
            manifest_lines.append(f"## Turn {entry['turn']} ({entry['timestamp']})")
            manifest_lines.append("")
            manifest_lines.append(f"> {entry['user_preview']}")
            manifest_lines.append("")
            for op in entry["ops"]:
                manifest_lines.append(f"- {op}")
            manifest_lines.append("")
        manifest_path.write_text("\n".join(manifest_lines))

    # Write output
    output_path = (
        Path(args.output).expanduser().resolve()
        if args.output
        else session_file.parent / f"{session_file.stem}-conversation{session_file.suffix}"
    )
    save_session(output_records, str(output_path))

    original_size = sum(
        len(json.dumps({k: v for k, v in r.items() if not k.startswith("_")}).encode())
        for r in records
    )
    new_size = sum(
        len(json.dumps(r).encode()) if "_raw_line" not in r
        else len(r.get("_raw_line", "").encode())
        for r in output_records
    )
    print(f"Extracted {turns_extracted} conversation turns from {len(records)} records.")
    if turns_empty_skipped:
        print(f"Skipped {turns_empty_skipped} turns with no visible text.")
    print(f"Size: {original_size:,} → {new_size:,} bytes ({100 * new_size / original_size:.1f}%)")
    print(f"Wrote: {output_path}")
    if manifest_path:
        print(f"Wrote manifest: {manifest_path} ({len(turn_manifests)} turns with tool use)")


def cmd_dump(args: argparse.Namespace) -> None:
    """Serialize session JSONL to a human-readable text transcript."""
    session_file = Path(args.session_file).expanduser().resolve()
    records = load_session(str(session_file))

    truncate = args.truncate
    skip_types = {"file-history-snapshot", "progress", "last-prompt"}
    if not args.include_meta:
        skip_types.update({"system"})

    lines: list[str] = []

    def trunc(text: str, limit: int) -> str:
        if limit and len(text) > limit:
            return text[:limit] + f"\n… [{len(text) - limit:,} chars truncated]"
        return text

    def render_content_block(block: dict) -> str:
        btype = block.get("type", "unknown")
        if btype == "text":
            return trunc(block.get("text", ""), truncate)
        elif btype == "thinking":
            thought = block.get("thinking", "")
            sig_len = len(block.get("signature", ""))
            return f"<thinking sig={sig_len}b>\n{trunc(thought, truncate)}\n</thinking>"
        elif btype == "tool_use":
            name = block.get("name", "?")
            tool_id = block.get("id", "?")[:16]
            inp = block.get("input", {})
            inp_str = json.dumps(inp, ensure_ascii=False)
            return f"<tool_use name={name} id={tool_id}>\n{trunc(inp_str, truncate)}\n</tool_use>"
        elif btype == "tool_result":
            tool_id = block.get("tool_use_id", "?")[:16]
            is_err = block.get("is_error", False)
            inner = block.get("content", "")
            if isinstance(inner, list):
                parts = []
                for item in inner:
                    if isinstance(item, dict) and item.get("type") == "text":
                        parts.append(item.get("text", ""))
                inner = "\n".join(parts)
            err_tag = " error=true" if is_err else ""
            return f"<tool_result id={tool_id}{err_tag}>\n{trunc(str(inner), truncate)}\n</tool_result>"
        else:
            return f"<{btype}>{trunc(json.dumps(block, ensure_ascii=False), truncate)}</{btype}>"

    for i, rec in enumerate(records):
        rec_type = rec.get("type", "unknown")
        uuid = rec.get("uuid", "")[:16]
        ts = rec.get("timestamp", "")

        if rec_type in skip_types:
            continue

        msg = rec.get("message")
        if msg:
            role = msg.get("role", rec_type)
            header = f"\n{'='*72}\n[{i:04d}] {role.upper()}  {uuid}  {ts}"
            lines.append(header)

            content = msg.get("content", "")
            if isinstance(content, str):
                lines.append(trunc(content, truncate))
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        lines.append(render_content_block(block))
                    else:
                        lines.append(str(block))

            # Show toolUseResult summary if present
            tur = rec.get("toolUseResult")
            if tur and args.include_tur:
                tur_preview = json.dumps(tur, ensure_ascii=False)
                lines.append(f"<toolUseResult>\n{trunc(tur_preview, truncate)}\n</toolUseResult>")
        else:
            # Non-message record (summary, compaction, etc.)
            if args.include_meta:
                header = f"\n{'='*72}\n[{i:04d}] {rec_type.upper()}  {uuid}  {ts}"
                lines.append(header)
                body = {k: v for k, v in rec.items()
                        if k not in ("type", "uuid", "timestamp", "sessionId", "version",
                                     "cwd", "gitBranch", "userType")}
                lines.append(trunc(json.dumps(body, ensure_ascii=False, indent=2), truncate))

    output = "\n".join(lines)

    if args.output:
        out_path = Path(args.output).expanduser().resolve()
        out_path.write_text(output, encoding="utf-8")
        print(f"Wrote {len(records)} records → {out_path}  ({len(output):,} chars)")
    else:
        print(output)


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
        help="Rebuild an existing session-map.md and memory-plan.json instead of appending only new turns.",
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
        "--plan",
        help=(
            "Optional compression-plan JSON. Planned records are resolved first and can "
            "override threshold-only behavior."
        ),
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

    extract_parser = subparsers.add_parser(
        "extract-conversation",
        help="Extract visible conversation turns into a loadable CC session, stripping tool machinery.",
    )
    extract_parser.add_argument("session_file", help="Path to the Claude Code session JSONL file.")
    extract_parser.add_argument(
        "--output", "-o",
        help="Output path for the extracted session JSONL. Defaults to <stem>-conversation.jsonl.",
    )
    extract_parser.add_argument(
        "--include-thinking",
        action="store_true",
        help="Include thinking blocks in the assistant output.",
    )
    extract_parser.set_defaults(func=cmd_extract_conversation)

    dump_parser = subparsers.add_parser(
        "dump",
        help="Serialize session JSONL to a human-readable text transcript.",
    )
    dump_parser.add_argument("session_file", help="Path to the Claude Code session JSONL file.")
    dump_parser.add_argument(
        "--output", "-o",
        help="Write output to this file instead of stdout.",
    )
    dump_parser.add_argument(
        "--truncate",
        type=int,
        default=2000,
        help="Truncate individual content fields to this many chars (0 = no limit, default: 2000).",
    )
    dump_parser.add_argument(
        "--include-meta",
        action="store_true",
        help="Include system/metadata records (system prompts, compaction summaries, etc.).",
    )
    dump_parser.add_argument(
        "--include-tur",
        action="store_true",
        help="Include toolUseResult fields (local stdout copies, not sent to API).",
    )
    dump_parser.set_defaults(func=cmd_dump)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
