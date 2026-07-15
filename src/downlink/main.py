"""
bridge-downlink 主入口

启动流程:
  1. 加载 config.yaml
  2. 初始化日志
  3. 启动 RPC Server（VSOA Server，真正 VSOA RPC，/bridge/send_command）
  4. 连接 MQTT Broker
  5. 连接 Pub/Sub Client（VSOA Client，/ctrl/cmd 盲转发）
  6. 进入 VSOA 事件循环
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import time
import traceback

_here = os.path.dirname(os.path.abspath(__file__))
if _here not in sys.path:
    sys.path.insert(0, _here)

from config import load_config
from dedup import DedupCache
from device_registry import DeviceRegistry
from mqtt_handler import MQTTHandler
from pubsub_handler import PubSubHandler
from rpc_server import RpcServer


def setup_logging(config) -> logging.Logger:
    log_config = config.logging
    level = getattr(logging, log_config.level.upper(), logging.INFO)
    root_logger = logging.getLogger("bridge")
    root_logger.setLevel(level)

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.setFormatter(logging.Formatter(log_config.format, datefmt=log_config.date_format))
    root_logger.addHandler(console)

    log_file = getattr(log_config, "file", None)
    if log_file:
        log_dir = os.path.dirname(log_file)
        if log_dir and not os.path.isdir(log_dir):
            os.makedirs(log_dir, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(level)
        file_handler.setFormatter(logging.Formatter(log_config.format, datefmt=log_config.date_format))
        root_logger.addHandler(file_handler)

    return root_logger


def main() -> None:
    config_path = os.environ.get("BRIDGE_CONFIG", "config.yaml")
    logger = logging.getLogger("bridge")
    config = None

    try:
        config = load_config(config_path)
    except Exception:
        logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)s] %(message)s")
        logger = logging.getLogger("bridge")
        logger.error("config load failed:\n%s", traceback.format_exc())
        sys.exit(1)

    logger = setup_logging(config)
    logger.info("===== bridge-downlink starting =====")
    logger.info("config: %s", config_path)

    rpc_server: RpcServer | None = None
    mqtt_handler: MQTTHandler | None = None
    pubsub_handler: PubSubHandler | None = None

    def shutdown(signum=None, frame=None) -> None:
        logger.info("shutting down...")
        if pubsub_handler:
            pubsub_handler.stop()
        if mqtt_handler:
            mqtt_handler.disconnect()
        if rpc_server:
            rpc_server.stop()
        logger.info("bridge-downlink stopped")
        sys.exit(0)

    signal.signal(signal.SIGINT, lambda s, f: shutdown())
    signal.signal(signal.SIGTERM, lambda s, f: shutdown())

    try:
        # 提前提取配置引用（registry/dedup 初始化需要）
        cmd_cfg = config.command
        mqtt_cfg = config.mqtt

        # ---- 3. 加载设备注册表 + 幂等去重（v3.0 新增） ----
        registry_cfg = getattr(config, "device_registry", None)
        registry: DeviceRegistry | None = None
        if registry_cfg:
            registry_path = registry_cfg.source
            if not os.path.isabs(registry_path):
                registry_path = os.path.join(os.path.dirname(config_path) or ".", registry_path)
            if os.path.isfile(registry_path):
                registry = DeviceRegistry(registry_path)
                logger.info("[OK] Device registry: %d devices loaded", registry.device_count)
            else:
                logger.warning("[WARN] Device registry file not found: %s", registry_path)

        dedup_cfg = cmd_cfg.dedup
        dedup: DedupCache | None = None
        if getattr(dedup_cfg, "enabled", True):
            dedup = DedupCache(
                ttl_seconds=getattr(dedup_cfg, "ttl_seconds", 300),
                max_size=getattr(dedup_cfg, "max_size", 10000),
            )
            logger.info("[OK] Dedup cache: ttl=%ds max=%d",
                        getattr(dedup_cfg, "ttl_seconds", 300),
                        getattr(dedup_cfg, "max_size", 10000))
        else:
            logger.info("[OK] Dedup cache: disabled")

        # ---- 4. 启动 RPC Server ----
        rpc_cfg = config.vsoa.rpc_server

        retry_cfg = getattr(cmd_cfg, "retry", None)
        rpc_server = RpcServer(
            bind_host=rpc_cfg.bind_host,
            port=rpc_cfg.port,
            endpoint=rpc_cfg.endpoint,
            max_timeout_ms=cmd_cfg.max_timeout_ms,
            mqtt_topic_prefix=mqtt_cfg.topic_prefix,
            mqtt_topic_prefixes=getattr(mqtt_cfg, "topic_prefixes", {}),
            mqtt_publisher=None,
            registry=registry,
            dedup=dedup,
            retry_max_retries=getattr(retry_cfg, "max_retries", 3) if retry_cfg else 3,
            retry_backoff_base_ms=getattr(retry_cfg, "backoff_base_ms", 500) if retry_cfg else 500,
        )
        rpc_server.start()
        if not rpc_server.is_running:
            logger.error("RPC Server failed to start")
            sys.exit(1)
        logger.info("[OK] RPC Server on %s:%d endpoint=%s", rpc_cfg.bind_host, rpc_cfg.port, rpc_cfg.endpoint)

        # ---- 4. 连接 MQTT ----
        mqtt_handler = MQTTHandler()
        ok = mqtt_handler.connect(
            broker=mqtt_cfg.broker, port=mqtt_cfg.port,
            client_id=mqtt_cfg.client_id, keepalive=mqtt_cfg.keepalive,
            reconnect_enabled=mqtt_cfg.reconnect.enabled,
            reconnect_interval_ms=mqtt_cfg.reconnect.interval_ms,
            reconnect_max_retries=mqtt_cfg.reconnect.max_retries,
        )
        if not ok:
            logger.error("MQTT connect failed")
            sys.exit(1)
        logger.info("[OK] MQTT connected: %s:%d", mqtt_cfg.broker, mqtt_cfg.port)

        # 注入 MQTT 依赖到 RPC Server
        rpc_server._publisher = mqtt_handler.publish
        rpc_server._is_connected = lambda: mqtt_handler.is_connected

        # ---- 5. 连接 Pub/Sub Client ----
        vsoa_cfg = config.vsoa
        reconnect_cfg = vsoa_cfg.reconnect
        pubsub_handler = PubSubHandler(
            server_url=vsoa_cfg.pubsub_client.server_url,
            subscribe_urls=list(vsoa_cfg.pubsub_client.subscribe_urls),
            max_timeout_ms=cmd_cfg.max_timeout_ms,
            mqtt_topic_prefix=mqtt_cfg.topic_prefix,
            mqtt_topic_prefixes=getattr(mqtt_cfg, "topic_prefixes", {}),
            mqtt_publisher=mqtt_handler.publish,
            ack_publish_url=getattr(vsoa_cfg.pubsub_client, "ack_publish_url", "/ctrl/ack"),
            registry=registry,
            dedup=dedup,
            reconnect_interval_ms=getattr(reconnect_cfg, "interval_ms", 3000),
            reconnect_max_retries=getattr(reconnect_cfg, "max_retries", 10),
            reconnect_backoff_multiplier=getattr(reconnect_cfg, "backoff_multiplier", 2.0),
        )

        max_retries = 10
        for attempt in range(1, max_retries + 1):
            ok = pubsub_handler.connect()
            if ok:
                break
            logger.warning("VSOA PubSub connect attempt %d/%d, retrying...", attempt, max_retries)
            time.sleep(3)
        else:
            logger.error("VSOA PubSub connect failed after %d attempts", max_retries)
            sys.exit(1)
        logger.info("[OK] PubSub subscribed: %s", vsoa_cfg.pubsub_client.subscribe_urls)

        # ---- 6. 运行 ----
        logger.info("[INFO] bridge-downlink running (RPC:%d + PubSub)", rpc_cfg.port)
        pubsub_handler.run_forever()

    except KeyboardInterrupt:
        pass
    except Exception:
        logger.error("runtime error:\n%s", traceback.format_exc())
    finally:
        shutdown()


if __name__ == "__main__":
    main()
