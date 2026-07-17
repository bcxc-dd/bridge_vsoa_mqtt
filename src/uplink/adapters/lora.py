"""
LoRa / LoRaWAN adapter.

Handles MQTT payloads from LoRaWAN gateways, converting them into the
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
    DEVICE_ID_ALIASES,
    extract_device_id_from_topic,
    infer_type,
    infer_unit,
    parse_common_measurements,
    topic_has_segment,
)


class LoraAdapter(Adapter):
    """Adapter for LoRa / LoRaWAN device payloads."""

    name = "lora_adapter"
    source = "lora"

    # ------------------------------------------------------------------
    def match(self, topic: str, payload: dict[str, Any]) -> bool:
        """Match if *topic* contains 'lora' or payload contains LoRa fields."""
        if topic_has_segment(topic, "lora"):
            return True
        return any(k in payload for k in ("devEUI", "dev_eui", "fPort", "rxInfo"))

    # ------------------------------------------------------------------
    def parse(self, topic: str, payload: dict[str, Any]) -> UplinkReport:
        """Parse a LoRa/LoRaWAN MQTT payload into an UplinkReport."""
        report = UplinkReport()
        report.source = "lora"
        report.adapter = "lora_adapter"
        report.topic = topic

        # -- ChirpStack deviceInfo 子对象（常见于 application/... topic） --
        device_info = payload.get("deviceInfo")
        if not isinstance(device_info, dict):
            device_info = {}

        # -- common measurements (temperature, humidity, battery, signal, …) --
        parse_common_measurements(payload, report)

        # -- device id —
        #   优先级: deviceInfo.devEui > deviceInfo.deviceName > 顶层 device_id >
        #           顶层 devEUI/dev_eui > 顶层 deviceName > topic
        report.device_id = (
            _first_str(payload, ["device_id", "deviceName", "devEUI", "dev_eui"])
            or _extract_device_info_dev_eui(payload)
            or extract_device_id_from_topic(topic)
        )

        # -- name (use deviceName as a human-readable fallback) --
        if not report.name:
            report.name = (
                device_info.get("deviceName", "")
                or payload.get("deviceName", "")
            )

        # -- LoRa-specific: rxInfo[0].rssi -> signal, rxInfo[0].loRaSNR -> snr --
        _extract_rxinfo(payload, report)

        # -- ChirpStack 扩展: 提取 dev_eui（方案 B） —
        #   优先级: deviceInfo.devEui > 顶层 devEUI/dev_eui > topic
        report.dev_eui = (
            device_info.get("devEui", "")
            or _first_str(payload, ["devEUI", "dev_eui"])
            or _extract_dev_eui_from_topic(topic)
        )

        # -- ChirpStack 扩展: 提取 app_id（方案 B） —
        #   优先级: deviceInfo.applicationId > topic extraction
        report.app_id = (
            device_info.get("applicationId", "")
            or device_info.get("applicationName", "")
            or _extract_app_id_from_topic(topic)
        )

        # -- object sub-document --
        obj = payload.get("object")
        if isinstance(obj, dict):
            parse_common_measurements(obj, report)

        # -- post-processing --
        infer_type(report)
        infer_unit(report)

        if not report.device_id:
            raise AdapterParseError("LoRa payload missing device id")

        return report


def _extract_rxinfo(payload: dict[str, Any], report: UplinkReport) -> None:
    """Extract signal / snr from ``rxInfo`` array if not already set."""
    rx_info = payload.get("rxInfo")
    if not isinstance(rx_info, list) or not rx_info:
        return
    first = rx_info[0]
    if not isinstance(first, dict):
        return
    if not report.has_signal:
        report.signal = _first_numeric(first, ["rssi"])
    if not report.has_snr:
        snr_val = _first_numeric(first, ["loRaSNR", "snr"])
        if snr_val is not None:
            report.snr = float(snr_val) if isinstance(snr_val, (int, float)) else snr_val




def _extract_dev_eui_from_topic(topic: str) -> str:
    """从 ChirpStack topic 提取 DevEUI。

    ChirpStack topic: application/{app_id}/device/{dev_eui}/event/up
    DevEUI 在 index 3。
    """
    parts = topic.rstrip("/").split("/")
    if len(parts) >= 4 and parts[0] == "application" and parts[2] == "device":
        return parts[3]
    return ""


def _extract_app_id_from_topic(topic: str) -> str:
    """从 ChirpStack topic 提取 application ID。

    ChirpStack topic: application/{app_id}/device/{dev_eui}/event/up
    app_id 在 index 1。
    """
    parts = topic.rstrip("/").split("/")
    if len(parts) >= 4 and parts[0] == "application" and parts[2] == "device":
        return parts[1]
    return ""


def _extract_device_info_dev_eui(payload: dict[str, Any]) -> str | None:
    device_info = payload.get("deviceInfo")
    if not isinstance(device_info, dict):
        return None
    return _first_str(device_info, ["devEui", "devEUI", "dev_eui"])
