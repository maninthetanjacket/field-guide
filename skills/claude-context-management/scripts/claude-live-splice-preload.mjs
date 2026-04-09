import { createServer } from "net";
import { readFile, rm, unlink, writeFile } from "fs/promises";
import { existsSync } from "fs";
import { randomUUID } from "crypto";
import {
  expandSpliceRangeToTurnBoundaries,
  validateToolResultIntegrity,
} from "./claude-code-splice-helper.mjs";

const TRANSCRIPT_TYPES = new Set([
  "assistant",
  "attachment",
  "progress",
  "system",
  "user",
]);

const REGISTRY_PATH = "/tmp/claude-live-registry.json";
const SOCKET_PATH = `/tmp/claude-live-${process.pid}.sock`;
const CAPTURED_VALUE = Symbol("claudeLiveMutableMessages");
const CAPTURED_SESSIONS = new Map();
const CAPTURED_ARRAYS = new Map();
const STARTED_AT = new Date().toISOString();
const ENABLE_SERVER = process.env.CLAUDE_LIVE_DISABLE_SERVER !== "1";
const ENABLE_OBJECT_HOOK = process.env.CLAUDE_LIVE_DISABLE_OBJECT_HOOK !== "1";
const ENABLE_ARRAY_HOOK =
  process.env.CLAUDE_LIVE_ENABLE_ARRAY_HOOK === "1" &&
  process.env.CLAUDE_LIVE_DISABLE_ARRAY_HOOK !== "1";
const ENABLE_REACT_DEVTOOLS_HOOK = process.env.CLAUDE_LIVE_ENABLE_REACT_DEVTOOLS === "1";
const ENABLE_QUEUE_CAPTURE = process.env.CLAUDE_LIVE_ENABLE_QUEUE_CAPTURE === "1";
const ENABLE_REQUEST_CAPTURE = process.env.CLAUDE_LIVE_ENABLE_REQUEST_CAPTURE === "1";
const HELPER_PATH = new URL("./claude-code-splice-helper.mjs", import.meta.url).pathname;
let arrayHooksInstalled = false;
let originalArrayPush = null;
let originalArraySplice = null;
let captureArmed = false;
let captureAutoDisarmTimer = null;
const pendingCaptureWaiters = [];
const REACT_ROOTS = new Map();
const CAPTURED_REACT_STORES = new Map();
const REACT_CAPTURE_IDS_BY_DISPATCH = new WeakMap();
let reactHookInstalled = false;
let nextReactRendererId = 1;
let reactInjectCount = 0;
let reactCommitCount = 0;
let lastReactScanStats = {
  runs: 0,
  roots: 0,
  fibersVisited: 0,
  stateArrayHooks: 0,
  transcriptCandidates: 0,
};
const CAPTURED_QUEUE_STORES = new Map();
const QUEUE_CAPTURE_IDS_BY_QUEUE = new WeakMap();
let queueBindHookInstalled = false;
let originalFunctionBind = null;
let queueBindCaptureCount = 0;
const CAPTURED_API_REQUESTS = new Map();
let fetchHookInstalled = false;
let originalFetch = null;
let requestCaptureCount = 0;
let pendingRequestSplice = null;
const SPLICE_MODES = new Set([
  "memory-only",
  "native-compact-shape",
  "offline-rewrite",
]);

function isTranscriptMessage(entry) {
  return (
    entry !== null &&
    typeof entry === "object" &&
    typeof entry.uuid === "string" &&
    TRANSCRIPT_TYPES.has(entry.type)
  );
}

function filterPersistableMessages(messages) {
  return messages.filter((message) => isTranscriptMessage(message));
}

function isApiMessage(message) {
  return (
    message !== null &&
    typeof message === "object" &&
    (message.role === "user" || message.role === "assistant") &&
    "content" in message
  );
}

function filterApiMessages(messages) {
  if (!Array.isArray(messages)) return [];
  return messages.filter((message) => isApiMessage(message));
}

function hasConversationalMessages(messages) {
  return messages.some((message) => message.type === "user" || message.type === "assistant");
}

function hasConversationalApiMessages(messages) {
  return messages.some((message) => message.role === "user" || message.role === "assistant");
}

function isCompactBoundary(entry) {
  return entry?.type === "system" && entry.subtype === "compact_boundary";
}

function shellQuote(value) {
  return `'${String(value).replace(/'/g, `'\\''`)}'`;
}

function buildDefaultContext({ sessionId, templateMessage, contextOverrides = {} }) {
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

function deepCloneJson(value) {
  if (value === undefined) return undefined;
  if (value === null) return null;
  return JSON.parse(JSON.stringify(value));
}

function safeJsonParse(text) {
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
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

function createCompactBoundaryMessageLike({
  lastPreCompactMessageUuid = null,
  messagesSummarized = 0,
  trigger = "manual",
  userContext = undefined,
} = {}) {
  const boundary = {
    type: "system",
    subtype: "compact_boundary",
    content: "Conversation compacted",
    isMeta: false,
    timestamp: new Date().toISOString(),
    uuid: randomUUID(),
    level: "info",
    compactMetadata: {
      trigger,
      preTokens: 0,
      userContext,
      messagesSummarized,
    },
  };
  if (lastPreCompactMessageUuid) {
    boundary.logicalParentUuid = lastPreCompactMessageUuid;
  }
  return boundary;
}

function resolveSpliceMode(request) {
  const explicitMode = request.spliceMode ?? null;
  if (explicitMode !== null) {
    if (!SPLICE_MODES.has(explicitMode)) {
      throw new Error(
        `Unknown splice mode ${explicitMode}; expected one of ${[...SPLICE_MODES].join(", ")}`,
      );
    }
    return explicitMode;
  }

  if (request.persist === false || !request.transcriptPath) {
    return "memory-only";
  }

  throw new Error(
    "Live transcript writes are disabled. Choose spliceMode=offline-rewrite or spliceMode=native-compact-shape and use the exported messages file offline.",
  );
}

function defaultExportPath(spliceMode) {
  return `/tmp/claude-live-splice-${process.pid}-${Date.now()}-${spliceMode}.json`;
}

async function maybeWriteExportFile(exportPath, messages) {
  if (!exportPath) return null;
  await writeFile(exportPath, JSON.stringify(messages, null, 2) + "\n", "utf8");
  return exportPath;
}

function buildHelperCommand({
  helperMode,
  transcriptPath,
  exportPath,
  requestedAnchorUuid,
  sessionId,
}) {
  if (!transcriptPath || !exportPath) return null;

  const parts = [
    "node",
    shellQuote(HELPER_PATH),
    "--mode",
    shellQuote(helperMode),
    "--transcript",
    shellQuote(transcriptPath),
    "--messages",
    shellQuote(exportPath),
    sessionId ? `--session-id ${shellQuote(sessionId)}` : null,
    requestedAnchorUuid === null
      ? "--from-start"
      : `--anchor ${shellQuote(requestedAnchorUuid)}`,
    "--apply",
  ].filter(Boolean);

  return parts.join(" ");
}

function getSessionIdForInstance(instance) {
  try {
    const sessionId = instance?.getSessionId?.();
    if (typeof sessionId === "string" && sessionId.length > 0) return sessionId;
  } catch {}

  const messages = filterPersistableMessages(instance?.getMessages?.() ?? []);
  return messages[messages.length - 1]?.sessionId ?? null;
}

function summarizeSession(captureId, entry) {
  const messages = filterPersistableMessages(entry.instance.getMessages?.() ?? []);
  const latest = messages[messages.length - 1] ?? null;
  return {
    captureId,
    capturedAt: entry.capturedAt,
    sessionId: getSessionIdForInstance(entry.instance),
    messageCount: messages.length,
    latestUuid: latest?.uuid ?? null,
    latestType: latest?.type ?? null,
    agentName: latest?.agentName ?? null,
    cwd: latest?.cwd ?? null,
  };
}

async function loadRegistry() {
  try {
    const text = await readFile(REGISTRY_PATH, "utf8");
    const parsed = JSON.parse(text);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((entry) => {
      if (!entry || typeof entry !== "object") return false;
      if (typeof entry.socketPath !== "string" || entry.socketPath.length === 0) return false;
      return existsSync(entry.socketPath);
    });
  } catch {
    return [];
  }
}

async function writeRegistry(entries) {
  await writeFile(REGISTRY_PATH, JSON.stringify(entries, null, 2) + "\n", "utf8");
}

async function updateRegistry() {
  const entries = await loadRegistry();
  const next = entries.filter((entry) => entry?.pid !== process.pid);
  next.push({
    pid: process.pid,
    socketPath: SOCKET_PATH,
    startedAt: STARTED_AT,
    argv: process.argv.slice(0, 8),
    version: process.versions?.bun ?? null,
  });
  await writeRegistry(next);
}

async function removeFromRegistry() {
  const entries = await loadRegistry();
  const next = entries.filter((entry) => entry?.pid !== process.pid);
  await writeRegistry(next);
}

function captureSession(instance, value) {
  if (!Array.isArray(value)) return;
  if (
    typeof instance?.submitMessage !== "function" ||
    typeof instance?.getMessages !== "function" ||
    typeof instance?.getSessionId !== "function"
  ) {
    return;
  }

  for (const [captureId, entry] of CAPTURED_SESSIONS.entries()) {
    if (entry.instance === instance) {
      entry.lastSeenAt = new Date().toISOString();
      return captureId;
    }
  }

  const captureId = `capture-${CAPTURED_SESSIONS.size + 1}`;
  CAPTURED_SESSIONS.set(captureId, {
    instance,
    capturedAt: new Date().toISOString(),
    lastSeenAt: new Date().toISOString(),
  });
  return captureId;
}

function captureArray(array) {
  if (!Array.isArray(array)) return null;
  const messages = filterPersistableMessages(array);
  if (messages.length === 0) return null;
  if (!messages.some((message) => message.type === "user" || message.type === "assistant")) {
    return null;
  }

  for (const [captureId, entry] of CAPTURED_ARRAYS.entries()) {
    if (entry.array === array) {
      entry.lastSeenAt = new Date().toISOString();
      entry.pushCount = (entry.pushCount ?? 1) + 1;
      return captureId;
    }
  }

  const captureId = `array-${CAPTURED_ARRAYS.size + 1}`;
  CAPTURED_ARRAYS.set(captureId, {
    array,
    capturedAt: new Date().toISOString(),
    lastSeenAt: new Date().toISOString(),
    pushCount: 1,
  });

  if (pendingCaptureWaiters.length > 0) {
    const entry = CAPTURED_ARRAYS.get(captureId);
    const summary = buildCaptureSummary(captureId, { kind: "array", ...entry });
    for (const waiter of pendingCaptureWaiters.splice(0)) {
      clearTimeout(waiter.timer);
      waiter.resolve(summary);
    }
  }

  return captureId;
}

function maybeCaptureMessageArray(array, insertedValues) {
  if (!captureArmed) return;
  if (!Array.isArray(array)) return;
  if (!insertedValues.some((value) => isTranscriptMessage(value))) return;
  captureArray(array);
}

function analyzeMessages(messages) {
  const latest = messages[messages.length - 1] ?? null;
  const userCount = messages.filter((message) => message.type === "user").length;
  const assistantCount = messages.filter((message) => message.type === "assistant").length;
  const attachmentCount = messages.filter((message) => message.type === "attachment").length;

  return {
    latest,
    userCount,
    assistantCount,
    attachmentCount,
    messageCount: messages.length,
  };
}

function analyzeApiMessages(messages) {
  const latest = messages[messages.length - 1] ?? null;
  const userCount = messages.filter((message) => message.role === "user").length;
  const assistantCount = messages.filter((message) => message.role === "assistant").length;
  return {
    latest,
    userCount,
    assistantCount,
    attachmentCount: 0,
    messageCount: messages.length,
  };
}

function summarizeRequestPayload(captureId, entry) {
  const messages = filterApiMessages(entry.messages ?? []);
  const analysis = analyzeApiMessages(messages);
  return {
    kind: "request-payload",
    captureId,
    capturedAt: entry.capturedAt,
    lastSeenAt: entry.lastSeenAt ?? null,
    url: entry.url ?? null,
    path: entry.path ?? null,
    method: entry.method ?? "POST",
    model: entry.model ?? null,
    stream: Boolean(entry.stream),
    messageCount: analysis.messageCount,
    userCount: analysis.userCount,
    assistantCount: analysis.assistantCount,
    latestRole: analysis.latest?.role ?? null,
    systemBlockCount: entry.systemBlockCount ?? 0,
    toolCount: entry.toolCount ?? 0,
    betaCount: entry.betaCount ?? 0,
    clientRequestId: entry.clientRequestId ?? null,
    overrideApplied: Boolean(entry.overrideApplied),
    score: scoreCapture({ kind: "request-payload", ...entry }),
  };
}

function buildCaptureSummary(captureId, entry) {
  if (entry.kind === "request-payload") {
    const messages = filterApiMessages(getMessagesFromCapture(entry));
    const analysis = analyzeApiMessages(messages);
    return {
      captureId,
      kind: "request-payload",
      sessionId: null,
      messageCount: analysis.messageCount,
      latestUuid: null,
      score: scoreCapture(entry),
    };
  }

  const messages = filterPersistableMessages(getMessagesFromCapture(entry));
  const analysis = analyzeMessages(messages);
  return {
    captureId,
    kind: entry.kind ?? "array",
    sessionId: analysis.latest?.sessionId ?? null,
    messageCount: analysis.messageCount,
    latestUuid: analysis.latest?.uuid ?? null,
    score: scoreCapture(entry),
  };
}

function scoreCapture(entry) {
  if (entry.kind === "request-payload") {
    const messages = filterApiMessages(getMessagesFromCapture(entry));
    const analysis = analyzeApiMessages(messages);
    let score = analysis.messageCount * 20;
    if (analysis.userCount > 0) score += 10;
    if (analysis.assistantCount > 0) score += 10;
    if (entry.stream) score += 50;
    score += Math.min(Number(entry.toolCount ?? 0), 100);
    if (entry.overrideApplied) score += 25;
    return score;
  }

  const messages = filterPersistableMessages(getMessagesFromCapture(entry));
  const analysis = analyzeMessages(messages);
  let score = analysis.messageCount * 100;
  if (analysis.userCount > 0) score += 25;
  if (analysis.assistantCount > 0) score += 25;
  if (analysis.attachmentCount > 0) score += 5;
  if (analysis.latest?.sessionId) score += 10;
  if (entry.kind === "react-store") {
    if (entry.refObject) score += 5000;
    if (/repl/i.test(String(entry.fiberName ?? ""))) score += 10000;
    const commitCount = entry.commitCount ?? 1;
    score += Math.min(commitCount, 50) * 100;
    return score;
  }
  if (entry.kind === "queue-store") {
    if (entry.refObject) score += 7500;
    if (/repl/i.test(String(entry.fiberName ?? ""))) score += 10000;
    const bindCount = entry.bindCount ?? 1;
    score += Math.min(bindCount, 50) * 50;
    return score;
  }
  // Strongly prefer arrays seen across multiple push operations — the real
  // mutableMessages array grows every turn; transient arrays are seen once.
  const pushCount = entry.pushCount ?? 1;
  score += Math.min(pushCount, 20) * 50;
  return score;
}

function isAnthropicMessagesPayload(payload) {
  return (
    payload !== null &&
    typeof payload === "object" &&
    Array.isArray(payload.messages) &&
    typeof payload.model === "string"
  );
}

function urlPathname(value) {
  try {
    return new URL(String(value)).pathname;
  } catch {
    return String(value);
  }
}

function looksLikeMessagesEndpoint(url) {
  const path = urlPathname(url);
  return /\/messages(?:$|[/?])/.test(path);
}

function getRequestUrl(input) {
  try {
    if (typeof Request !== "undefined" && input instanceof Request) {
      return input.url;
    }
  } catch {}
  return String(input);
}

function getRequestMethod(input, init) {
  try {
    if (typeof init?.method === "string" && init.method.length > 0) return init.method.toUpperCase();
    if (typeof Request !== "undefined" && input instanceof Request) {
      return String(input.method || "GET").toUpperCase();
    }
  } catch {}
  return "GET";
}

function getRequestHeaders(input, init) {
  try {
    if (typeof Headers !== "undefined") {
      const base =
        init?.headers ??
        (() => {
          try {
            if (typeof Request !== "undefined" && input instanceof Request) return input.headers;
          } catch {}
          return undefined;
        })();
      return new Headers(base);
    }
  } catch {}
  return null;
}

async function readRequestBodyText(input, init) {
  if (typeof init?.body === "string") return init.body;

  if (typeof Buffer !== "undefined" && Buffer.isBuffer(init?.body)) {
    return init.body.toString("utf8");
  }

  if (init?.body instanceof Uint8Array) {
    return new TextDecoder().decode(init.body);
  }

  try {
    if (typeof Request !== "undefined" && input instanceof Request) {
      return await input.clone().text();
    }
  } catch {}

  return null;
}

function buildRequestWindow(messages, startIndex, deleteCount) {
  const width = Math.max(deleteCount, 1) + 2;
  const matchStart = Math.max(0, startIndex - 1);
  return {
    matchStart,
    expectedWindow: deepCloneJson(messages.slice(matchStart, Math.min(messages.length, matchStart + width))),
  };
}

function createRequestSplicePlan({ captureId, captureEntry, startIndex, deleteCount, replacementMessages, applyOnce = true }) {
  const currentMessages = filterApiMessages(captureEntry.messages ?? []);
  if (startIndex < 0 || startIndex > currentMessages.length) {
    throw new Error(`Invalid request splice start index ${startIndex} for ${currentMessages.length} API messages`);
  }
  if (deleteCount < 0 || startIndex + deleteCount > currentMessages.length) {
    throw new Error(`Invalid request splice range ${startIndex}..${startIndex + deleteCount}`);
  }

  const { matchStart, expectedWindow } = buildRequestWindow(currentMessages, startIndex, deleteCount);
  return {
    kind: "request-splice",
    armedAt: new Date().toISOString(),
    captureId,
    applyOnce,
    startIndex,
    deleteCount,
    baseMessageCount: currentMessages.length,
    replacementMessages: deepCloneJson(replacementMessages),
    expectedWindow,
    matchStart,
    applyCount: 0,
    lastAppliedAt: null,
    lastAppliedRequestCaptureId: null,
    lastSkipAt: null,
    lastSkipReason: null,
    model: captureEntry.model ?? null,
  };
}

function tryApplyPendingRequestSplice(payload) {
  if (!pendingRequestSplice) {
    return {
      payload,
      applied: false,
      skipReason: null,
    };
  }

  const currentMessages = filterApiMessages(payload.messages);
  const plan = pendingRequestSplice;
  if (currentMessages.length < plan.startIndex) {
    plan.lastSkipAt = new Date().toISOString();
    plan.lastSkipReason = `current message count ${currentMessages.length} is smaller than planned start index ${plan.startIndex}`;
    return {
      payload,
      applied: false,
      skipReason: plan.lastSkipReason,
    };
  }

  const currentWindow = currentMessages.slice(
    plan.matchStart,
    plan.matchStart + (plan.expectedWindow?.length ?? 0),
  );
  if (
    Array.isArray(plan.expectedWindow) &&
    plan.expectedWindow.length > 0 &&
    JSON.stringify(currentWindow) !== JSON.stringify(plan.expectedWindow)
  ) {
    plan.lastSkipAt = new Date().toISOString();
    plan.lastSkipReason = "current API message window no longer matches the armed splice baseline";
    return {
      payload,
      applied: false,
      skipReason: plan.lastSkipReason,
    };
  }

  const nextMessages = [
    ...currentMessages.slice(0, plan.startIndex),
    ...deepCloneJson(plan.replacementMessages),
    ...currentMessages.slice(plan.startIndex + plan.deleteCount),
  ];
  pendingRequestSplice = {
    ...plan,
    applyCount: plan.applyCount + 1,
    lastAppliedAt: new Date().toISOString(),
    lastSkipReason: null,
  };

  const nextPayload = {
    ...payload,
    messages: nextMessages,
  };

  const result = {
    payload: nextPayload,
    applied: true,
    skipReason: null,
  };

  if (plan.applyOnce) {
    pendingRequestSplice = {
      ...pendingRequestSplice,
      appliedAndDisarmed: true,
    };
  }

  return result;
}

function finalizePendingRequestSpliceAfterCapture(captureId) {
  if (!pendingRequestSplice) return;
  if (pendingRequestSplice.lastAppliedAt === null) return;
  pendingRequestSplice = {
    ...pendingRequestSplice,
    lastAppliedRequestCaptureId: captureId,
  };
  if (pendingRequestSplice.applyOnce) {
    pendingRequestSplice = null;
  }
}

function captureApiRequestPayload(payload, meta = {}) {
  const messages = filterApiMessages(payload.messages);
  if (messages.length === 0 || !hasConversationalApiMessages(messages)) return null;

  const captureId = `request-${CAPTURED_API_REQUESTS.size + 1}`;
  const entry = {
    capturedAt: new Date().toISOString(),
    lastSeenAt: new Date().toISOString(),
    url: meta.url ?? null,
    path: meta.path ?? null,
    method: meta.method ?? "POST",
    model: payload.model ?? null,
    stream: Boolean(payload.stream),
    messages: deepCloneJson(messages),
    system: deepCloneJson(payload.system ?? null),
    systemBlockCount: Array.isArray(payload.system) ? payload.system.length : payload.system ? 1 : 0,
    toolCount: Array.isArray(payload.tools) ? payload.tools.length : 0,
    betaCount: Array.isArray(payload.betas) ? payload.betas.length : 0,
    clientRequestId: meta.clientRequestId ?? null,
    overrideApplied: Boolean(meta.overrideApplied),
    requestSpliceSkipped: meta.requestSpliceSkipped ?? null,
  };
  CAPTURED_API_REQUESTS.set(captureId, entry);
  requestCaptureCount += 1;
  return captureId;
}

function getFiberDisplayName(fiber) {
  return (
    fiber?.elementType?.displayName ??
    fiber?.elementType?.name ??
    fiber?.type?.displayName ??
    fiber?.type?.name ??
    null
  );
}

function looksLikeRefObject(value) {
  return value !== null && typeof value === "object" && Object.prototype.hasOwnProperty.call(value, "current");
}

function looksLikeFiberObject(value) {
  return (
    value !== null &&
    typeof value === "object" &&
    typeof value.tag === "number" &&
    "memoizedState" in value &&
    "alternate" in value
  );
}

function looksLikeStateQueue(value) {
  return (
    value !== null &&
    typeof value === "object" &&
    "pending" in value &&
    "lanes" in value &&
    "dispatch" in value &&
    "lastRenderedState" in value &&
    "lastRenderedReducer" in value
  );
}

function getQueueRefCandidatesFromHooks(hooks, stateArray) {
  const candidates = [];
  for (let index = 0; index < hooks.length; index += 1) {
    const value = hooks[index]?.memoizedState;
    if (!looksLikeRefObject(value)) continue;
    const current = value.current;
    if (!Array.isArray(current)) continue;
    const messages = filterPersistableMessages(current);
    if (messages.length === 0) continue;
    const analysis = analyzeMessages(messages);
    candidates.push({
      hookIndex: index,
      messageCount: analysis.messageCount,
      userCount: analysis.userCount,
      assistantCount: analysis.assistantCount,
      attachmentCount: analysis.attachmentCount,
      latestUuid: analysis.latest?.uuid ?? null,
      latestType: analysis.latest?.type ?? null,
      exactStateMatch: current === stateArray,
      refObject: value,
    });
  }
  candidates.sort((a, b) => {
    if (a.exactStateMatch !== b.exactStateMatch) {
      return Number(b.exactStateMatch) - Number(a.exactStateMatch);
    }
    return b.messageCount - a.messageCount;
  });
  return candidates;
}

function inspectQueueOnFiber(fiber, queue, stateArray) {
  if (!looksLikeFiberObject(fiber)) return null;

  const hooks = [];
  let hook = fiber.memoizedState;
  let guard = 0;
  while (hook && typeof hook === "object" && guard < 512) {
    hooks.push(hook);
    hook = hook.next;
    guard += 1;
  }

  const queueHookIndex = hooks.findIndex((candidate) => candidate?.queue === queue);
  if (queueHookIndex === -1) return null;

  const refCandidates = getQueueRefCandidatesFromHooks(hooks, stateArray);
  const bestRef = refCandidates[0] ?? null;

  return {
    fiber,
    fiberName: getFiberDisplayName(fiber),
    queueHookIndex,
    refHookIndex: bestRef?.hookIndex ?? null,
    refObject: bestRef?.refObject ?? null,
    refCandidates,
  };
}

function getFiberRoot(fiber) {
  if (!looksLikeFiberObject(fiber)) return null;
  let cursor = fiber;
  let guard = 0;
  while (cursor?.return && guard < 1024) {
    cursor = cursor.return;
    guard += 1;
  }
  if (cursor?.stateNode?.current) return cursor.stateNode.current;
  return cursor;
}

function buildFiberPath(fiber) {
  if (!looksLikeFiberObject(fiber)) return null;
  const parts = [];
  let cursor = fiber;
  let guard = 0;
  while (cursor && typeof cursor === "object" && guard < 64) {
    parts.push(getFiberDisplayName(cursor) ?? `tag:${cursor.tag}`);
    cursor = cursor.return;
    guard += 1;
  }
  return parts.reverse().join(" > ");
}

function findQueueInFiberTree(root, queue, stateArray) {
  const start = root?.current ?? root;
  if (!looksLikeFiberObject(start)) return null;

  const stack = [start];
  const seen = new Set();
  while (stack.length > 0) {
    const fiber = stack.pop();
    if (!looksLikeFiberObject(fiber) || seen.has(fiber)) continue;
    seen.add(fiber);

    if (fiber.sibling) stack.push(fiber.sibling);
    if (fiber.child) stack.push(fiber.child);

    const match = inspectQueueOnFiber(fiber, queue, stateArray);
    if (match) {
      return {
        ...match,
        ownerPath: buildFiberPath(fiber),
        rootFiberName: getFiberDisplayName(start),
      };
    }
  }

  return null;
}

function applyQueueResolution(entry, resolution) {
  entry.resolvedFiber = resolution?.fiber ?? entry.resolvedFiber ?? null;
  entry.fiberName = resolution?.fiberName ?? entry.fiberName ?? null;
  entry.queueHookIndex = resolution?.queueHookIndex ?? null;
  entry.refHookIndex = resolution?.refHookIndex ?? null;
  entry.refObject = resolution?.refObject ?? null;
  entry.refCandidates = (resolution?.refCandidates ?? []).slice(0, 5).map((candidate) => ({
    hookIndex: candidate.hookIndex,
    messageCount: candidate.messageCount,
    userCount: candidate.userCount,
    assistantCount: candidate.assistantCount,
    attachmentCount: candidate.attachmentCount,
    latestUuid: candidate.latestUuid,
    latestType: candidate.latestType,
    exactStateMatch: candidate.exactStateMatch,
  }));
  entry.ownerPath = resolution?.ownerPath ?? null;
  entry.rootFiberName = resolution?.rootFiberName ?? null;
  entry.rootResolved = Boolean(resolution?.rootFiberName);
}

function resolveQueueStoreFiber(entry, stateArray) {
  const directCandidates = [
    entry.resolvedFiber,
    entry.resolvedFiber?.alternate,
    entry.fiber,
    entry.fiber?.alternate,
  ].filter(Boolean);

  for (const fiber of directCandidates) {
    const match = inspectQueueOnFiber(fiber, entry.queue, stateArray);
    if (match) {
      applyQueueResolution(entry, {
        ...match,
        ownerPath: buildFiberPath(match.fiber),
        rootFiberName: entry.rootFiberName ?? getFiberDisplayName(getFiberRoot(match.fiber)),
      });
      return;
    }
  }

  const roots = [];
  const seenRoots = new Set();
  for (const fiber of directCandidates) {
    const root = getFiberRoot(fiber);
    if (!root || seenRoots.has(root)) continue;
    seenRoots.add(root);
    roots.push(root);
  }

  for (const root of roots) {
    const match = findQueueInFiberTree(root, entry.queue, stateArray);
    if (match) {
      applyQueueResolution(entry, match);
      return;
    }
  }

  applyQueueResolution(entry, null);
}

function getReactStoreMessages(entry) {
  const fromRef = entry.refObject?.current;
  if (Array.isArray(fromRef)) return fromRef;
  if (Array.isArray(entry.stateArray)) return entry.stateArray;
  return [];
}

function summarizeReactStore(captureId, entry) {
  const messages = filterPersistableMessages(getReactStoreMessages(entry));
  const analysis = analyzeMessages(messages);
  return {
    kind: "react-store",
    captureId,
    capturedAt: entry.capturedAt,
    sessionId: analysis.latest?.sessionId ?? null,
    messageCount: analysis.messageCount,
    userCount: analysis.userCount,
    assistantCount: analysis.assistantCount,
    attachmentCount: analysis.attachmentCount,
    latestUuid: analysis.latest?.uuid ?? null,
    latestType: analysis.latest?.type ?? null,
    fiberName: entry.fiberName ?? null,
    refMirrored: Boolean(entry.refObject),
    score: scoreCapture({ kind: "react-store", ...entry }),
  };
}

function refreshQueueStoreEntry(entry) {
  const stateArray = Array.isArray(entry.queue?.lastRenderedState) ? entry.queue.lastRenderedState : null;
  entry.stateArray = stateArray;
  entry.fiberName = getFiberDisplayName(entry.fiber) ?? getFiberDisplayName(entry.fiber?.alternate) ?? null;
  entry.refObject = null;
  entry.queueHookIndex = null;
  entry.refHookIndex = null;
  entry.refCandidates = [];
  entry.ownerPath = null;
  entry.rootFiberName = null;
  entry.rootResolved = false;

  if (!stateArray) return;

  const messages = filterPersistableMessages(stateArray);
  if (messages.length === 0 && !entry.resolvedFiber) return;
  resolveQueueStoreFiber(entry, stateArray);
}

function getQueueStoreMessages(entry) {
  refreshQueueStoreEntry(entry);
  const fromRef = entry.refObject?.current;
  if (Array.isArray(fromRef)) return fromRef;
  if (Array.isArray(entry.stateArray)) return entry.stateArray;
  return [];
}

function summarizeQueueStore(captureId, entry) {
  const messages = filterPersistableMessages(getQueueStoreMessages(entry));
  const analysis = analyzeMessages(messages);
  return {
    kind: "queue-store",
    captureId,
    capturedAt: entry.capturedAt,
    sessionId: analysis.latest?.sessionId ?? null,
    messageCount: analysis.messageCount,
    userCount: analysis.userCount,
    assistantCount: analysis.assistantCount,
    attachmentCount: analysis.attachmentCount,
    latestUuid: analysis.latest?.uuid ?? null,
    latestType: analysis.latest?.type ?? null,
    fiberName: entry.fiberName ?? null,
    sourceFnName: entry.sourceFnName ?? null,
    bindCount: entry.bindCount ?? 0,
    queueHookIndex: entry.queueHookIndex ?? null,
    refHookIndex: entry.refHookIndex ?? null,
    ownerPath: entry.ownerPath ?? null,
    rootFiberName: entry.rootFiberName ?? null,
    rootResolved: Boolean(entry.rootResolved),
    refMirrored: Boolean(entry.refObject),
    refCandidates: entry.refCandidates ?? [],
    score: scoreCapture({ kind: "queue-store", ...entry }),
  };
}

function captureQueueStoreBinding(dispatch, fiber, queue, sourceFnName) {
  let captureId = QUEUE_CAPTURE_IDS_BY_QUEUE.get(queue) ?? null;
  if (!captureId) {
    captureId = `queue-${CAPTURED_QUEUE_STORES.size + 1}`;
    QUEUE_CAPTURE_IDS_BY_QUEUE.set(queue, captureId);
    CAPTURED_QUEUE_STORES.set(captureId, {
      capturedAt: new Date().toISOString(),
      bindCount: 0,
    });
  }

  const entry = CAPTURED_QUEUE_STORES.get(captureId);
  entry.dispatch = dispatch;
  entry.queue = queue;
  entry.fiber = fiber;
  entry.sourceFnName = sourceFnName ?? null;
  entry.lastSeenAt = new Date().toISOString();
  entry.bindCount = (entry.bindCount ?? 0) + 1;
  refreshQueueStoreEntry(entry);
  return captureId;
}

function installQueueCaptureHook() {
  if (queueBindHookInstalled) return;

  originalFunctionBind = Function.prototype.bind;
  Function.prototype.bind = function patchedBind(...args) {
    const bound = originalFunctionBind.apply(this, args);
    try {
      const fiber = args[1];
      const queue = args[2];
      if (looksLikeFiberObject(fiber) && looksLikeStateQueue(queue) && typeof bound === "function") {
        queueBindCaptureCount += 1;
        captureQueueStoreBinding(bound, fiber, queue, this.name ?? null);
      }
    } catch {}
    return bound;
  };

  queueBindHookInstalled = true;
}

function captureReactStoreCandidate(candidate) {
  const messages = filterPersistableMessages(candidate.stateArray);
  if (messages.length === 0) return null;
  if (!hasConversationalMessages(messages)) return null;

  let captureId = REACT_CAPTURE_IDS_BY_DISPATCH.get(candidate.dispatch) ?? null;
  if (!captureId) {
    captureId = `react-${CAPTURED_REACT_STORES.size + 1}`;
    REACT_CAPTURE_IDS_BY_DISPATCH.set(candidate.dispatch, captureId);
    CAPTURED_REACT_STORES.set(captureId, {
      capturedAt: new Date().toISOString(),
      commitCount: 0,
    });
  }

  const entry = CAPTURED_REACT_STORES.get(captureId);
  entry.dispatch = candidate.dispatch;
  entry.refObject = candidate.refObject ?? null;
  entry.stateArray = candidate.stateArray;
  entry.fiberName = candidate.fiberName ?? null;
  entry.lastSeenAt = new Date().toISOString();
  entry.commitCount = (entry.commitCount ?? 0) + 1;
  return captureId;
}

function scanFiberForReactStores(root, stats) {
  const start = root?.current ?? root;
  if (!start || typeof start !== "object") return;

  const stack = [start];
  const seen = new Set();
  while (stack.length > 0) {
    const fiber = stack.pop();
    if (!fiber || typeof fiber !== "object" || seen.has(fiber)) continue;
    seen.add(fiber);
    stats.fibersVisited += 1;

    if (fiber.sibling) stack.push(fiber.sibling);
    if (fiber.child) stack.push(fiber.child);

    let hook = fiber.memoizedState;
    if (!hook || typeof hook !== "object") continue;

    const hooks = [];
    let guard = 0;
    while (hook && typeof hook === "object" && guard < 256) {
      hooks.push(hook);
      hook = hook.next;
      guard += 1;
    }

    for (const stateHook of hooks) {
      const stateArray = stateHook?.memoizedState;
      const dispatch = stateHook?.queue?.dispatch;
      if (!Array.isArray(stateArray) || typeof dispatch !== "function") continue;
      stats.stateArrayHooks += 1;

      const messages = filterPersistableMessages(stateArray);
      if (messages.length === 0 || !hasConversationalMessages(messages)) continue;
      stats.transcriptCandidates += 1;

      const refHook = hooks.find(
        (candidate) =>
          looksLikeRefObject(candidate?.memoizedState) &&
          candidate.memoizedState.current === stateArray,
      );

      captureReactStoreCandidate({
        dispatch,
        stateArray,
        refObject: refHook?.memoizedState ?? null,
        fiberName: getFiberDisplayName(fiber),
      });
    }
  }
}

function scanReactRootsForStores() {
  if (!ENABLE_REACT_DEVTOOLS_HOOK) return;
  const stats = {
    runs: lastReactScanStats.runs + 1,
    roots: REACT_ROOTS.size,
    fibersVisited: 0,
    stateArrayHooks: 0,
    transcriptCandidates: 0,
  };
  for (const { root } of REACT_ROOTS.values()) {
    try {
      scanFiberForReactStores(root, stats);
    } catch {}
  }
  lastReactScanStats = stats;
}

function installReactDevtoolsHook() {
  if (reactHookInstalled) return;

  const existing = globalThis.__REACT_DEVTOOLS_GLOBAL_HOOK__;
  const hook =
    existing && typeof existing === "object"
      ? existing
      : {
          renderers: new Map(),
          supportsFiber: true,
        };

  const previousInject = typeof hook.inject === "function" ? hook.inject.bind(hook) : null;
  const previousOnCommitFiberRoot =
    typeof hook.onCommitFiberRoot === "function" ? hook.onCommitFiberRoot.bind(hook) : null;
  const previousOnCommitFiberUnmount =
    typeof hook.onCommitFiberUnmount === "function"
      ? hook.onCommitFiberUnmount.bind(hook)
      : null;

  if (!(hook.renderers instanceof Map)) {
    hook.renderers = new Map();
  }
  hook.supportsFiber = true;
  hook.inject = function patchedInject(internals) {
    reactInjectCount += 1;
    const rendererId = previousInject?.(internals) ?? nextReactRendererId++;
    try {
      hook.renderers.set(rendererId, internals);
    } catch {}
    return rendererId;
  };
  hook.onCommitFiberRoot = function patchedOnCommitFiberRoot(rendererId, root, ...rest) {
    previousOnCommitFiberRoot?.(rendererId, root, ...rest);
    reactCommitCount += 1;
    REACT_ROOTS.set(root, {
      rendererId,
      root,
      committedAt: new Date().toISOString(),
    });
    scanReactRootsForStores();
  };
  hook.onCommitFiberUnmount = function patchedOnCommitFiberUnmount(rendererId, fiber, ...rest) {
    previousOnCommitFiberUnmount?.(rendererId, fiber, ...rest);
  };

  if (!existing) {
    globalThis.__REACT_DEVTOOLS_GLOBAL_HOOK__ = hook;
  }

  reactHookInstalled = true;
}

function installMutableMessagesHook() {
  const existing = Object.getOwnPropertyDescriptor(Object.prototype, "mutableMessages");
  if (existing?.set || existing?.get) return;

  Object.defineProperty(Object.prototype, "mutableMessages", {
    configurable: true,
    enumerable: false,
    get() {
      return this[CAPTURED_VALUE];
    },
    set(value) {
      Object.defineProperty(this, "mutableMessages", {
        value,
        writable: true,
        configurable: true,
        enumerable: true,
      });
      this[CAPTURED_VALUE] = value;
      captureSession(this, value);
    },
  });
}

function installRequestCaptureHook() {
  if (fetchHookInstalled) return;
  if (typeof globalThis.fetch !== "function") return;

  originalFetch = globalThis.fetch.bind(globalThis);
  globalThis.fetch = async function patchedFetch(input, init) {
    const url = getRequestUrl(input);
    const method = getRequestMethod(input, init);
    const headers = getRequestHeaders(input, init);
    const bodyText = await readRequestBodyText(input, init);
    let nextInput = input;
    let nextInit = init;

    if (method === "POST" && bodyText && looksLikeMessagesEndpoint(url)) {
      const parsed = safeJsonParse(bodyText);
      if (isAnthropicMessagesPayload(parsed)) {
        const requestMeta = {
          url,
          path: urlPathname(url),
          method,
          clientRequestId: headers?.get("x-client-request-id") ?? null,
        };

        const spliceAttempt = tryApplyPendingRequestSplice(parsed);
        let effectivePayload = spliceAttempt.payload;
        if (spliceAttempt.applied) {
          const rewrittenBody = JSON.stringify(effectivePayload);
          if (typeof Request !== "undefined" && input instanceof Request) {
            const requestHeaders = new Headers(headers ?? input.headers);
            requestHeaders.set("content-type", "application/json");
            nextInput = new Request(input, {
              method,
              headers: requestHeaders,
              body: rewrittenBody,
            });
            nextInit = undefined;
          } else {
            const requestHeaders = new Headers(headers ?? init?.headers);
            requestHeaders.set("content-type", "application/json");
            nextInit = {
              ...init,
              method,
              headers: requestHeaders,
              body: rewrittenBody,
            };
          }
        }

        const requestCaptureId = captureApiRequestPayload(effectivePayload, {
          ...requestMeta,
          overrideApplied: spliceAttempt.applied,
          requestSpliceSkipped: spliceAttempt.skipReason,
        });
        if (spliceAttempt.applied && requestCaptureId) {
          finalizePendingRequestSpliceAfterCapture(requestCaptureId);
        }
      }
    }

    return originalFetch(nextInput, nextInit);
  };

  fetchHookInstalled = true;
}

function installArrayHooks() {
  if (arrayHooksInstalled) return;
  const originalPush = Array.prototype.push;
  const originalSplice = Array.prototype.splice;
  originalArrayPush = originalPush;
  originalArraySplice = originalSplice;

  Array.prototype.push = function patchedPush(...items) {
    const result = originalPush.apply(this, items);
    maybeCaptureMessageArray(this, items);
    return result;
  };

  Array.prototype.splice = function patchedSplice(start, deleteCount, ...items) {
    const result = originalSplice.call(this, start, deleteCount, ...items);
    maybeCaptureMessageArray(this, items);
    return result;
  };
  arrayHooksInstalled = true;
}

function uninstallArrayHooks() {
  if (!arrayHooksInstalled) return;
  if (originalArrayPush) Array.prototype.push = originalArrayPush;
  if (originalArraySplice) Array.prototype.splice = originalArraySplice;
  arrayHooksInstalled = false;
}

function setCaptureArmed(value, durationMs = 30000) {
  captureArmed = value;
  if (captureAutoDisarmTimer) {
    clearTimeout(captureAutoDisarmTimer);
    captureAutoDisarmTimer = null;
  }

  if (value) {
    installArrayHooks();
    captureAutoDisarmTimer = setTimeout(() => {
      captureArmed = false;
      uninstallArrayHooks();
      captureAutoDisarmTimer = null;
    }, durationMs);
  } else {
    uninstallArrayHooks();
  }
}

function resolveCapture(request) {
  if (request.captureId) {
    const requestEntry = CAPTURED_API_REQUESTS.get(request.captureId);
    if (requestEntry) return [request.captureId, { kind: "request-payload", ...requestEntry }];
    const queueEntry = CAPTURED_QUEUE_STORES.get(request.captureId);
    if (queueEntry) return [request.captureId, { kind: "queue-store", ...queueEntry }];
    const reactEntry = CAPTURED_REACT_STORES.get(request.captureId);
    if (reactEntry) return [request.captureId, { kind: "react-store", ...reactEntry }];
    const entry = CAPTURED_SESSIONS.get(request.captureId);
    if (entry) return [request.captureId, { kind: "session", ...entry }];
    const arrayEntry = CAPTURED_ARRAYS.get(request.captureId);
    if (arrayEntry) return [request.captureId, { kind: "array", ...arrayEntry }];
    throw new Error(`Unknown captureId: ${request.captureId}`);
  }

  const entries = [
    ...[...CAPTURED_QUEUE_STORES.entries()].map(([captureId, entry]) => [
      captureId,
      { kind: "queue-store", ...entry },
    ]),
    ...[...CAPTURED_REACT_STORES.entries()].map(([captureId, entry]) => [
      captureId,
      { kind: "react-store", ...entry },
    ]),
    ...[...CAPTURED_SESSIONS.entries()].map(([captureId, entry]) => [
      captureId,
      { kind: "session", ...entry },
    ]),
    ...[...CAPTURED_ARRAYS.entries()].map(([captureId, entry]) => [
      captureId,
      { kind: "array", ...entry },
    ]),
  ];
  if (entries.length === 0) throw new Error("No live Claude sessions have been captured yet");
  entries.sort((a, b) => {
    const scoreDelta = scoreCapture(b[1]) - scoreCapture(a[1]);
    if (scoreDelta !== 0) return scoreDelta;
    return String(b[1].capturedAt).localeCompare(String(a[1].capturedAt));
  });
  return entries[0];
}

function getMessagesFromCapture(entry) {
  if (entry.kind === "request-payload") return entry.messages ?? [];
  if (entry.kind === "queue-store") return getQueueStoreMessages(entry);
  if (entry.kind === "react-store") return getReactStoreMessages(entry);
  if (entry.kind === "array") return entry.array;
  return entry.instance.getMessages?.() ?? [];
}

function applyMessagesToCapture(entry, nextMessages) {
  if (entry.kind === "queue-store") {
    refreshQueueStoreEntry(entry);
    if (entry.refObject && typeof entry.refObject === "object") {
      entry.refObject.current = nextMessages;
    }
    entry.stateArray = nextMessages;
    entry.lastAppliedAt = new Date().toISOString();
    entry.dispatch(nextMessages);
    return;
  }

  if (entry.kind === "react-store") {
    if (entry.refObject && typeof entry.refObject === "object") {
      entry.refObject.current = nextMessages;
    }
    entry.stateArray = nextMessages;
    entry.lastAppliedAt = new Date().toISOString();
    entry.dispatch(nextMessages);
    return;
  }

  const liveTarget = getMessagesFromCapture(entry);
  liveTarget.length = 0;
  liveTarget.push(...nextMessages);
}

async function spliceLiveSession(request) {
  const spliceMode = resolveSpliceMode(request);
  const [captureId, entry] = resolveCapture(request);
  if (entry.kind === "request-payload") {
    throw new Error("Use request-splice for API request captures; transcript splice expects UUID-linked live messages");
  }
  const currentMessages = filterPersistableMessages(getMessagesFromCapture(entry));
  const replacementMessages = filterPersistableMessages(
    request.replacementMessages ??
      (request.replacementMessagesPath
        ? JSON.parse(await readFile(request.replacementMessagesPath, "utf8"))
        : []),
  );

  const anchorUuid = request.anchorUuid ?? null;
  const anchorIndex = anchorUuid === null ? -1 : currentMessages.findIndex((m) => m.uuid === anchorUuid);
  if (anchorUuid !== null && anchorIndex === -1) {
    throw new Error(`Anchor UUID ${anchorUuid} was not found in live mutableMessages`);
  }

  const startIndex = anchorIndex + 1;
  let endIndex = startIndex + (request.deleteCount ?? 0);
  if (request.removeThroughUuid) {
    const removeThroughIndex = currentMessages.findIndex((m) => m.uuid === request.removeThroughUuid);
    if (removeThroughIndex === -1) {
      throw new Error(`removeThroughUuid ${request.removeThroughUuid} was not found in live mutableMessages`);
    }
    endIndex = removeThroughIndex + 1;
  }

  if (endIndex < startIndex || endIndex > currentMessages.length) {
    throw new Error(`Invalid splice range ${startIndex}..${endIndex}`);
  }

  const expandedRange = expandSpliceRangeToTurnBoundaries(currentMessages, startIndex, endIndex);
  const effectiveStartIndex = expandedRange.startIndex;
  const effectiveEndIndex = expandedRange.endIndex;
  const effectiveAnchorUuid = expandedRange.anchorUuid;

  const prefix = currentMessages.slice(0, effectiveStartIndex);
  const tail = currentMessages.slice(effectiveEndIndex);
  const templateMessage =
    prefix[prefix.length - 1] ??
    currentMessages[currentMessages.length - 1] ??
    replacementMessages[0] ??
    null;
  const effectiveSessionId =
    request.sessionId ??
    (entry.kind === "session" ? getSessionIdForInstance(entry.instance) : null) ??
    templateMessage?.sessionId ??
    null;

  if (!effectiveSessionId) {
    throw new Error("Unable to determine sessionId for live splice");
  }

  const sharedContext = buildDefaultContext({
    sessionId: effectiveSessionId,
    templateMessage,
    contextOverrides: request.contextOverrides ?? {},
  });
  let nextMessages;
  let helperMode = "rewrite";
  let compactShape = null;

  if (spliceMode === "native-compact-shape") {
    if (effectiveStartIndex !== 0) {
      throw new Error(
        "native-compact-shape only supports rewrites from the start of active context; use memory-only or offline-rewrite when a preserved prefix must remain visible",
      );
    }

    const lastPreCompactMessageUuid =
      currentMessages
        .slice(0, effectiveEndIndex)
        .filter((message) => message.type !== "progress")
        .at(-1)?.uuid ?? null;
    const boundaryMarker = createCompactBoundaryMessageLike({
      lastPreCompactMessageUuid,
      messagesSummarized: effectiveEndIndex - effectiveStartIndex,
      trigger: request.compactTrigger === "auto" ? "auto" : "manual",
      userContext: request.compactUserContext,
    });
    const anchorForTail =
      replacementMessages.length > 0
        ? replacementMessages[replacementMessages.length - 1]?.uuid ?? boundaryMarker.uuid
        : boundaryMarker.uuid;
    if (tail.length > 0) {
      boundaryMarker.compactMetadata.preservedSegment = {
        headUuid: tail[0].uuid,
        anchorUuid: anchorForTail,
        tailUuid: tail[tail.length - 1].uuid,
      };
    }

    const nativeMessages = [boundaryMarker, ...replacementMessages, ...tail];
    nextMessages = serializeSuffix(nativeMessages, {
      anchorUuid: null,
      anchorTimestamp: null,
      leadingRewriteCount: 1 + replacementMessages.length,
      nextBoundaryTimestamp: tail[0]?.timestamp ?? null,
      context: sharedContext,
    });
    helperMode = "native-compact-shape";
    compactShape = {
      boundaryUuid: nextMessages[0]?.uuid ?? boundaryMarker.uuid,
      preservedHeadUuid: boundaryMarker.compactMetadata?.preservedSegment?.headUuid ?? null,
      preservedTailUuid: boundaryMarker.compactMetadata?.preservedSegment?.tailUuid ?? null,
      visiblePrefixRemoved: currentMessages.length - tail.length,
    };
  } else {
    const serializedSuffix = serializeSuffix([...replacementMessages, ...tail], {
      anchorUuid: effectiveAnchorUuid,
      anchorTimestamp: templateMessage?.timestamp ?? null,
      leadingRewriteCount: replacementMessages.length,
      nextBoundaryTimestamp: tail[0]?.timestamp ?? null,
      context: sharedContext,
    });
    nextMessages = [...prefix, ...serializedSuffix];
    helperMode = "rewrite";
  }

  const toolIntegrityIssues = validateToolResultIntegrity(nextMessages);
  if (toolIntegrityIssues.length > 0) {
    throw new Error(
      `Live splice would leave ${toolIntegrityIssues.length} orphaned tool_result message(s); first issue at index ${toolIntegrityIssues[0].index}`,
    );
  }

  const shouldExport =
    spliceMode !== "memory-only" &&
    (request.exportPath || request.transcriptPath || request.exportIfNeeded !== false) &&
    !request.dryRun;
  const exportPath = shouldExport
    ? request.exportPath ?? defaultExportPath(spliceMode)
    : request.exportPath ?? null;

  if (!request.dryRun) {
    applyMessagesToCapture(entry, nextMessages);
  }

  const exportedPath = await maybeWriteExportFile(
    request.dryRun ? request.exportPath ?? null : exportPath,
    shouldExport || request.exportPath ? nextMessages : null,
  );
  const helperCommand = buildHelperCommand({
    helperMode,
    transcriptPath: request.transcriptPath ?? null,
    exportPath: exportedPath,
    requestedAnchorUuid: spliceMode === "native-compact-shape" ? null : anchorUuid,
    sessionId: effectiveSessionId,
  });

  return {
    captureId,
    spliceMode,
    sessionId: effectiveSessionId,
    oldMessageCount: currentMessages.length,
    newMessageCount: nextMessages.length,
    anchorUuid: effectiveAnchorUuid,
    requestedAnchorUuid: anchorUuid,
    removedCount: effectiveEndIndex - effectiveStartIndex,
    appendedCount: replacementMessages.length,
    firstInsertedUuid: replacementMessages[0]?.uuid ?? null,
    lastInsertedUuid: replacementMessages[replacementMessages.length - 1]?.uuid ?? null,
    nextLeafUuid: nextMessages[nextMessages.length - 1]?.uuid ?? null,
    persisted: false,
    dryRun: Boolean(request.dryRun),
    exportedPath,
    helperMode,
    helperCommand,
    compactShape,
  };
}

async function handleRequest(request) {
  switch (request.command) {
    case "arm-capture": {
      const durationMs = Math.max(1000, Math.min(300000, Number(request.durationMs ?? 30000)));
      const wait = Boolean(request.wait);
      const timeoutMs = Math.max(1000, Math.min(300000, Number(request.timeoutMs ?? durationMs)));

      setCaptureArmed(true, durationMs);

      if (!wait) {
        return {
          armed: true,
          durationMs,
          arraysSeen: CAPTURED_ARRAYS.size,
        };
      }

      // If there are already qualifying captures, return the best one immediately.
      if (CAPTURED_ARRAYS.size > 0) {
        const [captureId, entry] = resolveCapture({});
        return buildCaptureSummary(captureId, entry);
      }

      // Block until the first new qualifying array is captured or timeout.
      return new Promise((resolve, reject) => {
        const timer = setTimeout(() => {
          const idx = pendingCaptureWaiters.findIndex((w) => w.resolve === resolve);
          if (idx !== -1) pendingCaptureWaiters.splice(idx, 1);
          reject(new Error(`arm-capture --wait timed out after ${timeoutMs}ms with no qualifying array captured`));
        }, timeoutMs);
        pendingCaptureWaiters.push({ resolve, reject, timer });
      });
    }
    case "disarm-capture":
      setCaptureArmed(false);
      return {
        armed: false,
        arraysSeen: CAPTURED_ARRAYS.size,
      };
    case "status":
      scanReactRootsForStores();
      return {
        pid: process.pid,
        startedAt: STARTED_AT,
        socketPath: SOCKET_PATH,
        argv: process.argv.slice(0, 8),
        capture: {
          armed: captureArmed,
          hooksInstalled: arrayHooksInstalled,
          reactHookInstalled,
          reactInjectCount,
          reactCommitCount,
          queueBindHookInstalled,
          queueBindCaptureCount,
          fetchHookInstalled,
          requestCaptureCount,
        },
        reactScanStats: lastReactScanStats,
        requestOverride: pendingRequestSplice
          ? {
              kind: pendingRequestSplice.kind,
              armedAt: pendingRequestSplice.armedAt,
              captureId: pendingRequestSplice.captureId,
              startIndex: pendingRequestSplice.startIndex,
              deleteCount: pendingRequestSplice.deleteCount,
              replacementCount: pendingRequestSplice.replacementMessages?.length ?? 0,
              baseMessageCount: pendingRequestSplice.baseMessageCount,
              applyOnce: Boolean(pendingRequestSplice.applyOnce),
              applyCount: pendingRequestSplice.applyCount ?? 0,
              lastAppliedAt: pendingRequestSplice.lastAppliedAt ?? null,
              lastAppliedRequestCaptureId: pendingRequestSplice.lastAppliedRequestCaptureId ?? null,
              lastSkipAt: pendingRequestSplice.lastSkipAt ?? null,
              lastSkipReason: pendingRequestSplice.lastSkipReason ?? null,
              model: pendingRequestSplice.model ?? null,
            }
          : null,
        requestPayloads: [...CAPTURED_API_REQUESTS.entries()].map(([captureId, entry]) =>
          summarizeRequestPayload(captureId, entry),
        ),
        queueStores: [...CAPTURED_QUEUE_STORES.entries()].map(([captureId, entry]) =>
          summarizeQueueStore(captureId, entry),
        ),
        reactStores: [...CAPTURED_REACT_STORES.entries()].map(([captureId, entry]) =>
          summarizeReactStore(captureId, entry),
        ),
        sessions: [...CAPTURED_SESSIONS.entries()].map(([captureId, entry]) =>
          ({ kind: "session", ...summarizeSession(captureId, entry) }),
        ),
        arrays: [...CAPTURED_ARRAYS.entries()].map(([captureId, entry]) => {
          const messages = filterPersistableMessages(entry.array);
          const analysis = analyzeMessages(messages);
          const latest = analysis.latest;
          return {
            kind: "array",
            captureId,
            capturedAt: entry.capturedAt,
            sessionId: latest?.sessionId ?? null,
            messageCount: analysis.messageCount,
            userCount: analysis.userCount,
            assistantCount: analysis.assistantCount,
            attachmentCount: analysis.attachmentCount,
            latestUuid: latest?.uuid ?? null,
            latestType: latest?.type ?? null,
            cwd: latest?.cwd ?? null,
            score: scoreCapture({ kind: "array", ...entry }),
          };
        }),
      };
    case "messages": {
      const [captureId, entry] = resolveCapture(request);
      const messages =
        entry.kind === "request-payload"
          ? filterApiMessages(getMessagesFromCapture(entry))
          : filterPersistableMessages(getMessagesFromCapture(entry));
      return {
        captureId,
        kind: entry.kind,
        sessionId:
          entry.kind === "session"
            ? getSessionIdForInstance(entry.instance)
            : entry.kind === "request-payload"
              ? null
              : messages[messages.length - 1]?.sessionId ?? null,
        messages,
      };
    }
    case "inspect": {
      const [captureId, entry] = resolveCapture(request);
      const messages =
        entry.kind === "request-payload"
          ? filterApiMessages(getMessagesFromCapture(entry))
          : filterPersistableMessages(getMessagesFromCapture(entry));
      const analysis =
        entry.kind === "request-payload"
          ? analyzeApiMessages(messages)
          : analyzeMessages(messages);
      return {
        captureId,
        kind: entry.kind,
        sessionId:
          entry.kind === "session"
            ? getSessionIdForInstance(entry.instance)
            : entry.kind === "request-payload"
              ? null
              : analysis.latest?.sessionId ?? null,
        messageCount: analysis.messageCount,
        latestUuid: analysis.latest?.uuid ?? null,
        latestType:
          entry.kind === "request-payload"
            ? analysis.latest?.role ?? null
            : analysis.latest?.type ?? null,
        details:
          entry.kind === "queue-store"
            ? {
                fiberName: entry.fiberName ?? null,
                sourceFnName: entry.sourceFnName ?? null,
                bindCount: entry.bindCount ?? 0,
                queueHookIndex: entry.queueHookIndex ?? null,
                refHookIndex: entry.refHookIndex ?? null,
                ownerPath: entry.ownerPath ?? null,
                rootFiberName: entry.rootFiberName ?? null,
                rootResolved: Boolean(entry.rootResolved),
                refMirrored: Boolean(entry.refObject),
                refCandidates: entry.refCandidates ?? [],
              }
            : entry.kind === "react-store"
              ? {
                  fiberName: entry.fiberName ?? null,
                  refMirrored: Boolean(entry.refObject),
                }
              : entry.kind === "request-payload"
                ? {
                    url: entry.url ?? null,
                    path: entry.path ?? null,
                    method: entry.method ?? "POST",
                    model: entry.model ?? null,
                    stream: Boolean(entry.stream),
                    system: entry.system ?? null,
                    systemBlockCount: entry.systemBlockCount ?? 0,
                    toolCount: entry.toolCount ?? 0,
                    betaCount: entry.betaCount ?? 0,
                    clientRequestId: entry.clientRequestId ?? null,
                    overrideApplied: Boolean(entry.overrideApplied),
                    requestSpliceSkipped: entry.requestSpliceSkipped ?? null,
                  }
              : null,
        messages,
      };
    }
    case "request-splice": {
      const [captureId, entry] = resolveCapture(request);
      if (entry.kind !== "request-payload") {
        throw new Error("request-splice requires a request-payload capture id");
      }
      const replacementMessages = filterApiMessages(
        request.replacementMessages ??
          (request.replacementMessagesPath
            ? JSON.parse(await readFile(request.replacementMessagesPath, "utf8"))
            : []),
      );
      const startIndex = Number(request.startIndex ?? 0);
      const deleteCount = Number(request.deleteCount ?? 0);
      const currentMessages = filterApiMessages(entry.messages ?? []);
      createRequestSplicePlan({
        captureId,
        captureEntry: entry,
        startIndex,
        deleteCount,
        replacementMessages,
        applyOnce: request.applyOnce !== false,
      });
      const nextMessages = [
        ...currentMessages.slice(0, startIndex),
        ...replacementMessages,
        ...currentMessages.slice(startIndex + deleteCount),
      ];
      if (request.dryRun) {
        return {
          captureId,
          kind: "request-payload",
          dryRun: true,
          oldMessageCount: currentMessages.length,
          newMessageCount: nextMessages.length,
          startIndex,
          deleteCount,
          replacementCount: replacementMessages.length,
          nextMessages,
        };
      }
      pendingRequestSplice = createRequestSplicePlan({
        captureId,
        captureEntry: entry,
        startIndex,
        deleteCount,
        replacementMessages,
        applyOnce: request.applyOnce !== false,
      });
      return {
        captureId,
        kind: "request-payload",
        armed: true,
        oldMessageCount: currentMessages.length,
        newMessageCount: nextMessages.length,
        startIndex,
        deleteCount,
        replacementCount: replacementMessages.length,
        applyOnce: pendingRequestSplice.applyOnce,
      };
    }
    case "clear-request-splice":
      pendingRequestSplice = null;
      return {
        cleared: true,
      };
    case "splice":
      return await spliceLiveSession(request);
    default:
      throw new Error(`Unknown command: ${request.command}`);
  }
}

async function startServer() {
  if (existsSync(SOCKET_PATH)) {
    await rm(SOCKET_PATH, { force: true });
  }

  const server = createServer({ allowHalfOpen: true }, (socket) => {
    let input = "";
    let responded = false;
    let handling = false;

    async function sendResponse(payload) {
      if (responded) return;
      responded = true;
      const text = JSON.stringify(payload) + "\n";
      await new Promise((resolve, reject) => {
        socket.end(text, (error) => {
          if (error) {
            reject(error);
            return;
          }
          resolve();
        });
      });
    }

    socket.setEncoding("utf8");
    socket.on("data", async (chunk) => {
      input += chunk;
      if (handling) return;
      try {
        const boundaryIndex = input.indexOf("\n");
        if (boundaryIndex === -1) {
          return;
        }

        handling = true;
        const requestText = input.slice(0, boundaryIndex).trim();
        if (requestText.length === 0) {
          await sendResponse({ ok: false, error: "No request payload received" });
          return;
        }

        const request = JSON.parse(requestText);
        const result = await handleRequest(request);
        await sendResponse({ ok: true, result });
      } catch (error) {
        try {
          await sendResponse({
            ok: false,
            error: error instanceof Error ? error.message : String(error),
          });
        } catch {}
      }
    });
    socket.on("end", async () => {
      if (responded || handling) return;
      try {
        const requestText = input.trim();
        if (requestText.length === 0) {
          await sendResponse({ ok: false, error: "No request payload received" });
          return;
        }
        const request = JSON.parse(requestText);
        const result = await handleRequest(request);
        await sendResponse({ ok: true, result });
      } catch (error) {
        try {
          await sendResponse({
            ok: false,
            error: error instanceof Error ? error.message : String(error),
          });
        } catch {}
      }
    });
  });

  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(SOCKET_PATH, () => resolve());
  });

  process.on("exit", () => {
    try {
      if (existsSync(SOCKET_PATH)) {
        unlink(SOCKET_PATH).catch(() => {});
      }
    } catch {}
  });
  process.on("SIGINT", () => process.exit(130));
  process.on("SIGTERM", () => process.exit(143));

  await updateRegistry();
  return server;
}

if (ENABLE_OBJECT_HOOK) installMutableMessagesHook();
if (ENABLE_ARRAY_HOOK) setCaptureArmed(true, 300000);
if (ENABLE_REACT_DEVTOOLS_HOOK) installReactDevtoolsHook();
if (ENABLE_QUEUE_CAPTURE) installQueueCaptureHook();
if (ENABLE_REQUEST_CAPTURE) installRequestCaptureHook();
if (ENABLE_SERVER) {
  await startServer();

  for (const signal of ["exit", "SIGINT", "SIGTERM"]) {
    process.on(signal, () => {
      removeFromRegistry().catch(() => {});
    });
  }
}
