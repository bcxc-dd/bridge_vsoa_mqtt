from __future__ import annotations

import json
import time
from types import SimpleNamespace

from src.scene_engine import SceneEngine
from src.scene_engine import engine as engine_module


class Registry:
    def __init__(self, *ids):
        self.devices = {device_id: SimpleNamespace(device_id=device_id) for device_id in ids}

    def lookup(self, device_id):
        return self.devices.get(device_id)


def make_engine(tmp_path, published, events=None):
    def publish(topic, payload, qos):
        published.append((topic, json.loads(payload), qos))
        return True

    engine = SceneEngine(
        str(tmp_path / "scenes.yaml"), publish, "bridge/downlink", {},
        Registry("zb-buzzer-01", "zb-led-01", "eora_s3_400tb_001"),
        event_publisher=(lambda url, data: events.append((url, data))) if events is not None else None,
    )
    engine.start()
    return engine


def test_threshold_trigger_persists_and_publishes(tmp_path):
    published, events = [], []
    engine = make_engine(tmp_path, published, events)
    rule = engine.add_rule({
        "name": "高温告警", "condition_logic": "and",
        "conditions": [{"sensor": "temperature", "operator": "gt", "value": 40}],
        "actions": [{"device_type": "zigbee", "device_id": "zb-led-01", "action": "set", "params": {"led": "blink"}}],
        "cooldown_seconds": 60,
    })
    source = SimpleNamespace(device_id="sensor-01", temperature=42.5, raw={"temperature": 42.5})
    triggered = engine.on_uplink(source)
    assert triggered[0]["scene_id"] == rule["scene_id"]
    assert published[0][0].endswith("/zigbee/zb-led-01/set")
    assert published[0][1]["params"] == {"led": "blink"}
    assert events[0][0] == "/scene/trigger"
    assert engine.on_uplink(source) == []
    engine.reload()
    assert engine.get_rule(rule["scene_id"])["last_triggered_at"]


def test_pir_uses_rising_edge(tmp_path):
    published = []
    engine = make_engine(tmp_path, published)
    engine.add_rule({
        "name": "人员经过", "conditions": [{"sensor": "pir", "operator": "eq", "value": True}],
        "actions": [{"device_type": "zigbee", "device_id": "zb-buzzer-01", "action": "set", "params": {"buzzer": "on"}}],
        "cooldown_seconds": 0,
    })
    source = SimpleNamespace(device_id="pir-01", raw={"pir": True})
    assert len(engine.on_uplink(source)) == 1
    assert engine.on_uplink(source) == []
    source.raw["pir"] = False
    assert engine.on_uplink(source) == []
    source.raw["pir"] = True
    assert len(engine.on_uplink(source)) == 1


def test_duration_sends_stop_command(tmp_path):
    published = []
    engine = make_engine(tmp_path, published)
    engine.add_rule({
        "name": "短鸣", "conditions": [{"sensor": "smoke", "operator": "eq", "value": True}],
        "actions": [{"device_type": "zigbee", "device_id": "zb-buzzer-01", "action": "set", "params": {"buzzer": "on"}}],
        "duration_seconds": 1, "cooldown_seconds": 10,
    })
    engine.on_uplink(SimpleNamespace(device_id="smoke-01", raw={"smoke": True}))
    time.sleep(1.2)
    assert [item[1]["params"] for item in published] == [{"buzzer": "on"}, {"buzzer": "off"}]
    engine.stop()


def test_eora_scene_uses_compact_motor_contract(tmp_path):
    published = []
    engine = make_engine(tmp_path, published)
    engine.add_rule({
        "name": "土壤干燥启动电机",
        "conditions": [{"sensor": "soil_moisture", "operator": "lt", "value": 20}],
        "actions": [{
            "device_type": "generic", "device_id": "eora_s3_400tb_001",
            "action": "set", "params": {"motor": "on"},
        }],
        "cooldown_seconds": 0,
    })

    engine.on_uplink(SimpleNamespace(device_id="soil-01", raw={"soil_moisture": 10}))

    assert published == [(
        "bridge/downlink/generic/eora_s3_400tb_001/cmd",
        {"cmd": "motor", "value": "on"},
        1,
    )]


def test_eora_scene_uses_compact_led_contract(tmp_path):
    published = []
    engine = make_engine(tmp_path, published)
    engine.add_rule({
        "name": "夜间开启 LED",
        "conditions": [{"sensor": "illuminance", "operator": "lt", "value": 10}],
        "actions": [{
            "device_type": "generic", "device_id": "eora_s3_400tb_001",
            "action": "set", "params": {"led": "on"},
        }],
        "cooldown_seconds": 0,
    })

    engine.on_uplink(SimpleNamespace(device_id="light-01", raw={"illuminance": 5}))

    assert published == [(
        "bridge/downlink/generic/eora_s3_400tb_001/cmd",
        {"cmd": "led", "value": "on"},
        1,
    )]


def test_condition_only_accepts_selected_source_device(tmp_path):
    published = []
    engine = make_engine(tmp_path, published)
    engine.add_rule({
        "name": "指定设备高温", "conditions": [{
            "device_id": "sensor-allowed", "sensor": "temperature",
            "operator": "gt", "value": 30,
        }],
        "actions": [{"device_type": "zigbee", "device_id": "zb-led-01", "action": "set", "params": {"led": "blink"}}],
        "cooldown_seconds": 0,
    })
    assert engine.on_uplink(SimpleNamespace(device_id="sensor-other", temperature=35, raw={"temperature": 35})) == []
    assert len(engine.on_uplink(SimpleNamespace(device_id="sensor-allowed", temperature=35, raw={"temperature": 35}))) == 1


def test_level_condition_waits_for_hold_seconds(tmp_path, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(engine_module.time, "monotonic", lambda: clock[0])
    published = []
    engine = make_engine(tmp_path, published)
    engine.add_rule({
        "name": "高温持续一秒", "conditions": [{
            "sensor": "temperature", "operator": "gt", "value": 30,
            "trigger_mode": "level", "hold_seconds": 1,
        }],
        "actions": [{"device_type": "zigbee", "device_id": "zb-led-01", "action": "set", "params": {"led": "blink"}}],
        "cooldown_seconds": 0,
    })
    source = SimpleNamespace(device_id="sensor-01", temperature=35, raw={"temperature": 35})
    assert engine.on_uplink(source) == []
    clock[0] = 101.1
    assert len(engine.on_uplink(source)) == 1
