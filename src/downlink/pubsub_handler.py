"""
VSOA Pub/Sub 订阅模块

bridge 作为 VSOA Client 连接业务层 VSOA Server，订阅 /ctrl/cmd。
收到命令 → 校验 → 设备注册表 → 幂等去重 → 构造 MQTT 消息 → 发布 → publish ACK。

v3.0 变更:
  - 集成 DeviceRegistry（设备存在性检查 → 2203）
  - 集成 DedupCache（幂等去重 → 2006）
  - traceId 生成与注入（ACK / MQTT payload / 日志）
  - VSOA 断连自动重连（run_forever 内部指数退避重连）
"""

from __future__ import annotations

import logging
import threading
import time
import traceback
from typing import Any, Callable

import vsoa

from src.trace_id import generate_trace_id
from src.error_codes import (
    SUCCESS,
    ERR_CMD_DUPLICATE_ID,
    ERR_DEVICE_NOT_FOUND,
    ERR_MQTT_PUBLISH_FAILED,
)

from src.device_registry import DeviceRegistry

try:
    from .command import validate as _cmd_validate, build_mqtt_message, build_ack
    from .dedup import DedupCache
except ImportError:
    from command import validate as _cmd_validate, build_mqtt_message, build_ack  # type: ignore[no-redef]
    from dedup import DedupCache  # type: ignore[no-redef]

logger = logging.getLogger("bridge.pubsub")


class PubSubHandler:
    """VSOA Pub/Sub 订阅客户端（/ctrl/cmd 订阅 + /ctrl/ack 回执发布 + 自动重连）。"""

    def __init__(
        self,
        server_url: str = "vsoa://127.0.0.1:3000",
        subscribe_urls: list[str] | None = None,
        max_timeout_ms: int = 60000,
        mqtt_topic_prefix: str = "bridge/downlink",
        mqtt_topic_prefixes: dict | None = None,
        mqtt_publisher: Callable[[str, str], bool] | None = None,
        ack_publish_url: str = "/ctrl/ack",
        ack_publisher: Callable[[str, dict], None] | None = None,
        # --- v3.0 新增 ---
        registry: DeviceRegistry | None = None,
        dedup: DedupCache | None = None,
        reconnect_interval_ms: int = 3000,
        reconnect_max_retries: int = 10,
        reconnect_backoff_multiplier: float = 2.0,
    ) -> None:
        self._server_url = server_url
        self._subscribe_urls = subscribe_urls or ["/ctrl/cmd"]
        self._max_timeout_ms = max_timeout_ms
        self._topic_prefix = mqtt_topic_prefix
        self._topic_prefixes = mqtt_topic_prefixes or {}
        self._publisher = mqtt_publisher
        self._ack_publish_url = ack_publish_url
        self._ack_publisher = ack_publisher  # 统一 VSOA Server 的 publish 函数
        self._registry = registry
        self._dedup = dedup
        self._reconnect_interval_ms = reconnect_interval_ms
        self._reconnect_max_retries = reconnect_max_retries
        self._reconnect_backoff_multiplier = reconnect_backoff_multiplier
        self._client: vsoa.Client | None = None
        self._running = False

    # ------------------------------------------------------------------
    # 连接
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        """首次连接 VSOA Server + 订阅。"""
        self._client = vsoa.Client()

        # 保存引用供 on_message 闭包使用
        max_timeout = self._max_timeout_ms
        topic_prefix = self._topic_prefix
        topic_prefixes = self._topic_prefixes
        ack_url = self._ack_publish_url
        ack_pub = self._ack_publisher
        publisher = self._publisher
        registry = self._registry
        dedup = self._dedup

        def _publish_ack(cmd: dict, error_code: int, trace_id: str) -> None:
            """通过统一 VSOA Server (3001) publish ACK 到 /ctrl/ack。"""
            if ack_pub is None:
                logger.warning("[PubSub] ACK publisher not configured, skipping ACK")
                return
            try:
                ack = build_ack(cmd, error_code, trace_id=trace_id)
                ack_pub(ack_url, ack)
                logger.debug("[PubSub] ACK sent: cmd_id=%s trace=%s err=%d",
                             cmd.get("command_id", "?"), trace_id, error_code)
            except Exception:
                logger.error("[PubSub] ACK publish failed:\n%s", traceback.format_exc())

        def on_message(cli: Any, url: str, payload: Any, quick: bool) -> None:
            """v3.0 命令处理流程（spec §3.3）。"""
            # ⓪ 生成 traceId
            trace_id = generate_trace_id()

            # 提取命令 dict
            cmd: dict[str, Any] = {}
            try:
                if payload and hasattr(payload, "param") and payload.param:
                    cmd = dict(payload.param)
                elif isinstance(payload, dict):
                    cmd = payload
            except Exception:
                cmd = {}

            cmd_id = cmd.get("command_id", "?")
            logger.debug("[PubSub] cmd_id=%s trace=%s received", cmd_id, trace_id)

            # ① Schema 校验（Pub/Sub 跳过 timeout 检查）
            is_valid, err_code = _cmd_validate(cmd, max_timeout, check_timeout=False)
            if not is_valid:
                logger.warning("[PubSub] cmd_id=%s trace=%s validation failed (code=%d)",
                               cmd_id, trace_id, err_code)
                _publish_ack(cmd, err_code, trace_id)
                return

            # ② 设备注册表检查（v3.0 新增）
            if registry is not None:
                device = registry.lookup(cmd.get("device_id", ""))
                if device is None:
                    logger.warning("[PubSub] cmd_id=%s trace=%s device not in registry: %s",
                                   cmd_id, trace_id, cmd.get("device_id"))
                    _publish_ack(cmd, ERR_DEVICE_NOT_FOUND.code, trace_id)
                    return

            # ③ 幂等去重（v3.0 新增）
            if dedup is not None:
                if not dedup.check_and_mark(cmd.get("command_id", "")):
                    logger.warning("[PubSub] cmd_id=%s trace=%s duplicate command",
                                   cmd_id, trace_id)
                    _publish_ack(cmd, ERR_CMD_DUPLICATE_ID.code, trace_id)
                    return

            # ④ 构造 MQTT 消息
            try:
                topic, payload_str = build_mqtt_message(
                    cmd, topic_prefix, topic_prefixes, trace_id=trace_id,
                )
            except Exception:
                logger.error("[PubSub] cmd_id=%s trace=%s build_mqtt_message error:\n%s",
                             cmd_id, trace_id, traceback.format_exc())
                _publish_ack(cmd, SUCCESS, trace_id)
                return

            # ⑤ 发布 MQTT
            publish_ok = False
            if publisher:
                try:
                    publish_ok = publisher(topic, payload_str)
                except Exception:
                    logger.error("[PubSub] cmd_id=%s trace=%s MQTT publish error:\n%s",
                                 cmd_id, trace_id, traceback.format_exc())

            logger.info("[PubSub] cmd_id=%s trace=%s device=%s/%s action=%s -> MQTT %s",
                        cmd_id, trace_id, cmd.get("device_type"), cmd.get("device_id"),
                        cmd.get("action"), "OK" if publish_ok else "FAIL")

            # ⑤ 发送 ACK
            ack_code = SUCCESS if publish_ok else ERR_MQTT_PUBLISH_FAILED.code
            _publish_ack(cmd, ack_code, trace_id)

        self._client.onmessage = on_message

        # 连接 VSOA Client（订阅 /ctrl/cmd）
        return self._do_connect_and_subscribe()

    def _do_connect_and_subscribe(self) -> bool:
        """执行实际连接 + 订阅。ACK 通过注入的 ack_publisher 发布（统一 VSOA Server 3001）。"""
        if not self._client:
            self._client = vsoa.Client()
            self._client.onmessage = None  # will be set by connect()

        err = self._client.connect(self._server_url)
        if err != 0:
            logger.error("[PubSub] connect failed: err=%d", err)
            return False

        for url in self._subscribe_urls:
            if self._client.subscribe(url):
                logger.info("[PubSub] subscribed: %s", url)
            else:
                logger.error("[PubSub] subscribe failed: %s", url)
                return False

        self._running = True
        logger.info("[PubSub] ready: server=%s subs=%s ack_url=%s",
                     self._server_url, self._subscribe_urls, self._ack_publish_url)
        return True

    # ------------------------------------------------------------------
    # 事件循环 + 自动重连（v3.0 新增）
    # ------------------------------------------------------------------

    def run_forever(self) -> None:
        """进入 VSOA 事件循环，断开时自动重连。"""
        if not self._client:
            logger.error("[PubSub] client not initialized")
            return
        logger.info("[PubSub] entering event loop (auto-reconnect enabled)...")
        while self._running:
            try:
                self._client.run()
            except vsoa.Error as e:
                if not self._running:
                    break
                logger.warning("[PubSub] VSOA disconnected (err=%d), reconnecting...", e.code)
            except Exception:
                if not self._running:
                    break
                logger.error("[PubSub] unexpected error:\n%s", traceback.format_exc())

            if not self._running:
                break

            # 尝试重连
            if not self._reconnect_with_backoff():
                logger.error("[PubSub] Reconnect failed after max retries, exiting")
                break

        self._running = False
        logger.info("[PubSub] event loop exited")

    def _reconnect_with_backoff(self) -> bool:
        """指数退避重连。成功返回 True，耗尽返回 False。"""
        for attempt in range(self._reconnect_max_retries):
            interval_s = (
                self._reconnect_interval_ms
                * (self._reconnect_backoff_multiplier ** attempt)
                / 1000.0
            )
            logger.info("[PubSub] Reconnect attempt %d/%d in %.1fs...",
                        attempt + 1, self._reconnect_max_retries, interval_s)
            time.sleep(interval_s)

            if self._do_connect_and_subscribe():
                logger.info("[PubSub] Reconnected (attempt %d)", attempt + 1)
                return True

            logger.warning("[PubSub] Reconnect attempt %d/%d failed",
                          attempt + 1, self._reconnect_max_retries)
        return False

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def stop(self) -> None:
        self._running = False
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None
        logger.info("[PubSub] disconnected")

    @property
    def is_running(self) -> bool:
        return self._running
