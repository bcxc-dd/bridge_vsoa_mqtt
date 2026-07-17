"""
Base types and utilities for uplink adapters.

Defines:
  - UplinkReport  : unified data model produced by every adapter
  - Adapter       : abstract interface (match + parse)
  - AdapterParseError : raised when a payload cannot be parsed
  - FieldAliases  : recognised sensor field name aliases
  - Helper functions shared by all adapters.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Field alias tables – one dict per semantic field
# ---------------------------------------------------------------------------

#: Recognised field names that carry a device identifier.
DEVICE_ID_ALIASES = [
    "device_id", "id",
    "deviceName", "devEUI", "dev_eui",
    "friendly_name", "ieeeAddr", "ieee_addr",
]

#: Measurement field aliases — key = canonical name, value = list of aliases.
MEASUREMENT_ALIASES: dict[str, list[str]] = {
    "temperature":  ["temperature", "temp"],
    "humidity":     ["humidity", "hum"],
    "pressure":     ["pressure", "barometer"],
    "battery":      ["battery", "battery_level"],
    "signal":       ["signal", "rssi", "linkquality"],
    "snr":          ["snr", "loRaSNR"],
}

#: Direct string fields mapped by canonical name → aliases.
STRING_FIELD_ALIASES: dict[str, list[str]] = {
    "type":   ["type", "device_type"],
    "status": ["status"],
    "unit":   ["unit"],
    "name":   ["name"],
}


# ---------------------------------------------------------------------------
# UplinkReport
# ---------------------------------------------------------------------------

@dataclass
class UplinkReport:
    """Unified uplink data model produced by every adapter.

    Mirrors ``uplink_report_t`` from the original C implementation.
    """

    source: str = ""            # "lora" | "zigbee" | "bridge" | "mqtt"
    adapter: str = ""           # "lora_adapter" | "zigbee_adapter" | "generic_adapter"
    device_id: str = ""
    name: str = ""
    type: str = ""              # "temperature" | "humidity" | "pressure" | "multi" | "status"
    status: str = "online"
    unit: str = ""
    topic: str = ""

    timestamp: int = 0            # Unix epoch ms

    # ChirpStack / LoRaWAN 扩展字段（方案 B）
    dev_eui: str = ""             # LoRaWAN DevEUI（从上行 topic 或 payload 提取）
    app_id: str = ""              # ChirpStack application ID（从上行 topic 提取）

    temperature: Optional[float] = None
    humidity: Optional[float] = None
    pressure: Optional[float] = None
    snr: Optional[float] = None

    battery: Optional[int] = None
    signal: Optional[int] = None

    @property
    def has_temperature(self) -> bool:
        return self.temperature is not None

    @property
    def has_humidity(self) -> bool:
        return self.humidity is not None

    @property
    def has_pressure(self) -> bool:
        return self.pressure is not None

    @property
    def has_battery(self) -> bool:
        return self.battery is not None

    @property
    def has_signal(self) -> bool:
        return self.signal is not None

    @property
    def has_snr(self) -> bool:
        return self.snr is not None


# ---------------------------------------------------------------------------
# Adapter interface
# ---------------------------------------------------------------------------

class AdapterParseError(Exception):
    """Raised by an adapter when a payload cannot be parsed."""


class Adapter(ABC):
    """Abstract adapter — each concrete adapter handles one device source.

    Subclasses implement :meth:`match` and :meth:`parse`.
    """

    # Set by subclasses.
    name: str = "base"
    source: str = "unknown"

    @abstractmethod
    def match(self, topic: str, payload: dict[str, Any]) -> bool:
        """Return True if this adapter should handle *topic* + *payload*."""

    @abstractmethod
    def parse(self, topic: str, payload: dict[str, Any]) -> UplinkReport:
        """Parse *payload* into an :class:`UplinkReport`.

        Must raise :class:`AdapterParseError` when the payload cannot be
        successfully converted (e.g. missing device id).
        """


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _first_str(payload: dict[str, Any], candidates: list[str]) -> Optional[str]:
    """Return the first value from *payload* whose key is in *candidates*."""
    for key in candidates:
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
        # accept numeric IDs coerced to string
        if isinstance(val, (int, float)):
            return str(val)
    return None


def parse_common_measurements(payload: dict[str, Any], report: UplinkReport) -> None:
    """Populate *report* fields from *payload* using the alias tables above.

    Called by every adapter after setting source/adapter.
    """
    # -- string fields --
    for canonical, aliases in STRING_FIELD_ALIASES.items():
        value = _first_str(payload, aliases)
        if value is not None:
            setattr(report, canonical, value)

    # -- numeric fields --
    for canonical, aliases in MEASUREMENT_ALIASES.items():
        value = _first_numeric(payload, aliases)
        if value is not None:
            setattr(report, canonical, value)

    # -- timestamp (int64) --
    ts = payload.get("timestamp")
    if isinstance(ts, (int, float)):
        report.timestamp = int(ts)


def _first_numeric(payload: dict[str, Any], candidates: list[str]) -> Optional[float | int]:
    for key in candidates:
        val = payload.get(key)
        if val is None:
            continue
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            return val
        if isinstance(val, str):
            try:
                return float(val) if "." in val or "e" in val.lower() else int(val)
            except (ValueError, TypeError):
                continue
    return None


def extract_device_id_from_topic(topic: str) -> str:
    """Extract the device_id segment from an uplink topic.

    For ``bridge/uplink/{source}/{device_id}/{action}`` the device_id is at
    index 3.  For ChirpStack ``application/{app}/device/{devEUI}/event/up``
    the device id follows the ``device`` segment.  For simpler topics
    (e.g. ``lora/+/up``) we return the second-to-last segment.
    """
    parts = topic.rstrip("/").split("/")
    if len(parts) >= 5 and parts[0] == "bridge" and parts[1] == "uplink":
        return parts[3]
    if parts[:1] == ["application"]:
        for idx, part in enumerate(parts):
            if part == "device" and idx + 1 < len(parts):
                return parts[idx + 1]
    if len(parts) >= 3:
        return parts[-2]
    return ""


def topic_has_segment(topic: str, segment: str) -> bool:
    """Return True if *segment* is present anywhere in *topic*."""
    return segment in topic.rstrip("/").split("/")


def infer_type(report: UplinkReport) -> None:
    """Auto-detect device type from available measurements."""
    if report.type:
        return
    metrics = sum([report.has_temperature, report.has_humidity, report.has_pressure])
    if metrics > 1:
        report.type = "multi"
    elif report.has_temperature:
        report.type = "temperature"
    elif report.has_humidity:
        report.type = "humidity"
    elif report.has_pressure:
        report.type = "pressure"
    else:
        report.type = "status"


def infer_unit(report: UplinkReport) -> None:
    """Auto-detect unit from type when not explicitly provided."""
    if report.unit:
        return
    mapping = {
        "temperature": "celsius",
        "humidity":    "percent",
        "pressure":    "kpa",
    }
    report.unit = mapping.get(report.type, "")
