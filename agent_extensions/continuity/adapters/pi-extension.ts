/**
 * pi adapter for the provider-neutral continuity capsules.
 *
 * Injects the project capsule once at the start of a session, and refreshes
 * it before compaction and on shutdown. All state lives in the neutral
 * capsule store; this file only bridges pi's lifecycle events to the CLI.
 *
 * Point AGENT_EXTENSIONS_DIR at the checkout if it is not C:/code/agent-extensions.
 */
import { execFile } from "node:child_process";
import { join } from "node:path";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const REPO = process.env.AGENT_EXTENSIONS_DIR || "C:/code/agent-extensions";
const LAUNCHER = join(REPO, "agent_extensions", "continuity", "adapters", "capsule.py");
const PYTHON = process.platform === "win32" ? "python" : "python3";

function capsule(args: string[], cwd: string, timeoutMs = 15000): Promise<string> {
  return new Promise((resolve) => {
    execFile(
      PYTHON,
      [LAUNCHER, ...args, "--cwd", cwd],
      { timeout: timeoutMs, windowsHide: true },
      (error, stdout) => resolve(error ? "" : stdout.toString().trim()),
    );
  });
}

export default function (pi: ExtensionAPI) {
  let pendingInjection = false;

  pi.on("session_start", async () => {
    pendingInjection = true;
  });

  pi.on("before_agent_start", async (_event, ctx) => {
    if (!pendingInjection) return;
    pendingInjection = false;
    const text = await capsule(["render", "--format", "plain"], ctx.cwd);
    if (!text) return;
    return {
      message: { customType: "continuity", content: text, display: true },
    };
  });

  pi.on("session_before_compact", async (_event, ctx) => {
    await capsule(["capture", "--auto"], ctx.cwd);
  });

  pi.on("session_compact", async (_event, ctx) => {
    await capsule(["capture", "--auto"], ctx.cwd);
  });

  pi.on("session_shutdown", async (_event, ctx) => {
    await capsule(["capture", "--auto"], ctx.cwd);
  });
}
