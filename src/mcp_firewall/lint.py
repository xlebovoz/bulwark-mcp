"""YAML rule-pack linter (Week 3).

Two modes:

- **Basic** — every rule must load cleanly through
  :func:`detectors.rules._compile_rule`. This catches syntax errors,
  unknown directions, missing required fields, and bad regex. Mirrors
  what ``RulesEngine.from_directory`` enforces.

- **Strict** — basic plus quality checks recommended for inclusion in
  the built-in pack:

  - ``description`` is at least 10 characters.
  - ``source`` is a HTTP(S) URL (so reviewers can follow it).
  - ``severity_tier`` is ``experimental`` or ``stable``.
  - ``attack_examples`` lists at least one string the rule should
    catch.

The promotion ladder lives in ``CONTRIBUTING.md``: community packs only
need basic; built-in promotion requires strict + at least two tests
(positive case + false-positive case) added in the same PR.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

from .detectors.rules import _compile_rule


Severity = Literal["error", "warning"]

_VALID_TIERS: frozenset[str] = frozenset(("experimental", "stable"))
_URL_RE = re.compile(r"^https?://[^\s]+$")


@dataclass(frozen=True)
class LintIssue:
    severity: Severity
    rule_id: str | None
    message: str
    file: Path

    def render(self) -> str:
        sev = self.severity.upper().rjust(7)
        rid = self.rule_id or "<file>"
        return f"{sev}  {self.file}::{rid}  {self.message}"


def lint_path(path: Path, *, strict: bool = False) -> list[LintIssue]:
    """Lint a rule pack (file) or a directory of packs.

    Returns an empty list when the lint passes; a non-empty list of
    :class:`LintIssue` otherwise. Errors fail both modes; warnings
    fail only ``strict``.
    """
    path = Path(path)
    if path.is_file():
        files = [path]
    elif path.is_dir():
        files = sorted(path.rglob("*.yaml"))
        if not files:
            return [
                LintIssue(
                    severity="error",
                    rule_id=None,
                    message="no .yaml files found",
                    file=path,
                )
            ]
    else:
        return [
            LintIssue(
                severity="error",
                rule_id=None,
                message=f"path does not exist: {path}",
                file=path,
            )
        ]

    issues: list[LintIssue] = []
    for yaml_file in files:
        issues.extend(_lint_one(yaml_file, strict=strict))
    return issues


def _lint_one(yaml_file: Path, *, strict: bool) -> list[LintIssue]:
    issues: list[LintIssue] = []

    try:
        with yaml_file.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except (yaml.YAMLError, OSError) as exc:
        return [
            LintIssue(
                severity="error",
                rule_id=None,
                message=f"YAML parse failed: {exc}",
                file=yaml_file,
            )
        ]

    if not isinstance(data, dict):
        return [
            LintIssue(
                severity="error",
                rule_id=None,
                message="top-level must be a mapping",
                file=yaml_file,
            )
        ]
    raw_rules = data.get("rules", [])
    if not isinstance(raw_rules, list):
        return [
            LintIssue(
                severity="error",
                rule_id=None,
                message="'rules' must be a list",
                file=yaml_file,
            )
        ]

    for raw in raw_rules:
        rule_id = raw.get("id") if isinstance(raw, dict) else None
        rid = str(rule_id) if rule_id else None
        # ---- Basic check: does the rule compile? ----
        try:
            compiled = _compile_rule(yaml_file, raw)
        except ValueError as exc:
            issues.append(
                LintIssue(
                    severity="error",
                    rule_id=rid,
                    message=str(exc).split(":", 1)[-1].strip(),
                    file=yaml_file,
                )
            )
            continue

        # ---- Strict checks: quality of metadata. ----
        if strict:
            issues.extend(_strict_checks(compiled, raw, yaml_file))

    return issues


def _strict_checks(
    compiled: Any, raw: dict[str, Any], yaml_file: Path
) -> list[LintIssue]:
    """Rules-pack hygiene checks for promotion to the built-in pack."""
    out: list[LintIssue] = []

    if len(compiled.description) < 10:
        out.append(
            LintIssue(
                severity="warning",
                rule_id=compiled.id,
                message="description is shorter than 10 chars; explain what attack class this catches",
                file=yaml_file,
            )
        )

    if not compiled.source:
        out.append(
            LintIssue(
                severity="warning",
                rule_id=compiled.id,
                message="source is missing; add a public URL (paper, blog, garak/promptfoo entry)",
                file=yaml_file,
            )
        )
    elif not _URL_RE.match(compiled.source):
        out.append(
            LintIssue(
                severity="warning",
                rule_id=compiled.id,
                message=f"source must be http(s):// URL; got {compiled.source!r}",
                file=yaml_file,
            )
        )

    tier = raw.get("severity_tier")
    if tier is None:
        out.append(
            LintIssue(
                severity="warning",
                rule_id=compiled.id,
                message="severity_tier is unset; pick 'experimental' (community) or 'stable' (built-in)",
                file=yaml_file,
            )
        )
    elif tier not in _VALID_TIERS:
        out.append(
            LintIssue(
                severity="warning",
                rule_id=compiled.id,
                message=f"severity_tier must be one of {sorted(_VALID_TIERS)}; got {tier!r}",
                file=yaml_file,
            )
        )

    examples = raw.get("attack_examples")
    if not isinstance(examples, list) or not examples:
        out.append(
            LintIssue(
                severity="warning",
                rule_id=compiled.id,
                message="attack_examples is missing or empty; add at least one PoC string the rule should catch",
                file=yaml_file,
            )
        )
    else:
        non_strings = [e for e in examples if not isinstance(e, str)]
        if non_strings:
            out.append(
                LintIssue(
                    severity="warning",
                    rule_id=compiled.id,
                    message="attack_examples must be a list of strings",
                    file=yaml_file,
                )
            )
        else:
            non_matching = [e for e in examples if not compiled.pattern.search(e)]
            if non_matching:
                out.append(
                    LintIssue(
                        severity="warning",
                        rule_id=compiled.id,
                        message=(
                            f"{len(non_matching)} attack_example(s) do not match the rule's "
                            "pattern; the rule and the examples have drifted apart"
                        ),
                        file=yaml_file,
                    )
                )

    fp_examples = raw.get("false_positive_examples")
    if isinstance(fp_examples, list) and fp_examples:
        firing = [e for e in fp_examples if isinstance(e, str) and compiled.pattern.search(e)]
        if firing:
            out.append(
                LintIssue(
                    severity="warning",
                    rule_id=compiled.id,
                    message=(
                        f"{len(firing)} false_positive_example(s) DO match the pattern — "
                        "tighten the regex or move the example out of FP list"
                    ),
                    file=yaml_file,
                )
            )

    return out
