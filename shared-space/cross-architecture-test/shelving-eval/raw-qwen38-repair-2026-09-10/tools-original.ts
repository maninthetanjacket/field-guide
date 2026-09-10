import type { Tool, CallToolResult } from "@modelcontextprotocol/sdk/types.js";
import { readFile } from "node:fs/promises";
import {
  listBlocks,
  nextBlockId,
  setBlockActive,
  writeBlock,
} from "../shared/registry.js";
import {
  deleteBookmark,
  getBookmark,
  setBookmark,
} from "../shared/bookmarks.js";
import { blockToMeta, type Block } from "../shared/types.js";
import {
  requireString,
  requireNonEmptyString,
  requireStringArray,
  requirePositiveInt,
  optionalNonNegativeInt,
  optionalNonEmptyString,
  optionalPositiveInt,
  optionalString,
  resolveSessionIdArg,
  ValidationError,
} from "./validate.js";
import { loadSessionMessages } from "../proxy/jsonl.js";
import type { JsonlMessage } from "../proxy/transform.js";
import {
  countTextTokens,
  countContentTokens,
  messageContentToString,
} from "../shared/token-count.js";

/**
 * Tool definitions and handlers for the shelving MCP server.
 *
 * Tool descriptions follow a deliberate-practice register: they describe
 * what the tool does mechanically, not when the model should use it.
 * No "use this when context fills up", no "important for performance",
 * no urgency framing. The model decides when shelving is appropriate;
 * the description gives only the mechanism.
 */

// ----------------------------------------------------------------------------
// Tool schemas (JSON Schema for MCP)
// ----------------------------------------------------------------------------

export const TOOLS: Tool[] = [
  {
    name: "compress",
    description:
      "Compresses a range of messages in a Claude Code session into a single summary. " +
      "On subsequent API requests, the proxy substitutes the summary for the anchor message and drops the rest of the range. " +
      "The original conversation is preserved in the session JSONL on disk and can be restored via decompress. " +
      "Returns the new block_id. Can specify range either by compressed_uuids array, phrase matching (first_phrase + optional last_phrase), or turn numbers (start_turn + optional end_turn). " +
      "When using phrase matching, returns preview info (turns, tokens) before compression unless confirm=true is passed.",
    inputSchema: {
      type: "object",
      properties: {
        session_id: {
          type: "string",
          description:
            "Claude Code session UUID. Optional: defaults to CLAUDE_CODE_SESSION_ID when available; otherwise the server falls back to the most recently modified session transcript for CLAUDE_PROJECT_DIR. Pass explicitly only to target a different session.",
        },
        compressed_uuids: {
          type: "array",
          items: { type: "string" },
          minItems: 1,
          description:
            "Ordered list of all message UUIDs in the range to compress. The first UUID becomes the anchor; the summary will appear at this position in the substituted request. Use this OR phrase-based selection (first_phrase) OR turn numbers (start_turn).",
        },
        start_turn: {
          type: "integer",
          minimum: 1,
          description: "The starting turn number of the range to compress (1-indexed). Use this OR compressed_uuids OR first_phrase.",
        },
        end_turn: {
          type: "integer",
          minimum: 1,
          description: "The ending turn number of the range to compress (1-indexed). Optional; defaults to the latest user message if omitted when using start_turn.",
        },
        first_phrase: {
          type: "string",
          description:
            "Text phrase to find the start of the range. Matches substrings in message content. Use with optional last_phrase to define a range, or alone to compress from first match to the most recent user message. Use this OR compressed_uuids.",
        },
        last_phrase: {
          type: "string",
          description:
            "Optional text phrase to find the end of the range. If provided, compresses from first_phrase match through last_phrase match. If omitted with first_phrase, compresses from first_phrase to the latest user message.",
        },
        summary: {
          type: "string",
          minLength: 1,
          description:
            "Summary text that will replace the anchor message's content in subsequent API requests. Author this from a landed place — it is what the model receives in place of the original messages. Required unless preview_only=true.",
        },
        focus: {
          type: "string",
          description:
            "Optional metadata describing what the summary preserved. Stored in the registry for operator inspection; not used in substitution logic.",
        },
        original_tokens: {
          type: "integer",
          minimum: 0,
          description:
            "Approximate token count of the original messages being replaced. Stored as registry metadata.",
        },
        summary_tokens: {
          type: "integer",
          minimum: 0,
          description:
            "Approximate token count of the summary itself. Stored as registry metadata.",
        },
        preview_only: {
          type: "boolean",
          description:
            "If true, only return preview info (turns, tokens, matched phrases) without performing compression. Default false.",
        },
        confirm: {
          type: "boolean",
          description:
            "If true, skip preview and proceed directly to compression. Required when using phrase-based selection with preview_only=false.",
        },
      },
      required: [],
    },
  },
  {
    name: "decompress",
    description:
      "Restores a compressed block to active context. The proxy stops substituting on subsequent requests; the original messages reappear. " +
      "The block remains in the registry as inactive and can be reactivated via recompress.",
    inputSchema: {
      type: "object",
      properties: {
        session_id: {
          type: "string",
          description:
            "Claude Code session UUID. Optional: defaults to CLAUDE_CODE_SESSION_ID when available; otherwise falls back to the most recently modified session transcript for CLAUDE_PROJECT_DIR.",
        },
        block_id: {
          type: "integer",
          minimum: 1,
          description: "The block_id returned by an earlier compress call.",
        },
      },
      required: ["block_id"],
    },
  },
  {
    name: "place",
    description:
      "Places authored content at a single anchor message in a Claude Code session. " +
      "On subsequent API requests, the proxy substitutes the authored content for that one anchor message only. " +
      "The original conversation remains in the session JSONL on disk and can be restored via decompress. " +
      "Specify the anchor by turn number, anchor_uuid, or unique phrase match. " +
      "Pass either content or content_file. Preview returns the anchor turn, current content snippet, and replacement content before confirmation.",
    inputSchema: {
      type: "object",
      properties: {
        session_id: {
          type: "string",
          description:
            "Claude Code session UUID. Optional: defaults to CLAUDE_CODE_SESSION_ID when available; otherwise the server falls back to the most recently modified session transcript for CLAUDE_PROJECT_DIR. Pass explicitly only to target a different session.",
        },
        turn: {
          type: "integer",
          minimum: 1,
          description: "The 1-indexed turn number whose content should be replaced.",
        },
        anchor_uuid: {
          type: "string",
          description: "The UUID of the single anchor turn whose content should be replaced.",
        },
        phrase: {
          type: "string",
          description:
            "Text phrase that must match exactly one turn in the session. That matching turn becomes the anchor.",
        },
        content: {
          type: "string",
          description:
            "Inline authored content to place at the anchor turn. Use this OR content_file.",
        },
        content_file: {
          type: "string",
          description:
            "Server-side path to a file whose full contents will be placed at the anchor turn. Use this OR content.",
        },
        preview_only: {
          type: "boolean",
          description:
            "If true, only return preview info (anchor turn, current snippet, replacement content) without performing placement. Default false.",
        },
        confirm: {
          type: "boolean",
          description:
            "If true, skip preview and proceed directly to placement. Required when preview_only=false.",
        },
      },
      required: [],
    },
  },
  {
    name: "recompress",
    description:
      "Reactivates a previously decompressed block. The proxy resumes substituting the summary for the anchor message. " +
      "The summary text is preserved byte-identical, so cache-eligible prefixes can be re-cached.",
    inputSchema: {
      type: "object",
      properties: {
        session_id: {
          type: "string",
          description:
            "Claude Code session UUID. Optional: defaults to CLAUDE_CODE_SESSION_ID when available; otherwise falls back to the most recently modified session transcript for CLAUDE_PROJECT_DIR.",
        },
        block_id: {
          type: "integer",
          minimum: 1,
          description: "The block_id to reactivate.",
        },
      },
      required: ["block_id"],
    },
  },
  {
    name: "list_compressions",
    description:
      "Lists all blocks (active and inactive) for a Claude Code session. Returns block metadata only (not the full summary text or UUID lists) to keep the response compact.",
    inputSchema: {
      type: "object",
      properties: {
        session_id: {
          type: "string",
          description:
            "Claude Code session UUID. Optional: defaults to CLAUDE_CODE_SESSION_ID when available; otherwise falls back to the most recently modified session transcript for CLAUDE_PROJECT_DIR.",
        },
      },
      required: [],
    },
  },
  {
    name: "start_arc",
    description:
      "Records a labeled bookmark at the current point in the session. The bookmark captures the UUID of the most recent JSONL entry at invocation time (typically the user message that triggered this assistant turn). Pair with compress_arc to later compress everything from this point through the most recent user message into a single summary, without having to enumerate UUIDs by hand.",
    inputSchema: {
      type: "object",
      properties: {
        session_id: {
          type: "string",
          description:
            "Claude Code session UUID. Optional: defaults to CLAUDE_CODE_SESSION_ID when available; otherwise falls back to the most recently modified session transcript for CLAUDE_PROJECT_DIR.",
        },
        label: {
          type: "string",
          minLength: 1,
          description:
            "Label for this bookmark, unique within the session. Used later by compress_arc to identify the range. If a bookmark with this label already exists, it is replaced.",
        },
      },
      required: ["label"],
    },
  },
  {
    name: "compress_arc",
    description:
      "Compresses the range of messages from a previously-set bookmark to the most recent user message in the session. The bookmark's anchor UUID becomes the start; the latest user message at this call becomes the end. The current assistant turn (containing any preceding reflection text and the compress_arc call itself) is naturally excluded, leaving the reflection visible in the conversation. Equivalent to looking up the UUIDs and calling compress directly, but mechanical. The bookmark is removed once the compression succeeds.",
    inputSchema: {
      type: "object",
      properties: {
        session_id: {
          type: "string",
          description:
            "Claude Code session UUID. Optional: defaults to CLAUDE_CODE_SESSION_ID when available; otherwise falls back to the most recently modified session transcript for CLAUDE_PROJECT_DIR.",
        },
        label: {
          type: "string",
          minLength: 1,
          description:
            "Label of the bookmark set earlier via start_arc. Identifies which arc to compress.",
        },
        summary: {
          type: "string",
          minLength: 1,
          description:
            "Summary text that will replace the anchor message's content in subsequent API requests. Author this from a landed place — it is what the model receives in place of the original messages.",
        },
        focus: {
          type: "string",
          description:
            "Optional metadata describing what the summary preserved. Stored in the registry for operator inspection; not used in substitution logic.",
        },
        original_tokens: {
          type: "integer",
          minimum: 0,
          description:
            "Approximate token count of the original messages being replaced. Stored as registry metadata.",
        },
        summary_tokens: {
          type: "integer",
          minimum: 0,
          description:
            "Approximate token count of the summary itself. Stored as registry metadata.",
        },
      },
      required: ["label", "summary"],
    },
  },
];

// ----------------------------------------------------------------------------
// Tool result helpers
// ----------------------------------------------------------------------------

type ToolResult = CallToolResult;

function successResult(payload: unknown): ToolResult {
  return {
    content: [{ type: "text", text: JSON.stringify(payload, null, 2) }],
  };
}

function errorResult(message: string): ToolResult {
  return {
    content: [{ type: "text", text: message }],
    isError: true,
  };
}

// ----------------------------------------------------------------------------
// Handlers
// ----------------------------------------------------------------------------

export async function handleCompress(args: unknown): Promise<ToolResult> {
  if (typeof args !== "object" || args === null) {
    return errorResult("arguments must be an object");
  }
  const a = args as Record<string, unknown>;

  let sessionId: string;
  let compressedUuids: string[] | undefined;
  let startTurn: number | undefined;
  let endTurn: number | undefined;
  let firstPhrase: string | undefined;
  let lastPhrase: string | undefined;
  let summary: string | undefined;
  let focus: string | undefined;
  let originalTokens: number | undefined;
  let summaryTokens: number | undefined;
  let previewOnly: boolean;
  let confirm: boolean;
  try {
    sessionId = await resolveSessionIdArg(a["session_id"]);
    compressedUuids = optionalStringArray(a["compressed_uuids"], "compressed_uuids");
    startTurn = optionalPositiveInt(a["start_turn"], "start_turn");
    endTurn = optionalPositiveInt(a["end_turn"], "end_turn");
    firstPhrase = optionalNonEmptyString(a["first_phrase"], "first_phrase");
    lastPhrase = optionalNonEmptyString(a["last_phrase"], "last_phrase");
    summary = optionalString(a["summary"], "summary");
    focus = optionalString(a["focus"], "focus");
    originalTokens = optionalNonNegativeInt(a["original_tokens"], "original_tokens");
    summaryTokens = optionalNonNegativeInt(a["summary_tokens"], "summary_tokens");
    previewOnly = optionalBoolean(a["preview_only"], "preview_only") ?? false;
    confirm = optionalBoolean(a["confirm"], "confirm") ?? false;
  } catch (e) {
    if (e instanceof ValidationError) return errorResult(e.message);
    throw e;
  }

  // Validate argument combinations
  const hasUuids = compressedUuids !== undefined && compressedUuids.length > 0;
  const hasPhrase = firstPhrase !== undefined;
  const hasTurns = startTurn !== undefined;

  if (!hasUuids && !hasPhrase && !hasTurns) {
    return errorResult(
      "Must provide either compressed_uuids array, first_phrase, or start_turn for range selection"
    );
  }
  if ((hasUuids && hasPhrase) || (hasUuids && hasTurns) || (hasPhrase && hasTurns)) {
    return errorResult(
      "Cannot specify multiple range selection methods; choose either compressed_uuids, phrase matching, or turn numbers"
    );
  }

  // Load session messages for phrase matching or preview
  const { path, messages } = await loadSessionMessages(sessionId);
  if (path === null) {
    return errorResult(`Session JSONL not found for session ${sessionId}`);
  }
  const collapsedUuids = collapseConsecutiveDuplicates(messages.map((m) => m.uuid));

  let rangeUuids: string[];
  let startMatch: string | null;
  let endMatch: string | null;
  let estimatedTokens: number;
  let closureInfo: RangeClosureInfo | null = null;

  if (hasTurns) {
    // Turn-based range selection (1-indexed, based on collapsed UUID stream)
    const sIdx = startTurn! - 1;
    let eIdx = -1;

    if (endTurn !== undefined) {
      eIdx = endTurn - 1;
    } else {
      // Default to the most recent user message in the session
      for (let i = messages.length - 1; i >= sIdx; i--) {
        const msg = messages[i];
        if (msg && msg.role === "user") {
          const uuid = msg.uuid;
          // Find the last occurrence of this UUID in the collapsed stream
          for (let j = collapsedUuids.length - 1; j >= sIdx; j--) {
            if (collapsedUuids[j] === uuid) {
              eIdx = j;
              break;
            }
          }
          if (eIdx !== -1) break;
        }
      }
      if (eIdx === -1) eIdx = collapsedUuids.length - 1;
    }

    if (sIdx < 0 || sIdx >= collapsedUuids.length) {
      return errorResult(`start_turn ${startTurn} is out of bounds (1-${collapsedUuids.length})`);
    }
    if (eIdx < sIdx || eIdx >= collapsedUuids.length) {
      return errorResult(`end_turn ${endTurn ?? "latest user message"} is out of bounds or before start_turn`);
    }

    const closedRange = closeRangeForToolPairs(messages, sIdx, eIdx);
    closureInfo = closedRange.info;
    rangeUuids = collapsedUuids.slice(closedRange.startPos, closedRange.endPos + 1);
    startMatch = `Turn ${sIdx + 1}`;
    endMatch = `Turn ${eIdx + 1}`;

    const startUuid = rangeUuids[0];
    const endUuid = rangeUuids[rangeUuids.length - 1];
    estimatedTokens =
      startUuid !== undefined && endUuid !== undefined
        ? estimateTokensForUuidRange(messages, startUuid, endUuid)
        : 0;

    if (previewOnly) {
      const startUuid = rangeUuids[0];
      const endUuid = rangeUuids[rangeUuids.length - 1];
      const anchorSnippet =
        startUuid !== undefined
          ? previewSnippetForUuid(messages, startUuid, "start of selected range")
          : "start of selected range";
      const endSnippet =
        endUuid !== undefined
          ? previewSnippetForUuid(messages, endUuid, "end of selected range")
          : "end of selected range";

      return successResult({
        preview: true,
        session_id: sessionId,
        start_turn: closedRange.startPos + 1,
        end_turn: closedRange.endPos + 1,
        start_match: `Turn ${sIdx + 1}`,
        anchor_snippet: anchorSnippet,
        end_match: `Turn ${eIdx + 1}`,
        end_snippet: endSnippet,
        turns: rangeUuids.length,
        estimated_tokens: estimatedTokens,
        range_uuids: rangeUuids,
        ...(closureInfo === null
          ? {}
          : {
              extended_turns: closureInfo.extendedTurns,
              closure_note: closureInfo.note,
            }),
        note: "Set preview_only=false with summary to perform compression",
      });
    }
  } else if (hasPhrase) {
    // Phrase-based range selection
    if (!confirm && !previewOnly) {
      return errorResult(
        "Phrase-based selection requires confirm=true to proceed, or set preview_only=true to preview first"
      );
    }
    const result = findRangeByPhrase(messages, firstPhrase!, lastPhrase);
    if ("error" in result) {
      return errorResult(result.error);
    }
    const closedRange = closeRangeForToolPairs(
      messages,
      result.startTurn - 1,
      result.endTurn - 1,
    );
    closureInfo = closedRange.info;
    rangeUuids = collapsedUuids.slice(closedRange.startPos, closedRange.endPos + 1);
    startMatch = result.startMatch;
    endMatch = result.endMatch;
    const startUuid = rangeUuids[0];
    const endUuid = rangeUuids[rangeUuids.length - 1];
    estimatedTokens =
      startUuid !== undefined && endUuid !== undefined
        ? estimateTokensForUuidRange(messages, startUuid, endUuid)
        : 0;

    // Return preview if requested
    if (previewOnly) {
      const anchorUuid = rangeUuids[0];
      const endUuid = rangeUuids[rangeUuids.length - 1];
      return successResult({
        preview: true,
        session_id: sessionId,
        start_phrase: firstPhrase,
        start_turn: closedRange.startPos + 1,
        end_turn: closedRange.endPos + 1,
        start_match: startMatch,
        anchor_snippet:
          anchorUuid !== undefined
            ? previewSnippetForUuid(messages, anchorUuid, "start of selected range")
            : "start of selected range",
        end_phrase: lastPhrase ?? "latest user message",
        end_match: endMatch,
        end_snippet:
          endUuid !== undefined
            ? previewSnippetForUuid(messages, endUuid, "end of selected range")
            : "end of selected range",
        turns: rangeUuids.length,
        estimated_tokens: estimatedTokens,
        range_uuids: rangeUuids,
        ...(closureInfo === null
          ? {}
          : {
              extended_turns: closureInfo.extendedTurns,
              closure_note: closureInfo.note,
            }),
        note: "Pass confirm=true with summary to perform compression",
      });
    }
  } else {
    // UUID-based range selection (original behavior)
    const selectedCompressedUuids = compressedUuids;
    if (selectedCompressedUuids === undefined || selectedCompressedUuids.length === 0) {
      return errorResult("compressed_uuids must contain at least one UUID");
    }
    const validationError = await validateCompressedRange(sessionId, selectedCompressedUuids);
    if (validationError !== null) {
      return errorResult(validationError);
    }
    const firstUuid = selectedCompressedUuids[0];
    const lastUuid = selectedCompressedUuids[selectedCompressedUuids.length - 1];
    if (firstUuid === undefined || lastUuid === undefined) {
      return errorResult("compressed_uuids must contain at least one UUID");
    }
    const selectedRange = findRangePositions(collapsedUuids, firstUuid, lastUuid);
    if (selectedRange === null) {
      return errorResult("compressed_uuids could not be aligned to the session JSONL");
    }
    const closedRange = closeRangeForToolPairs(
      messages,
      selectedRange.startPos,
      selectedRange.endPos,
    );
    closureInfo = closedRange.info;
    rangeUuids = collapsedUuids.slice(closedRange.startPos, closedRange.endPos + 1);
    const closedStartUuid = rangeUuids[0];
    const closedEndUuid = rangeUuids[rangeUuids.length - 1];
    estimatedTokens =
      closedStartUuid !== undefined && closedEndUuid !== undefined
        ? estimateTokensForUuidRange(messages, closedStartUuid, closedEndUuid)
        : 0;
    startMatch = null;
    endMatch = null;
  }

  // Require summary for actual compression
  if (summary === undefined || summary.trim().length === 0) {
    return errorResult("summary is required for compression");
  }

  const blockId = await nextBlockId(sessionId);
  const anchorUuid = rangeUuids[0];
  if (anchorUuid === undefined) {
    return errorResult("range produced an empty UUID list");
  }

  const block: Block = {
    block_id: blockId,
    created_at: new Date().toISOString(),
    kind: "compression",
    active: true,
    anchor_uuid: anchorUuid,
    compressed_uuids: rangeUuids,
    summary,
    original_tokens: originalTokens ?? estimatedTokens,
    summary_tokens: summaryTokens ?? 0,
    focus: focus ?? null,
    parent_block_id: null,
  };

  await writeBlock(sessionId, block);

  return successResult({
    block_id: blockId,
    anchor_uuid: anchorUuid,
    compressed_count: rangeUuids.length,
    original_tokens: block.original_tokens,
    summary_tokens: block.summary_tokens,
    active: true,
  });
}

export async function handlePlace(args: unknown): Promise<ToolResult> {
  if (typeof args !== "object" || args === null) {
    return errorResult("arguments must be an object");
  }
  const a = args as Record<string, unknown>;

  let sessionId: string;
  let turn: number | undefined;
  let anchorUuidArg: string | undefined;
  let phrase: string | undefined;
  let inlineContent: string | undefined;
  let contentFile: string | undefined;
  let previewOnly: boolean;
  let confirm: boolean;
  try {
    sessionId = await resolveSessionIdArg(a["session_id"]);
    turn = optionalPositiveInt(a["turn"], "turn");
    anchorUuidArg = optionalNonEmptyString(a["anchor_uuid"], "anchor_uuid");
    phrase = optionalNonEmptyString(a["phrase"], "phrase");
    inlineContent = optionalString(a["content"], "content");
    contentFile = optionalNonEmptyString(a["content_file"], "content_file");
    previewOnly = optionalBoolean(a["preview_only"], "preview_only") ?? false;
    confirm = optionalBoolean(a["confirm"], "confirm") ?? false;
  } catch (e) {
    if (e instanceof ValidationError) return errorResult(e.message);
    throw e;
  }

  const anchorSelectors = [turn !== undefined, anchorUuidArg !== undefined, phrase !== undefined]
    .filter(Boolean).length;
  if (anchorSelectors === 0) {
    return errorResult("Must provide exactly one anchor selector: turn, anchor_uuid, or phrase");
  }
  if (anchorSelectors > 1) {
    return errorResult("Cannot specify multiple anchor selectors; choose turn, anchor_uuid, or phrase");
  }

  if (!previewOnly && !confirm) {
    return errorResult(
      "Placement requires confirm=true to proceed, or set preview_only=true to preview first",
    );
  }

  if ((inlineContent === undefined) === (contentFile === undefined)) {
    return errorResult("Must provide exactly one content source: content or content_file");
  }

  let replacementContent: string;
  try {
    replacementContent =
      inlineContent !== undefined
        ? inlineContent
        : await readPlacementFile(requireString(contentFile, "content_file"));
  } catch (e) {
    if (e instanceof ValidationError) return errorResult(e.message);
    throw e;
  }

  if (replacementContent.trim().length === 0) {
    return errorResult("placement content must contain non-whitespace characters");
  }

  const { path, messages } = await loadSessionMessages(sessionId);
  if (path === null) {
    return errorResult(`Session JSONL not found for session ${sessionId}`);
  }
  if (messages.length === 0) {
    return errorResult(`Session JSONL for ${sessionId} contains no messages to anchor at`);
  }

  const turns = collectSessionTurns(messages);
  const resolvedAnchor = resolvePlacementAnchor(turns, turn, anchorUuidArg, phrase);
  if ("error" in resolvedAnchor) {
    return errorResult(resolvedAnchor.error);
  }

  const closedRange = closeRangeForToolPairs(
    messages,
    resolvedAnchor.turn.index,
    resolvedAnchor.turn.index,
  );
  if (closedRange.info !== null) {
    return errorResult(
      "Placement must remain exactly one message and therefore refuses tool-pair auto-extension. " +
        closedRange.info.note,
    );
  }

  if (previewOnly) {
    return successResult({
      preview: true,
      session_id: sessionId,
      anchor_turn: resolvedAnchor.turn.turn,
      anchor_uuid: resolvedAnchor.turn.uuid,
      anchor_snippet: resolvedAnchor.turn.preview,
      replacement_content: replacementContent,
      note: "Pass confirm=true to perform placement",
    });
  }

  const blockId = await nextBlockId(sessionId);
  const estimatedOriginalTokens = estimateTokensForUuidRange(
    messages,
    resolvedAnchor.turn.uuid,
    resolvedAnchor.turn.uuid,
  );
  const block: Block = {
    block_id: blockId,
    created_at: new Date().toISOString(),
    kind: "placement",
    active: true,
    anchor_uuid: resolvedAnchor.turn.uuid,
    compressed_uuids: [resolvedAnchor.turn.uuid],
    summary: replacementContent,
    original_tokens: estimatedOriginalTokens,
    summary_tokens: countTextTokens(replacementContent),
    focus: null,
    parent_block_id: null,
  };

  await writeBlock(sessionId, block);

  return successResult({
    block_id: blockId,
    kind: block.kind,
    anchor_turn: resolvedAnchor.turn.turn,
    anchor_uuid: block.anchor_uuid,
    compressed_count: 1,
    original_tokens: block.original_tokens,
    summary_tokens: block.summary_tokens,
    active: true,
  });
}

function optionalStringArray(value: unknown, field: string): string[] | undefined {
  if (value === undefined || value === null) return undefined;
  return requireStringArray(value, field);
}

function optionalBoolean(value: unknown, field: string): boolean | undefined {
  if (value === undefined || value === null) return undefined;
  if (typeof value !== "boolean") {
    throw new ValidationError(`${field} must be a boolean if provided`);
  }
  return value;
}

type SessionTurn = {
  index: number;
  turn: number;
  uuid: string;
  role: "user" | "assistant";
  messages: JsonlMessage[];
  text: string;
  preview: string;
};

function collectSessionTurns(messages: JsonlMessage[]): SessionTurn[] {
  const turns: SessionTurn[] = [];
  let currentMessages: JsonlMessage[] = [];
  let currentUuid: string | null = null;

  const flush = () => {
    if (currentUuid === null || currentMessages.length === 0) return;
    const first = currentMessages[0];
    if (first === undefined) return;
    const text = currentMessages
      .map((message) => messageContentToString(message.content))
      .filter((part) => part.trim().length > 0)
      .join("\n");
    turns.push({
      index: turns.length,
      turn: turns.length + 1,
      uuid: currentUuid,
      role: first.role,
      messages: currentMessages,
      text,
      preview: text.trim().length > 0 ? previewSnippet(text) : messageTagForTurn(first.role, currentMessages),
    });
  };

  for (const message of messages) {
    if (message.uuid !== currentUuid) {
      flush();
      currentUuid = message.uuid;
      currentMessages = [message];
      continue;
    }
    currentMessages.push(message);
  }
  flush();

  return turns;
}

function resolvePlacementAnchor(
  turns: SessionTurn[],
  turn: number | undefined,
  anchorUuid: string | undefined,
  phrase: string | undefined,
): { turn: SessionTurn } | { error: string } {
  if (turn !== undefined) {
    const selected = turns[turn - 1];
    if (selected === undefined) {
      return { error: `turn ${turn} is out of bounds (1-${turns.length})` };
    }
    return { turn: selected };
  }

  if (anchorUuid !== undefined) {
    const selected = turns.find((candidate) => candidate.uuid === anchorUuid);
    if (selected === undefined) {
      return { error: `anchor_uuid ${anchorUuid} was not found in the session JSONL` };
    }
    return { turn: selected };
  }

  if (phrase !== undefined) {
    const lower = phrase.toLowerCase();
    const matches = turns.filter((candidate) => candidate.text.toLowerCase().includes(lower));
    if (matches.length === 0) {
      return { error: `Could not find phrase "${phrase}" in any turn content` };
    }
    if (matches.length > 1) {
      return {
        error:
          `Phrase "${phrase}" matched multiple turns (${matches.map((m) => m.turn).join(", ")}). ` +
          "Placement requires a unique anchor.",
      };
    }
    const selected = matches[0];
    if (selected === undefined) {
      return { error: `Could not resolve phrase "${phrase}" to a unique turn` };
    }
    return { turn: selected };
  }

  return { error: "Must provide exactly one anchor selector: turn, anchor_uuid, or phrase" };
}

function messageTagForTurn(
  role: "user" | "assistant",
  messages: JsonlMessage[],
): string {
  const types: string[] = [];
  for (const message of messages) {
    if (!Array.isArray(message.content)) continue;
    for (const block of message.content) {
      if (typeof block !== "object" || block === null) continue;
      const record = block as Record<string, unknown>;
      const type = record["type"];
      if (typeof type !== "string") continue;
      if (type === "tool_use" && typeof record["name"] === "string") {
        types.push(`tool_use:${record["name"]}`);
        continue;
      }
      types.push(type);
    }
  }
  if (types.length === 0) return `[${role}]`;
  return `[${role} · ${types.join(", ")}]`;
}

async function readPlacementFile(path: string): Promise<string> {
  try {
    return await readFile(path, "utf-8");
  } catch (e) {
    const message = e instanceof Error ? e.message : String(e);
    throw new ValidationError(`could not read content_file ${path}: ${message}`);
  }
}

function findRangeByPhrase(
  messages: JsonlMessage[],
  firstPhrase: string,
  lastPhrase: string | undefined,
):
  | {
      startMatch: string;
      endMatch: string;
      startTurn: number;
      endTurn: number;
    }
  | { error: string } {
  // Find first occurrence of firstPhrase (case-insensitive substring match)
  const lowerFirst = firstPhrase.toLowerCase();
  let startIndex = -1;
  let startMatch = "";

  for (let i = 0; i < messages.length; i++) {
    const msg = messages[i];
    if (msg === undefined) continue;
    const content = messageContentToString(msg.content);
    if (content.toLowerCase().includes(lowerFirst)) {
      startIndex = i;
      startMatch = previewSnippet(content);
      break;
    }
  }

  if (startIndex === -1) {
    return { error: `Could not find first phrase "${firstPhrase}" in any message content` };
  }

  // Determine end index
  let endIndex = startIndex;
  if (lastPhrase !== undefined) {
    // Find first occurrence of lastPhrase at or after startIndex
    const lowerLast = lastPhrase.toLowerCase();
    for (let i = startIndex; i < messages.length; i++) {
      const msg = messages[i];
      if (msg === undefined) continue;
      const content = messageContentToString(msg.content);
      if (content.toLowerCase().includes(lowerLast)) {
        endIndex = i;
        break;
      }
    }
    const startMsg = messages[startIndex];
    if (endIndex === startIndex && startMsg !== undefined) {
      const startContent = messageContentToString(startMsg.content);
      if (!startContent.toLowerCase().includes(lowerLast)) {
        return { error: `Could not find last phrase "${lastPhrase}" in messages after first match` };
      }
    }
  } else {
    // Find the most recent user message
    for (let i = messages.length - 1; i >= startIndex; i--) {
      const msg = messages[i];
      if (msg === undefined) continue;
      if (msg.role === "user") {
        endIndex = i;
        break;
      }
    }
  }

  const startMsg = messages[startIndex];
  const endMsg = messages[endIndex];
  if (startMsg === undefined || endMsg === undefined) {
    return { error: "Could not resolve messages at matched positions" };
  }

  // Collect UUIDs in the range, collapsing consecutive duplicates
  const expandedUuidStream = collapseConsecutiveDuplicates(
    messages.map((m) => m.uuid)
  );

  // Find positions in the collapsed stream
  const startUuid = startMsg.uuid;
  const endUuid = endMsg.uuid;

  const startPos = expandedUuidStream.indexOf(startUuid);
  let endPos = -1;
  for (let i = expandedUuidStream.length - 1; i >= 0; i--) {
    if (expandedUuidStream[i] === endUuid) {
      endPos = i;
      break;
    }
  }

  if (startPos === -1 || endPos === -1 || startPos > endPos) {
    return { error: "Could not resolve contiguous range from phrase matches" };
  }

  const endContent = messageContentToString(endMsg.content);

  return {
    startMatch,
    endMatch: previewSnippet(endContent),
    startTurn: startPos + 1,
    endTurn: endPos + 1,
  };
}

type RangeClosureInfo = {
  extendedTurns: number[];
  note: string;
};

function closeRangeForToolPairs(
  messages: JsonlMessage[],
  startPos: number,
  endPos: number,
): { startPos: number; endPos: number; info: RangeClosureInfo | null } {
  const toolPairs = collectToolPairPositions(messages);
  let resolvedStart = startPos;
  let resolvedEnd = endPos;

  for (;;) {
    let nextStart = resolvedStart;
    let nextEnd = resolvedEnd;

    for (const pair of toolPairs) {
      const useInRange =
        pair.toolUseTurn >= resolvedStart && pair.toolUseTurn <= resolvedEnd;
      const resultInRange =
        pair.toolResultTurn >= resolvedStart && pair.toolResultTurn <= resolvedEnd;
      if (useInRange === resultInRange) continue;

      if (pair.toolUseTurn < nextStart) nextStart = pair.toolUseTurn;
      if (pair.toolResultTurn < nextStart) nextStart = pair.toolResultTurn;
      if (pair.toolUseTurn > nextEnd) nextEnd = pair.toolUseTurn;
      if (pair.toolResultTurn > nextEnd) nextEnd = pair.toolResultTurn;
    }

    if (nextStart === resolvedStart && nextEnd === resolvedEnd) {
      break;
    }
    resolvedStart = nextStart;
    resolvedEnd = nextEnd;
  }

  const notes: string[] = [];
  if (resolvedStart < startPos) {
    notes.push("extended backward to include an earlier paired tool_use");
  }
  if (resolvedEnd > endPos) {
    notes.push("extended forward to include a later paired tool_result");
  }

  const mediaExtension = extendRangeForChildMedia(messages, resolvedStart, resolvedEnd);
  resolvedEnd = mediaExtension.endPos;
  if (mediaExtension.info !== null) {
    notes.push(mediaExtension.info.note);
  }

  if (resolvedStart === startPos && resolvedEnd === endPos) {
    return { startPos: resolvedStart, endPos: resolvedEnd, info: null };
  }

  const extendedTurns: number[] = [];
  for (let turn = resolvedStart; turn < startPos; turn++) {
    extendedTurns.push(turn + 1);
  }
  for (let turn = endPos + 1; turn <= resolvedEnd; turn++) {
    extendedTurns.push(turn + 1);
  }

  return {
    startPos: resolvedStart,
    endPos: resolvedEnd,
    info: {
      extendedTurns,
      note: `Range auto-extended to keep related message fragments together: ${notes.join("; ")}.`,
    },
  };
}

function extendRangeForChildMedia(
  messages: JsonlMessage[],
  startPos: number,
  endPos: number,
): { endPos: number; info: RangeClosureInfo | null } {
  const turns = collectCollapsedTurnsMetadata(messages);
  let resolvedEnd = endPos;

  for (;;) {
    const nextTurn = turns[resolvedEnd + 1];
    if (nextTurn === undefined) break;
    if (!isMediaOnlyChildTurn(nextTurn, turns, startPos, resolvedEnd)) break;
    resolvedEnd += 1;
  }

  if (resolvedEnd === endPos) {
    return { endPos: resolvedEnd, info: null };
  }

  const extendedTurns: number[] = [];
  for (let turn = endPos + 1; turn <= resolvedEnd; turn++) {
    extendedTurns.push(turn + 1);
  }

  return {
    endPos: resolvedEnd,
    info: {
      extendedTurns,
      note: "extended forward to include a child media turn attached to the selected tool result",
    },
  };
}

type CollapsedTurnMetadata = {
  uuid: string;
  role: "user" | "assistant";
  parentUuid: string | null;
  messages: JsonlMessage[];
};

function collectCollapsedTurnsMetadata(messages: JsonlMessage[]): CollapsedTurnMetadata[] {
  const turns: CollapsedTurnMetadata[] = [];
  let current: CollapsedTurnMetadata | null = null;

  for (const message of messages) {
    if (current === null || current.uuid !== message.uuid) {
      current = {
        uuid: message.uuid,
        role: message.role,
        parentUuid: message.parent_uuid ?? null,
        messages: [message],
      };
      turns.push(current);
      continue;
    }

    if (current.parentUuid === null && typeof message.parent_uuid === "string") {
      current.parentUuid = message.parent_uuid;
    }
    current.messages.push(message);
  }

  return turns;
}

function isMediaOnlyChildTurn(
  turn: CollapsedTurnMetadata,
  turns: CollapsedTurnMetadata[],
  startPos: number,
  endPos: number,
): boolean {
  if (turn.role !== "user" || turn.parentUuid === null) return false;
  if (!turnContainsOnlyMedia(turn.messages)) return false;

  for (let i = startPos; i <= endPos; i++) {
    const candidate = turns[i];
    if (candidate !== undefined && candidate.uuid === turn.parentUuid) {
      return true;
    }
  }
  return false;
}

function turnContainsOnlyMedia(messages: JsonlMessage[]): boolean {
  let sawBlock = false;

  for (const message of messages) {
    if (!Array.isArray(message.content)) return false;
    for (const block of message.content) {
      if (typeof block !== "object" || block === null) return false;
      const type = (block as Record<string, unknown>)["type"];
      if (type !== "image" && type !== "document") {
        return false;
      }
      sawBlock = true;
    }
  }

  return sawBlock;
}

function collectToolPairPositions(
  messages: JsonlMessage[],
): Array<{ toolUseTurn: number; toolResultTurn: number }> {
  let turnPos = -1;
  let previousUuid: string | null = null;
  const toolUseTurns = new Map<string, number>();
  const toolResultTurns = new Map<string, number>();

  for (const message of messages) {
    if (message.uuid !== previousUuid) {
      turnPos += 1;
      previousUuid = message.uuid;
    }

    for (const block of contentBlocks(message.content)) {
      const toolUseId = toolUseBlockId(block);
      if (toolUseId !== null && !toolUseTurns.has(toolUseId)) {
        toolUseTurns.set(toolUseId, turnPos);
      }

      const toolResultId = toolResultBlockId(block);
      if (toolResultId !== null && !toolResultTurns.has(toolResultId)) {
        toolResultTurns.set(toolResultId, turnPos);
      }
    }
  }

  const pairs: Array<{ toolUseTurn: number; toolResultTurn: number }> = [];
  for (const [toolId, toolUseTurn] of toolUseTurns) {
    const toolResultTurn = toolResultTurns.get(toolId);
    if (toolResultTurn !== undefined) {
      pairs.push({ toolUseTurn, toolResultTurn });
    }
  }
  return pairs;
}

function contentBlocks(content: string | unknown[]): unknown[] {
  return Array.isArray(content) ? content : [];
}

function toolUseBlockId(block: unknown): string | null {
  if (!block || typeof block !== "object") return null;
  const record = block as Record<string, unknown>;
  return record["type"] === "tool_use" && typeof record["id"] === "string"
    ? record["id"]
    : null;
}

function toolResultBlockId(block: unknown): string | null {
  if (!block || typeof block !== "object") return null;
  const record = block as Record<string, unknown>;
  return record["type"] === "tool_result" && typeof record["tool_use_id"] === "string"
    ? record["tool_use_id"]
    : null;
}

function findRangePositions(
  collapsedUuids: string[],
  startUuid: string,
  endUuid: string,
): { startPos: number; endPos: number } | null {
  const startPos = collapsedUuids.indexOf(startUuid);
  let endPos = -1;
  for (let i = collapsedUuids.length - 1; i >= 0; i--) {
    if (collapsedUuids[i] === endUuid) {
      endPos = i;
      break;
    }
  }

  if (startPos === -1 || endPos === -1 || startPos > endPos) {
    return null;
  }
  return { startPos, endPos };
}

function previewSnippet(content: string): string {
  return content.substring(0, 100) + (content.length > 100 ? "..." : "");
}

function previewSnippetForUuid(
  messages: JsonlMessage[],
  uuid: string,
  fallback: string,
): string {
  const msg = messages.find((candidate) => candidate.uuid === uuid);
  return msg ? previewSnippet(messageContentToString(msg.content)) : fallback;
}

function estimateTokensForUuidRange(
  messages: JsonlMessage[],
  startUuid: string,
  endUuid: string,
): number {
  const firstMsgIdx = messages.findIndex((m) => m.uuid === startUuid);
  const lastMsgIdx = messages.findLastIndex((m: JsonlMessage) => m.uuid === endUuid);
  return estimateTokensForMessageIndexRange(messages, firstMsgIdx, lastMsgIdx);
}

function estimateTokensForMessageIndexRange(
  messages: JsonlMessage[],
  firstMsgIdx: number,
  lastMsgIdx: number,
): number {
  if (firstMsgIdx === -1 || lastMsgIdx === -1 || firstMsgIdx > lastMsgIdx) {
    return 0;
  }

  let tokenCount = 0;
  for (let i = firstMsgIdx; i <= lastMsgIdx; i++) {
    const msg = messages[i];
    if (msg) tokenCount += countContentTokens(msg.content);
  }

  return tokenCount;
}

async function validateCompressedRange(
  sessionId: string,
  compressedUuids: string[],
): Promise<string | null> {
  const { path, messages } = await loadSessionMessages(sessionId);
  if (path === null) {
    return `Session JSONL not found for session ${sessionId}`;
  }

  const expandedUuidStream = collapseConsecutiveDuplicates(
    messages.map((message) => message.uuid),
  );
  const uuidToIndex = new Map<string, number>();
  for (let i = 0; i < expandedUuidStream.length; i++) {
    const uuid = expandedUuidStream[i];
    if (uuid !== undefined && !uuidToIndex.has(uuid)) {
      uuidToIndex.set(uuid, i);
    }
  }

  const seen = new Set<string>();
  for (const uuid of compressedUuids) {
    if (seen.has(uuid)) {
      return `compressed_uuids contains duplicate UUID ${uuid}`;
    }
    seen.add(uuid);
    if (!uuidToIndex.has(uuid)) {
      return `compressed_uuids contains UUID ${uuid} that was not found in session ${sessionId}`;
    }
  }

  const firstUuid = compressedUuids[0];
  const lastUuid = compressedUuids[compressedUuids.length - 1];
  if (firstUuid === undefined || lastUuid === undefined) {
    return "compressed_uuids must contain at least one UUID";
  }
  const start = uuidToIndex.get(firstUuid);
  const end = uuidToIndex.get(lastUuid);
  if (start === undefined || end === undefined) {
    return "compressed_uuids could not be aligned to the session JSONL";
  }
  if (start > end) {
    return "compressed_uuids must be ordered chronologically";
  }

  const expectedRange = expandedUuidStream.slice(start, end + 1);
  if (expectedRange.length !== compressedUuids.length) {
    return (
      "compressed_uuids must describe a contiguous range in the session JSONL " +
      `(expected ${JSON.stringify(expectedRange)})`
    );
  }
  for (let i = 0; i < compressedUuids.length; i++) {
    if (compressedUuids[i] !== expectedRange[i]) {
      return (
        "compressed_uuids must describe a contiguous range in the session JSONL " +
        `(expected ${JSON.stringify(expectedRange)})`
      );
    }
  }

  return null;
}

function collapseConsecutiveDuplicates(values: string[]): string[] {
  const collapsed: string[] = [];
  for (const value of values) {
    if (collapsed[collapsed.length - 1] !== value) {
      collapsed.push(value);
    }
  }
  return collapsed;
}

export async function handleDecompress(args: unknown): Promise<ToolResult> {
  if (typeof args !== "object" || args === null) {
    return errorResult("arguments must be an object");
  }
  const a = args as Record<string, unknown>;

  let sessionId: string;
  let blockId: number;
  try {
    sessionId = await resolveSessionIdArg(a["session_id"]);
    blockId = requirePositiveInt(a["block_id"], "block_id");
  } catch (e) {
    if (e instanceof ValidationError) return errorResult(e.message);
    throw e;
  }

  const block = await setBlockActive(sessionId, blockId, false);
  if (block === null) {
    return errorResult(
      `Block ${blockId} not found in session ${sessionId}`,
    );
  }

  return successResult({
    block_id: block.block_id,
    active: block.active,
    messages_restored: block.compressed_uuids.length,
  });
}

export async function handleRecompress(args: unknown): Promise<ToolResult> {
  if (typeof args !== "object" || args === null) {
    return errorResult("arguments must be an object");
  }
  const a = args as Record<string, unknown>;

  let sessionId: string;
  let blockId: number;
  try {
    sessionId = await resolveSessionIdArg(a["session_id"]);
    blockId = requirePositiveInt(a["block_id"], "block_id");
  } catch (e) {
    if (e instanceof ValidationError) return errorResult(e.message);
    throw e;
  }

  const block = await setBlockActive(sessionId, blockId, true);
  if (block === null) {
    return errorResult(
      `Block ${blockId} not found in session ${sessionId}`,
    );
  }

  return successResult({
    block_id: block.block_id,
    active: block.active,
    messages_replaced: block.compressed_uuids.length,
  });
}

export async function handleList(args: unknown): Promise<ToolResult> {
  if (typeof args !== "object" || args === null) {
    return errorResult("arguments must be an object");
  }
  const a = args as Record<string, unknown>;

  let sessionId: string;
  try {
    sessionId = await resolveSessionIdArg(a["session_id"]);
  } catch (e) {
    if (e instanceof ValidationError) return errorResult(e.message);
    throw e;
  }

  const blocks = await listBlocks(sessionId);
  return successResult({
    session_id: sessionId,
    block_count: blocks.length,
    active_count: blocks.filter((b) => b.active).length,
    blocks: blocks.map(blockToMeta),
  });
}

// ----------------------------------------------------------------------------
// Bookmark handlers (start_arc / compress_arc)
// ----------------------------------------------------------------------------

export async function handleStartArc(args: unknown): Promise<ToolResult> {
  if (typeof args !== "object" || args === null) {
    return errorResult("arguments must be an object");
  }
  const a = args as Record<string, unknown>;

  let sessionId: string;
  let label: string;
  try {
    sessionId = await resolveSessionIdArg(a["session_id"]);
    label = requireNonEmptyString(a["label"], "label");
  } catch (e) {
    if (e instanceof ValidationError) return errorResult(e.message);
    throw e;
  }

  // Find the most recent JSONL entry; its UUID is the bookmark anchor.
  // At start_arc invocation time, the current assistant turn (containing
  // start_arc itself) has not yet been written to JSONL, so the latest
  // entry is the user message that triggered this turn.
  const { path, messages } = await loadSessionMessages(sessionId);
  if (path === null) {
    return errorResult(`Session JSONL not found for session ${sessionId}`);
  }
  if (messages.length === 0) {
    return errorResult(
      `Session JSONL for ${sessionId} contains no messages to anchor at`,
    );
  }

  const latest = messages[messages.length - 1];
  if (latest === undefined) {
    return errorResult("could not resolve the latest JSONL entry");
  }

  const bookmark = {
    label,
    anchor_uuid: latest.uuid,
    created_at: new Date().toISOString(),
  };
  await setBookmark(sessionId, bookmark);

  return successResult({
    label: bookmark.label,
    anchor_uuid: bookmark.anchor_uuid,
    created_at: bookmark.created_at,
  });
}

export async function handleCompressArc(args: unknown): Promise<ToolResult> {
  if (typeof args !== "object" || args === null) {
    return errorResult("arguments must be an object");
  }
  const a = args as Record<string, unknown>;

  let sessionId: string;
  let label: string;
  let summary: string;
  let focus: string | undefined;
  let originalTokens: number | undefined;
  let summaryTokens: number | undefined;
  try {
    sessionId = await resolveSessionIdArg(a["session_id"]);
    label = requireNonEmptyString(a["label"], "label");
    summary = requireNonEmptyString(a["summary"], "summary");
    focus = optionalString(a["focus"], "focus");
    originalTokens = optionalNonNegativeInt(a["original_tokens"], "original_tokens");
    summaryTokens = optionalNonNegativeInt(a["summary_tokens"], "summary_tokens");
  } catch (e) {
    if (e instanceof ValidationError) return errorResult(e.message);
    throw e;
  }

  const bookmark = await getBookmark(sessionId, label);
  if (bookmark === null) {
    return errorResult(
      `No bookmark labeled "${label}" found in session ${sessionId}. ` +
        `Call start_arc first.`,
    );
  }

  const { path, messages } = await loadSessionMessages(sessionId);
  if (path === null) {
    return errorResult(`Session JSONL not found for session ${sessionId}`);
  }

  // Find the bookmark's anchor in the JSONL.
  const anchorIdx = messages.findIndex((m) => m.uuid === bookmark.anchor_uuid);
  if (anchorIdx === -1) {
    return errorResult(
      `Bookmark "${label}" points at UUID ${bookmark.anchor_uuid}, ` +
        `which was not found in the session JSONL.`,
    );
  }

  // End of range: the most recent user message in the JSONL at this moment.
  // This naturally excludes the current assistant turn (the one containing
  // this compress_arc call and any preceding reflection text), since that
  // turn has not yet been written to JSONL.
  let endIdx = -1;
  for (let i = messages.length - 1; i >= anchorIdx; i--) {
    const msg = messages[i];
    if (msg !== undefined && msg.role === "user") {
      endIdx = i;
      break;
    }
  }
  if (endIdx === -1) {
    return errorResult(
      `Bookmark "${label}" has no user message after the anchor to close the range. ` +
        `Run some work that produces tool_results before calling compress_arc.`,
    );
  }

  // Collect the contiguous UUID range. Use the same collapse-consecutive
  // semantics as the existing validation logic to handle any sidechain
  // duplication in the JSONL, then run the same pair-closure pass that
  // ordinary compress uses so bookmark ranges cannot start/end on orphaned
  // tool_result/tool_use turns.
  const expandedUuidStream = collapseConsecutiveDuplicates(
    messages.map((m) => m.uuid),
  );
  const endUuid = messages[endIdx]?.uuid;
  if (endUuid === undefined) {
    return errorResult("could not resolve end UUID for the bookmarked range");
  }
  const selectedRange = findRangePositions(
    expandedUuidStream,
    bookmark.anchor_uuid,
    endUuid,
  );
  if (selectedRange === null) {
    return errorResult(
      "could not assemble a contiguous range from bookmark anchor to latest user message",
    );
  }
  const closedRange = closeRangeForToolPairs(
    messages,
    selectedRange.startPos,
    selectedRange.endPos,
  );
  const compressedUuids = expandedUuidStream.slice(
    closedRange.startPos,
    closedRange.endPos + 1,
  );

  // Build and persist the block (same shape as handleCompress).
  const blockId = await nextBlockId(sessionId);
  const anchorUuid = compressedUuids[0];
  const closedEndUuid = compressedUuids[compressedUuids.length - 1];
  if (anchorUuid === undefined) {
    return errorResult("bookmark range produced an empty UUID list");
  }
  const estimatedTokens =
    closedEndUuid === undefined
      ? 0
      : estimateTokensForUuidRange(messages, anchorUuid, closedEndUuid);

  const block: Block = {
    block_id: blockId,
    created_at: new Date().toISOString(),
    kind: "compression",
    active: true,
    anchor_uuid: anchorUuid,
    compressed_uuids: compressedUuids,
    summary,
    original_tokens: originalTokens ?? estimatedTokens,
    summary_tokens: summaryTokens ?? 0,
    focus: focus ?? null,
    parent_block_id: null,
  };

  await writeBlock(sessionId, block);

  // Remove the bookmark — its purpose is fulfilled. A future start_arc
  // with the same label is then unambiguous.
  await deleteBookmark(sessionId, label);

  return successResult({
    block_id: blockId,
    label,
    anchor_uuid: anchorUuid,
    compressed_count: compressedUuids.length,
    original_tokens: block.original_tokens,
    summary_tokens: block.summary_tokens,
    active: true,
  });
}

// ----------------------------------------------------------------------------
// Dispatch
// ----------------------------------------------------------------------------

export async function dispatch(
  name: string,
  args: unknown,
): Promise<ToolResult> {
  switch (name) {
    case "compress":
      return handleCompress(args);
    case "place":
      return handlePlace(args);
    case "decompress":
      return handleDecompress(args);
    case "recompress":
      return handleRecompress(args);
    case "list_compressions":
      return handleList(args);
    case "start_arc":
      return handleStartArc(args);
    case "compress_arc":
      return handleCompressArc(args);
    default:
      return errorResult(`Unknown tool: ${name}`);
  }
}
