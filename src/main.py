"""
MQTT ↔ VSOA 桥接组件 — 统一入口

同时启动上行（MQTT→VSOA）和下行（VSOA→MQTT）处理管道。

启动:  python src/main.py [--config config.yaml] [--no-mqtt]

端口:
  3001 — VSOA Server（上行查询 + 下行 RPC + ACK/事件发布）
  3000 — 出站 VSOA Client（订阅业务层 /ctrl/cmd）
  1883 — 出站 MQTT（上行订阅 + 下行发布）
  9090 — TCP JSON Lines 注入（离线测试）
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any

# Ensure src/ and project root are on path
_SRC = Path(__file__).resolve().parent
_ROOT = _SRC.parent
for _p in (str(_SRC), str(_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from config import load_config, BridgeConfig
from device_registry import DeviceRegistry
from mqtt_handler import MQTTHandler

logger = logging.getLogger("bridge")

# ---------------------------------------------------------------------------
# globals for signal handler
# ---------------------------------------------------------------------------
_vsoa_server: Any = None        # unified vsoa.Server
_pubsub_handler: Any = None
_mqtt_handler: MQTTHandler | None = None
_tcp_inject: Any = None
_rpc_server: Any = None
_uplink_server: Any = None


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="MQTT-VSOA Bridge")
    parser.add_argument("--config", type=str, default="config.yaml",
                        help="Path to config.yaml")
    parser.add_argument("--no-mqtt", action="store_true",
                        help="Disable MQTT (offline mode)")
    args = parser.parse_args()

    # ---- 1. Load config ----
    try:
        config = load_config(args.config)
    except Exception:
        logging.basicConfig(level=logging.INFO)
        logging.getLogger("bridge").error("Config load failed:\n%s", traceback.format_exc())
        sys.exit(1)

    # ---- 2. Setup logging ----
    _setup_logging(config)

    logger.info("===== MQTT-VSOA Bridge v%s starting =====", config.bridge.version)
    logger.info("config: %s", args.config)

    # ---- 3. Device registry + dedup ----
    registry = DeviceRegistry(max_devices=config.uplink.max_devices)
    logger.info("[OK] DeviceRegistry: max=%d devices", config.uplink.max_devices)

    dedup = None
    if config.downlink.command.dedup.enabled:
        from downlink.dedup import DedupCache
        dedup = DedupCache(
            ttl_seconds=config.downlink.command.dedup.ttl_seconds,
            max_size=config.downlink.command.dedup.max_size,
        )
        logger.info("[OK] DedupCache: ttl=%ds max=%d",
                    config.downlink.command.dedup.ttl_seconds,
                    config.downlink.command.dedup.max_size)

    # ---- 4. MQTT Handler ----
    global _mqtt_handler
    mqtt_enabled = not args.no_mqtt
    if mqtt_enabled:
        _mqtt_handler = MQTTHandler()
        ok = _mqtt_handler.connect(
            broker=config.mqtt.broker,
            port=config.mqtt.port,
            client_id=config.mqtt.client_id,
            keepalive=config.mqtt.keepalive,
            username=config.mqtt.username,
            password=config.mqtt.password,
            reconnect_enabled=config.mqtt.reconnect.enabled,
            reconnect_interval_ms=config.mqtt.reconnect.interval_ms,
            reconnect_max_retries=config.mqtt.reconnect.max_retries,
        )
        if not ok:
            logger.error("MQTT connect failed")
            sys.exit(1)
        _mqtt_handler.subscribe(config.mqtt.uplink_topics, qos=config.mqtt.qos)
        logger.info("[OK] MQTT connected: %s:%d (%d uplink topics)",
                    config.mqtt.broker, config.mqtt.port,
                    len(config.mqtt.uplink_topics))
    else:
        _mqtt_handler = None
        logger.info("[INFO] MQTT disabled (--no-mqtt)")

    # ---- 5. Unified VSOA Server (port 3001) ----
    import vsoa
    global _vsoa_server
    _vsoa_server = vsoa.Server({
        "name": config.bridge.name,
        "version": config.bridge.version,
    })

    # Helper: VSOA publish wrapper for ACK + uplink notifications
    def _vsoa_publish(url: str, data: dict) -> None:
        try:
            _vsoa_server.publish(url, vsoa.Payload(param=data))
        except Exception:
            logger.warning("[VSOA] publish %s failed (non-fatal)", url, exc_info=True)

    # ---- 4b. ChirpStack 下行适配（方案 B） ----
    chirpstack_cfg = {
        "enabled": config.chirpstack.enabled,
        "confirmed": config.chirpstack.confirmed,
        "fPort": config.chirpstack.fPort,
        "application_id": config.chirpstack.application_id,
    } if config.chirpstack.enabled else None
    if chirpstack_cfg:
        logger.info("[OK] ChirpStack downlink: enabled (fPort=%d confirmed=%s)",
                    chirpstack_cfg["fPort"], chirpstack_cfg["confirmed"])

    # ---- 5a. Register uplink query endpoints on shared server ----
    from uplink.vsoa_server import UplinkVsoaServer
    from uplink.adapters import ADAPTERS
    global _uplink_server
    _uplink_server = UplinkVsoaServer(config, registry, adapters=ADAPTERS,
                                       server=_vsoa_server)

    # ---- 5b. Register downlink RPC handler on shared server ----
    from downlink.rpc_server import RpcServer
    global _rpc_server
    _rpc_server = RpcServer(
        bind_host=config.vsoa.server.bind_host,
        port=config.vsoa.server.port,
        endpoint="/bridge/send_command",
        max_timeout_ms=config.downlink.command.max_timeout_ms,
        mqtt_topic_prefix=config.mqtt.downlink_topic_prefix,
        mqtt_topic_prefixes=config.mqtt.downlink_topic_prefixes,
        mqtt_publisher=_mqtt_handler.publish if _mqtt_handler else None,
        mqtt_is_connected=lambda: _mqtt_handler.is_connected if _mqtt_handler else False,
        registry=registry,
        dedup=dedup,
        retry_max_retries=config.downlink.command.retry.max_retries,
        retry_backoff_base_ms=config.downlink.command.retry.backoff_base_ms,
        chirpstack_config=chirpstack_cfg,
        server=_vsoa_server,
    )

    # ---- 6. Start uplink VSOA Server ----
    _uplink_server.start()
    if not _uplink_server.is_running:
        logger.error("Uplink VSOA Server failed to start")
        sys.exit(1)
    logger.info("[OK] Uplink VSOA endpoints registered (port %d)", config.vsoa.server.port)

    # ---- 6b. Start downlink RPC Server ----
    _rpc_server.start()
    if not _rpc_server.is_running:
        logger.error("RPC Server failed to start")
        sys.exit(1)
    logger.info("[OK] RPC Server on %s:%d endpoint=%s",
                config.vsoa.server.bind_host, config.vsoa.server.port, "/bridge/send_command")

    # ---- 6c. Start shared VSOA Server (now that all handlers are registered) ----
    _vsoa_ready = False

    def _run_vsoa():
        nonlocal _vsoa_ready
        _vsoa_ready = True
        logger.info("[VSOA] Unified server listening on %s:%d",
                    config.vsoa.server.bind_host, config.vsoa.server.port)
        try:
            _vsoa_server.run(config.vsoa.server.bind_host, config.vsoa.server.port)
        except Exception:
            logger.error("[VSOA] Unified server exception:\n%s", traceback.format_exc())
        finally:
            logger.info("[VSOA] Unified server stopped")

    _vsoa_thread = threading.Thread(target=_run_vsoa, daemon=True, name="vsoa-unified")
    _vsoa_thread.start()

    # wait for server to start listening
    waited = 0
    while not _vsoa_ready and waited < 50:
        time.sleep(0.1)
        waited += 1
    if not _vsoa_ready:
        logger.error("Unified VSOA Server failed to start")
        sys.exit(1)
    logger.info("[OK] Unified VSOA Server listening on %s:%d",
                config.vsoa.server.bind_host, config.vsoa.server.port)

    # ---- 7. TCP Inject (port 9090) ----
    from uplink.tcp_inject import TcpInjectServer
    global _tcp_inject

    def _process_uplink(topic: str, payload: dict) -> None:
        """上行处理管道: adapter → registry.upsert → VSOA 通知。"""
        from uplink.adapters import select_adapter
        from uplink.adapters.base import AdapterParseError
        adapter = select_adapter(topic, payload)
        try:
            report_obj = adapter.parse(topic, payload)
        except AdapterParseError as exc:
            logger.warning("[UPLINK] adapter=%s parse_failed: %s  topic=%s",
                           adapter.name, exc, topic)
            return

        dev, created = registry.upsert(report_obj)
        if dev is None:
            return

        _vsoa_publish("/device/update", dev.to_json())
        _vsoa_publish("/bridge/event", {
            "event": "data_received", "device_id": report_obj.device_id,
            "source": report_obj.source, "adapter": report_obj.adapter,
            "timestamp": int(time.time() * 1000),
        })

        action = "registered" if created else "updated"
        logger.info("[UPLINK] %s source=%s adapter=%s device=%s type=%s",
                    action, report_obj.source, report_obj.adapter,
                    report_obj.device_id, report_obj.type)

    _tcp_inject = TcpInjectServer(
        callback=lambda topic, payload: _process_uplink(topic, payload),
        port=config.uplink.tcp_inject_port,
    )
    _tcp_inject.start()
    logger.info("[OK] TCP inject: 0.0.0.0:%d", config.uplink.tcp_inject_port)

    # ---- 8. PubSub Client (connect business layer VSOA Server :3000) ----
    # In --no-mqtt (offline) mode, skip PubSub — no business-layer VSOA server
    if mqtt_enabled:
        from downlink.pubsub_handler import PubSubHandler
        global _pubsub_handler
        _pubsub_handler = PubSubHandler(
            server_url=config.vsoa.pubsub_client.server_url,
            subscribe_urls=list(config.vsoa.pubsub_client.subscribe_urls),
            max_timeout_ms=config.downlink.command.max_timeout_ms,
            mqtt_topic_prefix=config.mqtt.downlink_topic_prefix,
            mqtt_topic_prefixes=config.mqtt.downlink_topic_prefixes,
            mqtt_publisher=_mqtt_handler.publish if _mqtt_handler else None,
            ack_publish_url=config.vsoa.pubsub_client.ack_publish_url,
            ack_publisher=lambda url, data: _vsoa_publish(url, data),
            registry=registry,
            dedup=dedup,
            reconnect_interval_ms=config.vsoa.reconnect.interval_ms,
            reconnect_max_retries=config.vsoa.reconnect.max_retries,
            reconnect_backoff_multiplier=config.vsoa.reconnect.backoff_multiplier,
            chirpstack_config=chirpstack_cfg,
        )

        max_retries = config.vsoa.reconnect.max_retries
        for attempt in range(1, max_retries + 1):
            ok = _pubsub_handler.connect()
            if ok:
                break
            logger.warning("PubSub connect attempt %d/%d, retrying...", attempt, max_retries)
            time.sleep(config.vsoa.reconnect.interval_ms / 1000.0)
        else:
            logger.error("PubSub connect failed after %d attempts", max_retries)
            sys.exit(1)
        logger.info("[OK] PubSub subscribed: %s", config.vsoa.pubsub_client.subscribe_urls)
    else:
        logger.info("[INFO] PubSub disabled (offline mode)")

    # Set uplink callback for MQTT messages
    if _mqtt_handler:
        _mqtt_handler.set_uplink_callback(
            lambda topic, payload: _process_uplink(topic, payload)
        )

    # ---- 9. Banner ----
    print("=" * 56)
    print(f"[INFO] MQTT-VSOA Bridge v{config.bridge.version} started")
    print(f"[INFO] VSOA Server         : {config.vsoa.server.bind_host}:{config.vsoa.server.port}")
    print(f"[INFO]   RPC: /bridge/health, /adapter/list, /uplink/schema")
    print(f"[INFO]   RPC: /device/list, /device/all/data, /device/{{id}}/data")
    print(f"[INFO]   RPC: /bridge/send_command (downlink)")
    print(f"[INFO]   Pub: /device/update, /bridge/event, /ctrl/ack")
    print(f"[INFO] MQTT                 : {'enabled' if mqtt_enabled else 'disabled'} "
          f"({config.mqtt.broker}:{config.mqtt.port})")
    print(f"[INFO]   Uplink topics      : {len(config.mqtt.uplink_topics)}")
    print(f"[INFO]   Downlink prefix    : {config.mqtt.downlink_topic_prefix}")
    if _pubsub_handler is not None:
        print(f"[INFO] PubSub subscribed    : {config.vsoa.pubsub_client.subscribe_urls} "
              f"→ {config.vsoa.pubsub_client.ack_publish_url}")
    else:
        print(f"[INFO] PubSub               : disabled (offline mode)")
    print(f"[INFO] TCP inject           : 0.0.0.0:{config.uplink.tcp_inject_port}")
    print(f"[INFO] Device registry      : max {config.uplink.max_devices} devices")
    print("=" * 56)

    # ---- 10. Signal handling ----
    def shutdown(signum=None, frame=None) -> None:
        logger.info("Shutting down...")
        if _pubsub_handler:
            _pubsub_handler.stop()
        if _tcp_inject:
            _tcp_inject.stop()
        if _mqtt_handler:
            _mqtt_handler.disconnect()
        if _rpc_server:
            _rpc_server.stop()
        if _uplink_server:
            _uplink_server.stop()
        logger.info("Bridge stopped")
        sys.exit(0)

    signal.signal(signal.SIGINT, lambda s, f: shutdown())
    signal.signal(signal.SIGTERM, lambda s, f: shutdown())

    # ---- 11. Event loop ----
    if _pubsub_handler is not None:
        try:
            _pubsub_handler.run_forever()
        except KeyboardInterrupt:
            pass
        except Exception:
            logger.error("Runtime error:\n%s", traceback.format_exc())
        finally:
            shutdown()
    else:
        # Offline mode: just wait for signal
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            shutdown()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _setup_logging(config: BridgeConfig) -> None:
    log_cfg = config.logging
    level = getattr(logging, log_cfg.level.upper(), logging.INFO)
    root = logging.getLogger("bridge")
    root.setLevel(level)

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.setFormatter(logging.Formatter(log_cfg.format, datefmt=log_cfg.date_format))
    root.addHandler(console)

    if log_cfg.file:
        log_dir = Path(log_cfg.file).parent
        if log_dir and not log_dir.is_dir():
            log_dir.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_cfg.file, encoding="utf-8")
        fh.setLevel(level)
        fh.setFormatter(logging.Formatter(log_cfg.format, datefmt=log_cfg.date_format))
        root.addHandler(fh)


if __name__ == "__main__":
    main()
