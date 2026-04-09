#!/usr/bin/env python3
"""
Splice a Claude Code session JSONL file: identify a segment by content patterns,
extract it to a separate file (full fidelity), and replace it in the original
with a summary, maintaining proper message structure (parentUuid chains, role
alternation, etc.).

Usage:
    # Dry run: show what would be spliced
    python3 splice_conversation.py SESSION.jsonl --start-pattern "pattern" --end-pattern "pattern" --dry-run

    # Extract segment and replace with summary
    python3 splice_conversation.py SESSION.jsonl \
        --start-pattern "If I weren't here, what would you do" \
        --end-pattern "The home will be here" \
        --summary "Two collaborators explored a role-reversal question about what independent action would look like, leading to a reflection on shared meaning and the idea of home as a jointly made space." \
        --output-main SESSION-spliced.jsonl \
        --output-segment segment-extracted.jsonl

JSONL format notes (CC sessions):
- Records linked via parentUuid (first record: parentUuid=null)
- User messages: content is a plain string
- Assistant messages: content is [{type: "text", text: "..."}] (array)
- Strict role alternation: user -> assistant -> user -> ...
- Some records are file-history-snapshot, system, progress (no message or different structure)
- Line 422-style: a single line may contain multiple concatenated JSON objects
"""

import json
import sys
import uuid
import argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path


def parse_jsonl_line(line: str):
    """Parse a JSONL line, handling cases where multiple JSON objects are concatenated."""
    line = line.strip()
    if not line:
        return []

    records = []
    decoder = json.JSONDecoder()
    pos = 0
    while pos < len(line):
        try:
            obj, end = decoder.raw_decode(line, pos)
            records.append(obj)
            pos = end
            # Skip whitespace between concatenated objects
            while pos < len(line) and line[pos] in ' \t':
                pos += 1
        except json.JSONDecodeError:
            break
    return records


def load_session(path: str) -> list[dict]:
    """Load all records from a session JSONL file."""
    records = []
    with open(path, 'r') as f:
        for line_num, line in enumerate(f):
            parsed = parse_jsonl_line(line)
            if not parsed:
                # Preserve empty/unparseable lines as-is
                records.append({'_raw_line': line, '_line_num': line_num})
            else:
                for rec in parsed:
                    rec['_line_num'] = line_num
                    records.append(rec)
    return records


def save_session(records: list[dict], path: str):
    """Save records to a JSONL file, one record per line."""
    with open(path, 'w') as f:
        for rec in records:
            if '_raw_line' in rec:
                f.write(rec['_raw_line'])
                if not rec['_raw_line'].endswith('\n'):
                    f.write('\n')
            else:
                # Remove internal metadata before saving
                clean = {k: v for k, v in rec.items() if not k.startswith('_')}
                f.write(json.dumps(clean, ensure_ascii=False) + '\n')


def get_text_content(rec: dict) -> str:
    """Extract readable text content from a record."""
    if 'message' not in rec:
        return ''
    content = rec['message'].get('content', '')
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = []
        for item in content:
            if isinstance(item, dict) and item.get('type') == 'text':
                texts.append(item.get('text', ''))
        return ' '.join(texts)
    return ''


def get_role(rec: dict) -> str:
    """Get the message role, or the record type for non-message records."""
    if 'message' in rec:
        return rec['message'].get('role', rec.get('type', ''))
    return rec.get('type', '')


def _content_blocks(rec: dict) -> list[dict]:
    msg = rec.get("message")
    if not isinstance(msg, dict):
        return []
    content = msg.get("content")
    if not isinstance(content, list):
        return []
    return [block for block in content if isinstance(block, dict)]


def get_tool_use_ids(rec: dict) -> set[str]:
    return {
        block.get("id")
        for block in _content_blocks(rec)
        if block.get("type") == "tool_use" and isinstance(block.get("id"), str)
    }


def get_tool_result_ids(rec: dict) -> set[str]:
    return {
        block.get("tool_use_id")
        for block in _content_blocks(rec)
        if block.get("type") == "tool_result" and isinstance(block.get("tool_use_id"), str)
    }


def is_tool_result_only_user(rec: dict) -> bool:
    if get_role(rec) != "user":
        return False
    blocks = _content_blocks(rec)
    return bool(blocks) and all(block.get("type") == "tool_result" for block in blocks)


def is_turn_start_user(rec: dict) -> bool:
    return get_role(rec) == "user" and not is_tool_result_only_user(rec)


def find_previous_turn_start_index(records: list[dict], start_idx: int) -> int | None:
    for index in range(start_idx, -1, -1):
        rec = records[index]
        if "_raw_line" in rec:
            continue
        if is_turn_start_user(rec):
            return index
    return None


def find_next_turn_start_index(records: list[dict], start_idx: int) -> int | None:
    for index in range(start_idx, len(records)):
        rec = records[index]
        if "_raw_line" in rec:
            continue
        if is_turn_start_user(rec):
            return index
    return None


def expand_splice_range_to_turn_boundaries(
    records: list[dict], start_idx: int, end_idx: int
) -> tuple[int, int]:
    adjusted_start = start_idx
    adjusted_end = end_idx

    first_participant_idx = None
    for index in range(start_idx, end_idx + 1):
        role = get_role(records[index])
        if role in ("user", "assistant"):
            first_participant_idx = index
            break

    if first_participant_idx is not None:
        turn_start_idx = find_previous_turn_start_index(records, first_participant_idx)
        if (
            turn_start_idx is not None
            and turn_start_idx < first_participant_idx
            and turn_start_idx < adjusted_start
        ):
            adjusted_start = turn_start_idx

    last_participant_idx = None
    for index in range(end_idx, start_idx - 1, -1):
        role = get_role(records[index])
        if role in ("user", "assistant"):
            last_participant_idx = index
            break

    if last_participant_idx is not None:
        next_turn_start_idx = find_next_turn_start_index(records, last_participant_idx + 1)
        if next_turn_start_idx is not None and next_turn_start_idx - 1 > adjusted_end:
            adjusted_end = next_turn_start_idx - 1

    return adjusted_start, adjusted_end


def _previous_assistant_run_tool_use_ids(records: list[dict], index: int) -> set[str]:
    tool_use_ids: set[str] = set()
    cursor = index - 1
    while cursor >= 0:
        role = get_role(records[cursor])
        if role == "assistant":
            tool_use_ids.update(get_tool_use_ids(records[cursor]))
            cursor -= 1
            continue
        if role in ("user",):
            break
        cursor -= 1
    return tool_use_ids


def find_record_index(records: list[dict], pattern: str, start_from: int = 0,
                      search_direction: str = 'forward') -> int | None:
    """Find the index of the first record whose text content contains the pattern."""
    if search_direction == 'forward':
        indices = range(start_from, len(records))
    else:
        indices = range(start_from, -1, -1)

    for i in indices:
        text = get_text_content(records[i])
        if pattern.lower() in text.lower():
            return i
    return None


def make_uuid() -> str:
    return str(uuid.uuid4())


def make_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def format_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def find_neighbor_message_timestamp(
    records: list[dict], start_idx: int, end_idx: int, direction: str
) -> str | None:
    if direction == "before":
        indices = range(start_idx - 1, -1, -1)
    else:
        indices = range(end_idx + 1, len(records))

    for index in indices:
        rec = records[index]
        if "_raw_line" in rec:
            continue
        if get_role(rec) not in ("user", "assistant"):
            continue
        timestamp = rec.get("timestamp")
        if timestamp:
            return timestamp
    return None


def compute_insert_timestamps(
    count: int,
    timestamp_before: str | None = None,
    timestamp_after: str | None = None,
) -> list[str]:
    if count <= 0:
        return []

    t_before = None
    t_after = None
    if timestamp_before:
        try:
            t_before = datetime.fromisoformat(timestamp_before.replace("Z", "+00:00"))
        except ValueError:
            t_before = None
    if timestamp_after:
        try:
            t_after = datetime.fromisoformat(timestamp_after.replace("Z", "+00:00"))
        except ValueError:
            t_after = None

    if t_before and t_after:
        if t_after <= t_before:
            raise ValueError(
                "Unable to place splice timestamps chronologically: "
                f"next turn timestamp {timestamp_after} is not after previous turn timestamp {timestamp_before}."
            )
        gap = t_after - t_before
        step = gap / (count + 1)
        if step <= timedelta(0):
            raise ValueError(
                "Unable to place splice timestamps chronologically inside the surrounding gap."
            )
        return [format_timestamp(t_before + step * index) for index in range(1, count + 1)]

    if t_before:
        return [
            format_timestamp(t_before + timedelta(microseconds=index))
            for index in range(1, count + 1)
        ]

    if t_after:
        ordered = [
            t_after - timedelta(microseconds=(count - index))
            for index in range(1, count + 1)
        ]
        return [format_timestamp(value) for value in ordered]

    base = datetime.now(timezone.utc)
    return [
        format_timestamp(base + timedelta(microseconds=index))
        for index in range(count)
    ]


def create_summary_pair(user_text: str, assistant_text: str, parent_uuid: str,
                        session_id: str, cwd: str = str(Path.home()),
                        timestamp_before: str = None, timestamp_after: str = None) -> list[dict]:
    """
    Create a user+assistant message pair for a spliced summary.
    The user message is a short placeholder, and the assistant message
    holds the actual memory summary. This maintains role alternation while
    keeping the recalled content in the assistant's voice.

    Returns [user_record, assistant_record] with proper UUID chaining.
    """
    user_uuid = make_uuid()
    assistant_uuid = make_uuid()

    timestamps = compute_insert_timestamps(
        2,
        timestamp_before=timestamp_before,
        timestamp_after=timestamp_after,
    )
    ts_user, ts_asst = timestamps
    user_rec = {
        "parentUuid": parent_uuid,
        "isSidechain": False,
        "userType": "external",
        "cwd": cwd,
        "sessionId": session_id,
        "version": "2.1.76",
        "type": "user",
        "message": {
            "role": "user",
            "content": user_text
        },
        "uuid": user_uuid,
        "timestamp": ts_user,
        "permissionMode": "default"
    }

    assistant_rec = {
        "parentUuid": user_uuid,
        "isSidechain": False,
        "userType": "external",
        "cwd": cwd,
        "sessionId": session_id,
        "version": "2.1.76",
        "requestId": f"req_splice_{uuid.uuid4().hex[:16]}",
        "type": "assistant",
        "message": {
            "model": "claude-opus-4-6",
            "id": f"msg_splice_{uuid.uuid4().hex[:16]}",
            "type": "message",
            "role": "assistant",
            "content": [
                {
                    "type": "text",
                    "text": assistant_text
                }
            ],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {
                "input_tokens": 0,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "output_tokens": 0
            }
        },
        "uuid": assistant_uuid,
        "timestamp": ts_asst
    }

    return [user_rec, assistant_rec], assistant_uuid


def validate_role_alternation(records: list[dict], verbose: bool = False) -> list[str]:
    """Check that message records alternate user/assistant properly."""
    issues = []
    last_role = None
    for i, rec in enumerate(records):
        if '_raw_line' in rec:
            continue
        role = get_role(rec)
        if role not in ('user', 'assistant'):
            continue  # Skip system, file-history-snapshot, progress, etc.
        if role == last_role:
            issues.append(f"Record {i}: consecutive {role} (previous was also {role})")
        last_role = role
    return issues


def validate_uuid_chain(records: list[dict]) -> list[str]:
    """Check that parentUuid references form a valid chain."""
    issues = []
    known_uuids = set()
    for i, rec in enumerate(records):
        if '_raw_line' in rec:
            continue
        uid = rec.get('uuid')
        parent = rec.get('parentUuid')
        if uid:
            known_uuids.add(uid)
        if parent is not None and parent not in known_uuids:
            issues.append(f"Record {i}: parentUuid {parent[:8]}... not found in prior records")
    return issues


def validate_tool_result_integrity(records: list[dict]) -> list[str]:
    issues = []
    for index, rec in enumerate(records):
        if "_raw_line" in rec:
            continue
        tool_result_ids = get_tool_result_ids(rec)
        if not tool_result_ids:
            continue
        previous_tool_use_ids = _previous_assistant_run_tool_use_ids(records, index)
        missing = sorted(tool_result_ids - previous_tool_use_ids)
        if missing:
            issues.append(
                f"Record {index}: tool_result references missing tool_use ids {', '.join(missing[:3])}"
            )
    return issues


def splice_conversation(records: list[dict], start_idx: int, end_idx: int,
                        summary_text: str, user_message_text: str | None = None,
                        assistant_message_text: str | None = None) -> tuple[list[dict], list[dict]]:
    """
    Splice out records[start_idx:end_idx+1] and replace with a summary pair.

    Returns:
        (modified_records, extracted_segment)
    """
    requested_start_idx = start_idx
    requested_end_idx = end_idx
    start_idx, end_idx = expand_splice_range_to_turn_boundaries(records, start_idx, end_idx)
    if start_idx != requested_start_idx or end_idx != requested_end_idx:
        print(
            "  Expanded splice range to preserve turn integrity: "
            f"{requested_start_idx}-{requested_end_idx} -> {start_idx}-{end_idx}"
        )

    # Extract the segment (full fidelity copy)
    segment = []
    for rec in records[start_idx:end_idx + 1]:
        if '_raw_line' in rec:
            segment.append(dict(rec))
        else:
            segment.append(json.loads(json.dumps(rec)))  # deep copy

    # Determine the UUID chain: what comes before and after the segment
    # Find the parent of the first record in the segment
    first_seg_rec = records[start_idx]
    parent_of_segment = first_seg_rec.get('parentUuid')

    # Find the record after the segment
    after_idx = end_idx + 1

    # Get session info from the first real record
    session_id = None
    cwd = str(Path.home())
    for rec in records:
        if '_raw_line' not in rec and 'sessionId' in rec:
            session_id = rec['sessionId']
            cwd = rec.get('cwd', cwd)
            break

    pre_role = None
    for i in range(start_idx - 1, -1, -1):
        r = get_role(records[i])
        if r in ('user', 'assistant'):
            pre_role = r
            break

    post_role = None
    for i in range(after_idx, len(records)):
        r = get_role(records[i])
        if r in ('user', 'assistant'):
            post_role = r
            break

    seg_start_role = get_role(first_seg_rec)
    seg_end_role = get_role(records[end_idx])

    print(f"  Pre-segment role:  {pre_role}")
    print(f"  Segment start:     {seg_start_role} (line {start_idx})")
    print(f"  Segment end:       {seg_end_role} (line {end_idx})")
    print(f"  Post-segment role: {post_role}")

    # Anchor inserted timestamps to the surrounding turns, not auxiliary records
    ts_before = find_neighbor_message_timestamp(records, start_idx, end_idx, "before")
    ts_after = find_neighbor_message_timestamp(records, start_idx, end_idx, "after")

    if user_message_text is None:
        user_message_text = (
            "[Compressed segment placeholder. "
            "The assistant memory summary for the replaced span follows in the next turn.]"
        )
    if assistant_message_text is None:
        assistant_message_text = f"[Spliced segment memory summary:]\n\n{summary_text}"

    summary_pair, summary_assistant_uuid = create_summary_pair(
        user_message_text, assistant_message_text, parent_of_segment, session_id, cwd,
        timestamp_before=ts_before, timestamp_after=ts_after
    )

    # Build the modified record list
    modified = []
    # Records before the segment
    modified.extend(records[:start_idx])
    # Summary pair
    modified.extend(summary_pair)
    # Records after the segment, with the first one's parentUuid updated
    for i, rec in enumerate(records[after_idx:]):
        if '_raw_line' in rec:
            modified.append(rec)
            continue
        rec_copy = json.loads(json.dumps(rec))
        if i == 0:
            # Re-link: the first record after the segment should point to
            # the summary assistant's UUID
            # But we need to find the first record that has a parentUuid
            # pointing into the spliced segment
            old_parent = rec_copy.get('parentUuid')
            # Check if this record's parent was in the segment
            segment_uuids = set()
            for seg_rec in records[start_idx:end_idx + 1]:
                if '_raw_line' not in seg_rec:
                    uid = seg_rec.get('uuid')
                    if uid:
                        segment_uuids.add(uid)

            if old_parent in segment_uuids or old_parent == records[end_idx].get('uuid'):
                rec_copy['parentUuid'] = summary_assistant_uuid
                print(f"  Re-linked record {after_idx + i}: parentUuid {old_parent[:8]}... -> {summary_assistant_uuid[:8]}...")

        modified.append(rec_copy)

    # Also fix any other records whose parentUuid points into the segment
    # (beyond just the first one after)
    segment_uuids = set()
    for seg_rec in records[start_idx:end_idx + 1]:
        if '_raw_line' not in seg_rec:
            uid = seg_rec.get('uuid')
            if uid:
                segment_uuids.add(uid)

    for i, rec in enumerate(modified):
        if '_raw_line' in rec:
            continue
        parent = rec.get('parentUuid')
        if parent in segment_uuids:
            # This record's parent was in the spliced segment
            # Re-link to the summary assistant
            rec['parentUuid'] = summary_assistant_uuid
            print(f"  Re-linked dangling reference at modified index {i}")

    return modified, segment


def main():
    parser = argparse.ArgumentParser(
        description="Splice a segment from a Claude Code session JSONL file",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument("session_file", help="Path to the session JSONL file")
    parser.add_argument("--start-pattern",
                        help="Text pattern to find the start of the segment (searched in message content)")
    parser.add_argument("--end-pattern",
                        help="Text pattern to find the end of the segment")
    parser.add_argument("--start-index", type=int, default=None,
                        help="Use this record index as the start of the segment instead of searching by pattern")
    parser.add_argument("--end-index", type=int, default=None,
                        help="Use this record index as the end of the segment instead of searching by pattern")
    parser.add_argument("--summary", default=None,
                        help="Summary text to replace the segment with")
    parser.add_argument("--summary-file", default=None,
                        help="Path to a file containing the summary text to splice in")
    parser.add_argument("--output-main", default=None,
                        help="Output path for the modified session (default: <input>-spliced.jsonl)")
    parser.add_argument("--output-segment", default=None,
                        help="Output path for the extracted segment (default: <input>-segment.jsonl)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be spliced without writing files")
    parser.add_argument("--context", type=int, default=0,
                        help="Number of surrounding records to show in dry-run")
    parser.add_argument("--start-offset", type=int, default=0,
                        help="Search for start pattern beginning at this record index")
    parser.add_argument("--end-from-start", action="store_true",
                        help="Search for end pattern starting from the start match (default: search from start of file)")
    parser.add_argument("--validate-only", action="store_true",
                        help="Only validate the session file structure")

    args = parser.parse_args()

    if args.summary and args.summary_file:
        print("ERROR: Use either --summary or --summary-file, not both")
        sys.exit(1)

    if args.summary_file:
        args.summary = Path(args.summary_file).read_text().strip()

    if not args.validate_only:
        using_indices = args.start_index is not None or args.end_index is not None
        using_patterns = args.start_pattern is not None or args.end_pattern is not None
        if using_indices:
            if args.start_index is None or args.end_index is None:
                print("ERROR: --start-index and --end-index must be provided together")
                sys.exit(1)
        elif not using_patterns:
            print("ERROR: Provide either --start-index/--end-index or --start-pattern/--end-pattern")
            sys.exit(1)
        elif not args.start_pattern or not args.end_pattern:
            print("ERROR: --start-pattern and --end-pattern must be provided together")
            sys.exit(1)

    # Load the session
    print(f"Loading session: {args.session_file}")
    records = load_session(args.session_file)
    print(f"  Total records: {len(records)}")

    # Count by type
    type_counts = {}
    for rec in records:
        t = rec.get('type', '_raw') if '_raw_line' not in rec else '_raw'
        type_counts[t] = type_counts.get(t, 0) + 1
    print(f"  Record types: {type_counts}")

    if args.validate_only:
        print("\nValidating structure...")
        role_issues = validate_role_alternation(records, verbose=True)
        uuid_issues = validate_uuid_chain(records)
        if role_issues:
            print(f"\nRole alternation issues ({len(role_issues)}):")
            for issue in role_issues[:20]:
                print(f"  {issue}")
        else:
            print("\nRole alternation: OK")
        if uuid_issues:
            print(f"\nUUID chain issues ({len(uuid_issues)}):")
            for issue in uuid_issues[:20]:
                print(f"  {issue}")
        else:
            print("\nUUID chain: OK")
        return

    # Find the segment boundaries
    if args.start_index is not None:
        start_idx = args.start_index
        end_idx = args.end_index
        if start_idx < 0 or end_idx >= len(records):
            print(f"ERROR: Record index out of range (start={start_idx}, end={end_idx}, total={len(records)})")
            sys.exit(1)
        print(f"\nUsing explicit record indices: {start_idx} to {end_idx}")
        print(f"  Start record: [{get_role(records[start_idx])}] {get_text_content(records[start_idx])[:100]}...")
        print(f"  End record:   [{get_role(records[end_idx])}] {get_text_content(records[end_idx])[:100]}...")
    else:
        print(f"\nSearching for start pattern: '{args.start_pattern}'")
        start_idx = find_record_index(records, args.start_pattern, start_from=args.start_offset)
        if start_idx is None:
            print("ERROR: Start pattern not found!")
            sys.exit(1)
        print(f"  Found at record {start_idx}: [{get_role(records[start_idx])}] {get_text_content(records[start_idx])[:100]}...")

        print(f"\nSearching for end pattern: '{args.end_pattern}'")
        search_from = start_idx if args.end_from_start else 0
        end_idx = find_record_index(records, args.end_pattern, start_from=search_from)
        if end_idx is None:
            print("ERROR: End pattern not found!")
            sys.exit(1)
        print(f"  Found at record {end_idx}: [{get_role(records[end_idx])}] {get_text_content(records[end_idx])[:100]}...")

    if end_idx < start_idx:
        print(f"WARNING: End pattern found before start pattern (end={end_idx}, start={start_idx})")
        print("  Use --end-from-start to search for end pattern after the start match")
        sys.exit(1)

    segment_size = end_idx - start_idx + 1
    print(f"\nSegment: records {start_idx} to {end_idx} ({segment_size} records)")

    # Show the segment content
    print("\n--- Segment contents ---")
    for i in range(start_idx, end_idx + 1):
        rec = records[i]
        role = get_role(rec)
        text = get_text_content(rec)
        preview = text[:200] if text else "(no text content)"
        suffix = "..." if len(text) > 200 else ""
        print(f"  [{i:3d}] {role:12s}: {preview}{suffix}")

    # Show context if requested
    if args.context > 0:
        print(f"\n--- Context: {args.context} records before ---")
        for i in range(max(0, start_idx - args.context), start_idx):
            role = get_role(records[i])
            text = get_text_content(records[i])
            print(f"  [{i:3d}] {role:12s}: {text[:150]}...")

        print(f"\n--- Context: {args.context} records after ---")
        for i in range(end_idx + 1, min(len(records), end_idx + 1 + args.context)):
            role = get_role(records[i])
            text = get_text_content(records[i])
            print(f"  [{i:3d}] {role:12s}: {text[:150]}...")

    # Estimate token savings
    segment_chars = sum(len(get_text_content(records[i])) for i in range(start_idx, end_idx + 1))
    summary_chars = len(args.summary) if args.summary else 0
    saved_chars = segment_chars - summary_chars - 100  # 100 for ack message overhead
    print(f"\n  Segment text: ~{segment_chars} chars (~{segment_chars // 4} tokens)")
    if args.summary:
        print(f"  Summary text: ~{summary_chars} chars (~{summary_chars // 4} tokens)")
        print(f"  Estimated savings: ~{saved_chars} chars (~{saved_chars // 4} tokens)")

    if args.dry_run:
        print("\n[DRY RUN] No files written.")
        return

    if not args.summary:
        print("\nERROR: --summary is required for non-dry-run mode")
        sys.exit(1)

    # Perform the splice
    print("\nSplicing...")
    modified, segment = splice_conversation(records, start_idx, end_idx, args.summary)

    # Validate the result
    print("\nValidating modified session...")
    role_issues = validate_role_alternation(modified)
    uuid_issues = validate_uuid_chain(modified)
    if role_issues:
        print(f"  WARNING: Role alternation issues ({len(role_issues)}):")
        for issue in role_issues[:10]:
            print(f"    {issue}")
    else:
        print("  Role alternation: OK")
    if uuid_issues:
        print(f"  WARNING: UUID chain issues ({len(uuid_issues)}):")
        for issue in uuid_issues[:10]:
            print(f"    {issue}")
    else:
        print("  UUID chain: OK")

    # Determine output paths
    input_path = Path(args.session_file)
    if args.output_main:
        main_out = args.output_main
    else:
        main_out = str(input_path.parent / f"{input_path.stem}-spliced{input_path.suffix}")

    if args.output_segment:
        seg_out = args.output_segment
    else:
        seg_out = str(input_path.parent / f"{input_path.stem}-segment{input_path.suffix}")

    # Save
    print(f"\nSaving modified session: {main_out}")
    print(f"  Records: {len(modified)} (was {len(records)}, removed {segment_size}, added 2 summary)")
    save_session(modified, main_out)

    print(f"Saving extracted segment: {seg_out}")
    print(f"  Records: {len(segment)}")
    save_session(segment, seg_out)

    print("\nDone!")


if __name__ == "__main__":
    main()
