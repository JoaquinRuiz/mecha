from __future__ import annotations

import json
import re

import keyring
import pytest
from typer.testing import CliRunner

from mecha.cli import app
from mecha.core.paths import db_path
from tests.fakes import body, fake, fake_aiza
from tests.memkeyring import NoKeyring

runner = CliRunner()
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def run(*args: str, input: str | None = None) -> tuple[int, str]:
    r = runner.invoke(app, list(args), input=input, env={"COLUMNS": "250"})
    return r.exit_code, ANSI.sub("", r.output)


def ls_json() -> dict[str, object]:
    r = runner.invoke(app, ["ls", "--json"])
    return json.loads(r.stdout)  # type: ignore[no-any-return]


def test_add_from_stdin_and_ls() -> None:
    code, output = run("add", "--label", "prod", "--tag", "x", input=fake("sk-ant-api03-") + "\n")
    assert code == 0, output
    assert "Added" in output
    code, output = run("ls")
    assert code == 0  # grey only
    assert "prod" in output
    assert "not checked" in output


def test_ls_exit_codes() -> None:
    assert run("ls")[0] == 0
    run("add", input=fake("xai-"))
    assert run("ls")[0] == 0
    run("add", input=fake_aiza())
    assert run("ls")[0] == 1
    run("add", "--expires", "2020-01-01", input=fake("sk-proj-", 100))
    assert run("ls")[0] == 2


def test_ls_json_schema() -> None:
    run("add", input=fake("sk-ant-oat01-", 95))
    data = ls_json()
    assert data["schema_version"] == 1
    assert data["exit_code"] == 0
    creds = data["credentials"]
    assert isinstance(creds, list)
    assert len(creds) == 1
    item = creds[0]
    assert set(item) == {"credential", "result"}
    assert item["result"]["overall"] == "local"
    assert item["result"]["validity"]["value"] == "not_applicable"
    assert "secret" not in json.dumps(item).lower()


def test_detect_from_stdin() -> None:
    code, output = run("detect", "--json", input=fake_aiza())
    assert code == 0
    det = json.loads(output)
    assert det["pattern_id"] == "gemini_standard_key"
    assert det["retiring"] is True
    assert not db_path().exists(), "detect never creates the store"


def test_detect_human_output_does_not_echo_secret() -> None:
    secret = fake("sk-or-v1-", 64)
    code, output = run("detect", input=secret)
    assert code == 0
    assert secret not in output
    assert "openrouter" in output


def test_ambiguous_without_tty_needs_provider() -> None:
    secret = fake("sk-", 48)
    code, output = run("add", input=secret)
    assert code == 2
    assert "--provider" in output
    assert run("add", "--provider", "openai", input=secret)[0] == 0


def test_secret_is_never_accepted_as_argument() -> None:
    secret = fake("xai-")
    code, _ = run("add", secret)
    assert code == 2  # usage error: no positional argument
    assert not db_path().exists() or ls_json()["credentials"] == []
    code, _ = run("detect", secret)
    assert code == 2


def test_rm() -> None:
    run("add", input=fake("xai-"))
    cid = ls_json()["credentials"][0]["credential"]["id"]  # type: ignore[index]
    code, output = run("rm", cid)
    assert code == 2
    assert "--yes" in output
    assert run("rm", cid[:8], "--yes")[0] == 0
    assert ls_json()["credentials"] == []
    assert run("rm", "NOPE1234", "--yes")[0] == 2


def test_locked_store_exits_3_with_clear_message() -> None:
    keyring.set_keyring(NoKeyring())
    code, output = run("add", input=fake("xai-"))
    assert code == 3
    assert "MECHA_PASSPHRASE_FILE" in output


def test_passphrase_set_and_recover(monkeypatch: pytest.MonkeyPatch, memory_keyring) -> None:  # type: ignore[no-untyped-def]
    run("add", input=fake("xai-"))
    monkeypatch.setenv("MECHA_PASSPHRASE", "recovery phrase")
    assert run("passphrase", "set")[0] == 0
    memory_keyring.data.clear()
    assert run("ls")[0] == 0  # unlocked by passphrase
    monkeypatch.delenv("MECHA_PASSPHRASE")
    memory_keyring.data.clear()
    code, output = run("ls")
    assert code == 3
    assert "passphrase" in output


def test_unknown_format_flags() -> None:
    code, output = run("add", "--provider", "xai", "--kind", "management_key", input=body(48))
    assert code == 0, output
    assert ls_json()["credentials"][0]["credential"]["kind"] == "management_key"  # type: ignore[index]


def test_typer_never_shows_locals() -> None:
    assert app.pretty_exceptions_show_locals is False
    assert app.pretty_exceptions_enable is False
