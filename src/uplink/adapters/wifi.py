"""WiFi MQTT uplink adapter."""

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


class WifiAdapter(Adapter):
    """Handle environmental and camera data uploaded directly over WiFi."""

    name = "wifi_adapter"
    source = "wifi"
    EORA_TOPICS = {
        "s3/eora-s3-400tb-001/data",
        "bridge/uplink/generic/eora_s3_400tb_001/data",
    }

    def match(self, topic: str, payload: dict[str, Any]) -> bool:
        del payload
        return (
            topic in self.EORA_TOPICS
            or topic_has_segment(topic, "wifi")
            or topic_has_segment(topic, "camera")
        )

    def parse(self, topic: str, payload: dict[str, Any]) -> UplinkReport:
        report = UplinkReport(source=self.source, adapter=self.name, topic=topic)
        parse_common_measurements(payload, report)
        report.device_id = (
            _first_str(payload, ["device_id", "id", "deviceName"])
            or extract_device_id_from_topic(topic)
        )
        if topic in self.EORA_TOPICS:
            report.device_id = "eora_s3_400tb_001"
            report.name = report.name or "eora-s3-400tb-001"
        infer_type(report)
        infer_unit(report)
        if not report.device_id:
            raise AdapterParseError("WiFi MQTT payload missing device id")
        return report
