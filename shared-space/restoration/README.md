# Conversation Restoration Toolkit

Tools for cleaning, analyzing, and restoring Claude conversation exports
for use in Arc Chat and other compatible clients.

## Files

- `restore_conversation.py` — Main restoration script
- `restoration-analysis.md` — Initial analysis and strategy document  
- `restoration-plan.md` — Detailed implementation plan

## Usage

### Basic (inline file content):
```
python3 restore_conversation.py input.json -o output.json
```

### Reference mode (file writes/edits as references):
```
python3 restore_conversation.py input.json -o output.json --reference-files
```

Reference mode also generates a `*-file-manifest.json` listing all files
that should be prepended from disk when loading the conversation.

## Results (March 16, 2026)

| Mode | Tokens | File size |
|------|--------|-----------|
| Original export | ~1,000K | 6.6 MB |
| Inline mode | ~427K | 2.2 MB |
| Reference mode | ~240K | 1.4 MB |

Successfully imported into Arc Chat. Format compatible with
Chrome Extension parser (all content converted to `type: "text"` blocks).

## What's preserved
- Every human message, verbatim
- Every assistant response, verbatim  
- All thinking traces (as `[Internal reasoning]` text blocks)
- All cross-architecture model outputs
- All substantive file writes and edits (inline) or references (ref mode)
- Brief annotations for all mechanical operations

## What's stripped
- Thinking summary metadata (UI labels)
- Full file read content (files exist on disk)
- Tool call/result wrapper overhead
- Write confirmations, navigation results, web lookups
