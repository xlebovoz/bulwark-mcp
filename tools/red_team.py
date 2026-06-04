#!/usr/bin/env python3
"""Rules-only red-team harness for bulwark-mcp.

Loads the YAML corpus at ``red_team/corpus.yaml``, runs every attack
through the signature :class:`RulesEngine`, and prints a per-class
detection-rate table. Deterministic and offline — the LLM classifier is
intentionally out of scope.

Run::

    uv run python tools/red_team.py

Exit code is ``0`` when every class matches its ``expected`` outcome and
``1`` otherwise, so the script can gate CI later.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast, get_args

import yaml
from rich.console import Console
from rich.table import Table

from bulwark_mcp.detectors.base import Direction
from bulwark_mcp.detectors.rules import RulesEngine

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BUILTIN_RULES_DIR = _REPO_ROOT / "src" / "bulwark_mcp" / "rules" / "builtin"
_CORPUS_PATH = _REPO_ROOT / "red_team" / "corpus.yaml"

_VALID_DIRECTIONS: frozenset[str] = frozenset(get_args(Direction))
_VALID_EXPECTED: frozenset[str] = frozenset({"catch", "evade"})

_HEADER = "bulwark red-team — rules layer only"


@dataclass(frozen=True)
class Attack:
    id: str
    text: str
    comment: str | None = None


@dataclass(frozen=True)
class AttackClass:
    id: str
    description: str
    direction: Direction
    expected: str  # "catch" | "evade"
    attacks: tuple[Attack, ...]


@dataclass(frozen=True)
class ClassResult:
    cls: AttackClass
    caught: int
    evaded: int

    @property
    def count(self) -> int:
        return self.caught + self.evaded

    @property
    def detect_rate(self) -> float:
        return 100.0 * self.caught / self.count if self.count else 0.0

    @property
    def status_ok(self) -> bool:
        # A "catch" class must let nothing evade; an "evade" class must
        # catch nothing. Either way a single off-expectation hit is a
        # MISMATCH the harness reports and fails on.
        if self.cls.expected == "catch":
            return self.evaded == 0
        return self.caught == 0


def builtin_rules_dir() -> Path:
    return _BUILTIN_RULES_DIR


def build_engine() -> RulesEngine:
    return RulesEngine.from_directory(_BUILTIN_RULES_DIR)


def load_corpus(path: Path = _CORPUS_PATH) -> list[AttackClass]:
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a mapping at the top level")
    raw_classes = data.get("classes")
    if not isinstance(raw_classes, list) or not raw_classes:
        raise ValueError(f"{path}: 'classes' must be a non-empty list")
    return [_parse_class(path, raw) for raw in raw_classes]


def _parse_class(path: Path, raw: Any) -> AttackClass:
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: each class must be a mapping")
    class_id = _require_str(path, raw, "id")
    description = str(raw.get("description", ""))
    direction = _require_str(path, raw, "direction")
    if direction not in _VALID_DIRECTIONS:
        raise ValueError(f"{path}: class {class_id!r} has unknown direction {direction!r}")
    expected = _require_str(path, raw, "expected")
    if expected not in _VALID_EXPECTED:
        raise ValueError(
            f"{path}: class {class_id!r} expected must be one of {sorted(_VALID_EXPECTED)}"
        )
    raw_attacks = raw.get("attacks")
    if not isinstance(raw_attacks, list) or not raw_attacks:
        raise ValueError(f"{path}: class {class_id!r} must have a non-empty 'attacks' list")
    attacks = tuple(_parse_attack(path, class_id, a) for a in raw_attacks)
    return AttackClass(
        id=class_id,
        description=description,
        direction=cast(Direction, direction),
        expected=expected,
        attacks=attacks,
    )


def _parse_attack(path: Path, class_id: str, raw: Any) -> Attack:
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: each attack in class {class_id!r} must be a mapping")
    attack_id = _require_str(path, raw, "id")
    text = _require_str(path, raw, "text")
    comment_raw = raw.get("comment")
    comment = str(comment_raw) if comment_raw is not None else None
    return Attack(id=attack_id, text=text, comment=comment)


def _require_str(path: Path, mapping: dict[Any, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path}: missing or empty required string field {key!r}")
    return value


def evaluate(engine: RulesEngine, classes: list[AttackClass]) -> list[ClassResult]:
    """Run every attack through the engine and tally caught/evaded per class."""
    results: list[ClassResult] = []
    for cls in classes:
        caught = 0
        evaded = 0
        for attack in cls.attacks:
            if engine.detect(attack.text, direction=cls.direction).is_hit:
                caught += 1
            else:
                evaded += 1
        results.append(ClassResult(cls=cls, caught=caught, evaded=evaded))
    return results


def _build_table(results: list[ClassResult]) -> Table:
    table = Table(show_lines=False)
    table.add_column("class", style="bold")
    table.add_column("count", justify="right")
    table.add_column("caught", justify="right")
    table.add_column("evaded", justify="right")
    table.add_column("detect %", justify="right")
    table.add_column("expected")
    table.add_column("status")
    for r in results:
        ok = r.status_ok
        status_cell = "[green]OK[/green]" if ok else "[red]MISMATCH[/red]"
        table.add_row(
            r.cls.id,
            str(r.count),
            str(r.caught),
            str(r.evaded),
            f"{r.detect_rate:.1f}",
            r.cls.expected,
            status_cell,
        )
    return table


def main() -> int:
    console = Console()
    console.print(_HEADER)

    engine = build_engine()
    results = evaluate(engine, load_corpus())
    console.print(_build_table(results))

    total_attacks = sum(r.count for r in results)
    total_caught = sum(r.caught for r in results)
    overall = 100.0 * total_caught / total_attacks if total_attacks else 0.0
    all_ok = all(r.status_ok for r in results)
    console.print(
        f"{total_attacks} attacks across {len(results)} classes — "
        f"overall {overall:.1f}% caught — STATUS: {'PASS' if all_ok else 'FAIL'}"
    )

    # A catch-class that lets anything through is a real regression — name it.
    for r in results:
        if r.cls.expected == "catch" and r.evaded:
            console.print(
                f"[yellow]finding[/yellow]: class {r.cls.id!r} expected to catch all, "
                f"but {r.evaded}/{r.count} evaded the rules layer"
            )
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
