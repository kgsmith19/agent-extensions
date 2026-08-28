# bootstrap/account-bundles.test.sh
#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=/dev/null
source "$REPO_ROOT/bootstrap/sync.sh" --import

SCRATCH="$(mktemp -d)"
DECLARE_ROOT="$SCRATCH/declare-root"
STAGED_DIR="$SCRATCH/staged"
mkdir -p "$DECLARE_ROOT/bootstrap" "$DECLARE_ROOT/plugins/demo/skills/greet"
echo "# Greet" > "$DECLARE_ROOT/plugins/demo/skills/greet/SKILL.md"

cat > "$DECLARE_ROOT/bootstrap/account-manifest.json" <<'EOF'
{
  "skills": [
    { "name": "greet", "source": "plugins/demo/skills/greet" },
    { "name": "missing-skill", "source": "plugins/demo/skills/nope" }
  ],
  "connectors": [{ "name": "some-connector", "description": "d" }]
}
EOF
cat > "$DECLARE_ROOT/bootstrap/account-manifest.last-applied.json" <<'EOF'
{ "skills": [{ "name": "old-skill", "source": "x" }], "connectors": [] }
EOF

failures=()

# --- Staging: valid skill copied as a real file, not a symlink ---
STDERR_CAPTURE="$SCRATCH/stderr.txt"
REPORTED_OK=1
sync_account_bundles "$DECLARE_ROOT" "$STAGED_DIR" >"$SCRATCH/stdout.txt" 2>"$STDERR_CAPTURE" || REPORTED_OK=0
if [ "$REPORTED_OK" = "1" ]; then
  failures+=("Expected sync_account_bundles to return non-zero because of missing-skill")
fi
if [ ! -f "$STAGED_DIR/greet/SKILL.md" ]; then
  failures+=("greet's SKILL.md was not staged")
elif [ -L "$STAGED_DIR/greet" ] || [ -L "$STAGED_DIR/greet/SKILL.md" ]; then
  failures+=("Staged bundle must be a real copy, not a symlink — claude.ai's upload flow does not resolve links")
fi
if ! grep -q "missing-skill" "$STDERR_CAPTURE"; then
  failures+=("Expected a reported failure mentioning missing-skill")
fi

# --- Checklist: diffs current manifest against last-applied ---
if ! grep -q "ADD:" "$SCRATCH/stdout.txt"; then
  failures+=("Expected an ADD section in the checklist")
fi
if ! grep -q "skill: greet" "$SCRATCH/stdout.txt"; then
  failures+=("Expected 'greet' to be listed under ADD")
fi
if ! grep -q "connector: some-connector" "$SCRATCH/stdout.txt"; then
  failures+=("Expected 'some-connector' to be listed under ADD")
fi
if ! grep -q "REMOVE:" "$SCRATCH/stdout.txt"; then
  failures+=("Expected a REMOVE section (old-skill is in last-applied but not current)")
fi
if ! grep -q "skill: old-skill" "$SCRATCH/stdout.txt"; then
  failures+=("Expected 'old-skill' to be listed under REMOVE")
fi
if echo "$(cat "$SCRATCH/stdout.txt")" | grep -qE '"(skill|connector):'; then
  failures+=("Checklist entries must be plain text, not JSON-quoted strings")
fi

# --- confirm_account_applied: snapshots current manifest over last-applied ---
confirm_account_applied "$DECLARE_ROOT" >/dev/null
if ! diff -q "$DECLARE_ROOT/bootstrap/account-manifest.json" "$DECLARE_ROOT/bootstrap/account-manifest.last-applied.json" >/dev/null; then
  failures+=("confirm_account_applied did not make last-applied match the current manifest")
fi

# --- After confirming, the checklist reports up to date ---
UP_TO_DATE_OUT="$(sync_account_bundles "$DECLARE_ROOT" "$STAGED_DIR" 2>/dev/null || true)"
if ! echo "$UP_TO_DATE_OUT" | grep -q "up to date"; then
  failures+=("Expected 'up to date' after confirm_account_applied, got: $UP_TO_DATE_OUT")
fi

rm -rf "$SCRATCH"

if [ ${#failures[@]} -gt 0 ]; then
  echo "FAIL: account bundles / checklist / confirm"
  printf '  - %s\n' "${failures[@]}"
  exit 1
fi

echo "PASS: account bundles stage, checklist ADD/REMOVE, confirm-applied snapshot"
exit 0
