"""Typer CLI entry point for Auto Dev Loop."""

from pathlib import Path

import typer

from ._paths import ADL_CONFIG

app = typer.Typer(name="adl", help="Auto Dev Loop — autonomous development daemon")


def _version_callback(value: bool) -> None:
    if value:
        typer.echo("auto-dev-loop 0.1.0")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False, "--version", "-V",
        callback=_version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    pass


@app.command()
def run(
    config: Path = typer.Option(
        ADL_CONFIG,
        "--config", "-c",
        help="Path to config YAML file.",
    ),
    once: bool = typer.Option(
        False, "--once",
        help="Run one poll cycle and exit (useful for testing).",
    ),
) -> None:
    """Start the Auto Dev Loop daemon."""
    from .main import run_daemon
    run_daemon(str(config), once=once)


@app.command()
def validate(
    config: Path = typer.Option(
        ADL_CONFIG,
        "--config", "-c",
        help="Path to config YAML file.",
    ),
) -> None:
    """Validate config, agents, and workflows."""
    from .config import load_config, resolve_repo_config, ConfigError
    from .models import RepoConfig

    try:
        cfg = load_config(config)
        # Validate per-repo overrides by resolving each repo
        for i, repo_cfg in enumerate(cfg.repos):
            if not isinstance(repo_cfg, RepoConfig):
                continue
            try:
                resolve_repo_config(repo_cfg, cfg)
            except (TypeError, KeyError, ValueError, AttributeError, ConfigError) as e:
                raise ConfigError(f"Invalid per-repo override in repos[{i}] ({repo_cfg.path}): {e}")
        typer.echo(f"Config OK: {len(cfg.repos)} repo(s), version {cfg.version}")
    except ConfigError as e:
        typer.echo(f"Config error: {e}", err=True)
        raise typer.Exit(1)


@app.command()
def init(
    config: Path = typer.Option(
        ADL_CONFIG,
        "--config", "-c",
        help="Path where the generated config YAML should be written.",
    ),
) -> None:
    """Run the one-time setup wizard and generate config.yaml."""
    from .init_wizard import run_init_wizard

    run_init_wizard(config)


@app.command()
def add(
    path: Path | None = typer.Argument(
        None,
        help="Path to the git repository to onboard. Defaults to current directory.",
    ),
    config: Path = typer.Option(
        ADL_CONFIG,
        "--config", "-c",
        help="Path to config YAML file.",
    ),
) -> None:
    """Onboard a repository — detect GitHub info, scaffold agents, update config."""
    from .add_repo import run_add_wizard

    run_add_wizard(path, config)
