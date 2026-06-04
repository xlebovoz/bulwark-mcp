"""bulwark doctor — environment diagnostic.

Runs five checks and prints a Rich table with PASS / WARN / FAIL plus
a short suggestion per failed check. Off the hot path; safe to run
any time.

The checks are deliberately narrow. We don't try to predict every
deployment shape; we just look at the things that account for ~95% of
new-user issues:

1. Python ≥ 3.11 (the runtime contract).
2. Ollama listening + the configured model loaded (warn if not — the
   detector still works in rules-only mode).
3. The audit DB is writable and at schema version 2.
4. The shipped rules pack loads cleanly and the default policy
   validates. If the user provided a custom policies.yaml, we lint
   that too.
5. The capability filter: OK when an allowlist is configured, WARN
   (never FAIL) when it is empty — fail-open means every tool call
   passes through, which we surface rather than block silently.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from typing import Literal

import httpx

from .config import Settings
from .detectors.rules import RulesEngine
from .lint import lint_path
from .policy import Policy, default_policy
from .storage import SCHEMA_VERSION, Storage

CheckStatus = Literal["pass", "warn", "fail"]


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: CheckStatus
    detail: str
    suggestion: str | None = None


async def run_checks(settings: Settings) -> list[CheckResult]:
    """Run all five checks and return their results in display order."""
    return [
        _check_python(),
        await _check_ollama(settings),
        await _check_db(settings),
        await _check_rules_and_policy(settings),
        _check_capability(settings),
    ]


def _check_python() -> CheckResult:
    major, minor = sys.version_info[:2]
    have = f"{major}.{minor}"
    if (major, minor) >= (3, 11):
        return CheckResult(
            name="Python version",
            status="pass",
            detail=f"running {have}",
        )
    return CheckResult(
        name="Python version",
        status="fail",
        detail=f"running {have}; need >= 3.11",
        suggestion=(
            "Install a newer Python (pyenv, brew, or apt) and recreate the venv. "
            "The package's pyproject.toml pins requires-python = '>=3.11'."
        ),
    )


async def _check_ollama(settings: Settings) -> CheckResult:
    url = settings.detector.ollama_url.rstrip("/")
    model = settings.detector.ollama_model
    if not settings.detector.llm_enabled:
        return CheckResult(
            name="Ollama",
            status="warn",
            detail="LLM classifier disabled in config; rules-only mode",
            suggestion=(
                "Set detector.llm.enabled: true in your config to use the local LLM classifier."
            ),
        )
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(f"{url}/api/tags")
            resp.raise_for_status()
            tags = resp.json()
    except httpx.HTTPError as exc:
        return CheckResult(
            name="Ollama",
            status="warn",
            detail=f"cannot reach {url} ({type(exc).__name__})",
            suggestion=(
                "Start Ollama (`ollama serve`) and pull the model: "
                f"`ollama pull {model}`. The proxy still works in rules-only "
                "mode without it — the circuit breaker handles the absence."
            ),
        )
    except Exception as exc:
        return CheckResult(
            name="Ollama",
            status="warn",
            detail=f"unexpected error reading {url}/api/tags: {exc!r}",
        )
    names = {item.get("name") for item in tags.get("models", []) if isinstance(item, dict)}
    if model in names:
        return CheckResult(
            name="Ollama",
            status="pass",
            detail=f"reachable at {url}, model '{model}' loaded",
        )
    return CheckResult(
        name="Ollama",
        status="warn",
        detail=f"reachable at {url} but model '{model}' is not pulled",
        suggestion=f"Pull the configured model: `ollama pull {model}`",
    )


async def _check_db(settings: Settings) -> CheckResult:
    db_path = settings.db_path
    try:
        async with Storage(db_path) as storage:
            current = await storage._current_schema_version()
    except Exception as exc:
        return CheckResult(
            name="Audit log DB",
            status="fail",
            detail=f"cannot open {db_path} ({type(exc).__name__}: {exc})",
            suggestion=(
                "Check directory permissions on the parent path or pass "
                "--db-path /writable/dir/log.db. The proxy will not start "
                "without a writable DB."
            ),
        )
    if current == SCHEMA_VERSION:
        return CheckResult(
            name="Audit log DB",
            status="pass",
            detail=f"writable, schema version {current}",
        )
    if current < SCHEMA_VERSION:
        return CheckResult(
            name="Audit log DB",
            status="warn",
            detail=f"schema version {current} — migration to v{SCHEMA_VERSION} pending",
            suggestion=(
                "The next time you run `bulwark run ...` the migration "
                "will apply automatically. Run it once before relying on "
                "the new det_* columns."
            ),
        )
    return CheckResult(
        name="Audit log DB",
        status="warn",
        detail=f"schema version {current} is newer than this binary expects ({SCHEMA_VERSION})",
        suggestion="Upgrade bulwark-mcp, or point --db-path at a fresh DB.",
    )


async def _check_rules_and_policy(settings: Settings) -> CheckResult:
    det = settings.detector
    issues = lint_path(det.rules_dir)
    errors = [i for i in issues if i.severity == "error"]
    if errors:
        return CheckResult(
            name="Rules + policy",
            status="fail",
            detail=f"{len(errors)} error(s) in {det.rules_dir}",
            suggestion=(
                f"Run `bulwark rules lint {det.rules_dir}` for the full "
                "list. Most errors are bad regex or missing required fields."
            ),
        )
    try:
        rules = RulesEngine.from_directory(det.rules_dir)
    except Exception as exc:
        return CheckResult(
            name="Rules + policy",
            status="fail",
            detail=f"rules loader raised {type(exc).__name__}: {exc}",
        )
    try:
        if det.policies_file is not None:
            policy = Policy.from_file(det.policies_file)
            policy_origin = str(det.policies_file)
        else:
            policy = default_policy()
            policy_origin = "<built-in>"
    except Exception as exc:
        return CheckResult(
            name="Rules + policy",
            status="fail",
            detail=f"policy load failed: {type(exc).__name__}: {exc}",
            suggestion=(
                "Check policies.yaml syntax. See docs/RUNBOOK.md for the "
                "schema and a worked example."
            ),
        )
    return CheckResult(
        name="Rules + policy",
        status="pass",
        detail=(
            f"{len(rules)} rules loaded from {det.rules_dir}; "
            f"policy '{policy_origin}' has {len(policy)} rule(s) over default '{policy.default}'"
        ),
    )


def _check_capability(settings: Settings) -> CheckResult:
    """Report the capability filter's state. Never fails — an empty
    allowlist is the documented fail-open default, surfaced as a WARN."""
    cap = settings.capability
    n = len(cap.allowed_tools)
    if n > 0:
        scope = f" for server '{cap.server_name}'" if cap.server_name else ""
        return CheckResult(
            name="Capability filter",
            status="pass",
            detail=f"active — {n} tool(s) allowlisted{scope}",
        )
    return CheckResult(
        name="Capability filter",
        status="warn",
        detail="inactive — no allowlist configured; all tool calls pass through",
        suggestion=(
            "Add a `capability.allowed_tools` list (and `capability.server_name`) "
            "to your config to enable name-based filtering. See "
            "https://github.com/churik5/bulwark-mcp#capability-filter."
        ),
    )


def overall_status(results: list[CheckResult]) -> CheckStatus:
    """Worst status across all checks. ``fail`` > ``warn`` > ``pass``."""
    if any(r.status == "fail" for r in results):
        return "fail"
    if any(r.status == "warn" for r in results):
        return "warn"
    return "pass"


# ---------------------------------------------------------------------
# Entry point used by the CLI — kept here so cli.py is purely IO glue.
# ---------------------------------------------------------------------


async def doctor(settings: Settings) -> tuple[list[CheckResult], CheckStatus]:
    results = await run_checks(settings)
    return results, overall_status(results)


def doctor_sync(settings: Settings) -> tuple[list[CheckResult], CheckStatus]:
    """Synchronous wrapper — convenient for the CLI which does not own
    an event loop yet at the time it calls doctor."""
    return asyncio.run(doctor(settings))
