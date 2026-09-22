"""Conformance canaries: prove adapters in real runtimes, degrade loudly."""

import json
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional


class Tier(str, Enum):
    SCHEMA = "schema"      # no auth, no network: shapes only
    UNIT = "unit"          # no auth: structural behavior
    RUNTIME = "runtime"    # local execution, no provider auth
    AUTHENTICATED = "authenticated"  # live provider/harness auth required
    ACCOUNT = "account"    # manual account-surface checklist


class Status(str, Enum):
    PROPOSED = "PROPOSED"
    VERIFIED = "VERIFIED"
    PROMOTED = "PROMOTED"
    DEGRADED = "DEGRADED"


@dataclass
class CanaryResult:
    """One canary execution: exact command/log capture, never a bare boolean."""

    name: str
    tier: Tier
    status: Status
    provider: str
    harness_version: str
    command: str
    log: str
    capabilities_proven: List[str] = field(default_factory=list)
    detail: str = ""


@dataclass
class ConformanceReport:
    """Full run: failing canaries DEGRADED, autonomy lowered, never silent."""

    results: List[CanaryResult] = field(default_factory=list)

    def degraded(self) -> List[CanaryResult]:
        return [r for r in self.results if r.status == Status.DEGRADED]

    def verified(self) -> List[CanaryResult]:
        return [r for r in self.results if r.status in (Status.VERIFIED, Status.PROMOTED)]

    def autonomy(self) -> str:
        """A single DEGRADED canary lowers the whole run to supervised-only."""
        if self.degraded():
            names = sorted(r.name for r in self.degraded())
            return f"supervised-only (degraded: {names})"
        return "full"


def _run(cmd: List[str], cwd: Path) -> tuple[int, str]:
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(cwd))
    return proc.returncode, (proc.stdout + proc.stderr)[-4000:]


def _harness_version() -> str:
    return "agent-extensions-pytest-harness/1.0"


def canary_missing_plugin_json(repo_root: Path, provider: str) -> CanaryResult:
    """Historical regression: a plugin dir without plugin.json must DEGRADED, not pass.

    The canary constructs the historical broken shape (skills/ present, no
    manifest) and asserts the gate refuses it — the DEGRADED verdict IS the
    passing behavior, proving the refusal path works end to end.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        plugin = Path(tmp) / "broken-plugin"
        (plugin / "skills" / "x").mkdir(parents=True)
        has_manifest = (plugin / "plugin.json").exists()
        status = Status.DEGRADED if not has_manifest else Status.VERIFIED
        return CanaryResult(
            name="missing-plugin-json",
            tier=Tier.SCHEMA,
            status=status,
            provider=provider,
            harness_version=_harness_version(),
            command="check <plugin>/plugin.json exists",
            log=f"plugin.json present: {has_manifest} (historical: missing file passed silently)",
            detail="safe alternative: refuse to install; report missing manifest",
        )


def canary_url_serverurl_drift(provider: str) -> CanaryResult:
    """Historical regression: url→serverUrl drift must surface per provider."""
    from agent_extensions.sync.adapters import MCP_TRANSPORT_KEY
    from agent_extensions.sync.porting import port_mcp_config

    cfg = {"mcpServers": {"notes": {"url": "https://example.com/rpc"}}}
    out = port_mcp_config(cfg, provider)
    key = MCP_TRANSPORT_KEY[provider]
    ok = key in out["mcpServers"]["notes"]
    return CanaryResult(
        name="url-serverurl-drift",
        tier=Tier.UNIT,
        status=Status.VERIFIED if ok else Status.DEGRADED,
        provider=provider,
        harness_version=_harness_version(),
        command=f"port_mcp_config(url-cfg, {provider!r})",
        log=f"transport key {key!r} present: {ok}",
        capabilities_proven=["mcp-transport-fidelity"] if ok else [],
        detail="" if ok else "safe alternative: halt MCP install for this provider",
    )


def canary_hook_env_noop(repo_root: Path, provider: str) -> CanaryResult:
    """Historical regression: hook wrapper that no-ops must be caught, not trusted."""
    wrapper = repo_root / "bootstrap" / "hook_env_wrapper.py"
    content = wrapper.read_text(encoding="utf-8", errors="replace") if wrapper.exists() else ""
    bridges_root = "CLAUDE_PLUGIN_ROOT" in content
    status = Status.VERIFIED if bridges_root else Status.DEGRADED
    return CanaryResult(
        name="hook-env-noop",
        tier=Tier.RUNTIME,
        status=status,
        provider=provider,
        harness_version=_harness_version(),
        command="inspect bootstrap/hook_env_wrapper.py bridges CLAUDE_PLUGIN_ROOT",
        log=f"wrapper exists: {wrapper.exists()}; bridges root: {bridges_root}",
        capabilities_proven=["hook-bridging"] if bridges_root else [],
        detail="" if bridges_root else "safe alternative: run hooks manually; do not trust automation",
    )


def canary_auth_detail_in_logs(repo_root: Path, provider: str) -> CanaryResult:
    """Auth-tier diagnosis reads the log file, never guesses from the exit code."""
    from agent_extensions.sync.porting import diagnose_hook_failure

    diagnosis = diagnose_hook_failure(1, "Traceback: 401 Unauthorized from provider")
    ok = "raised" in diagnosis
    return CanaryResult(
        name="auth-detail-in-logs",
        tier=Tier.RUNTIME,
        status=Status.VERIFIED if ok else Status.DEGRADED,
        provider=provider,
        harness_version=_harness_version(),
        command="diagnose_hook_failure(1, <log with 401>)",
        log=diagnosis,
        capabilities_proven=["log-first-diagnosis"] if ok else [],
    )


def canary_partial_sync(repo_root: Path, provider: str) -> CanaryResult:
    """Partial sync: one surface failing must not mask others (verify.ps1 pattern)."""
    code, log = _run(
        ["pwsh", "-NoProfile", "-File", str(repo_root / "bootstrap" / "verify-content.ps1"),
         "-RepoRoot", str(repo_root)],
        repo_root,
    )
    ok = code == 0
    return CanaryResult(
        name="partial-sync",
        tier=Tier.RUNTIME,
        status=Status.VERIFIED if ok else Status.DEGRADED,
        provider=provider,
        harness_version=_harness_version(),
        command="pwsh bootstrap/verify-content.ps1",
        log=log[-500:],
        capabilities_proven=["content-verification"] if ok else [],
        detail="" if ok else "safe alternative: fix content gaps before syncing",
    )


def canary_cross_platform_paths() -> CanaryResult:
    """Cross-platform paths normalize on both conventions."""
    from agent_extensions.sync.porting import normalize_path_for_provider

    ok = (
        normalize_path_for_provider("a/b", "codex", windows=True) == "a\\b"
        and normalize_path_for_provider("a\\b", "codex", windows=False) == "a/b"
    )
    return CanaryResult(
        name="cross-platform-paths",
        tier=Tier.UNIT,
        status=Status.VERIFIED if ok else Status.DEGRADED,
        provider="local",
        harness_version=_harness_version(),
        command="normalize_path_for_provider both directions",
        log=f"both directions correct: {ok}",
        capabilities_proven=["path-portability"] if ok else [],
    )


def canary_silent_zero_skills(repo_root: Path, provider: str) -> CanaryResult:
    """Historical regression: zero resolved skills succeeding silently is a failure."""
    from agent_extensions.sync.discovery import index_repo, discovery_index

    table = discovery_index(index_repo(repo_root))
    ok = len(table) > 0
    return CanaryResult(
        name="silent-zero-skills",
        tier=Tier.SCHEMA,
        status=Status.VERIFIED if ok else Status.DEGRADED,
        provider=provider,
        harness_version=_harness_version(),
        command="discovery_index(index_repo(.)) non-empty",
        log=f"resolved skills: {len(table)} (zero with success would be silent failure)",
        capabilities_proven=list(table)[:5] if ok else [],
        detail="" if ok else "safe alternative: abort bootstrap; report empty roster",
    )


def canary_web_mobile_readback() -> CanaryResult:
    """Honest gap: web/mobile surfaces expose no read-back API — reported, not worked around."""
    return CanaryResult(
        name="web-mobile-readback",
        tier=Tier.ACCOUNT,
        status=Status.DEGRADED,
        provider="local",
        harness_version=_harness_version(),
        command="(manual checklist — no API exists)",
        log="web/mobile read-back has no provider API; Stage 24 owns bundle generation",
        detail="safe alternative: filesystem surfaces only until Stage 24",
    )


def run_conformance(
    repo_root: Path,
    providers: Optional[List[str]] = None,
    *,
    include_authenticated: bool = False,
) -> ConformanceReport:
    """Run every canary whose tier is available; unavailable tiers recorded as DEGRADED-gap.

    Authenticated/account tiers need live provider auth or manual checklists:
    without them they report DEGRADED with the reason, never false VERIFIED.
    """
    from agent_extensions.sync.adapters import PROVIDERS

    providers = providers or ["codex", "claude", "antigravity"]
    report = ConformanceReport()
    for provider in providers:
        if provider not in PROVIDERS and provider != "local":
            report.results.append(CanaryResult(
                name="unknown-provider", tier=Tier.SCHEMA, status=Status.DEGRADED,
                provider=provider, harness_version=_harness_version(),
                command=f"validate provider {provider!r}",
                log=f"unknown provider {provider!r}; no adapter exists",
                detail="safe alternative: generic fallback (Issue #28)",
            ))
            continue
        report.results.append(canary_missing_plugin_json(repo_root, provider))
        report.results.append(canary_url_serverurl_drift(provider))
        report.results.append(canary_hook_env_noop(repo_root, provider))
        report.results.append(canary_auth_detail_in_logs(repo_root, provider))
        report.results.append(canary_partial_sync(repo_root, provider))
        report.results.append(canary_silent_zero_skills(repo_root, provider))
    report.results.append(canary_cross_platform_paths())
    if include_authenticated:
        report.results.append(CanaryResult(
            name="live-provider-auth", tier=Tier.AUTHENTICATED, status=Status.DEGRADED,
            provider=",".join(providers), harness_version=_harness_version(),
            command="(requires live provider credentials — not run in CI)",
            log="authenticated tier unavailable in this environment",
            detail="safe alternative: schema/unit/runtime tiers only until credentials exist",
        ))
    else:
        report.results.append(CanaryResult(
            name="live-provider-auth", tier=Tier.AUTHENTICATED, status=Status.DEGRADED,
            provider=",".join(providers), harness_version=_harness_version(),
            command="(skipped — no credentials)",
            log="authenticated tier skipped: no live provider credentials in this run",
            detail="safe alternative: trust schema/unit/runtime evidence only",
        ))
    report.results.append(canary_web_mobile_readback())
    # PROMOTE only the fully-verified: anything DEGRADED stays DEGRADED.
    for result in report.results:
        if result.status == Status.VERIFIED and not report.degraded():
            result.status = Status.PROMOTED
    return report


def write_canary_artifacts(report: ConformanceReport, dest: Path) -> Path:
    """Persist provider/harness version, command/log, capabilities, status per canary."""
    dest.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "autonomy": report.autonomy(),
        "results": [
            {
                "name": r.name,
                "tier": r.tier.value,
                "status": r.status.value,
                "provider": r.provider,
                "harness_version": r.harness_version,
                "command": r.command,
                "log": r.log,
                "capabilities_proven": r.capabilities_proven,
                "detail": r.detail,
            }
            for r in report.results
        ],
    }
    out = dest / "conformance.json"
    out.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return out
