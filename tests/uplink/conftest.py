"""
Shared fixtures for bridge-uplink tests.
"""

from __future__ import annotations

import json
import threading
import time

import pytest

# ---------------------------------------------------------------------------
# Standard test payloads (matching the C samples/)
# ---------------------------------------------------------------------------

@pytest.fixture
def lora_topic() -> str:
    return "bridge/uplink/lora/lora_env_01/data"


@pytest.fixture
def lora_payload() -> dict:
    return {
        "devEUI": "24e124136d000001",
        "deviceName": "lora_env_01",
        "applicationName": "factory-lora",
        "timestamp": 1783329001000,
        "fPort": 2,
        "rxInfo": [{"rssi": -57, "loRaSNR": 8.2}],
        "object": {
            "temperature": 23.6,
            "humidity": 56.2,
            "battery": 92,
        },
    }


@pytest.fixture
def zigbee_topic() -> str:
    return "bridge/uplink/zigbee/zigbee_env_01/data"


@pytest.fixture
def zigbee_payload() -> dict:
    return {
        "ieeeAddr": "0x00124b0024c00001",
        "friendly_name": "zigbee_env_01",
        "last_seen": 1783329002000,
        "linkquality": 154,
        "battery": 85,
        "temperature": 25.1,
        "humidity": 60.4,
        "status": "online",
    }


@pytest.fixture
def device_registry():
    from src.device_registry import DeviceRegistry
    return DeviceRegistry(max_devices=64)
