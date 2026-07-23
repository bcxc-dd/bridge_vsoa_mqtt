import asyncio
import base64
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from iot_test_platform.backend import app as platform


def profile(device_id, rules):
    return {
        "device_id": device_id,
        "name": device_id,
        "project": "zigbee",
        "device_type": "environment_sensor",
        "capabilities": [],
        "thresholds": {"rules": rules},
        "connection_source": "mqtt",
    }


def test_nested_metric_uses_saved_device_threshold(tmp_path, monkeypatch):
    database = platform.Database(tmp_path / "platform.db")
    database.initialize()
    database.upsert_device_profile(profile("0xB25B", [{
        "field": "humidity",
        "operator": "gte",
        "value": 70,
        "severity": "warning",
        "enabled": True,
    }]))
    monkeypatch.setattr(platform, "db", database)

    asyncio.run(platform.record_event(
        "mqtt",
        "uplink",
        "bridge/uplink/zigbee/0xB25B/data",
        {"device_id": "0xB25B", "dht11": {"humidity_percent": 76}},
    ))

    alerts = database.alerts()
    assert len(alerts) == 1
    assert alerts[0]["alert_type"] == "threshold:humidity:gte"
    assert alerts[0]["value"]["actual"] == 76
    assert "大于等于 70%" in alerts[0]["message"]


def test_active_threshold_alert_is_not_repeated(tmp_path, monkeypatch):
    database = platform.Database(tmp_path / "platform.db")
    database.initialize()
    database.upsert_device_profile(profile("sensor-01", [{
        "field": "temperature",
        "operator": "gt",
        "value": 30,
        "severity": "critical",
        "enabled": True,
    }]))
    monkeypatch.setattr(platform, "db", database)

    for value in (31, 32, 33):
        asyncio.run(platform.record_event(
            "mqtt",
            "uplink",
            "bridge/uplink/zigbee/sensor-01/data",
            {"device_id": "sensor-01", "temperature": value},
        ))

    assert len(database.alerts()) == 1
    assert database.alerts()[0]["severity"] == "critical"


def test_no_configured_rule_means_no_default_temperature_alert(tmp_path, monkeypatch):
    database = platform.Database(tmp_path / "platform.db")
    database.initialize()
    monkeypatch.setattr(platform, "db", database)

    asyncio.run(platform.record_event(
        "mqtt",
        "uplink",
        "bridge/uplink/lora/free-sensor/data",
        {"device_id": "free-sensor", "temperature": 80},
    ))

    assert database.alerts() == []


def test_hpv1_camera_status_never_triggers_threshold(tmp_path, monkeypatch):
    database = platform.Database(tmp_path / "platform.db")
    database.initialize()
    database.upsert_device_profile(profile("EoRa-HUB-400TB", [{
        "field": "temperature",
        "operator": "gt",
        "value": 30,
        "severity": "warning",
        "enabled": True,
    }]))
    monkeypatch.setattr(platform, "db", database)
    status = b"HP" + bytes([1, 2]) + (18512).to_bytes(4, "little") + bytes(8)

    asyncio.run(platform.record_event(
        "vsoa",
        "result",
        "/device/update",
        {
            "device_id": "EoRa-HUB-400TB",
            "data": base64.b64encode(status).decode(),
            "temperature": 97.4,
        },
    ))

    assert database.alerts() == []


def test_user_can_save_device_thresholds(tmp_path, monkeypatch):
    database = platform.Database(tmp_path / "platform.db")
    database.initialize()
    database.insert_event({
        "id": uuid.uuid4().hex,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": "mqtt",
        "direction": "uplink",
        "project": "lora",
        "device_id": "field-node-01",
        "channel": "bridge/uplink/lora/field-node-01/data",
        "payload": {"temperature": 26.5},
        "status": "ok",
    })
    monkeypatch.setattr(platform, "db", database)
    request = SimpleNamespace(state=SimpleNamespace(user={"sub": "user"}))
    payload = platform.DeviceThresholdRequest(rules=[{
        "field": "temperature",
        "operator": "gt",
        "value": 32,
        "severity": "warning",
        "enabled": True,
    }])

    saved = asyncio.run(platform.save_device_thresholds("field-node-01", payload, request))

    assert platform.required_role("/api/devices/field-node-01/thresholds", "PUT") == "user"
    assert saved["rules"][0]["value"] == 32
    assert database.threshold_rules("field-node-01")[0]["field"] == "temperature"


def test_device_metrics_survive_a_newer_downlink_message(tmp_path):
    database = platform.Database(tmp_path / "platform.db")
    database.initialize()
    base = {
        "source": "mqtt",
        "project": "zigbee",
        "device_id": "0xB25B",
        "status": "ok",
    }
    database.insert_event({
        **base,
        "id": uuid.uuid4().hex,
        "timestamp": "2026-07-23T08:00:00+00:00",
        "direction": "uplink",
        "channel": "bridge/uplink/zigbee/0xB25B/data",
        "payload": {"dht11": {"temperature_c": 25, "humidity_percent": 51}, "custom_air_index": 18.5},
    })
    database.insert_event({
        **base,
        "id": uuid.uuid4().hex,
        "timestamp": "2026-07-23T08:00:01+00:00",
        "direction": "downlink",
        "channel": "bridge/downlink/zigbee/0xB25B/set",
        "payload": {"params": {"data": "01"}},
    })

    device = database.device_summaries()[0]
    metrics = {item["field"]: item["value"] for item in device["available_metrics"]}

    assert metrics["temperature"] == 25
    assert metrics["humidity"] == 51
    assert metrics["custom_air_index"] == 18.5
    assert "timestamp" not in metrics
