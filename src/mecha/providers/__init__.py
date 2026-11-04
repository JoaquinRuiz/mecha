"""Provider plugins. A plugin is ``mecha.providers.<id>`` exposing ``PROVIDER``."""

from __future__ import annotations

import importlib
from functools import cache

from mecha.core.models import ProviderId
from mecha.providers.base import Provider

# Providers whose plugin is wired (OpenRouter arrives in phase 4).
ENABLED = (ProviderId.anthropic, ProviderId.openai, ProviderId.gemini, ProviderId.xai)


@cache
def get_provider(provider_id: ProviderId) -> Provider | None:
    if provider_id not in ENABLED:
        return None
    module = importlib.import_module(f"mecha.providers.{provider_id.value}")
    provider: Provider = module.PROVIDER
    return provider
