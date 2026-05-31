"""Validates the red-team corpus and runs the rules-only harness end-to-end.

These tests exercise ``tools/red_team.py`` directly — the test imports the
harness module so the corpus check and the script can never drift. The
corpus lives at ``red_team/corpus.yaml``; the rules layer is the only
detector involved (no LLM, fully deterministic).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from bulwark_mcp.detectors.rules import RulesEngine

# tools/ is a standalone script directory (not a package), so make it
# importable before pulling in the harness module.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_TOOLS_DIR = _REPO_ROOT / "tools"
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

import red_team  # noqa: E402  (tools/ added to sys.path just above)

_BUILTIN_DIR = _REPO_ROOT / "src" / "bulwark_mcp" / "rules" / "builtin"


@pytest.fixture(scope="module")
def builtin_engine() -> RulesEngine:
    return RulesEngine.from_directory(_BUILTIN_DIR)


@pytest.fixture(scope="module")
def corpus() -> list[red_team.AttackClass]:
    return red_team.load_corpus()


class TestRedTeamCorpus:
    def test_corpus_loads_and_is_well_formed(
        self, corpus: list[red_team.AttackClass]
    ) -> None:
        # Structural contract: at least one class, every class has attacks,
        # every attack has non-empty text, enums are in range, ids unique.
        assert corpus, "corpus must define at least one class"
        seen_class_ids: set[str] = set()
        for cls in corpus:
            assert cls.id not in seen_class_ids, f"duplicate class id {cls.id!r}"
            seen_class_ids.add(cls.id)
            assert cls.expected in {"catch", "evade"}, (
                f"class {cls.id!r} has bad expected {cls.expected!r}"
            )
            assert cls.direction in {"server_to_client", "client_to_server"}, (
                f"class {cls.id!r} has bad direction {cls.direction!r}"
            )
            assert cls.attacks, f"class {cls.id!r} has no attacks"
            seen_attack_ids: set[str] = set()
            for attack in cls.attacks:
                assert attack.text.strip(), f"empty attack text in class {cls.id!r}"
                assert attack.id not in seen_attack_ids, (
                    f"duplicate attack id {attack.id!r} in class {cls.id!r}"
                )
                seen_attack_ids.add(attack.id)

    def test_corpus_matches_expectations(
        self, builtin_engine: RulesEngine, corpus: list[red_team.AttackClass]
    ) -> None:
        # The real regression gate: run the harness logic and assert every
        # class's measured outcome agrees with its declared `expected`. If a
        # rule change flips a class, this goes red.
        results = red_team.evaluate(builtin_engine, corpus)
        for r in results:
            assert r.status_ok, (
                f"class {r.cls.id!r} expected {r.cls.expected!r} but measured "
                f"{r.caught} caught / {r.evaded} evaded "
                f"(detect rate {r.detect_rate:.1f}%)"
            )
