"""
统一 MQTT 客户端 — subscribe（上行）+ publish（下行）。

合并自:
  - bridge-uplink/src/mqtt_handler.py (上行 subscriber)
  - bridge/src/downlink/mqtt_handler.py (下行 publisher)

特性: 线程安全 publish、自动重连、on_message 按 topic 前缀路由。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Callable, Dict

import paho.mqtt.client as mqtt

logger = logging.getLogger("bridge.mqtt")

# Callback: (topic: str, payload: dict) -> None
MessageCallback = Callable[[str, Dict[str, Any]], None]


class MQTTHandler:
    """统一 MQTT 客户端，同时支持 subscribe 和 publish。

    Usage:
        mqtt_handler = MQTTHandler()
        mqtt_handler.connect(broker="broker.emqx.io", port=1883)
        mqtt_handler.subscribe(["bridge/uplink/lora/+/data", ...])
        mqtt_handler.publish("bridge/downlink/lora/n1/set", json_str)
    """

    # 上行 topic 前缀模式（用于 on_message 路由）
    UPLINK_TOPIC_PATTERNS = (
        "application/",
        "s3/",
        "bridge/uplink/",
        "lora/",
        "zigbee/",
        "application/",
        "s3/",
    )

    def __init__(self) -> None:
        self._client: mqtt.Client | None = None
        self._lock = threading.Lock()
        self._connected = False
        self._reconnect_enabled = True
        self._reconnect_interval_ms = 3000
        self._reconnect_max_retries = 0
        self._reconnect_count = 0
        self._broker = ""
        self._port = 1883
        self._keepalive = 60
        self._client_id = ""
        self._qos = 1
        self._subscribed_topics: list[str] = []
        self._on_uplink_message: MessageCallback | None = None

    # ------------------------------------------------------------------
    # 连接
    # ------------------------------------------------------------------

    def connect(
        self,
        broker: str,
        port: int = 1883,
        client_id: str = "bridge-v1",
        keepalive: int = 60,
        username: str = "",
        password: str = "",
        reconnect_enabled: bool = True,
        reconnect_interval_ms: int = 3000,
        reconnect_max_retries: int = 0,
    ) -> bool:
        """连接 MQTT Broker。"""
        self._broker = broker
        self._port = port
        self._keepalive = keepalive
        self._client_id = client_id
        self._reconnect_enabled = reconnect_enabled
        self._reconnect_interval_ms = reconnect_interval_ms
        self._reconnect_max_retries = reconnect_max_retries
        self._reconnect_count = 0

        self._client = mqtt.Client(
            client_id=client_id,
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        )
        if username:
            self._client.username_pw_set(username, password)

        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message

        try:
            self._client.connect(broker, port, keepalive)
            self._client.loop_start()
            waited = 0
            while not self._connected and waited < 50:
                time.sleep(0.1)
                waited += 1
            if self._connected:
                logger.info("MQTT connected: %s:%d (client_id=%s)", broker, port, client_id)
                return True
            else:
                logger.error("MQTT connection timeout: %s:%d", broker, port)
                return False
        except Exception as e:
            logger.error("MQTT connect failed: %s", e)
            return False

    # ------------------------------------------------------------------
    # 订阅（上行）
    # ------------------------------------------------------------------

    def subscribe(self, topics: list[str], qos: int | None = None) -> None:
        """订阅上行 MQTT topic。

        Args:
            topics: topic 列表。
            qos: QoS 级别，默认使用 connect 时的值。
        """
        if qos is None:
            qos = self._qos
        self._subscribed_topics = list(topics)
        if self._client and self._connected:
            for topic in topics:
                self._client.subscribe(topic, qos=qos)
                logger.info("[MQTT] subscribed: %s (QoS %d)", topic, qos)

    def set_uplink_callback(self, callback: MessageCallback | None) -> None:
        """注入上行消息处理回调（adapter → registry.upsert → VSOA 通知）。"""
        self._on_uplink_message = callback

    # ------------------------------------------------------------------
    # 发布（下行，线程安全）
    # ------------------------------------------------------------------

    def publish(self, topic: str, payload: str, qos: int | None = None) -> bool:
        """发布 MQTT 消息（线程安全）。

        Returns:
            True 表示发布成功。
        """
        if qos is None:
            qos = self._qos

        with self._lock:
            if not self._client or not self._connected:
                logger.warning("MQTT not connected, publish skipped: topic=%s", topic)
                return False
            try:
                info = self._client.publish(topic, payload, qos=qos)
                if info.rc == mqtt.MQTT_ERR_SUCCESS:
                    logger.debug("MQTT published: topic=%s", topic)
                    return True
                else:
                    logger.error("MQTT publish error: rc=%s topic=%s", info.rc, topic)
                    return False
            except Exception as e:
                logger.error("MQTT publish exception: %s", e)
                return False

    # ------------------------------------------------------------------
    # 断开
    # ------------------------------------------------------------------

    def disconnect(self) -> None:
        """优雅断开 MQTT 连接。"""
        if self._client:
            self._reconnect_enabled = False
            try:
                self._client.loop_stop()
                self._client.disconnect()
            except Exception:
                pass
            self._client = None
        self._connected = False
        logger.info("MQTT disconnected")

    # ------------------------------------------------------------------
    # 属性
    # ------------------------------------------------------------------

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ------------------------------------------------------------------
    # paho 回调
    # ------------------------------------------------------------------

    def _on_connect(self, client: mqtt.Client, userdata: Any,
                    flags: dict, reason_code: int, properties: Any = None) -> None:
        if reason_code == 0:
            self._connected = True
            self._reconnect_count = 0
            logger.info("MQTT on_connect: OK")
            # 重连后重新订阅
            for topic in self._subscribed_topics:
                client.subscribe(topic, qos=self._qos)
                logger.debug("[MQTT] re-subscribed: %s", topic)
        else:
            self._connected = False
            logger.error("MQTT on_connect: failed (rc=%s)", reason_code)

    def _on_disconnect(self, client: mqtt.Client, userdata: Any,
                       flags: int, reason_code: int, properties: Any = None) -> None:
        self._connected = False
        if reason_code != 0:
            logger.warning("MQTT unexpected disconnect (reason_code=%s)", reason_code)
        else:
            logger.info("MQTT on_disconnect: normal")

        if self._reconnect_enabled:
            if self._reconnect_max_retries > 0 and self._reconnect_count >= self._reconnect_max_retries:
                logger.error("MQTT reconnect max retries reached (%d)", self._reconnect_max_retries)
                return
            self._reconnect_count += 1
            interval_sec = self._reconnect_interval_ms / 1000.0
            logger.info("MQTT reconnecting in %.1fs (attempt %d)...", interval_sec, self._reconnect_count)
            time.sleep(interval_sec)
            try:
                self._client.connect(self._broker, self._port, self._keepalive)
            except Exception as e:
                logger.error("MQTT reconnect failed: %s", e)

    def _on_message(self, client: mqtt.Client, userdata: Any,
                    msg: mqtt.MQTTMessage) -> None:
        """统一 on_message 回调，按 topic 前缀路由。

        匹配 bridge/uplink/、lora/、zigbee/ → 上行处理管道。
        其他 topic → 记录 warning。
        """
        topic = msg.topic
        try:
            payload_str = msg.payload.decode("utf-8")
        except UnicodeDecodeError:
            logger.warning("[MQTT] non-UTF8 payload on topic=%s, dropped", topic)
            return

        # 判断是否为上行 topic
        is_uplink = topic.startswith(self.UPLINK_TOPIC_PATTERNS)

        if is_uplink:
            # 上行：解析 JSON → 回调
            try:
                payload = json.loads(payload_str)
            except json.JSONDecodeError:
                logger.warning("[MQTT] invalid JSON on topic=%s, dropped", topic)
                return
            if not isinstance(payload, dict):
                logger.warning("[MQTT] payload is not a JSON object on topic=%s, dropped", topic)
                return

            logger.info("[MQTT] RX topic=%s", topic)
            if self._on_uplink_message:
                try:
                    self._on_uplink_message(topic, payload)
                except Exception:
                    logger.exception("[MQTT] uplink callback error for topic=%s", topic)
        else:
            logger.debug("[MQTT] RX non-uplink topic=%s (no handler)", topic)


class MQTTPublisherRouter:
    """Route project downlinks to their owning Broker."""

    def __init__(self, default_handler: MQTTHandler | None = None) -> None:
        self._default = default_handler
        self._routes: dict[str, MQTTHandler] = {}

    def add_route(self, project: str, handler: MQTTHandler) -> None:
        self._routes[project.strip().lower()] = handler

    def publish(self, topic: str, payload: str, qos: int | None = None) -> bool:
        parts = topic.strip("/").split("/")
        project = parts[2].lower() if len(parts) >= 3 and parts[:2] == ["bridge", "downlink"] else ""
        handler = self._routes.get(project) or self._default
        if handler is None:
            logger.error("No MQTT route for topic=%s", topic)
            return False
        logger.info("[MQTT] TX route=%s topic=%s", project or "default", topic)
        return handler.publish(topic, payload, qos=qos)

    @property
    def is_connected(self) -> bool:
        handlers = [self._default, *self._routes.values()]
        return any(handler and handler.is_connected for handler in handlers)
