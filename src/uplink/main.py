"""
Entry point for the Python uplink bridge.

Start::

    python src/main.py [--no-mqtt] [--mqtt-broker tcp://host:port] [--config config.yaml]
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from pathlib import Path
from typing import Any

# Ensure the src directory is on sys.path (allows running from bridge-uplink/)
_SRC = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from .config import load_config, BridgeUplinkConfig
from .adapters import select_adapter, ADAPTERS
from .adapters.base import AdapterParseError
from .device_registry import DeviceRegistry
from .vsoa_server import UplinkVsoaServer
from .tcp_inject import TcpInjectServer

logger = logging.getLogger("bridge-uplink")


# ---------------------------------------------------------------------------
# globals (for signal handler)
# ---------------------------------------------------------------------------
_vsoa: UplinkVsoaServer | None = None
_tcp_inject: TcpInjectServer | None = None
_mqtt_handler: Any = None


# ---------------------------------------------------------------------------
def _process_uplink_message(
    topic: str,
    payload: dict[str, Any],
    registry: DeviceRegistry,
    vsoa: UplinkVsoaServer,
) -> None:
    """Core pipeline: adapter select -> parse -> upsert -> publish."""
    adapter = select_adapter(topic, payload)
    try:
        report = adapter.parse(topic, payload)
    except AdapterParseError as exc:
        logger.warning(
            "[UPLINK] adapter=%s parse_failed: %s  topic=%s",
            adapter.name, exc, topic,
        )
        return

    dev, created = registry.upsert(report)
    if dev is None:
        return  # registry full

    # Publish VSOA notifications
    vsoa.publish("/device/update", dev.to_json())
    vsoa.publish("/bridge/event", {
        "event":      "data_received",
        "device_id":  report.device_id,
        "source":     report.source,
        "adapter":    report.adapter,
        "timestamp":  int(time.time() * 1000),
    })

    action = "registered" if created else "updated"
    logger.info(
        "[UPLINK] %s source=%s adapter=%s device=%s type=%s",
        action, report.source, report.adapter, report.device_id, report.type,
    )


# ---------------------------------------------------------------------------
def _setup_logging(cfg: BridgeUplinkConfig) -> None:
    kwargs: dict = {
        "level":    getattr(logging, cfg.logging.level.upper(), logging.INFO),
        "format":   cfg.logging.format,
        "datefmt":  cfg.logging.date_format,
    }
    if cfg.logging.file:
        kwargs["filename"] = cfg.logging.file
    logging.basicConfig(**kwargs)


# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="VSOA Uplink Bridge")
    parser.add_argument("--no-mqtt", action="store_true",
                        help="Disable MQTT subscriber (offline mode)")
    parser.add_argument("--mqtt-broker", type=str, metavar="URI",
                        help="Override MQTT broker URI (e.g. tcp://broker.emqx.io:1883)")
    parser.add_argument("--config", type=str, default="config.yaml",
                        help="Path to config.yaml (default: config.yaml)")
    args = parser.parse_args()

    # Load configuration
    cfg = load_config(args.config)

    # Override broker from CLI
    if args.mqtt_broker:
        cfg.mqtt.broker = args.mqtt_broker

    _setup_logging(cfg)

    # -- Registry --
    registry = DeviceRegistry(max_devices=cfg.uplink.max_devices)

    # -- VSOA Server --
    global _vsoa
    _vsoa = UplinkVsoaServer(cfg.vsoa.server, registry, adapters=ADAPTERS)

    # -- TCP injection --
    global _tcp_inject
    _tcp_inject = TcpInjectServer(
        callback=lambda topic, payload: _process_uplink_message(
            topic, payload, registry, _vsoa,
        ),
        port=cfg.uplink.tcp_inject_port,
    )
    _tcp_inject.start()
    _vsoa.start()

    # -- MQTT Subscriber --
    global _mqtt_handler
    mqtt_enabled = not args.no_mqtt
    if mqtt_enabled:
        from mqtt_handler import UplinkMqttHandler
        _mqtt_handler = UplinkMqttHandler(
            cfg.mqtt,
            on_message_callback=lambda topic, payload: _process_uplink_message(
                topic, payload, registry, _vsoa,
            ),
        )
        _mqtt_handler.connect()
    else:
        _mqtt_handler = None

    # -- Banner --
    print("=" * 48)
    print("[INFO] VSOA Uplink Bridge started")
    print(f"[INFO] VSOA RPC port      : {cfg.vsoa.server.port}")
    print(f"[INFO] TCP injection port : {cfg.uplink.tcp_inject_port}")
    print(f"[INFO] MQTT subscriber    : {'enabled' if mqtt_enabled and _mqtt_handler else 'disabled'} "
          f"({cfg.mqtt.broker}:{cfg.mqtt.port})")
    print("[INFO] RPC: /bridge/health, /adapter/list, /uplink/schema")
    print("[INFO] RPC: /device/list, /device/all/data, /device/{{id}}/data")
    print("=" * 48)

    # -- Signal handling --
    def _shutdown(signum, frame):
        logger.info("Received signal %d, shutting down", signum)
        if _mqtt_handler:
            _mqtt_handler.stop()
        if _tcp_inject:
            _tcp_inject.stop()
        if _vsoa:
            _vsoa.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # -- Idle loop --
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        _shutdown(0, None)


if __name__ == "__main__":
    main()
