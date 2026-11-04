"""Load ``data/capabilities.toml`` (the capability contract)."""

from __future__ import annotations

import tomllib
from functools import cache
from importlib.resources import files
from typing import Literal

from pydantic import BaseModel

from mecha.core.models import CredentialKind, ProviderId

Level = Literal["yes", "no", "admin_key", "management_key", "error_only"]


class Capabilities(BaseModel):
    name: str
    validate_: Level
    funds: Level
    expiry: Level
    active_probe: str
    passive: dict[CredentialKind, str]
    phase: int = 2


@cache
def load_capabilities() -> dict[ProviderId, Capabilities]:
    text = files("mecha.data").joinpath("capabilities.toml").read_text(encoding="utf-8")
    raw = tomllib.loads(text)["providers"]
    out: dict[ProviderId, Capabilities] = {}
    for key, value in raw.items():
        value = {**value, "validate_": value.pop("validate")}
        out[ProviderId(key)] = Capabilities.model_validate(value)
    return out
