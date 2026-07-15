"""
Generic adapter.

Catch-all adapter for MQTT payloads that do not match LoRa or Zigbee.
"""

from __future__ import annotations

from typing import Any

from .base import (
    Adapter,
    AdapterParseError,
    UplinkReport,
    _first_str,
    extract_device_id_from_topic,
    infer_type,
    infer_unit,
    parse_common_measurements,
    topic_has_segment,
)


class GenericAdapter(Adapter):
    """Catch-all adapter — always matches.

    Uses ``bridge`` as source when the topic contains 'bridge', otherwise
    ``mqtt``.
    """

    name = "generic_adapter"
    source = "mqtt"

    # ------------------------------------------------------------------
    def match(self, topic: str, payload: dict[str, Any]) -> bool:
        """Always match — this is the fallback adapter."""
        return True

    # ------------------------------------------------------------------
    def parse(self, topic: str, payload: dict[str, Any]) -> UplinkReport:
        """Parse a generic MQTT payload into an UplinkReport."""
        report = UplinkReport()
        report.source = "bridge" if topic_has_segment(topic, "bridge") else "mqtt"
        report.adapter = "generic_adapter"
        report.topic = topic

        # -- common measurements --
        parse_common_measurements(payload, report)

        # -- device id (priority: device_id > id > topic) --
        report.device_id = (
            _first_str(payload, ["device_id", "id"])
            or extract_device_id_from_topic(topic)
        )

        # -- if topic action is 'status' and no type, default to "status" --
        if not report.type and topic.rstrip("/").rsplit("/", 1)[-1] == "status":
            report.type = "status"

        # -- post-processing --
        infer_type(report)
        infer_unit(report)

        if not report.device_id:
            raise AdapterParseError("MQTT payload missing device id")

        return report
