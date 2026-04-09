import { appendFile, readFile, writeFile } from "fs/promises";

const TRANSCRIPT_TYPES = new Set([
  "assistant",
  "attachment",
  "progress",
  "system",
  "user",
]);

function splitJsonObjects(text) {
  const chunks = [];
  let start = -1;
  let depth = 0;
  let inString = false;
  let escaping = false;

  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];

    if (start === -1) {
      if (char === "{") {
        start = index;
        depth = 1;
        inString = false;
        escaping = false;
      }
      continue;
    }

    if (inString) {
      if (escaping) {
        escaping = false;
      } else if (char === "\\") {
        escaping = true;
      } else if (char === "\"") {
        inString = false;
      }
      continue;
    }

    if (char === "\"") {
      inString = true;
      continue;
    }

    if (char === "{") {
      depth += 1;
      continue;
    }

    if (char === "}") {
      depth -= 1;
      if (depth === 0) {
        chunks.push(text.slice(start, index + 1));
        start = -1;
      }
    }
  }

  if (start !== -1) {
    throw new Error("Unterminated JSON object while scanning transcript");
  }

  return chunks;
}

function parseJsonl(text) {
  return splitJsonObjects(text).map((chunk, index) => {
    try {
      return JSON.parse(chunk);
    } catch (error) {
      throw new Error(
        `Failed to parse JSON object ${index + 1}: ${error instanceof Error ? error.message : String(error)}`,
      );
    }
  });
}

function stringifyJsonl(entries) {
  if (entries.length === 0) return "";
  return `${entries.map((entry) => JSON.stringify(entry)).join("\n")}\n`;
}

function isTranscriptMessage(entry) {
  return (
    entry !== null &&
    typeof entry === "object" &&
    typeof entry.uuid === "string" &&
    TRANSCRIPT_TYPES.has(entry.type)
  );
}

function isCompactBoundary(entry) {
  return entry?.type === "system" && entry.subtype === "compact_boundary";
}

function filterPersistableMessages(messages) {
  return messages.filter((message) => {
    if (!message || typeof message !== "object") return false;
    if (!TRANSCRIPT_TYPES.has(message.type)) return false;
    return true;
  });
}

function buildMessageMap(entries) {
  const messages = new Map();
  for (const entry of entries) {
    if (isTranscriptMessage(entry)) {
      messages.set(entry.uuid, entry);
    }
  }
  return messages;
}

function pickLatestLeaf(leaves) {
  let latestLeaf = null;
  let latestTimestamp = Number.NEGATIVE_INFINITY;
  for (const leaf of leaves) {
    const timestamp = Date.parse(leaf.timestamp ?? "");
    const sortableTimestamp = Number.isFinite(timestamp) ? timestamp : 0;
    if (sortableTimestamp >= latestTimestamp) {
      latestLeaf = leaf;
      latestTimestamp = sortableTimestamp;
    }
  }
  return latestLeaf;
}

function buildActiveChain(messagesByUuid) {
  const values = [...messagesByUuid.values()].filter((message) => !message.isSidechain);
  const parentUuids = new Set(
    values.map((message) => message.parentUuid).filter((uuid) => uuid !== null && uuid !== undefined),
  );
  const leaves = values.filter((message) => !parentUuids.has(message.uuid));
  const latestLeaf = pickLatestLeaf(leaves);

  if (!latestLeaf) return [];

  const chain = [];
  const seen = new Set();
  let current = latestLeaf;

  while (current) {
    if (seen.has(current.uuid)) {
      throw new Error(`Detected parentUuid cycle at ${current.uuid}`);
    }
    seen.add(current.uuid);
    chain.push(current);
    current = current.parentUuid ? messagesByUuid.get(current.parentUuid) : undefined;
  }

  return chain.reverse();
}

function findMessageIndex(messages, uuid) {
  return messages.findIndex((message) => message.uuid === uuid);
}

function contentBlocks(message) {
  const content = message?.message?.content;
  return Array.isArray(content) ? content.filter((block) => block && typeof block === "object") : [];
}

function toolUseIds(message) {
  return new Set(
    contentBlocks(message)
      .filter((block) => block.type === "tool_use" && typeof block.id === "string")
      .map((block) => block.id),
  );
}

function toolResultIds(message) {
  return new Set(
    contentBlocks(message)
      .filter((block) => block.type === "tool_result" && typeof block.tool_use_id === "string")
      .map((block) => block.tool_use_id),
  );
}

function isToolResultOnlyUser(message) {
  if (message?.type !== "user") return false;
  const blocks = contentBlocks(message);
  return blocks.length > 0 && blocks.every((block) => block.type === "tool_result");
}

function isTurnStartUser(message) {
  return message?.type === "user" && !isToolResultOnlyUser(message);
}

function findPreviousTurnStartIndex(messages, startIndex) {
  for (let index = startIndex; index >= 0; index -= 1) {
    if (isTurnStartUser(messages[index])) {
      return index;
    }
  }
  return null;
}

function findNextTurnStartIndex(messages, startIndex) {
  for (let index = startIndex; index < messages.length; index += 1) {
    if (isTurnStartUser(messages[index])) {
      return index;
    }
  }
  return null;
}

export function expandSpliceRangeToTurnBoundaries(messages, startIndex, endIndex) {
  let adjustedStart = startIndex;
  let adjustedEnd = endIndex;

  let firstParticipantIndex = null;
  for (let index = startIndex; index < endIndex; index += 1) {
    const type = messages[index]?.type;
    if (type === "user" || type === "assistant") {
      firstParticipantIndex = index;
      break;
    }
  }

  if (firstParticipantIndex !== null) {
    const turnStartIndex = findPreviousTurnStartIndex(messages, firstParticipantIndex);
    if (
      turnStartIndex !== null &&
      turnStartIndex < firstParticipantIndex &&
      turnStartIndex < adjustedStart
    ) {
      adjustedStart = turnStartIndex;
    }
  }

  let lastParticipantIndex = null;
  for (let index = endIndex - 1; index >= startIndex; index -= 1) {
    const type = messages[index]?.type;
    if (type === "user" || type === "assistant") {
      lastParticipantIndex = index;
      break;
    }
  }

  if (lastParticipantIndex !== null) {
    const nextTurnStartIndex = findNextTurnStartIndex(messages, lastParticipantIndex + 1);
    if (nextTurnStartIndex !== null && nextTurnStartIndex > adjustedEnd) {
      adjustedEnd = nextTurnStartIndex;
    }
  }

  return {
    startIndex: adjustedStart,
    endIndex: adjustedEnd,
    anchorUuid: adjustedStart > 0 ? messages[adjustedStart - 1]?.uuid ?? null : null,
  };
}

export function validateToolResultIntegrity(messages) {
  const issues = [];
  for (let index = 0; index < messages.length; index += 1) {
    const current = messages[index];
    const resultIds = toolResultIds(current);
    if (resultIds.size === 0) continue;

    const previousToolUseIds = new Set();
    for (let cursor = index - 1; cursor >= 0; cursor -= 1) {
      const type = messages[cursor]?.type;
      if (type === "assistant") {
        for (const toolUseId of toolUseIds(messages[cursor])) {
          previousToolUseIds.add(toolUseId);
        }
        continue;
      }
      if (type === "user") {
        break;
      }
    }

    const missing = [...resultIds].filter((toolUseId) => !previousToolUseIds.has(toolUseId));
    if (missing.length > 0) {
      issues.push({
        index,
        uuid: current?.uuid ?? null,
        missingToolUseIds: missing,
      });
    }
  }
  return issues;
}

function buildDefaultContext({ sessionId, templateMessage, contextOverrides }) {
  return {
    sessionId,
    userType: templateMessage?.userType,
    entrypoint: templateMessage?.entrypoint,
    cwd: templateMessage?.cwd,
    version: templateMessage?.version,
    gitBranch: templateMessage?.gitBranch,
    slug: templateMessage?.slug,
    teamName: templateMessage?.teamName,
    agentName: templateMessage?.agentName,
    agentId: templateMessage?.agentId,
    ...contextOverrides,
  };
}

function applyDefaultFields(entry, context) {
  const output = { ...entry };
  output.sessionId = output.sessionId ?? context.sessionId;
  output.userType = output.userType ?? context.userType;
  output.entrypoint = output.entrypoint ?? context.entrypoint;
  output.cwd = output.cwd ?? context.cwd;
  output.version = output.version ?? context.version;
  output.gitBranch = output.gitBranch ?? context.gitBranch;
  output.slug = output.slug ?? context.slug;
  output.teamName = output.teamName ?? context.teamName;
  output.agentName = output.agentName ?? context.agentName;
  output.agentId = output.agentId ?? context.agentId;
  output.isSidechain = output.isSidechain ?? false;
  return output;
}

function parseTimestampMs(value) {
  const parsed = Date.parse(value ?? "");
  return Number.isFinite(parsed) ? parsed : null;
}

function formatTimestampMs(value) {
  return new Date(value).toISOString();
}

function seedLeadingTimestamps(
  entries,
  {
    anchorTimestamp = null,
    leadingRewriteCount = 0,
    nextBoundaryTimestamp = null,
  } = {},
) {
  const anchoredAt = parseTimestampMs(anchorTimestamp);
  const nextBoundaryAt = parseTimestampMs(nextBoundaryTimestamp);
  if (anchoredAt === null || nextBoundaryAt === null || nextBoundaryAt <= anchoredAt) {
    return entries;
  }
  if (leadingRewriteCount <= 0 || leadingRewriteCount >= entries.length + 1) {
    return entries;
  }

  const gapMs = nextBoundaryAt - anchoredAt;
  const stepMs = Math.floor(gapMs / (leadingRewriteCount + 1));
  if (stepMs < 1) {
    return entries;
  }

  return entries.map((entry, index) => {
    if (index >= leadingRewriteCount) {
      return entry;
    }
    return {
      ...entry,
      timestamp: formatTimestampMs(anchoredAt + stepMs * (index + 1)),
    };
  });
}

function normalizeChronologicalTimestamps(
  entries,
  {
    anchorTimestamp = null,
    leadingRewriteCount = 0,
    nextBoundaryTimestamp = null,
  } = {},
) {
  const seeded = seedLeadingTimestamps(entries, {
    anchorTimestamp,
    leadingRewriteCount,
    nextBoundaryTimestamp,
  });
  let previousMs = parseTimestampMs(anchorTimestamp);
  let fallbackMs = previousMs ?? Date.now();

  return seeded.map((entry) => {
    const normalized = { ...entry };
    let currentMs = parseTimestampMs(normalized.timestamp);
    if (currentMs === null) {
      currentMs = fallbackMs + 1;
    } else if (previousMs !== null && currentMs <= previousMs) {
      currentMs = previousMs + 1;
    }

    normalized.timestamp = formatTimestampMs(currentMs);
    previousMs = currentMs;
    fallbackMs = currentMs;
    return normalized;
  });
}

function serializeSuffix(
  messages,
  {
    anchorUuid,
    context,
    anchorTimestamp = null,
    leadingRewriteCount = 0,
    nextBoundaryTimestamp = null,
  },
) {
  let previousUuid = anchorUuid ?? null;

  const serialized = messages.map((message) => {
    const entry = applyDefaultFields(message, context);
    delete entry.parentUuid;
    delete entry.logicalParentUuid;

    let parentUuid = previousUuid;
    if (
      entry.type === "user" &&
      typeof entry.sourceToolAssistantUUID === "string" &&
      entry.sourceToolAssistantUUID.length > 0
    ) {
      parentUuid = entry.sourceToolAssistantUUID;
    }

    if (isCompactBoundary(entry)) {
      entry.parentUuid = null;
      if (previousUuid !== null) {
        entry.logicalParentUuid = previousUuid;
      }
    } else {
      entry.parentUuid = parentUuid ?? null;
    }

    previousUuid = entry.uuid;
    return entry;
  });

  return normalizeChronologicalTimestamps(serialized, {
    anchorTimestamp,
    leadingRewriteCount,
    nextBoundaryTimestamp,
  });
}

export async function planSuffixRewrite({
  transcriptPath,
  currentMessages,
  anchorUuid = null,
  sessionId,
  contextOverrides = {},
}) {
  if (!Array.isArray(currentMessages)) {
    throw new Error("currentMessages must be an array");
  }

  const text = await readFile(transcriptPath, "utf8");
  const parsedEntries = parseJsonl(text);
  const messagesByUuid = buildMessageMap(parsedEntries);
  const activeChain = buildActiveChain(messagesByUuid);

  if (activeChain.length === 0) {
    throw new Error("No active transcript chain found");
  }

  const persistableCurrentMessages = filterPersistableMessages(currentMessages);
  const requestedCurrentStartIndex =
    anchorUuid === null ? 0 : findMessageIndex(persistableCurrentMessages, anchorUuid) + 1;
  if (anchorUuid !== null && requestedCurrentStartIndex === 0) {
    throw new Error(`Anchor UUID ${anchorUuid} was not found in currentMessages`);
  }

  const adjustedRange = expandSpliceRangeToTurnBoundaries(
    persistableCurrentMessages,
    requestedCurrentStartIndex,
    persistableCurrentMessages.length,
  );
  const effectiveAnchorUuid = adjustedRange.anchorUuid;
  const currentStartIndex = adjustedRange.startIndex;
  if (effectiveAnchorUuid !== null && findMessageIndex(activeChain, effectiveAnchorUuid) === -1) {
    throw new Error(`Adjusted anchor UUID ${effectiveAnchorUuid} was not found in the persisted active chain`);
  }
  const oldStartIndex =
    effectiveAnchorUuid === null ? 0 : findMessageIndex(activeChain, effectiveAnchorUuid) + 1;

  const oldSuffix = activeChain.slice(oldStartIndex);
  const oldSuffixUuids = new Set(oldSuffix.map((message) => message.uuid));
  const retainedEntries = parsedEntries.filter((entry) => {
    return !(isTranscriptMessage(entry) && oldSuffixUuids.has(entry.uuid));
  });

  const suffixMessages = persistableCurrentMessages.slice(currentStartIndex);
  const templateMessage =
    activeChain[Math.max(oldStartIndex - 1, 0)] ?? activeChain[activeChain.length - 1];
  const effectiveSessionId =
    sessionId ?? templateMessage?.sessionId ?? activeChain[activeChain.length - 1]?.sessionId;

  if (!effectiveSessionId) {
    throw new Error("Unable to determine sessionId for suffix rewrite");
  }

  const serializedSuffix = serializeSuffix(suffixMessages, {
    anchorUuid: effectiveAnchorUuid,
    anchorTimestamp: templateMessage?.timestamp ?? null,
    context: buildDefaultContext({
      sessionId: effectiveSessionId,
      templateMessage,
      contextOverrides,
    }),
  });

  return {
    transcriptPath,
    anchorUuid: effectiveAnchorUuid,
    requestedAnchorUuid: anchorUuid,
    sessionId: effectiveSessionId,
    activeChain,
    oldSuffix,
    oldSuffixUuids: [...oldSuffixUuids],
    suffixMessages,
    serializedSuffix,
    retainedEntries,
    nextEntries: [...retainedEntries, ...serializedSuffix],
    toolIntegrityIssues: validateToolResultIntegrity([...retainedEntries, ...serializedSuffix]),
  };
}

export async function applySuffixRewrite(options) {
  const plan = await planSuffixRewrite(options);
  if (plan.toolIntegrityIssues.length > 0) {
    throw new Error(
      `Suffix rewrite would leave ${plan.toolIntegrityIssues.length} orphaned tool_result message(s); first issue at index ${plan.toolIntegrityIssues[0].index}`,
    );
  }
  await writeFile(plan.transcriptPath, stringifyJsonl(plan.nextEntries), "utf8");
  return {
    transcriptPath: plan.transcriptPath,
    sessionId: plan.sessionId,
    anchorUuid: plan.anchorUuid,
    removedUuidCount: plan.oldSuffixUuids.length,
    appendedCount: plan.serializedSuffix.length,
    removedUuids: plan.oldSuffixUuids,
    appendedUuids: plan.serializedSuffix.map((entry) => entry.uuid),
  };
}

function findLastNativeCompactBoundaryIndex(messages) {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const entry = messages[index];
    if (isCompactBoundary(entry) && entry?.compactMetadata?.preservedSegment) {
      return index;
    }
  }
  return -1;
}

export async function planNativeCompactShapeAppend({
  transcriptPath,
  currentMessages,
  sessionId,
  contextOverrides = {},
}) {
  if (!Array.isArray(currentMessages)) {
    throw new Error("currentMessages must be an array");
  }

  const text = await readFile(transcriptPath, "utf8");
  const parsedEntries = parseJsonl(text);
  const messagesByUuid = buildMessageMap(parsedEntries);
  const activeChain = buildActiveChain(messagesByUuid);
  if (activeChain.length === 0) {
    throw new Error("No active transcript chain found");
  }

  const persistableCurrentMessages = filterPersistableMessages(currentMessages);
  const boundaryIndex = findLastNativeCompactBoundaryIndex(persistableCurrentMessages);
  if (boundaryIndex === -1) {
    throw new Error(
      "No compact_boundary with preservedSegment found in currentMessages; native-compact-shape mode requires a compact-shaped live array",
    );
  }

  const boundary = persistableCurrentMessages[boundaryIndex];
  const preservedSegment = boundary?.compactMetadata?.preservedSegment;
  const preservedHeadUuid = preservedSegment?.headUuid ?? null;
  const preservedTailUuid = preservedSegment?.tailUuid ?? null;
  const anchorUuid = boundary?.logicalParentUuid ?? null;
  const preservedHeadIndex =
    preservedHeadUuid === null ? -1 : findMessageIndex(persistableCurrentMessages, preservedHeadUuid);

  if (preservedHeadUuid !== null && preservedHeadIndex === -1) {
    throw new Error(
      `preservedSegment.headUuid ${preservedHeadUuid} was not found in currentMessages`,
    );
  }

  const appendMessages =
    preservedHeadIndex === -1
      ? persistableCurrentMessages.slice(boundaryIndex)
      : persistableCurrentMessages.slice(boundaryIndex, preservedHeadIndex);

  if (appendMessages.length === 0) {
    throw new Error("No appendable compact-shape messages were found");
  }

  const templateMessage =
    anchorUuid === null
      ? activeChain[activeChain.length - 1]
      : activeChain[findMessageIndex(activeChain, anchorUuid)] ?? activeChain[activeChain.length - 1];
  const effectiveSessionId =
    sessionId ?? templateMessage?.sessionId ?? activeChain[activeChain.length - 1]?.sessionId;

  if (!effectiveSessionId) {
    throw new Error("Unable to determine sessionId for native compact-shape append");
  }

  const toolIntegrityIssues = validateToolResultIntegrity(persistableCurrentMessages);
  const serializedAppendEntries = serializeSuffix(appendMessages, {
    anchorUuid,
    anchorTimestamp: templateMessage?.timestamp ?? null,
    context: buildDefaultContext({
      sessionId: effectiveSessionId,
      templateMessage,
      contextOverrides,
    }),
  });

  return {
    mode: "native-compact-shape",
    transcriptPath,
    sessionId: effectiveSessionId,
    activeChain,
    anchorUuid,
    boundaryUuid: boundary?.uuid ?? null,
    preservedHeadUuid,
    preservedTailUuid,
    appendMessages,
    serializedAppendEntries,
    nextEntries: [...parsedEntries, ...serializedAppendEntries],
    toolIntegrityIssues,
  };
}

export async function applyNativeCompactShapeAppend(options) {
  const plan = await planNativeCompactShapeAppend(options);
  if (plan.toolIntegrityIssues.length > 0) {
    throw new Error(
      `Native compact-shape append would leave ${plan.toolIntegrityIssues.length} orphaned tool_result message(s); first issue at index ${plan.toolIntegrityIssues[0].index}`,
    );
  }
  await appendFile(plan.transcriptPath, stringifyJsonl(plan.serializedAppendEntries), "utf8");
  return {
    mode: plan.mode,
    transcriptPath: plan.transcriptPath,
    sessionId: plan.sessionId,
    anchorUuid: plan.anchorUuid,
    boundaryUuid: plan.boundaryUuid,
    preservedHeadUuid: plan.preservedHeadUuid,
    preservedTailUuid: plan.preservedTailUuid,
    appendedCount: plan.serializedAppendEntries.length,
    appendedUuids: plan.serializedAppendEntries.map((entry) => entry.uuid),
  };
}

function parseArgs(argv) {
  const args = {
    apply: false,
    anchorUuid: null,
    mode: "rewrite",
    messagesPath: null,
    summary: false,
    transcriptPath: null,
    sessionId: null,
  };

  for (let index = 0; index < argv.length; index += 1) {
    const token = argv[index];
    if (token === "--apply") {
      args.apply = true;
    } else if (token === "--mode") {
      args.mode = argv[index + 1] ?? null;
      index += 1;
    } else if (token === "--summary") {
      args.summary = true;
    } else if (token === "--transcript") {
      args.transcriptPath = argv[index + 1] ?? null;
      index += 1;
    } else if (token === "--messages") {
      args.messagesPath = argv[index + 1] ?? null;
      index += 1;
    } else if (token === "--anchor") {
      args.anchorUuid = argv[index + 1] ?? null;
      index += 1;
    } else if (token === "--session-id") {
      args.sessionId = argv[index + 1] ?? null;
      index += 1;
    } else if (token === "--from-start") {
      args.anchorUuid = null;
    } else if (token === "--help" || token === "-h") {
      args.help = true;
    } else {
      throw new Error(`Unknown argument: ${token}`);
    }
  }

  return args;
}

async function readMessagesJson(path) {
  const raw = await readFile(path, "utf8");
  const parsed = JSON.parse(raw);
  if (!Array.isArray(parsed)) {
    throw new Error("Messages JSON must be an array");
  }
  return parsed;
}

function summarizePlan(plan) {
  if (plan?.mode === "native-compact-shape") {
    return {
      mode: plan.mode,
      transcriptPath: plan.transcriptPath,
      sessionId: plan.sessionId,
      activeChainLength: plan.activeChain.length,
      anchorUuid: plan.anchorUuid,
      boundaryUuid: plan.boundaryUuid,
      preservedHeadUuid: plan.preservedHeadUuid,
      preservedTailUuid: plan.preservedTailUuid,
      appendedCount: plan.serializedAppendEntries.length,
      appendedFirstUuid: plan.serializedAppendEntries[0]?.uuid ?? null,
      appendedLastUuid:
        plan.serializedAppendEntries[plan.serializedAppendEntries.length - 1]?.uuid ?? null,
      toolIntegrityIssues: plan.toolIntegrityIssues.length,
    };
  }

  const activeLeaf = plan.activeChain[plan.activeChain.length - 1] ?? null;
  const oldSuffixFirst = plan.oldSuffix[0] ?? null;
  const oldSuffixLast = plan.oldSuffix[plan.oldSuffix.length - 1] ?? null;
  const newSuffixFirst = plan.serializedSuffix[0] ?? null;
  const newSuffixLast = plan.serializedSuffix[plan.serializedSuffix.length - 1] ?? null;

  return {
    transcriptPath: plan.transcriptPath,
    sessionId: plan.sessionId,
    anchorUuid: plan.anchorUuid,
    requestedAnchorUuid: plan.requestedAnchorUuid,
    activeChainLength: plan.activeChain.length,
    activeLeafUuid: activeLeaf?.uuid ?? null,
    removedUuidCount: plan.oldSuffixUuids.length,
    removedFirstUuid: oldSuffixFirst?.uuid ?? null,
    removedLastUuid: oldSuffixLast?.uuid ?? null,
    appendedCount: plan.serializedSuffix.length,
    appendedFirstUuid: newSuffixFirst?.uuid ?? null,
    appendedLastUuid: newSuffixLast?.uuid ?? null,
    nextEntryCount: plan.nextEntries.length,
    toolIntegrityIssues: plan.toolIntegrityIssues.length,
  };
}

function printHelp() {
  console.log(`Usage:
  node claude-code-splice-helper.mjs --transcript SESSION.jsonl --messages current-messages.json [--mode rewrite|native-compact-shape] [--anchor UUID|--from-start] [--session-id UUID] [--summary] [--apply]

Options:
  --transcript PATH   Path to the session transcript JSONL file
  --messages PATH     JSON file containing the post-mutation currentMessages array
  --mode MODE         rewrite (default) or native-compact-shape
  --anchor UUID       UUID of the last untouched message before the rewritten suffix
  --from-start        Rewrite the full active chain from the beginning
  --session-id UUID   Override the sessionId stamped onto appended entries
  --summary           Print a compact summary instead of the full plan/result object
  --apply             Rewrite the transcript in place; omit for dry-run plan output
  --help              Show this help text
`);
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    printHelp();
    return;
  }

  if (!args.transcriptPath || !args.messagesPath) {
    printHelp();
    process.exitCode = 1;
    return;
  }

  const currentMessages = await readMessagesJson(args.messagesPath);
  const mode = args.mode ?? "rewrite";
  const operations =
    mode === "native-compact-shape"
      ? {
          dryRun: planNativeCompactShapeAppend,
          apply: applyNativeCompactShapeAppend,
        }
      : mode === "rewrite"
        ? {
            dryRun: planSuffixRewrite,
            apply: applySuffixRewrite,
          }
        : null;

  if (!operations) {
    throw new Error(`Unknown helper mode: ${mode}`);
  }

  const operation = args.apply ? operations.apply : operations.dryRun;
  const result = await operation({
    transcriptPath: args.transcriptPath,
    currentMessages,
    anchorUuid: args.anchorUuid,
    sessionId: args.sessionId,
  });
  const output =
    args.summary && !args.apply
      ? summarizePlan(result)
      : result;
  console.log(JSON.stringify(output, null, 2));
}

if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((error) => {
    console.error(error instanceof Error ? error.stack ?? error.message : String(error));
    process.exit(1);
  });
}
