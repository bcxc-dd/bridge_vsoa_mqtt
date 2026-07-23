"""Offline deployment smoke test for Python 3.8.10 bridge hosts."""

import sys

import vsoa

from src.config import load_config
from src.downlink.command import build_mqtt_message
from src.uplink.adapters import select_adapter


def main():
    if sys.version_info < (3, 8, 10):
        raise RuntimeError("Python 3.8.10 or newer is required")

    config = load_config("config.yaml")
    payload = {
        "device_id": "py38-node",
        "temperature": 24.5,
        "humidity": 58,
        "soil_moisture": 41,
        "precipitation": 1.2,
    }
    topic = "bridge/uplink/lora/py38-node/data"
    adapter = select_adapter(topic, payload)
    report = adapter.parse(topic, payload)
    if report.temperature != 24.5 or report.raw.get("soil_moisture") != 41:
        raise RuntimeError("LoRa uplink adapter smoke test failed")

    downlink_topic, downlink_payload = build_mqtt_message({
        "command_id": "py38-smoke",
        "device_type": "generic",
        "device_id": "eora_s3_400tb_001",
        "action": "set",
        "params": {"led": "on"},
    }, "bridge/downlink")
    if downlink_payload != '{"cmd":"led","value":"on"}':
        raise RuntimeError("Downlink command smoke test failed")

    vsoa.Server("Python 3.8 compatibility smoke test")
    print("Python:", sys.version.split()[0])
    print("VSOA:", vsoa.__file__)
    print("MQTT broker:", config.mqtt.broker)
    print("Uplink adapter:", adapter.name)
    print("Downlink:", downlink_topic, downlink_payload)
    print("Python 3.8 bridge smoke test: OK")


if __name__ == "__main__":
    main()
