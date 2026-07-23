import asyncio
from types import SimpleNamespace

from iot_test_platform.backend import app as platform


class FakeMqttService:
    connected = True

    def __init__(self):
        self.messages = []

    def publish(self, topic, payload):
        self.messages.append((topic, payload))
        return True


def test_lora_control_publishes_directly_without_vsoa_or_online_state(tmp_path, monkeypatch):
    database = platform.Database(tmp_path / "platform.db")
    database.initialize()
    mqtt_service = FakeMqttService()
    monkeypatch.setattr(platform, "db", database)
    monkeypatch.setattr(platform, "mqtt_service", mqtt_service)
    monkeypatch.setattr(platform.vsoa_service, "connected", False)
    request = platform.CommandRequest(
        device_id="0000000000000925",
        project="lora",
        command="set",
        parameters={"led": "on"},
        confirmed=True,
    )
    http_request = SimpleNamespace(state=SimpleNamespace(user={"sub": "tester"}))

    result = asyncio.run(platform.create_device_command(request, http_request))

    assert mqtt_service.messages == [
        (
            "bridge/downlink/generic/eora_s3_400tb_001/cmd",
            {"cmd": "led", "value": "on"},
        )
    ]
    assert result["status"] == "pending"
    assert result["topic"] == "bridge/downlink/generic/eora_s3_400tb_001/cmd"


def test_lora_motor_uses_same_wifi_control_contract(tmp_path, monkeypatch):
    database = platform.Database(tmp_path / "platform.db")
    database.initialize()
    mqtt_service = FakeMqttService()
    monkeypatch.setattr(platform, "db", database)
    monkeypatch.setattr(platform, "mqtt_service", mqtt_service)
    request = platform.CommandRequest(
        device_id="Ebyte Test Device 470",
        project="lora",
        command="set",
        parameters={"motor": "off"},
        confirmed=True,
    )
    http_request = SimpleNamespace(state=SimpleNamespace(user={"sub": "tester"}))

    asyncio.run(platform.create_device_command(request, http_request))

    assert mqtt_service.messages == [
        (
            "bridge/downlink/generic/eora_s3_400tb_001/cmd",
            {"cmd": "motor", "value": "off"},
        )
    ]
