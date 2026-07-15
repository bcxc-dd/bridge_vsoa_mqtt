"""
Zigbee adapter.

Handles Zigbee2MQTT-style MQTT payloads, converting them into the
unified :class:`UplinkReport` model.
"""

from __future__ import annotations

from typing import Any

from .base import (
    Adapter,
    AdapterParseError,
    UplinkReport,
    _first_numeric,
    _first_str,
    extract_device_id_from_topic,
    infer_type,
    infer_unit,
    parse_common_measurements,
    topic_has_segment,
)


class ZigbeeAdapter(Adapter):
    """Adapter for Zigbee / Zigbee2MQTT device payloads."""

    name = "zigbee_adapter"
    source = "zigbee"

    # ------------------------------------------------------------------
    def match(self, topic: str, payload: dict[str, Any]) -> bool:
        """Match if *topic* contains 'zigbee' or payload contains Zigbee fields."""
        if topic_has_segment(topic, "zigbee"):
            return True
        return any(k in payload for k in ("ieeeAddr", "ieee_addr", "friendly_name", "linkquality"))

    # ------------------------------------------------------------------
    def parse(self, topic: str, payload: dict[str, Any]) -> UplinkReport:
        """Parse a Zigbee2MQTT-style payload into an UplinkReport."""
        report = UplinkReport()
        report.source = "zigbee"
        report.adapter = "zigbee_adapter"
        report.topic = topic

        # -- common measurements --
        parse_common_measurements(payload, report)

        # -- last_seen as timestamp alias --
        if not report.timestamp:
            ts = payload.get("last_seen")
            if isinstance(ts, (int, float)):
                report.timestamp = int(ts)

        # -- device id (priority: device_id > friendly_name > ieeeAddr > ieee_addr > topic) --
        report.device_id = (
            _first_str(payload, ["device_id", "friendly_name", "ieeeAddr", "ieee_addr"])
            or extract_device_id_from_topic(topic)
        )

        # -- name --
        if not report.name:
            report.name = payload.get("friendly_name", "")

        # -- post-processing --
        infer_type(report)
        infer_unit(report)

        if not report.device_id:
            raise AdapterParseError("Zigbee payload missing device id")

        return report
