from __future__ import annotations

from typer.testing import CliRunner

from bank_risk.cli import app


def test_documented_scan_command_is_registered() -> None:
    result = CliRunner().invoke(app, ["scan", "--help"])
    assert result.exit_code == 0
    assert "--as-of" in result.output
    assert "--quarters" in result.output


def test_simulate_command_exposes_explicit_scenario_and_bank_selection() -> None:
    result = CliRunner().invoke(app, ["simulate", "--help"])
    assert result.exit_code == 0
    assert "--scenario" in result.output
    assert "--cert" in result.output


def test_export_help_does_not_offer_a_nonfunctional_format_switch() -> None:
    result = CliRunner().invoke(app, ["export", "--help"])
    assert result.exit_code == 0
    assert "--as-of" in result.output
    assert "--format" not in result.output
