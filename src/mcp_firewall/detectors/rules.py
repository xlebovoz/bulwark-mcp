"""YAML-driven rules detector (ADR-0004 §2).

A rule pack is a YAML file with this shape::

    rules:
      - id: role_hijack.ignore_previous
        description: "'Ignore previous instructions' family"
        pattern: '(?i)ignore\\s+(?:all\\s+)?previous\\s+instructions?'
        score: 0.85
        apply_to: [server_to_client]
        source: "https://simonwillison.net/..."

The loader compiles every regex up front so detection itself does no
parsing. A single :class:`RulesEngine` is shared by all directions; per
call we filter by ``apply_to`` so c2s frames never run s2c-only patterns
and vice versa.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, get_args

import yaml

from .base import Direction, RulesResult

logger = logging.getLogger(__name__)

_VALID_DIRECTIONS: frozenset[str] = frozenset(get_args(Direction))


@dataclass(frozen=True)
class CompiledRule:
    id: str
    description: str
    pattern: re.Pattern[str]
    score: float
    apply_to: tuple[Direction, ...]
    source: str | None = None


class RulesEngine:
    """In-memory bag of compiled rules.

    Construction is the only point that may raise. Detection itself is
    pure CPU work and never throws — at worst it returns an empty result.
    """

    def __init__(self, rules: Iterable[CompiledRule]) -> None:
        self._rules: tuple[CompiledRule, ...] = tuple(rules)

    @property
    def rules(self) -> tuple[CompiledRule, ...]:
        return self._rules

    def __len__(self) -> int:
        return len(self._rules)

    @classmethod
    def from_directory(cls, directory: Path) -> RulesEngine:
        """Load every ``*.yaml`` under ``directory`` recursively."""
        directory = Path(directory)
        if not directory.is_dir():
            raise FileNotFoundError(f"rules directory not found: {directory}")
        compiled: list[CompiledRule] = []
        seen_ids: set[str] = set()
        for yaml_file in sorted(directory.rglob("*.yaml")):
            for rule in _load_pack(yaml_file):
                if rule.id in seen_ids:
                    raise ValueError(f"duplicate rule id {rule.id!r} (last seen in {yaml_file})")
                seen_ids.add(rule.id)
                compiled.append(rule)
        logger.info("rules: loaded %d rules from %s", len(compiled), directory)
        return cls(compiled)

    def detect(self, text: str, *, direction: Direction) -> RulesResult:
        """Scan ``text`` and return matching rule ids + the max score.

        The check is bounded by the number of rules; for a few-dozen
        ruleset and tens of kilobytes of text we measure ~0.5-3 ms on
        an M-series Mac (see ``tests/test_perf.py``).
        """
        if not text:
            return RulesResult()
        hits: list[str] = []
        max_score = 0.0
        for rule in self._rules:
            if direction not in rule.apply_to:
                continue
            if rule.pattern.search(text):
                hits.append(rule.id)
                if rule.score > max_score:
                    max_score = rule.score
        return RulesResult(hits=tuple(hits), score=max_score)


def _load_pack(path: Path) -> list[CompiledRule]:
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected mapping at top level")
    raw_rules = data.get("rules", [])
    if not isinstance(raw_rules, list):
        raise ValueError(f"{path}: 'rules' must be a list")
    return [_compile_rule(path, rule) for rule in raw_rules]


def _compile_rule(source_file: Path, raw: Any) -> CompiledRule:
    if not isinstance(raw, dict):
        raise ValueError(f"{source_file}: each rule must be a mapping")

    rule_id = _required_str(raw, "id", source_file)
    pattern_str = _required_str(raw, "pattern", source_file)
    description = str(raw.get("description", ""))

    score_raw = raw.get("score", 0.5)
    try:
        score = float(score_raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{source_file}: rule {rule_id!r} score must be a number") from exc
    if not 0.0 <= score <= 1.0:
        raise ValueError(f"{source_file}: rule {rule_id!r} score {score} outside [0.0, 1.0]")

    apply_to_raw = raw.get("apply_to", ["server_to_client"])
    if isinstance(apply_to_raw, str):
        apply_to_raw = [apply_to_raw]
    if not isinstance(apply_to_raw, list):
        raise ValueError(f"{source_file}: rule {rule_id!r} apply_to must be a string or list")
    bad = [d for d in apply_to_raw if d not in _VALID_DIRECTIONS]
    if bad:
        raise ValueError(f"{source_file}: rule {rule_id!r} apply_to has unknown directions: {bad}")
    apply_to: tuple[Direction, ...] = tuple(apply_to_raw)

    try:
        pattern = re.compile(pattern_str, re.MULTILINE)
    except re.error as exc:
        raise ValueError(f"{source_file}: rule {rule_id!r} pattern invalid: {exc}") from exc

    source = raw.get("source")
    return CompiledRule(
        id=rule_id,
        description=description,
        pattern=pattern,
        score=score,
        apply_to=apply_to,
        source=str(source) if source is not None else None,
    )


def _required_str(mapping: dict[str, Any], key: str, source_file: Path) -> str:
    if key not in mapping:
        raise ValueError(f"{source_file}: rule missing required field {key!r}")
    value = mapping[key]
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{source_file}: rule field {key!r} must be a non-empty string")
    return value
