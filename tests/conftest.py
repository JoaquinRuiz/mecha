from __future__ import annotations

import os

# Must happen before keyring picks a backend: tests never touch the real system keyring.
os.environ["PYTHON_KEYRING_BACKEND"] = "tests.memkeyring.MemoryKeyring"

from collections.abc import Iterator
from pathlib import Path

import keyring
import pytest

from mecha.core.logging import install_log_redaction
from tests.memkeyring import MemoryKeyring

keyring.set_keyring(MemoryKeyring())
install_log_redaction()


@pytest.fixture(autouse=True)
def memory_keyring() -> Iterator[MemoryKeyring]:
    """A fresh in-memory keyring per test."""
    backend = MemoryKeyring()
    keyring.set_keyring(backend)
    yield backend
    keyring.set_keyring(MemoryKeyring())


@pytest.fixture(autouse=True)
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "mecha-home"
    monkeypatch.setenv("MECHA_HOME", str(home))
    for var in ("MECHA_PASSPHRASE", "MECHA_PASSPHRASE_FILE", "MECHA_LOG_LEVEL"):
        monkeypatch.delenv(var, raising=False)
    return home


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
