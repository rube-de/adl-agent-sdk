"""Tests for CLI skeleton."""

from pathlib import Path

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


def test_cli_init_help():
    result = runner.invoke(app, ["init", "--help"])
    assert result.exit_code == 0
    assert "wizard" in result.stdout.lower() or "config" in result.stdout.lower()


def test_cli_init_invokes_wizard(monkeypatch, tmp_path: Path):
    called: dict[str, Path] = {}

    def fake_run_init_wizard(config_path: Path) -> None:
        called["config_path"] = config_path

    monkeypatch.setattr(
        "auto_dev_loop.init_wizard.run_init_wizard",
        fake_run_init_wizard,
    )

    config_path = tmp_path / "config.yaml"
    result = runner.invoke(app, ["init", "--config", str(config_path)])

    assert result.exit_code == 0
    assert called["config_path"] == config_path
