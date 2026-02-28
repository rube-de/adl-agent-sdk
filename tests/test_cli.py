"""Tests for CLI skeleton."""

from typer.testing import CliRunner

from auto_dev_loop.cli import app


runner = CliRunner()


def test_cli_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.stdout


def test_cli_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "auto-dev-loop" in result.stdout.lower() or "adl" in result.stdout.lower()


def test_cli_run_help():
    result = runner.invoke(app, ["run", "--help"])
    assert result.exit_code == 0
    assert "config" in result.stdout.lower()
