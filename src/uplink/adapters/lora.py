"""
LoRa / LoRaWAN adapter.

Handles MQTT payloads from LoRaWAN gateways, converting them into the
unified :class:`UplinkReport` model.
"""

from __future__ import annotations

import base64
from datetime import datetime
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
        if (
            topic_has_segment(topic, "lora")
            or topic.startswith("application/")
        ):
            return True
        return any(
            k in payload
            for k in ("devEUI", "dev_eui", "fPort", "rxInfo", "deviceInfo")
        )

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

        # Keep every original payload field, then enrich it with normalized
        # device metadata and the optional 16/20-byte business frame.
        working = dict(payload)
        if device_info:
            working.setdefault("deviceName", device_info.get("deviceName"))
            working.setdefault("devEUI", device_info.get("devEui"))

        encoded = payload.get("data")
        has_binary = isinstance(encoded, str) and bool(encoded)
        if has_binary:
            try:
                binary = base64.b64decode(encoded, validate=True)
            except Exception as exc:
                raise AdapterParseError(f"invalid Base64 data: {exc}") from exc
            if binary[:2] in {b"HC", b"HP"}:
                # HC chunks and HP status packets belong to the camera
                # reassembler. Their lengths must never be interpreted as
                # the legacy environmental frame.
                working["camera_transport"] = "lorawan_hcv3"
                working["camera_packet"] = "chunk" if binary[:2] == b"HC" else "status"
                working["binary_length"] = len(binary)
                decoded = None
            else:
                decoded = parse_lora_binary(binary)
            if decoded is None and "binary_length" not in working:
                # ChirpStack may carry a device-specific frame. Keep the
                # original Base64 payload and its size instead of dropping it.
                working["binary_length"] = len(binary)
            elif decoded is not None:
                working.update(decoded)

        # -- common measurements (temperature, humidity, battery, signal, …) --
        parse_common_measurements(working, report)

        # -- device id —
        #   优先级: deviceInfo.devEui > deviceInfo.deviceName > 顶层 device_id >
        #           顶层 devEUI/dev_eui > 顶层 deviceName > topic
        report.device_id = (
            _first_str(working, ["device_id", "deviceName", "devEUI", "dev_eui"])
            or _extract_device_info_dev_eui(payload)
            or extract_device_id_from_topic(topic)
        )

        # -- name (use deviceName as a human-readable fallback) --
        if not report.name:
            report.name = (
                device_info.get("deviceName", "")
                or working.get("deviceName", "")
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

        if not report.timestamp and isinstance(payload.get("time"), str):
            try:
                report.timestamp = int(
                    datetime.fromisoformat(payload["time"].replace("Z", "+00:00")).timestamp()
                    * 1000
                )
            except ValueError:
                pass

        # -- post-processing --
        infer_type(report)
        infer_unit(report)

        if not report.device_id:
            raise AdapterParseError("LoRa payload missing device id")

        return report


def parse_lora_binary(payload: bytes) -> dict[str, Any] | None:
    """Decode the LoRa group's legacy 16-byte or environment 20-byte frame."""
    if len(payload) not in {16, 20}:
        return None
    decoded = {
        "seq": int.from_bytes(payload[0:2], "big"),
        "boot_id": hex(int.from_bytes(payload[2:6], "big")),
        "send_time_ms": int.from_bytes(payload[6:10], "big"),
        "lorawan_retry_count": payload[10],
        "temperature": int.from_bytes(payload[11:13], "big", signed=True) / 10.0,
        "humidity": int.from_bytes(payload[13:15], "big") / 10.0,
    }
    flags_index = 15
    if len(payload) == 20:
        decoded.update({
            "soil_moisture": int.from_bytes(payload[15:17], "big") / 10.0,
            "precipitation": int.from_bytes(payload[17:19], "big") / 10.0,
        })
        flags_index = 19
    decoded.update({
        "joined": bool(payload[flags_index] & 0x01),
        "application_retry": bool(payload[flags_index] & 0x08),
        "flags": hex(payload[flags_index]),
    })
    return decoded


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
