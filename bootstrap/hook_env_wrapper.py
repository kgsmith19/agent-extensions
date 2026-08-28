#!/usr/bin/env python3
"""Re-invokes a translated Claude Code plugin hook script with
CLAUDE_PLUGIN_ROOT set in the process environment.

Claude Code substitutes ${CLAUDE_PLUGIN_ROOT} into a hook's command string
AND sets it as an environment variable for the spawned process. Codex's and
Antigravity's hooks.json schemas only support a plain command string (no
env field on a hook entry, confirmed against their published docs), so
bootstrap/sync.sh and sync.ps1 rewrite a translated command of the form
`python3 "<script>"` to invoke this wrapper instead, restoring the half of
Claude Code's contract their schemas can't express. Hook scripts that read
os.environ['CLAUDE_PLUGIN_ROOT'] themselves — a common pattern for locating
sibling modules, e.g. hookify's core/ package next to its hooks/ scripts —
otherwise silently no-op: PLUGIN_ROOT comes back None, the sys.path insert
is skipped, and the following import fails and is swallowed.

Usage: hook_env_wrapper.py <plugin_dir> <script_path>
"""
import os
import runpy
import sys

if len(sys.argv) < 3:
    print(f"usage: {sys.argv[0]} <plugin_dir> <script_path>", file=sys.stderr)
    sys.exit(1)

plugin_dir, script_path = sys.argv[1], sys.argv[2]
os.environ["CLAUDE_PLUGIN_ROOT"] = plugin_dir
sys.argv = [script_path]
runpy.run_path(script_path, run_name="__main__")
