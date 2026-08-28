#!/usr/bin/env python3
"""Re-invokes a translated Claude Code plugin hook script with
CLAUDE_PLUGIN_ROOT set in the process environment, bridging hook input/output
contracts between Claude Code and target agent environments (Antigravity/Codex).

Claude Code substitutes ${CLAUDE_PLUGIN_ROOT} into a hook's command string
AND sets it as an environment variable for the spawned process. Codex's and
Antigravity's hooks.json schemas only support a plain command string (no
env field on a hook entry, confirmed against their published docs), so
bootstrap/sync.sh and sync.ps1 rewrite a translated command of the form
`python3 "<script>"` to invoke this wrapper instead.

In addition to setting CLAUDE_PLUGIN_ROOT, this wrapper translates the
JSON payload on stdin (e.g. Antigravity's `toolCall` -> Claude Code's
`tool_name` / `tool_input`) and the resulting stdout (e.g. Claude Code's
`hookSpecificOutput.permissionDecision = "deny"` -> Antigravity's
`decision = "deny"`), allowing Claude Code hook plugins like hookify to
function seamlessly under Antigravity's hook engine.

Usage: hook_env_wrapper.py <plugin_dir> <script_path>
"""
import io
import json
import os
import runpy
import sys
from typing import Any, Dict, Tuple


def adapt_input(raw_text: str) -> Tuple[str, bool]:
    """Adapts stdin payload to Claude Code hook format if needed.

    Returns (adapted_json_string, is_antigravity_format).
    """
    if not raw_text.strip():
        return raw_text, False

    try:
        data = json.loads(raw_text)
    except Exception:
        return raw_text, False

    if not isinstance(data, dict):
        return raw_text, False

    is_antigravity = False
    adapted = dict(data)

    # Antigravity toolCall translation
    if "toolCall" in data and isinstance(data["toolCall"], dict):
        is_antigravity = True
        tc = data["toolCall"]
        name = tc.get("name", "")
        args = tc.get("args", {}) or {}

        if name == "run_command":
            adapted["tool_name"] = "Bash"
            adapted["tool_input"] = {
                "command": args.get("CommandLine", args.get("command", ""))
            }
        elif name in ("write_to_file", "write_file"):
            adapted["tool_name"] = "Write"
            adapted["tool_input"] = {
                "file_path": args.get("TargetFile", args.get("file_path", "")),
                "content": args.get("CodeContent", args.get("content", "")),
            }
        elif name in ("replace_file_content", "edit_file"):
            adapted["tool_name"] = "Edit"
            adapted["tool_input"] = {
                "file_path": args.get("TargetFile", args.get("file_path", "")),
                "old_string": args.get("TargetContent", args.get("old_string", "")),
                "new_string": args.get("ReplacementContent", args.get("new_string", "")),
            }
        elif name in ("view_file", "read_file"):
            adapted["tool_name"] = "Read"
            adapted["tool_input"] = {
                "file_path": args.get("AbsolutePath", args.get("file_path", ""))
            }
        else:
            adapted["tool_name"] = name
            adapted["tool_input"] = args

        if "hook_event_name" not in adapted:
            adapted["hook_event_name"] = "PreToolUse"

    # Antigravity metadata field translation
    if "transcriptPath" in data:
        is_antigravity = True
        adapted["transcript_path"] = data["transcriptPath"]
    if "conversationId" in data:
        is_antigravity = True
        adapted["conversation_id"] = data["conversationId"]
    if "terminationReason" in data:
        is_antigravity = True
        if "hook_event_name" not in adapted:
            adapted["hook_event_name"] = "Stop"

    return json.dumps(adapted), is_antigravity


def adapt_output(output_text: str, is_antigravity: bool) -> str:
    """Translates Claude Code hook output to Antigravity format if needed."""
    if not is_antigravity or not output_text.strip():
        return output_text

    try:
        data = json.loads(output_text.strip())
    except Exception:
        return output_text

    if not isinstance(data, dict):
        return output_text

    # PreToolUse decision mapping
    hso = data.get("hookSpecificOutput")
    if isinstance(hso, dict):
        decision = hso.get("permissionDecision", "")
        if decision in ("deny", "block"):
            msg = data.get("systemMessage", "")
            return json.dumps({"decision": "deny", "reason": msg})
        elif decision == "ask":
            msg = data.get("systemMessage", "")
            return json.dumps({"decision": "ask", "reason": msg})
        elif decision == "allow":
            return json.dumps({"decision": "allow"})

    # Stop / general decision mapping
    if data.get("decision") == "block":
        reason = data.get("reason", data.get("systemMessage", ""))
        return json.dumps({"decision": "deny", "reason": reason})
    elif data.get("decision") == "continue":
        reason = data.get("reason", data.get("systemMessage", ""))
        return json.dumps({"decision": "continue", "reason": reason})

    return output_text


class AdaptedStdin(io.TextIOBase):
    def __init__(self, real_stdin):
        self._real_stdin = real_stdin
        self._buffer = None
        self.is_antigravity = False

    def _fill(self):
        if self._buffer is None:
            raw = self._real_stdin.read()
            adapted, self.is_antigravity = adapt_input(raw)
            self._buffer = io.StringIO(adapted)

    def read(self, size=-1):
        self._fill()
        return self._buffer.read(size)

    def readline(self, size=-1):
        self._fill()
        return self._buffer.readline(size)

    def isatty(self):
        return self._real_stdin.isatty()


class AdaptedStdout(io.TextIOBase):
    def __init__(self, real_stdout, get_stdin):
        self._real_stdout = real_stdout
        self._get_stdin = get_stdin
        self._buffer = io.StringIO()

    def write(self, s):
        self._buffer.write(s)
        return len(s)

    def flush(self):
        pass

    def finalize(self):
        content = self._buffer.getvalue()
        if content:
            is_ag = self._get_stdin().is_antigravity
            adapted = adapt_output(content, is_ag)
            self._real_stdout.write(adapted)
            self._real_stdout.flush()


def main():
    if len(sys.argv) < 3:
        print(f"usage: {sys.argv[0]} <plugin_dir> <script_path>", file=sys.stderr)
        sys.exit(1)

    plugin_dir, script_path = sys.argv[1], sys.argv[2]
    os.environ["CLAUDE_PLUGIN_ROOT"] = plugin_dir

    old_stdin = sys.stdin
    old_stdout = sys.stdout

    adapted_stdin = AdaptedStdin(old_stdin)
    adapted_stdout = AdaptedStdout(old_stdout, lambda: adapted_stdin)

    try:
        sys.stdin = adapted_stdin
        sys.stdout = adapted_stdout
        sys.argv = [script_path]
        runpy.run_path(script_path, run_name="__main__")
    except SystemExit:
        pass
    except Exception as e:
        print(f"Hook wrapper error executing {script_path}: {e}", file=sys.stderr)
    finally:
        adapted_stdout.finalize()
        sys.stdin = old_stdin
        sys.stdout = old_stdout


if __name__ == "__main__":
    main()
