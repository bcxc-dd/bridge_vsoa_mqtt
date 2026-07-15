"""
Unit tests for adapters — match & parse logic.

Covers the same scenarios as the LoRa/Zigbee injection in
``test_uplink.ps1``.
"""

from __future__ import annotations

import pytest

from src.uplink.adapters.base import (
    AdapterParseError,
    UplinkReport,
    infer_type,
    infer_unit,
    parse_common_measurements,
    extract_device_id_from_topic,
    topic_has_segment,
)
from src.uplink.adapters.lora import LoraAdapter
from src.uplink.adapters.zigbee import ZigbeeAdapter
from src.uplink.adapters.generic import GenericAdapter


# ========================================================================
# LoRa adapter
# ========================================================================

class TestLoraMatch:
    def test_topic_contains_lora(self):
        assert LoraAdapter().match("bridge/uplink/lora/dev1/data", {}) is True

    def test_payload_devEUI(self):
        assert LoraAdapter().match("other/topic", {"devEUI": "abc"}) is True

    def test_payload_dev_eui(self):
        assert LoraAdapter().match("other/topic", {"dev_eui": "abc"}) is True

    def test_payload_fPort(self):
        assert LoraAdapter().match("other/topic", {"fPort": 2}) is True

    def test_payload_rxInfo(self):
        assert LoraAdapter().match("other/topic", {"rxInfo": []}) is True

    def test_non_lora_returns_false(self):
        assert LoraAdapter().match("plain/data", {"temperature": 25.0}) is False


class TestLoraParse:
    def test_full_payload(self, lora_topic, lora_payload):
        r = LoraAdapter().parse(lora_topic, lora_payload)
        assert r.source == "lora"
        assert r.adapter == "lora_adapter"
        assert r.device_id == "lora_env_01"
        assert r.temperature == 23.6
        assert r.humidity == 56.2
        assert r.battery == 92
        assert r.signal == -57
        assert r.snr == 8.2
        assert r.type == "multi"

    def test_deviceName_as_id(self, lora_topic):
        payload = {"deviceName": "my_lora", "temperature": 20.0}
        r = LoraAdapter().parse(lora_topic, payload)
        assert r.device_id == "my_lora"

    def test_devEUI_as_fallback(self, lora_topic):
        payload = {"devEUI": "24e124136d000001", "temperature": 20.0}
        r = LoraAdapter().parse(lora_topic, payload)
        assert r.device_id == "24e124136d000001"

    def test_topic_fallback_for_device_id(self):
        r = LoraAdapter().parse(
            "bridge/uplink/lora/from_topic/data",
            {"temperature": 20.0},
        )
        assert r.device_id == "from_topic"

    def test_missing_device_id_raises(self):
        with pytest.raises(AdapterParseError, match="missing device id"):
            LoraAdapter().parse("no/device", {"temperature": 25})


# ========================================================================
# Zigbee adapter
# ========================================================================

class TestZigbeeMatch:
    def test_topic_contains_zigbee(self):
        assert ZigbeeAdapter().match("bridge/uplink/zigbee/dev1/data", {}) is True

    def test_payload_ieeeAddr(self):
        assert ZigbeeAdapter().match("other", {"ieeeAddr": "0x123"}) is True

    def test_payload_ieee_addr(self):
        assert ZigbeeAdapter().match("other", {"ieee_addr": "0x123"}) is True

    def test_payload_friendly_name(self):
        assert ZigbeeAdapter().match("other", {"friendly_name": "zb1"}) is True

    def test_payload_linkquality(self):
        assert ZigbeeAdapter().match("other", {"linkquality": 100}) is True

    def test_non_zigbee_returns_false(self):
        assert ZigbeeAdapter().match("plain", {"temperature": 25.0}) is False


class TestZigbeeParse:
    def test_full_payload(self, zigbee_topic, zigbee_payload):
        r = ZigbeeAdapter().parse(zigbee_topic, zigbee_payload)
        assert r.source == "zigbee"
        assert r.adapter == "zigbee_adapter"
        assert r.device_id == "zigbee_env_01"
        assert r.temperature == 25.1
        assert r.humidity == 60.4
        assert r.battery == 85
        assert r.signal == 154
        assert r.type == "multi"

    def test_last_seen_as_timestamp(self, zigbee_topic):
        payload = {"friendly_name": "zb1", "last_seen": 1783329002000, "temperature": 20.0}
        r = ZigbeeAdapter().parse(zigbee_topic, payload)
        assert r.timestamp == 1783329002000

    def test_friendly_name_as_id(self, zigbee_topic):
        payload = {"friendly_name": "my_zigbee", "temperature": 20.0}
        r = ZigbeeAdapter().parse(zigbee_topic, payload)
        assert r.device_id == "my_zigbee"

    def test_ieeeAddr_as_fallback(self, zigbee_topic):
        payload = {"ieeeAddr": "0x00124b0024c00001", "temperature": 20.0}
        r = ZigbeeAdapter().parse(zigbee_topic, payload)
        assert r.device_id == "0x00124b0024c00001"

    def test_missing_device_id_raises(self):
        with pytest.raises(AdapterParseError, match="missing device id"):
            ZigbeeAdapter().parse("no/device", {"temperature": 25})


# ========================================================================
# Generic adapter
# ========================================================================

class TestGenericMatch:
    def test_always_matches(self):
        assert GenericAdapter().match("anything", {}) is True
        assert GenericAdapter().match("", {}) is True


class TestGenericParse:
    def test_standard_payload(self):
        r = GenericAdapter().parse(
            "bridge/uplink/bridge/dev1/data",
            {"device_id": "dev1", "temperature": 30.0, "status": "online",
             "timestamp": 1783329001000},
        )
        assert r.device_id == "dev1"
        assert r.temperature == 30.0
        assert r.source == "bridge"
        assert r.adapter == "generic_adapter"
        assert r.type == "temperature"
        assert r.unit == "celsius"

    def test_id_field_as_fallback(self):
        r = GenericAdapter().parse("bridge/uplink/bridge/dev1/data",
                                   {"id": "dev99", "temperature": 20.0})
        assert r.device_id == "dev99"

    def test_topic_fallback(self):
        r = GenericAdapter().parse("bridge/uplink/bridge/from_topic/data",
                                   {"temperature": 20.0})
        assert r.device_id == "from_topic"

    def test_status_action(self):
        r = GenericAdapter().parse(
            "bridge/uplink/bridge/dev1/status",
            {"device_id": "dev1", "status": "offline"},
        )
        assert r.type == "status"

    def test_missing_device_id_raises(self):
        with pytest.raises(AdapterParseError, match="missing device id"):
            GenericAdapter().parse("no/device", {"temperature": 25})


# ========================================================================
# Common field parsing helpers
# ========================================================================

class TestCommonMeasurements:
    def test_aliases(self):
        r = UplinkReport()
        parse_common_measurements({
            "temp": 25.0,
            "hum": 60.0,
            "barometer": 101.3,
            "battery_level": 80,
            "rssi": -50,
            "loRaSNR": 7.0,
            "device_type": "multi",
        }, r)
        assert r.temperature == 25.0
        assert r.humidity == 60.0
        assert r.pressure == 101.3
        assert r.battery == 80
        assert r.signal == -50
        assert r.snr == 7.0
        assert r.type == "multi"

    def test_numeric_string_values(self):
        r = UplinkReport()
        parse_common_measurements({
            "temperature": "25.5",
            "humidity": "60",
        }, r)
        assert r.temperature == 25.5
        assert r.humidity == 60.0


class TestInferType:
    def test_multi_when_multiple_sensors(self):
        r = UplinkReport()
        r.temperature = 25.0
        r.humidity = 60.0
        infer_type(r)
        assert r.type == "multi"

    def test_temperature_only(self):
        r = UplinkReport()
        r.temperature = 25.0
        infer_type(r)
        assert r.type == "temperature"

    def test_no_sensor_defaults_to_status(self):
        r = UplinkReport()
        infer_type(r)
        assert r.type == "status"

    def test_does_not_overwrite_existing(self):
        r = UplinkReport()
        r.type = "custom"
        infer_type(r)
        assert r.type == "custom"


class TestInferUnit:
    def test_temperature(self):
        r = UplinkReport()
        r.type = "temperature"
        infer_unit(r)
        assert r.unit == "celsius"

    def test_humidity(self):
        r = UplinkReport()
        r.type = "humidity"
        infer_unit(r)
        assert r.unit == "percent"

    def test_pressure(self):
        r = UplinkReport()
        r.type = "pressure"
        infer_unit(r)
        assert r.unit == "kpa"

    def test_unknown_type(self):
        r = UplinkReport()
        r.type = "multi"
        infer_unit(r)
        assert r.unit == ""


class TestTopicHelpers:
    def test_has_segment(self):
        assert topic_has_segment("bridge/uplink/lora/dev/data", "lora") is True
        assert topic_has_segment("bridge/uplink/zigbee/dev/data", "zigbee") is True
        assert topic_has_segment("other/data", "lora") is False

    def test_extract_device_id_bridge_format(self):
        assert extract_device_id_from_topic(
            "bridge/uplink/lora/lora_env_01/data") == "lora_env_01"

    def test_extract_device_id_short_format(self):
        assert extract_device_id_from_topic("lora/dev123/up") == "dev123"

    def test_extract_device_id_no_segments(self):
        assert extract_device_id_from_topic("data") == ""
