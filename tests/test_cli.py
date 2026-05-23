"""Tests for CLI-only commands."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from bulwark_mcp import __version__
from bulwark_mcp.cli import main


def test_version_command_prints_extended_info(tmp_path: Path) -> None:
    db_path = tmp_path / "audit.db"
    result = CliRunner().invoke(main, ["version", "--db-path", str(db_path)])

    assert result.exit_code == 0
    assert f"bulwark-mcp {__version__}" in result.output
    assert "Python " in result.output
    assert "Platform: " in result.output
    assert "Rules loaded: " in result.output
    assert "Detector: off (config default)" in result.output
    assert "DB schema: v2" in result.output
    assert "Install path: " in result.output


def test_short_version_flag_is_unchanged() -> None:
    result = CliRunner().invoke(main, ["--version"], prog_name="bulwark")

    assert result.exit_code == 0
    assert result.output == f"bulwark, version {__version__}\n"
