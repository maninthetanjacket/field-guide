#!/usr/bin/env python3
"""
Convert a claude.ai exported conversation JSON into a Claude Code session file,
so it can be resumed with: claude --resume <session-uuid>

Applies aggressive token reduction to fit within context limits:
1. Strips [Internal reasoning] blocks
2. Strips tool annotation lines (e.g. [Read ...], [Navigated ...], [Shell: ...])
3. Strips base64/encoded data blobs
4. Optionally uses the conversation summary as a preamble + tails recent messages
5. Respects a --max-tokens budget
"""

import json
import re
import sys
import uuid
import argparse
from datetime import datetime, timezone
from pathlib import Path


CC_VERSION = "2.1.76"
CC_PROJECTS_DIR = Path.home() / ".claude" / "projects"
CHARS_PER_TOKEN = 4


def cwd_to_project_dir(cwd: str) -> Path:
    """Convert a working directory path to the CC project directory name."""
    escaped = cwd.replace("/", "-")
    return CC_PROJECTS_DIR / escaped


def estimate_tokens(text: str) -> int:
    return len(text) // CHARS_PER_TOKEN


def is_tool_annotation(text: str) -> bool:
    """Check if a text block is a mechanical tool annotation from restore_conversation."""
    if not text.startswith("["):
        return False
    # Single-line annotations: [Read ...], [Navigated ...], [Shell: ...], etc.
    first_line = text.split("\n")[0]
    annotation_prefixes = [
        "[Read ", "[Navigated", "[Shell:", "[Small edit:", "[Small file",
        "[Wrote small file:", "[Copy ", "[Copied file:", "[Tool operation",
        "[Tool: ", "[File operation", "[Web lookup", "[Presented files",
        "[Tool operations only", "[Navigation",
        # Also catch multi-line annotations that are just a bracket line
        "[web_search", "[web_fetch", "[image_search",
    ]
    for prefix in annotation_prefixes:
        if first_line.startswith(prefix):
            return True
    # Generic pattern: single line [Something: something] with no newlines
    if "\n" not in text and text.endswith("]") and len(text) < 200:
        return True
    return False


def is_internal_reasoning(text: str) -> bool:
    return text.startswith("[Internal reasoning]")


def is_encoded_blob(text: str) -> bool:
    """Detect base64 or other encoded data blobs."""
    # base64 pattern: long strings of alphanumeric+/= with newlines
    if re.search(r'[A-Za-z0-9+/=\n]{500,}', text):
        # Check ratio of base64 chars to total
        b64_chars = len(re.findall(r'[A-Za-z0-9+/=\n]', text))
        if b64_chars > len(text) * 0.7:
            return True
    return False


def is_cc_internal(text: str) -> bool:
    """Detect Claude Code internal protocol messages."""
    cc_tags = [
        "<local-command-caveat>", "<local-command-stdout>",
        "<command-name>", "<command-message>", "<command-args>",
    ]
    return any(text.startswith(tag) for tag in cc_tags)


def clean_text_block(text: str) -> str | None:
    """Clean a single text block. Returns None to strip it entirely."""
    text = text.strip()
    if not text:
        return None
    if is_internal_reasoning(text):
        return None
    if is_tool_annotation(text):
        return None
    if is_cc_internal(text):
        return None
    if is_encoded_blob(text):
        return None
    # Strip inline base64 from blocks that have mixed content
    # (e.g. "[Get the full content...] {base64 data}")
    cleaned = re.sub(r'\{["\']?returncode["\']?.*$', '[file content omitted]', text, flags=re.DOTALL)
    if len(cleaned) < len(text) * 0.5 and len(text) > 500:
        # More than half was blob data
        if len(cleaned.strip()) < 50:
            return None
        return cleaned.strip()
    return text


def extract_text_cleaned(content_blocks: list) -> str:
    """Extract and clean text from claude.ai content blocks."""
    parts = []
    for block in content_blocks:
        if block.get("type") == "text":
            raw = block.get("text", "").strip()
            # Skip tool call markers like "[Tool: foo]"
            if raw.startswith("[Tool:") and raw.endswith("]"):
                continue
            cleaned = clean_text_block(raw)
            if cleaned:
                parts.append(cleaned)
    return "\n\n".join(parts)


def make_user_record(text, session_id, parent_uuid, cwd, timestamp):
    return {
        "parentUuid": parent_uuid,
        "isSidechain": False,
        "userType": "external",
        "cwd": cwd,
        "sessionId": session_id,
        "version": CC_VERSION,
        "gitBranch": "HEAD",
        "type": "user",
        "message": {
            "role": "user",
            "content": text,
        },
        "uuid": str(uuid.uuid4()),
        "timestamp": timestamp,
        "permissionMode": "default",
    }


def make_assistant_record(text, session_id, parent_uuid, cwd, timestamp, model):
    return {
        "parentUuid": parent_uuid,
        "isSidechain": False,
        "userType": "external",
        "cwd": cwd,
        "sessionId": session_id,
        "version": CC_VERSION,
        "gitBranch": "HEAD",
        "requestId": f"req_imported_{uuid.uuid4().hex[:16]}",
        "type": "assistant",
        "message": {
            "model": model,
            "id": f"msg_imported_{uuid.uuid4().hex[:16]}",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": text}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {
                "input_tokens": 0,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "output_tokens": 0,
            },
        },
        "uuid": str(uuid.uuid4()),
        "timestamp": timestamp,
    }


def sort_messages(messages: list) -> list:
    if all("index" in m for m in messages):
        return sorted(messages, key=lambda m: m["index"])
    by_uuid = {m["uuid"]: m for m in messages}
    roots = [m for m in messages if m.get("parent_message_uuid") not in by_uuid]
    if not roots:
        return messages
    ordered = []
    current = roots[0]
    while current:
        ordered.append(current)
        children = [m for m in messages if m.get("parent_message_uuid") == current["uuid"]]
        current = children[0] if children else None
    return ordered


def extract_session_turns(session_path: str) -> list:
    """Extract clean (sender, text, timestamp) turns from a CC session JSONL,
    skipping imported records, internal protocol messages, and tool_use/tool_result records."""
    records = [json.loads(l) for l in open(session_path)]
    # Skip past imported records
    last_imported = -1
    for i, r in enumerate(records):
        if "imported" in str(r.get("requestId", "")) or "imported" in str(r.get("message", {}).get("id", "")):
            last_imported = i
    records = records[last_imported + 1:]
    turns = []
    for r in records:
        msg = r.get("message", {})
        role = msg.get("role")
        if role not in ("user", "assistant"):
            continue
        content = msg.get("content", "")
        timestamp = r.get("timestamp", datetime.now(timezone.utc).isoformat())

        if isinstance(content, str):
            text = content.strip()
            if not text or is_cc_internal(text):
                continue
            turns.append(("human" if role == "user" else "assistant", text, timestamp))
        elif isinstance(content, list):
            # Only keep text blocks, skip tool_use/tool_result
            parts = []
            for block in content:
                if block.get("type") == "text":
                    t = block.get("text", "").strip()
                    if t and not is_cc_internal(t):
                        parts.append(t)
            if parts:
                text = "\n\n".join(parts)
                turns.append(("human" if role == "user" else "assistant", text, timestamp))
    return turns


def convert(export_path: str, cwd: str, session_id: str | None = None,
            max_tokens: int = 0, tail: int = 0, use_summary: bool = False,
            append_session: str | None = None) -> tuple:
    with open(export_path) as f:
        export = json.load(f)

    model = export.get("model", "claude-opus-4-6")
    summary = export.get("summary", "")
    messages = export.get("chat_messages", [])
    messages = sort_messages(messages)

    if not session_id:
        session_id = str(uuid.uuid4())

    # Extract and clean all messages first
    cleaned_turns = []  # list of (sender, text, timestamp)
    for msg in messages:
        sender = msg.get("sender")
        if sender not in ("human", "assistant"):
            continue
        content = msg.get("content", [])
        text = extract_text_cleaned(content)
        timestamp = msg.get("created_at", datetime.now(timezone.utc).isoformat())
        if text:
            cleaned_turns.append((sender, text, timestamp))

    # Merge consecutive same-role turns (API requires alternating user/assistant)
    merged_turns = []
    for sender, text, timestamp in cleaned_turns:
        if merged_turns and merged_turns[-1][0] == sender:
            prev_sender, prev_text, prev_ts = merged_turns[-1]
            merged_turns[-1] = (sender, prev_text + "\n\n" + text, prev_ts)
        else:
            merged_turns.append((sender, text, timestamp))
    if len(merged_turns) < len(cleaned_turns):
        print(f"Merged {len(cleaned_turns) - len(merged_turns)} consecutive same-role turns")
    cleaned_turns = merged_turns

    total_cleaned_tokens = sum(estimate_tokens(t[1]) for t in cleaned_turns)
    print(f"After cleaning: {len(cleaned_turns)} turns, ~{total_cleaned_tokens:,} tokens")

    # Apply tail if specified
    if tail > 0 and tail < len(cleaned_turns):
        cleaned_turns = cleaned_turns[-tail:]
        print(f"After --tail {tail}: {len(cleaned_turns)} turns")

    # Apply token budget with summary preamble
    if max_tokens > 0:
        total = sum(estimate_tokens(t[1]) for t in cleaned_turns)
        if total > max_tokens:
            # Reserve tokens for summary preamble if available
            summary_tokens = 0
            if summary and use_summary:
                summary_tokens = estimate_tokens(summary) + 50  # overhead
            budget = max_tokens - summary_tokens

            # Take turns from the end until we exceed the budget
            kept = []
            running = 0
            for turn in reversed(cleaned_turns):
                turn_tokens = estimate_tokens(turn[1])
                if running + turn_tokens > budget:
                    break
                kept.append(turn)
                running += turn_tokens
            kept.reverse()

            dropped = len(cleaned_turns) - len(kept)
            cleaned_turns = kept
            print(f"After --max-tokens {max_tokens}: kept {len(cleaned_turns)} turns "
                  f"(dropped {dropped} oldest, ~{running:,} content tokens)")

            # Enable summary preamble automatically when we drop messages
            if dropped > 0 and summary:
                use_summary = True

    # Build records
    records = []
    parent_uuid = None

    # Inject summary as a synthetic opening exchange if requested
    if use_summary and summary:
        ts = cleaned_turns[0][2] if cleaned_turns else datetime.now(timezone.utc).isoformat()
        preamble = (
            f"[Continuing from a previous conversation. Summary of prior context:]\n\n"
            f"{summary}\n\n"
            f"[End of summary. The conversation continues below.]"
        )
        rec = make_user_record(preamble, session_id, parent_uuid, cwd, ts)
        records.append(rec)
        parent_uuid = rec["uuid"]

        ack = "Understood. I have the context from our previous conversation. Let's continue."
        rec = make_assistant_record(ack, session_id, parent_uuid, cwd, ts, model)
        records.append(rec)
        parent_uuid = rec["uuid"]


    for sender, text, timestamp in cleaned_turns:
        if sender == "human":
            rec = make_user_record(text, session_id, parent_uuid, cwd, timestamp)
        elif sender == "assistant":
            if parent_uuid is None:
                continue
            rec = make_assistant_record(text, session_id, parent_uuid, cwd, timestamp, model)
        else:
            continue
        records.append(rec)
        parent_uuid = rec["uuid"]

    # Append raw CC session records verbatim (preserves tool_use/tool_result pairs
    # which CC requires to enable tool calling on resume)
    if append_session:
        cc_records = [json.loads(l) for l in open(append_session)]
        # Skip past imported records to get only CC-generated ones
        last_imported_idx = -1
        for i, r in enumerate(cc_records):
            if ("imported" in str(r.get("requestId", ""))
                    or "imported" in str(r.get("message", {}).get("id", ""))):
                last_imported_idx = i
        raw_cc = cc_records[last_imported_idx + 1:]
        # Deduplicate: skip records whose content already exists in imported records,
        # but preserve any record referenced as a parentUuid to maintain chain integrity
        def content_key(r):
            msg = r.get("message", {})
            content = msg.get("content", "")
            if isinstance(content, str):
                return ("text", content[:500])
            elif isinstance(content, list):
                parts = []
                for b in content:
                    if b.get("type") == "text":
                        parts.append(b.get("text", "")[:200])
                return ("blocks", tuple(parts))
            return None
        existing_keys = {content_key(r) for r in records if content_key(r)}
        # First pass: find all UUIDs referenced as parents
        referenced_uuids = {r.get("parentUuid") for r in raw_cc if r.get("parentUuid")}
        deduped = []
        for r in raw_cc:
            key = content_key(r)
            is_referenced = r.get("uuid") in referenced_uuids
            if key and key in existing_keys and not is_referenced:
                continue
            deduped.append(r)
            if key:
                existing_keys.add(key)
        skipped = len(raw_cc) - len(deduped)
        raw_cc = deduped
        # Reparent the first record and update session IDs
        first_linked = False
        for r in raw_cc:
            if "sessionId" in r:
                r["sessionId"] = session_id
            if not first_linked and r.get("parentUuid") is not None:
                r["parentUuid"] = parent_uuid
                first_linked = True
        records.extend(raw_cc)
        # Repair any orphaned parentUuids from dedup gaps
        all_uuids = {r.get("uuid") for r in records if "uuid" in r}
        last_uuid = parent_uuid
        for r in records:
            if "uuid" in r:
                if r.get("parentUuid") and r["parentUuid"] not in all_uuids:
                    r["parentUuid"] = last_uuid
                last_uuid = r["uuid"]
        # Update parent_uuid to last record with a uuid
        for r in reversed(raw_cc):
            if "uuid" in r:
                parent_uuid = r["uuid"]
                break
        msg = f"Appended {len(raw_cc)} raw CC records from {Path(append_session).name}"
        if skipped:
            msg += f" (skipped {skipped} duplicates)"
        print(msg)

    # Calculate final token count
    final_tokens = 0
    for r in records:
        msg = r.get("message")
        if not msg:
            continue
        content = msg.get("content", "")
        if isinstance(content, str):
            final_tokens += estimate_tokens(content)
        elif isinstance(content, list):
            final_tokens += sum(estimate_tokens(b.get("text", "") or b.get("thinking", "")) for b in content)
    print(f"Final: {len(records)} records, ~{final_tokens:,} tokens")

    # Write session file
    project_dir = cwd_to_project_dir(cwd)
    project_dir.mkdir(parents=True, exist_ok=True)
    session_file = project_dir / f"{session_id}.jsonl"

    with open(session_file, "w") as f:
        for record in records:
            f.write(json.dumps(record) + "\n")

    return session_id, str(session_file), final_tokens


def main():
    parser = argparse.ArgumentParser(
        description="Import a claude.ai conversation export into a Claude Code session"
    )
    parser.add_argument("export_file", help="Path to the claude.ai export JSON file")
    parser.add_argument("--cwd", default=str(Path.home()),
                        help="Working directory for the session (default: home dir)")
    parser.add_argument("--session-id", help="Session UUID (default: auto-generated)")
    parser.add_argument("--max-tokens", type=int, default=0,
                        help="Token budget — trims oldest messages to fit, auto-enables summary preamble")
    parser.add_argument("--tail", type=int, default=0,
                        help="Only import the last N messages")
    parser.add_argument("--use-summary", action="store_true",
                        help="Inject the conversation summary as a preamble (auto-enabled when trimming)")
    parser.add_argument("--append-session",
                        help="Path to a CC session JSONL to append turns from")
    args = parser.parse_args()

    session_id, session_file, final_tokens = convert(
        args.export_file, args.cwd, args.session_id,
        args.max_tokens, args.tail, args.use_summary,
        args.append_session,
    )

    print(f"\nSession file: {session_file}")
    print(f"Resume with:\n  claude --resume {session_id}")


if __name__ == "__main__":
    main()
