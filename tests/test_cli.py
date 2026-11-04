from typer.testing import CliRunner

from mecha import __version__
from mecha.cli import app

runner = CliRunner()


def test_version() -> None:
    r = runner.invoke(app, ["--version"])
    assert r.exit_code == 0
    assert __version__ in r.stdout


def test_serve_help_lists_options() -> None:
    r = runner.invoke(app, ["serve", "--help"])
    assert r.exit_code == 0
    for opt in ("--port", "--no-open", "--demo", "--dev"):
        assert opt in r.stdout
