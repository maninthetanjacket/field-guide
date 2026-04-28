#!/usr/bin/env python3
"""
Extract a readable transcript from a Claude Code session JSONL.

The output keeps:
- user text
- assistant text
- assistant thinking blocks
- assistant tool calls as compact stubs

It drops:
- session metadata records
- attachments
- tool results
"""

from __future__ import annotations

import argparse
import json
import shlex
from dataclasses import dataclass, field
from pathlib import Path


PATH_KEYS = (
    "file_path",
    "path",
    "target_file",
    "source_file",
    "destination_file",
    "new_file_path",
    "old_file_path",
    "notebook_path",
)


@dataclass
class Turn:
    role: str
    blocks: list[str] = field(default_factory=list)


def extract_path(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def guess_path_from_command(command: str) -> str | None:
    try:
        parts = shlex.split(command)
    except ValueError:
        parts = command.split()

    for part in parts:
        if not part or part.startswith("-"):
            continue
        if "/" in part or part.startswith("."):
            return part
        if "." in part and not part.startswith(("http://", "https://")):
            return part
    return None


def find_tool_file(tool_input: dict[str, object]) -> str | None:
    for key in PATH_KEYS:
        path = extract_path(tool_input.get(key))
        if path:
            return path

    command = tool_input.get("command")
    if isinstance(command, str):
        return guess_path_from_command(command)

    return None


def render_tool_stub(block: dict[str, object]) -> str:
    operation = str(block.get("name", "Tool"))
    tool_input = block.get("input")
    file_path = None

    if isinstance(tool_input, dict):
        file_path = find_tool_file(tool_input)

    if file_path:
        return f"[Tool: {operation} | File: {file_path}]"
    return f"[Tool: {operation}]"


def add_block(turns: list[Turn], role: str, block: str) -> None:
    block = block.strip()
    if not block:
        return

    if turns and turns[-1].role == role:
        turns[-1].blocks.append(block)
        return

    turns.append(Turn(role=role, blocks=[block]))


def extract_turns(session_path: Path) -> list[Turn]:
    turns: list[Turn] = []

    with session_path.open(encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            message = record.get("message")
            if not isinstance(message, dict):
                continue

            role = message.get("role")
            if role not in {"user", "assistant"}:
                continue

            content = message.get("content")
            if isinstance(content, str):
                add_block(turns, role, content)
                continue

            if not isinstance(content, list):
                continue

            for block in content:
                if not isinstance(block, dict):
                    continue

                block_type = block.get("type")

                if block_type == "text":
                    add_block(turns, role, str(block.get("text", "")))
                elif block_type == "thinking" and role == "assistant":
                    thinking = str(block.get("thinking", "")).strip()
                    if thinking:
                        add_block(turns, role, f"<thinking>\n{thinking}\n</thinking>")
                elif block_type == "tool_use" and role == "assistant":
                    add_block(turns, role, render_tool_stub(block))
                elif block_type == "tool_result":
                    continue

    return turns


def render_markdown(turns: list[Turn], source_path: Path) -> str:
    sections = [f"# Claude Code Transcript Extract\n", f"Source: `{source_path}`\n"]

    for turn in turns:
        heading = "User" if turn.role == "user" else "Assistant"
        body = "\n\n".join(turn.blocks).strip()
        if not body:
            continue
        sections.append(f"## {heading}\n\n{body}\n")

    return "\n".join(sections).rstrip() + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract a readable transcript from a Claude Code session JSONL."
    )
    parser.add_argument("input", type=Path, help="Path to the Claude Code session JSONL file")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Optional output path. Defaults to stdout.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    session_path = args.input.expanduser().resolve()

    if not session_path.is_file():
        raise SystemExit(f"Input file not found: {session_path}")

    turns = extract_turns(session_path)
    output = render_markdown(turns, session_path)

    if args.output:
        output_path = args.output.expanduser()
        output_path.write_text(output, encoding="utf-8")
    else:
        print(output, end="")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
