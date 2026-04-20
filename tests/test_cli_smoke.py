from typer.testing import CliRunner

from magpie.cli import app

runner = CliRunner()


def test_help_runs():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "magpie" in result.stdout.lower()


def test_subcommands_listed():
    result = runner.invoke(app, ["--help"])
    for cmd in ("tag", "watch", "config"):
        assert cmd in result.stdout
