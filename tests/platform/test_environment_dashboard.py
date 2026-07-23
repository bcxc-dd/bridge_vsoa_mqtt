from datetime import datetime, timezone
import base64
import uuid

from iot_test_platform.backend.app import Database


def add_event(database, *, project, device_id, channel, payload):
    database.insert_event({
        "id": uuid.uuid4().hex,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": "mqtt",
        "direction": "uplink",
        "project": project,
        "device_id": device_id,
        "channel": channel,
        "payload": payload,
        "status": "ok",
    })


def test_lora_dashboard_recognizes_topic_and_nested_environment_fields(tmp_path):
    database = Database(tmp_path / "platform.db")
    database.initialize()
    add_event(
        database,
        project="generic",
        device_id="field-node-01",
        channel="bridge/uplink/lora/field-node-01/data",
        payload={
            "telemetry": {
                "temperature": 24.6,
                "soilHumidity": 43.2,
                "rain_level": 1.8,
                "co2_ppm": 612,
            },
            "sequence": 7,
        },
    )

    dashboard = database.lora_dashboard()

    assert [device["device_id"] for device in dashboard["devices"]] == ["field-node-01"]
    device = dashboard["devices"][0]
    assert device["latest"] == {
        "temperature": 24.6,
        "soil_moisture": 43.2,
        "rainfall": 1.8,
        "co2": 612,
    }
    assert {metric["field"] for metric in device["metrics"]} == {
        "temperature", "soil_moisture", "rainfall", "co2",
    }


def test_project_dashboard_does_not_mix_other_radio_topics(tmp_path):
    database = Database(tmp_path / "platform.db")
    database.initialize()
    add_event(
        database,
        project="generic",
        device_id="zigbee-node-01",
        channel="bridge/uplink/zigbee/zigbee-node-01/data",
        payload={"data": {"temperature": 22.0}},
    )

    assert database.lora_dashboard()["devices"] == []
    assert database.zigbee_dashboard()["devices"][0]["device_id"] == "zigbee-node-01"


def test_zigbee_dashboard_recognizes_real_nested_sensor_reports(tmp_path):
    database = Database(tmp_path / "platform.db")
    database.initialize()
    add_event(
        database,
        project="zigbee",
        device_id="0xC38F",
        channel="bridge/uplink/zigbee/0xC38F/data",
        payload={
            "event": "terminal1_sensor_report",
            "radio": {"rssi_dbm": -44},
            "sensor_type": "mq2_and_pir",
            "mq2": {"voltage_v": 0.301, "smoke_relative_percent": 9.1},
            "pir": {"digital_value": 0, "motion_detected": False, "state": "clear"},
        },
    )
    add_event(
        database,
        project="zigbee",
        device_id="0xB25B",
        channel="bridge/uplink/zigbee/0xB25B/data",
        payload={
            "event": "environment_sensor_report",
            "radio": {"rssi_dbm": -53},
            "sensor_type": "dht11_light_pressure",
            "dht11": {"temperature_c": 25, "humidity_percent": 51, "valid": True},
        },
    )

    dashboard = database.zigbee_dashboard()
    devices = {item["device_id"]: item for item in dashboard["devices"]}

    assert devices["0xC38F"]["latest"] == {
        "voltage": 0.301,
        "smoke": 9.1,
        "presence": False,
    }
    assert devices["0xB25B"]["latest"] == {"temperature": 25, "humidity": 51}
    links = {item["device_id"]: item for item in dashboard["links"]}
    assert links["0xC38F"]["rssi"] == -44
    assert links["0xB25B"]["rssi"] == -53


def test_lora_dashboard_renders_reassembled_hcv3_jpeg(tmp_path):
    database = Database(tmp_path / "platform.db")
    database.initialize()
    jpeg = b"\xff\xd8camera-frame\xff\xd9"
    encoded = base64.b64encode(jpeg).decode("ascii")
    add_event(
        database,
        project="lora",
        device_id="dc56b7d6a7dd94a1",
        channel="/device/update",
        payload={
            "device_id": "dc56b7d6a7dd94a1",
            "type": "camera",
            "camera_transport": "lorawan_hcv3",
            "image_b64": encoded,
            "image_mime": "image/jpeg",
            "format": "jpeg",
            "bytes": len(jpeg),
            "image_seq": 22,
        },
    )

    dashboard = database.lora_dashboard()

    assert len(dashboard["devices"]) == 1
    camera = dashboard["devices"][0]
    assert camera["device_type"] == "camera"
    assert camera["latest_image"] == f"data:image/jpeg;base64,{encoded}"
    assert camera["latest_frame"]["bytes"] == len(jpeg)


def test_lora_dashboard_does_not_treat_hcv3_chunk_as_environment_data(tmp_path):
    database = Database(tmp_path / "platform.db")
    database.initialize()
    add_event(
        database,
        project="lora",
        device_id="EoRa-HUB-400TB",
        channel="/device/update",
        payload={"temperature": 205.7, "humidity": 154.5},
    )
    chunk = b"HC\x03" + bytes(21) + b"jpeg-chunk"
    add_event(
        database,
        project="lora",
        device_id="EoRa-HUB-400TB",
        channel="/device/update",
        payload={
            "device_id": "EoRa-HUB-400TB",
            "type": "status",
            "fPort": 2,
            "data": base64.b64encode(chunk).decode("ascii"),
            # These values reproduce the legacy adapter's false binary decode.
            "temperature": 205.7,
            "humidity": 154.5,
            "soil_moisture": 1875.2,
            "precipitation": 819.1,
        },
    )

    dashboard = database.lora_dashboard()

    assert len(dashboard["devices"]) == 1
    camera = dashboard["devices"][0]
    assert camera["device_type"] == "camera"
    assert camera["latest"] == {}
    assert camera["points"] == []
    assert camera["metrics"] == []


def test_lora_dashboard_does_not_treat_hpv1_status_as_environment_data(tmp_path):
    database = Database(tmp_path / "platform.db")
    database.initialize()
    status = b"HP" + bytes([1, 2]) + (18512).to_bytes(4, "little") + bytes(8)
    add_event(
        database,
        project="lora",
        device_id="EoRa-HUB-400TB",
        channel="/device/update",
        payload={
            "device_id": "EoRa-HUB-400TB",
            "fPort": 2,
            "data": base64.b64encode(status).decode("ascii"),
            "temperature": 97.4,
            "humidity": 5746.4,
        },
    )

    dashboard = database.lora_dashboard()

    assert len(dashboard["devices"]) == 1
    camera = dashboard["devices"][0]
    assert camera["device_type"] == "camera"
    assert camera["latest"] == {}
    assert camera["points"] == []
