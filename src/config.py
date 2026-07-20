"""
Unified configuration loader for MQTT ↔ VSOA Bridge.

Reads config.yaml and returns a typed BridgeConfig dataclass.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


# ---------------------------------------------------------------------------
# Config dataclasses
# ---------------------------------------------------------------------------

@dataclass
class BridgeInfoConfig:
    name: str = "MQTT-VSOA Bridge"
    version: str = "1.0.0"


@dataclass
class VsoaServerConfig:
    bind_host: str = "127.0.0.1"
    port: int = 3001


@dataclass
class VsoaBusinessServerConfig:
    auto_start: bool = True
    bind_host: str = "0.0.0.0"
    port: int = 3000
    advertised_url: str = "vsoa://192.168.3.230:3000"


@dataclass
class VsoaPubSubClientConfig:
    server_url: str = "vsoa://127.0.0.1:3000"
    subscribe_urls: list[str] = field(default_factory=lambda: ["/ctrl/cmd"])
    ack_publish_url: str = "/ctrl/ack"


@dataclass
class VsoaReconnectConfig:
    enabled: bool = True
    interval_ms: int = 3000
    max_retries: int = 10
    backoff_multiplier: float = 2.0


@dataclass
class VsoaConfig:
    server: VsoaServerConfig = field(default_factory=VsoaServerConfig)
    business_server: VsoaBusinessServerConfig = field(
        default_factory=VsoaBusinessServerConfig
    )
    pubsub_client: VsoaPubSubClientConfig = field(default_factory=VsoaPubSubClientConfig)
    reconnect: VsoaReconnectConfig = field(default_factory=VsoaReconnectConfig)


@dataclass
class MqttReconnectConfig:
    enabled: bool = True
    interval_ms: int = 3000
    max_retries: int = 0  # 0 = infinite


@dataclass
class MqttConfig:
    broker: str = "broker.emqx.io"
    port: int = 1883
    username: str = ""
    password: str = ""
    keepalive: int = 60
    client_id: str = "bridge-v1"
    qos: int = 1
    retained: bool = False
    reconnect: MqttReconnectConfig = field(default_factory=MqttReconnectConfig)
    uplink_topics: list[str] = field(default_factory=list)
    downlink_topic_prefix: str = "bridge/downlink"
    downlink_topic_prefixes: dict = field(default_factory=dict)


@dataclass
class UplinkConfig:
    tcp_inject_port: int = 9090
    max_devices: int = 64
    max_json_len: int = 8192
    max_topic_len: int = 192
    max_device_id_len: int = 64
    adapters: list[str] = field(default_factory=lambda: ["lora", "zigbee", "generic"])


@dataclass
class DedupConfig:
    enabled: bool = True
    ttl_seconds: int = 300
    max_size: int = 10000


@dataclass
class RetryConfig:
    max_retries: int = 3
    backoff_base_ms: int = 500


@dataclass
class DownlinkCommandConfig:
    default_timeout_ms: int = 10000
    max_timeout_ms: int = 60000
    pending_queue_size: int = 100
    dedup: DedupConfig = field(default_factory=DedupConfig)
    retry: RetryConfig = field(default_factory=RetryConfig)


@dataclass
class DownlinkConfig:
    command: DownlinkCommandConfig = field(default_factory=DownlinkCommandConfig)


@dataclass
class ChirpstackConfig:
    enabled: bool = False
    confirmed: bool = True
    fPort: int = 1
    application_id: str = ""


@dataclass
class LoggingConfig:
    level: str = "INFO"
    format: str = "[%(asctime)s] [%(levelname)s] %(message)s"
    date_format: str = "%Y-%m-%d %H:%M:%S"
    file: str = "logs/bridge.log"


@dataclass
class BridgeConfig:
    """Root configuration for the MQTT-VSOA Bridge."""
    bridge: BridgeInfoConfig = field(default_factory=BridgeInfoConfig)
    vsoa: VsoaConfig = field(default_factory=VsoaConfig)
    mqtt: MqttConfig = field(default_factory=MqttConfig)
    uplink: UplinkConfig = field(default_factory=UplinkConfig)
    downlink: DownlinkConfig = field(default_factory=DownlinkConfig)
    chirpstack: ChirpstackConfig = field(default_factory=ChirpstackConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)


# ---------------------------------------------------------------------------
# Default uplink topics
# ---------------------------------------------------------------------------

DEFAULT_UPLINK_TOPICS = [
    "application/+/device/+/event/up",
    "s3/eora-s3-400tb-001/data",
    "bridge/uplink/lora/+/data",
    "bridge/uplink/zigbee/+/data",
    "bridge/uplink/generic/+/data",
    "bridge/uplink/generic/+/status",
    "bridge/uplink/generic/+/error",
    "lora/+/up",
    "zigbee/+/report",
]


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def _dict_to_dataclass(cls: type, data: dict | None) -> object:
    """Recursively convert a dict to a dataclass instance."""
    if data is None:
        return cls()
    field_types = {f.name: f.type for f in cls.__dataclass_fields__.values()}
    kwargs: dict[str, Any] = {}
    for key, value in data.items():
        if key in field_types:
            target_type = field_types[key]
            if hasattr(target_type, "__dataclass_fields__") and isinstance(value, dict):
                kwargs[key] = _dict_to_dataclass(target_type, value)
            else:
                kwargs[key] = value
    return cls(**kwargs)


def load_config(path: str | Path = "config.yaml") -> BridgeConfig:
    """Load configuration from a YAML file.

    Returns a BridgeConfig with defaults applied for missing fields.
    """
    path = Path(path)

    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    else:
        raw = {}

    config = _dict_to_dataclass(BridgeConfig, raw)

    # Apply default topics if none configured
    if not config.mqtt.uplink_topics:
        config.mqtt.uplink_topics = list(DEFAULT_UPLINK_TOPICS)

    return config
