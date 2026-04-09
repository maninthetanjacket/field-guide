import { createConnection } from "net";
import { readFile } from "fs/promises";
import { existsSync } from "fs";

const REGISTRY_PATH = "/tmp/claude-live-registry.json";

function parseArgs(argv) {
  if (argv.length > 0 && (argv[0] === "--help" || argv[0] === "-h")) {
    return { command: "help", help: true };
  }

  const [command = "status", ...rest] = argv;
  const args = {
    command,
    pid: null,
    captureId: null,
    anchorUuid: null,
    fromStart: false,
    deleteCount: 0,
    durationMs: null,
    wait: false,
    sessionId: null,
    messagesPath: null,
    transcriptPath: null,
    removeThroughUuid: null,
    dryRun: false,
    persist: true,
    spliceMode: null,
    exportPath: null,
    compactTrigger: "manual",
    compactUserContext: null,
    threshold: 3000,
    truncateAt: 2000,
    outputPath: null,
    startIndex: null,
    sticky: false,
  };

  for (let index = 0; index < rest.length; index += 1) {
    const token = rest[index];
    if (token === "--pid") {
      args.pid = Number(rest[index + 1] ?? "");
      index += 1;
    } else if (token === "--capture-id") {
      args.captureId = rest[index + 1] ?? null;
      index += 1;
    } else if (token === "--anchor") {
      args.anchorUuid = rest[index + 1] ?? null;
      index += 1;
    } else if (token === "--from-start") {
      args.fromStart = true;
      args.anchorUuid = null;
    } else if (token === "--delete-count") {
      args.deleteCount = Number(rest[index + 1] ?? "0");
      index += 1;
    } else if (token === "--duration-ms") {
      args.durationMs = Number(rest[index + 1] ?? "30000");
      index += 1;
    } else if (token === "--wait") {
      args.wait = true;
    } else if (token === "--session-id") {
      args.sessionId = rest[index + 1] ?? null;
      index += 1;
    } else if (token === "--messages") {
      args.messagesPath = rest[index + 1] ?? null;
      index += 1;
    } else if (token === "--transcript") {
      args.transcriptPath = rest[index + 1] ?? null;
      index += 1;
    } else if (token === "--remove-through") {
      args.removeThroughUuid = rest[index + 1] ?? null;
      index += 1;
    } else if (token === "--dry-run") {
      args.dryRun = true;
    } else if (token === "--no-persist") {
      args.persist = false;
    } else if (token === "--mode") {
      args.spliceMode = rest[index + 1] ?? null;
      index += 1;
    } else if (token === "--export") {
      args.exportPath = rest[index + 1] ?? null;
      index += 1;
    } else if (token === "--compact-trigger") {
      args.compactTrigger = rest[index + 1] ?? "manual";
      index += 1;
    } else if (token === "--compact-context") {
      args.compactUserContext = rest[index + 1] ?? null;
      index += 1;
    } else if (token === "--threshold") {
      args.threshold = Number(rest[index + 1] ?? "3000");
      index += 1;
    } else if (token === "--truncate") {
      args.truncateAt = Number(rest[index + 1] ?? "2000");
      index += 1;
    } else if (token === "--output" || token === "-o") {
      args.outputPath = rest[index + 1] ?? null;
      index += 1;
    } else if (token === "--start-index") {
      args.startIndex = Number(rest[index + 1] ?? "0");
      index += 1;
    } else if (token === "--sticky") {
      args.sticky = true;
    } else if (token === "--help" || token === "-h") {
      args.help = true;
    } else {
      throw new Error(`Unknown argument: ${token}`);
    }
  }

  return args;
}

async function readRegistry() {
  const text = await readFile(REGISTRY_PATH, "utf8");
  const parsed = JSON.parse(text);
  if (!Array.isArray(parsed)) {
    throw new Error("Registry is not an array");
  }
  return parsed;
}

async function resolveSocketPath(pid) {
  const entries = await readRegistry();
  if (entries.length === 0) {
    throw new Error("No live Claude wrapper processes are registered");
  }

  const entry = pid !== null ? entries.find((item) => item.pid === pid) : entries[entries.length - 1];
  if (!entry) {
    throw new Error(`No registered process found for pid ${pid}`);
  }
  if (!entry.socketPath || !existsSync(entry.socketPath)) {
    throw new Error(`Socket is unavailable for pid ${entry.pid}`);
  }
  return entry.socketPath;
}

async function sendRequest(socketPath, payload, socketTimeoutMs = 30000) {
  return await new Promise((resolve, reject) => {
    const socket = createConnection(socketPath);
    let output = "";
    let settled = false;

    function settleError(error) {
      if (settled) return;
      settled = true;
      reject(error);
    }

    function settleSuccess(value) {
      if (settled) return;
      settled = true;
      resolve(value);
    }

    socket.setEncoding("utf8");
    socket.setTimeout(socketTimeoutMs, () => {
      settleError(new Error("Socket timeout waiting for control response"));
      socket.destroy();
    });
    socket.on("connect", () => {
      socket.write(JSON.stringify(payload) + "\n");
    });
    socket.on("data", (chunk) => {
      output += chunk;
    });
    socket.on("end", () => {
      try {
        const trimmed = output.trim();
        if (trimmed.length === 0) {
          settleError(new Error("Socket closed before sending a response"));
          return;
        }
        const parsed = JSON.parse(trimmed);
        if (!parsed.ok) {
          settleError(new Error(parsed.error || "Unknown control error"));
          return;
        }
        settleSuccess(parsed.result);
      } catch (error) {
        settleError(error);
      }
    });
    socket.on("error", settleError);
  });
}

function sleep(ms) {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}

function pickBestArrayCapture(statusResult) {
  const arrays = Array.isArray(statusResult?.arrays) ? statusResult.arrays.slice() : [];
  arrays.sort((a, b) => {
    const scoreDelta = Number(b?.score ?? 0) - Number(a?.score ?? 0);
    if (scoreDelta !== 0) return scoreDelta;
    return String(b?.capturedAt ?? "").localeCompare(String(a?.capturedAt ?? ""));
  });
  return arrays[0] ?? null;
}

async function waitForCapturedArray(socketPath, { timeoutMs = 30000, beforeCaptureIds = new Set() } = {}) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const statusResult = await sendRequest(socketPath, { command: "status" });
    const arrays = Array.isArray(statusResult?.arrays) ? statusResult.arrays : [];
    const fresh = arrays
      .filter((entry) => !beforeCaptureIds.has(entry.captureId))
      .sort((a, b) => {
        const scoreDelta = Number(b?.score ?? 0) - Number(a?.score ?? 0);
        if (scoreDelta !== 0) return scoreDelta;
        return String(b?.capturedAt ?? "").localeCompare(String(a?.capturedAt ?? ""));
      });
    if (fresh.length > 0) {
      return fresh[0];
    }
    await sleep(250);
  }
  throw new Error(`arm-capture --wait timed out after ${timeoutMs}ms with no qualifying array captured`);
}

function truncate(text, limit) {
  if (!limit || text.length <= limit) return text;
  return text.slice(0, limit) + `\n… [${(text.length - limit).toLocaleString()} chars truncated]`;
}

function renderBlock(block, truncateAt) {
  const t = block?.type ?? "unknown";
  if (t === "text") {
    return truncate(block.text ?? "", truncateAt);
  }
  if (t === "thinking") {
    const sig = block.signature ?? "";
    return `<thinking sig=${sig.length}b>\n${truncate(block.thinking ?? "", truncateAt)}\n</thinking>`;
  }
  if (t === "tool_use") {
    const inp = JSON.stringify(block.input ?? {}, null, 2);
    return `<tool_use name=${block.name ?? "?"} id=${(block.id ?? "?").slice(0, 16)}>\n${truncate(inp, truncateAt)}\n</tool_use>`;
  }
  if (t === "tool_result") {
    const inner = block.content;
    let text = "";
    if (typeof inner === "string") {
      text = inner;
    } else if (Array.isArray(inner)) {
      text = inner
        .filter(i => i?.type === "text")
        .map(i => i.text ?? "")
        .join("\n");
    }
    const errTag = block.is_error ? " error=true" : "";
    return `<tool_result id=${(block.tool_use_id ?? "?").slice(0, 16)}${errTag}>\n${truncate(text, truncateAt)}\n</tool_result>`;
  }
  return `<${t}>${truncate(JSON.stringify(block), truncateAt)}</${t}>`;
}

async function cmdDump(args, socketPath) {
  const { captureId, truncateAt, outputPath } = args;

  const messagesResult = await sendRequest(socketPath, { command: "messages", captureId });
  const messages = messagesResult.messages ?? messagesResult;
  const isApiCapture =
    messagesResult.kind === "request-payload" ||
    (messages.length > 0 && messages[0] && !messages[0].message && typeof messages[0].role === "string");

  const SKIP_TYPES = new Set(["file-history-snapshot", "progress", "last-prompt", "system"]);
  const SEP = "=".repeat(72);

  const lines = [];
  lines.push(`# Live context dump — ${messages.length} records — ${new Date().toISOString()}`);
  lines.push(`# capture-id: ${captureId}`);
  lines.push(
    isApiCapture
      ? `# Note: reflects outbound Anthropic API messages as actually sent`
      : `# Note: reflects in-memory array (pre-API-normalisation); excludes toolUseResult/snapshot`,
  );

  for (let i = 0; i < messages.length; i++) {
    const rec = messages[i];
    const recType = rec?.type ?? "unknown";
    if (!isApiCapture && SKIP_TYPES.has(recType)) continue;

    const msg = isApiCapture ? rec : rec?.message;
    if (!msg) continue;

    const role = msg.role ?? recType;
    const uuid = isApiCapture ? "" : (rec.uuid ?? "").slice(0, 16);
    const ts = isApiCapture ? "" : rec.timestamp ?? "";
    lines.push(`\n${SEP}\n[${String(i).padStart(4, "0")}] ${role.toUpperCase()}  ${uuid}  ${ts}`);

    const content = msg.content;
    if (typeof content === "string") {
      lines.push(truncate(content, truncateAt));
    } else if (Array.isArray(content)) {
      for (const block of content) {
        if (block && typeof block === "object") {
          lines.push(renderBlock(block, truncateAt));
        }
      }
    }
  }

  const output = lines.join("\n");

  if (outputPath) {
    const { writeFile } = await import("fs/promises");
    await writeFile(outputPath, output, "utf8");
    const msgCount = messages.filter(m => !SKIP_TYPES.has(m?.type ?? "")).length;
    console.log(`Wrote ${msgCount} message records → ${outputPath}  (${output.length.toLocaleString()} chars)`);
  } else {
    process.stdout.write(output + "\n");
  }
}

function findToolName(messages, toolUseId) {
  if (!toolUseId) return "tool_result";
  for (let i = messages.length - 1; i >= 0; i--) {
    const msg = messages[i];
    const content = msg?.message?.content;
    if (!Array.isArray(content)) continue;
    for (const block of content) {
      if (block?.type === "tool_use" && block?.id === toolUseId) {
        return block.name || "tool_result";
      }
    }
  }
  return "tool_result";
}

async function cmdCompressReads(args, socketPath) {
  const { captureId, sessionId, threshold, dryRun, persist } = args;

  const messagesResult = await sendRequest(socketPath, {
    command: "messages",
    captureId,
  });
  const messages = messagesResult.messages ?? messagesResult;

  // Find large tool_result blocks and large tool_use inputs
  const inputThreshold = Math.max(threshold, 2000);
  const targets = [];
  for (let i = 0; i < messages.length; i++) {
    const msg = messages[i];
    const content = msg?.message?.content;
    if (!Array.isArray(content)) continue;
    for (const block of content) {
      // tool_result: compress large text content
      if (block?.type === "tool_result") {
        const inner = block.content;
        if (!Array.isArray(inner)) continue;
        for (const item of inner) {
          if (item?.type !== "text") continue;
          const text = item.text ?? "";
          if (text.length > threshold) {
            const toolName = findToolName(messages, block.tool_use_id);
            targets.push({
              kind: "tool_result",
              index: i,
              uuid: msg.uuid,
              toolName,
              originalSize: text.length,
              preview: text.slice(0, 60).replace(/\n/g, " "),
            });
          }
        }
      }
      // tool_use: compress large input payloads (Write, Edit, large Bash commands)
      if (block?.type === "tool_use" && block?.input) {
        const inputStr = JSON.stringify(block.input);
        if (inputStr.length > inputThreshold) {
          targets.push({
            kind: "tool_use_input",
            index: i,
            uuid: msg.uuid,
            toolName: block.name ?? "tool_use",
            toolUseId: block.id,
            originalSize: inputStr.length,
            preview: inputStr.slice(0, 60).replace(/\n/g, " "),
          });
        }
      }
    }
  }

  if (targets.length === 0) {
    console.log("No compressible blocks found above threshold. Nothing to compress.");
    return;
  }

  // Sort highest index first to avoid index-shift bugs
  targets.sort((a, b) => b.index - a.index);

  if (dryRun) {
    console.log(`Would compress ${targets.length} block(s):\n`);
    for (const t of targets) {
      const kindLabel = t.kind === "tool_use_input" ? "tool_use.input" : "tool_result";
      console.log(`  [${t.index}] ${t.uuid.slice(0, 8)}...  ${t.originalSize.toLocaleString()} chars  ${kindLabel}(${t.toolName})  "${t.preview}"`);
    }
    return;
  }

  let totalSaved = 0;
  let finalCount = messages.length;

  for (const target of targets) {
    // Deep-clone the target message and replace the large field with a placeholder
    const original = messages[target.index];
    const compressed = JSON.parse(JSON.stringify(original));
    const cContent = compressed.message.content;

    if (target.kind === "tool_use_input") {
      for (const block of cContent) {
        if (block?.type === "tool_use" && block?.id === target.toolUseId) {
          const saved = JSON.stringify(block.input).length;
          block.input = { _compressed: `[Input compressed — ${target.toolName}, ${saved.toLocaleString()} chars, tool call already executed in session]` };
          totalSaved += saved;
        }
      }
    } else {
      for (const block of cContent) {
        if (block?.type !== "tool_result") continue;
        if (!Array.isArray(block.content)) continue;
        for (const item of block.content) {
          if (item?.type === "text" && item.text.length > threshold) {
            const saved = item.text.length;
            item.text = `[Content compressed — ${target.toolName}, ${saved.toLocaleString()} chars, already read and processed in session]`;
            totalSaved += saved;
          }
        }
      }
    }

    const anchorUuid = messages[target.index - 1]?.uuid;
    if (!anchorUuid) {
      console.error(`  SKIP [${target.index}] — no preceding message to use as anchor`);
      continue;
    }

    const result = await sendRequest(socketPath, {
      command: "splice",
      captureId,
      anchorUuid,
      deleteCount: 1,
      sessionId,
      replacementMessages: [compressed],
      dryRun: false,
      persist,
    });
    finalCount = result.newMessageCount ?? finalCount;
    console.log(`  OK  [${target.index}] ${target.uuid.slice(0, 8)}...  saved ${target.originalSize.toLocaleString()} chars  (${target.toolName})`);
  }

  console.log(`\nCompressed ${targets.length} block(s). Total chars saved: ${totalSaved.toLocaleString()}. Messages: ${finalCount}.`);
}

function printHelp() {
  console.log(`Usage:
  node claude-live-splicectl.mjs status [--pid PID]
  node claude-live-splicectl.mjs arm-capture [--pid PID] [--duration-ms 30000] [--wait]
  node claude-live-splicectl.mjs disarm-capture [--pid PID]
  node claude-live-splicectl.mjs messages [--pid PID] [--capture-id ID]
  node claude-live-splicectl.mjs inspect [--pid PID] --capture-id ID
  node claude-live-splicectl.mjs request-splice --capture-id ID --start-index N --messages PATH [--delete-count N] [--pid PID] [--dry-run] [--sticky]
  node claude-live-splicectl.mjs clear-request-splice [--pid PID]
  node claude-live-splicectl.mjs splice (--anchor UUID | --from-start) --messages PATH [--mode memory-only|offline-rewrite|native-compact-shape] [--transcript PATH] [--export PATH] [--session-id UUID] [--delete-count N] [--remove-through UUID] [--compact-context TEXT] [--compact-trigger manual|auto] [--pid PID] [--capture-id ID] [--dry-run] [--no-persist]
  node claude-live-splicectl.mjs compress-reads --capture-id ID --session-id UUID [--threshold 3000] [--pid PID] [--dry-run] [--no-persist]
    Compresses large tool_result content blocks (threshold, default 3000 chars) and large
    tool_use input payloads (max(threshold,2000) chars) — e.g. Write/Edit calls with full
    file content. Replaces with short placeholders. Works highest-index-first to avoid shifts.
  node claude-live-splicectl.mjs dump --capture-id ID [--truncate 2000] [--output PATH] [--pid PID]
    Render the live in-memory message array as human-readable text. Shows role+content blocks
    only (excludes toolUseResult/snapshot/metadata — i.e. what actually gets sent to the API).
    Compare with session_memory.py dump to see what the JSONL contains vs what the model sees.
  request-splice uses API-normalized Anthropic messages from a request-payload capture, not transcript
    records. The armed splice applies to the next matching outbound API call and defaults to one-shot.

Splice modes:
  memory-only
    Safe default. Mutates the live in-memory array only.
  offline-rewrite
    Mutates live memory, exports the post-splice array, and returns a helper command
    for an offline suffix rewrite after Claude Code exits.
  native-compact-shape
    Compaction-like rewrite from the start of active context only. Mutates live memory
    into [compact_boundary, replacement..., kept-tail] and returns a helper command for
    offline append-only persistence using preservedSegment semantics.

Notes:
  --export writes the post-splice live array for later helper use. Non-memory modes
  auto-export to /tmp when no path is provided.
`);
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    printHelp();
    return;
  }

  if (args.command === "status") {
    const socketPath = await resolveSocketPath(args.pid);
    const result = await sendRequest(socketPath, { command: "status" });
    console.log(JSON.stringify(result, null, 2));
    return;
  }

  if (args.command === "arm-capture") {
    const socketPath = await resolveSocketPath(args.pid);
    const durationMs = args.durationMs ?? 30000;
    if (args.wait) {
      const statusBefore = await sendRequest(socketPath, { command: "status" });
      const existing = pickBestArrayCapture(statusBefore);
      if (existing) {
        console.log(JSON.stringify(existing, null, 2));
        return;
      }

      await sendRequest(
        socketPath,
        { command: "arm-capture", durationMs, wait: false, timeoutMs: durationMs },
        30000,
      );
      const result = await waitForCapturedArray(socketPath, {
        timeoutMs: durationMs,
        beforeCaptureIds: new Set((statusBefore.arrays ?? []).map((entry) => entry.captureId)),
      });
      console.log(JSON.stringify(result, null, 2));
      return;
    }

    const result = await sendRequest(
      socketPath,
      { command: "arm-capture", durationMs, wait: false, timeoutMs: durationMs },
      30000,
    );
    console.log(JSON.stringify(result, null, 2));
    return;
  }

  if (args.command === "disarm-capture") {
    const socketPath = await resolveSocketPath(args.pid);
    const result = await sendRequest(socketPath, { command: "disarm-capture" });
    console.log(JSON.stringify(result, null, 2));
    return;
  }

  if (args.command === "messages") {
    const socketPath = await resolveSocketPath(args.pid);
    const result = await sendRequest(socketPath, {
      command: "messages",
      captureId: args.captureId,
    });
    console.log(JSON.stringify(result, null, 2));
    return;
  }

  if (args.command === "inspect") {
    if (!args.captureId) {
      console.error("inspect requires --capture-id");
      printHelp();
      process.exitCode = 1;
      return;
    }
    const socketPath = await resolveSocketPath(args.pid);
    const result = await sendRequest(socketPath, {
      command: "inspect",
      captureId: args.captureId,
    });
    console.log(JSON.stringify(result, null, 2));
    return;
  }

  if (args.command === "request-splice") {
    if (!args.captureId || !args.messagesPath || args.startIndex === null) {
      console.error("request-splice requires --capture-id, --start-index, and --messages");
      printHelp();
      process.exitCode = 1;
      return;
    }
    const socketPath = await resolveSocketPath(args.pid);
    const replacementMessages = JSON.parse(await readFile(args.messagesPath, "utf8"));
    const result = await sendRequest(socketPath, {
      command: "request-splice",
      captureId: args.captureId,
      startIndex: args.startIndex,
      deleteCount: args.deleteCount,
      replacementMessages,
      dryRun: args.dryRun,
      applyOnce: !args.sticky,
    });
    console.log(JSON.stringify(result, null, 2));
    return;
  }

  if (args.command === "clear-request-splice") {
    const socketPath = await resolveSocketPath(args.pid);
    const result = await sendRequest(socketPath, {
      command: "clear-request-splice",
    });
    console.log(JSON.stringify(result, null, 2));
    return;
  }

  if (args.command === "splice") {
    if (!args.messagesPath || (!args.anchorUuid && !args.fromStart)) {
      printHelp();
      process.exitCode = 1;
      return;
    }
    const socketPath = await resolveSocketPath(args.pid);
    const replacementMessages = JSON.parse(await readFile(args.messagesPath, "utf8"));
    const result = await sendRequest(socketPath, {
      command: "splice",
      captureId: args.captureId,
      anchorUuid: args.fromStart ? null : args.anchorUuid,
      deleteCount: args.deleteCount,
      sessionId: args.sessionId,
      replacementMessages,
      transcriptPath: args.transcriptPath,
      removeThroughUuid: args.removeThroughUuid,
      dryRun: args.dryRun,
      persist: args.persist,
      spliceMode: args.spliceMode,
      exportPath: args.exportPath,
      compactTrigger: args.compactTrigger,
      compactUserContext: args.compactUserContext,
    });
    console.log(JSON.stringify(result, null, 2));
    return;
  }

  if (args.command === "dump") {
    if (!args.captureId) {
      console.error("dump requires --capture-id");
      printHelp();
      process.exitCode = 1;
      return;
    }
    const socketPath = await resolveSocketPath(args.pid);
    await cmdDump(args, socketPath);
    return;
  }

  if (args.command === "compress-reads") {
    if (!args.captureId || !args.sessionId) {
      console.error("compress-reads requires --capture-id and --session-id");
      printHelp();
      process.exitCode = 1;
      return;
    }
    const socketPath = await resolveSocketPath(args.pid);
    await cmdCompressReads(args, socketPath);
    return;
  }

  throw new Error(`Unknown command: ${args.command}`);
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(1);
});
