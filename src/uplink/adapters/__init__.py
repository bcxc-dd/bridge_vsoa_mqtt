"""
Adapters: LoRa / Zigbee / Generic.

The :func:`select_adapter` function picks the correct adapter for a
given MQTT topic + payload.
"""

from __future__ import annotations

from typing import Any

from .base import Adapter, AdapterParseError, UplinkReport
from .lora import LoraAdapter
from .zigbee import ZigbeeAdapter
from .generic import GenericAdapter

# ---------------------------------------------------------------------------
# Concrete adapter instances (ordered by priority)
# ---------------------------------------------------------------------------

_lora = LoraAdapter()
_zigbee = ZigbeeAdapter()
_generic = GenericAdapter()

ADAPTERS: list[Adapter] = [_lora, _zigbee, _generic]

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def select_adapter(topic: str, payload: dict[str, Any]) -> Adapter:
    """Return the first adapter that matches *topic* + *payload*.

    Fallback is always :class:`GenericAdapter`.
    """
    for adapter in ADAPTERS:
        if adapter.match(topic, payload):
            return adapter
    return _generic  # unreachable — generic always matches
