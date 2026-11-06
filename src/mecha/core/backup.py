"""Encrypted backups: ``mecha export`` / ``mecha restore``.

File format (version 1)::

    MECHA-BACKUP\\n
    {"format": 1, "cipher": "AES-256-GCM", "kdf": {...}, "created_at": ...}\\n   <- header
    <12-byte nonce><AES-256-GCM ciphertext of the JSON payload>

* The key comes from a passphrase via the KDF named in the header (Argon2id, or scrypt
  where Argon2id is unavailable), with its parameters and salt. The store's own key is
  **never** written: a backup opens with its passphrase on any machine.
* The header is the AEAD associated data: changing a byte of it (or of the ciphertext)
  makes the backup fail to open.
* The payload holds credentials with their secrets and settings, not the check history.
* There is no plaintext export.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from mecha.core.errors import MechaError, UnsupportedKdfError
from mecha.store.crypto import _derive, new_kdf_params

MAGIC = b"MECHA-BACKUP\n"
FORMAT = 1
NONCE = 12


class BackupError(MechaError):
    code = "backup"
    exit_code = 2


def encrypt_backup(payload: dict[str, Any], passphrase: str) -> bytes:
    if not passphrase:
        raise BackupError("A backup needs a passphrase.")
    params = new_kdf_params()
    header = json.dumps(
        {
            "format": FORMAT,
            "cipher": "AES-256-GCM",
            "kdf": params,
            "created_at": datetime.now(UTC).isoformat(),
        },
        sort_keys=True,
    ).encode()
    key = _derive(params, passphrase.encode("utf-8"))
    nonce = os.urandom(NONCE)
    ciphertext = AESGCM(key).encrypt(nonce, json.dumps(payload).encode(), MAGIC + header)
    return MAGIC + header + b"\n" + nonce + ciphertext


def read_header(data: bytes) -> tuple[dict[str, Any], bytes, bytes]:
    if not data.startswith(MAGIC):
        raise BackupError("Not a Mecha backup file.")
    rest = data[len(MAGIC) :]
    end = rest.find(b"\n")
    if end < 0:
        raise BackupError("Damaged backup header.")
    header_bytes, body = rest[:end], rest[end + 1 :]
    try:
        header = json.loads(header_bytes)
    except ValueError as exc:
        raise BackupError("Damaged backup header.") from exc
    if header.get("format") != FORMAT:
        raise BackupError(f"Unsupported backup format {header.get('format')!r}.")
    return header, header_bytes, body


def decrypt_backup(data: bytes, passphrase: str) -> dict[str, Any]:
    header, header_bytes, body = read_header(data)
    if len(body) <= NONCE:
        raise BackupError("Damaged backup.")
    try:
        key = _derive(header["kdf"], passphrase.encode("utf-8"))
    except UnsupportedKdfError as exc:
        kdf = header["kdf"].get("kdf")
        raise BackupError(
            f"This backup uses {kdf}, which this cryptography/OpenSSL build does not support."
        ) from exc
    try:
        plain = AESGCM(key).decrypt(body[:NONCE], body[NONCE:], MAGIC + header_bytes)
    except InvalidTag as exc:
        raise BackupError("Wrong passphrase, or the backup was modified.") from exc
    payload: dict[str, Any] = json.loads(plain)
    return payload


def write_private(path: Path, data: bytes, *, overwrite: bool = False) -> None:
    """Create ``path`` with 0600 from the start (never world-readable, even briefly)."""
    flags = os.O_WRONLY | os.O_CREAT | (os.O_TRUNC if overwrite else os.O_EXCL)
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise BackupError(f"{path} already exists (use --force to overwrite).") from exc
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    if os.name == "posix":
        path.chmod(0o600)
