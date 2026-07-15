"""
VSOA RPC Server — bridge 内嵌，暴露真正的 VSOA RPC 端点

业务层通过 client.fetch("/bridge/send_command", payload=cmd) 同步调用，
拿到 (header, payload, status)，payload.param 即回执。

RPC reply 使用官方公开 API: cli.reply(request.seqno, payload)
详见 python_overview.pdf 第 25 页。

v3.0 变更:
  - 集成 DeviceRegistry（设备存在性检查 → 2203）
  - 集成 DedupCache（幂等去重 → 2006）
  - traceId 生成与注入（ACK / MQTT payload / 日志）
  - _publish_with_retry() 替代 _publish_with_timeout()（自动重试）
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
    ERR_INTERNAL,
    ERR_MQTT_NOT_CONNECTED,
    ERR_MQTT_PUBLISH_FAILED,
    ERR_MQTT_PUBLISH_TIMEOUT,
)

from src.device_registry import DeviceRegistry

try:
    from .command import validate as _cmd_validate, build_mqtt_message, build_ack
    from .dedup import DedupCache
except ImportError:
    from command import validate as _cmd_validate, build_mqtt_message, build_ack  # type: ignore[no-redef]
    from dedup import DedupCache  # type: ignore[no-redef]

logger = logging.getLogger("bridge.rpc")


# ---------------------------------------------------------------------------
# RpcServer
# ---------------------------------------------------------------------------
class RpcServer:
    """VSOA RPC 服务端。

    v3.0 handler 内部流程 (spec §3.3):
      ⓪ 生成 traceId
      ① command.validate() 校验
      ② registry.lookup() 设备存在性检查
      ③ dedup.check_and_mark() 幂等去重
      ④ command.build_mqtt_message() 构造 MQTT 消息
      ⑤ _publish_with_retry() 发布（带重试）
      ⑥ cli.reply(seqno, payload) 发回执
    """

    def __init__(
        self,
        bind_host: str = "127.0.0.1",
        port: int = 3001,
        endpoint: str = "/bridge/send_command",
        max_timeout_ms: int = 60000,
        mqtt_topic_prefix: str = "bridge/downlink",
        mqtt_topic_prefixes: dict | None = None,
        mqtt_publisher: Callable[[str, str, int], bool] | None = None,
        mqtt_is_connected: Callable[[], bool] | None = None,
        # --- v3.0 新增 ---
        registry: DeviceRegistry | None = None,
        dedup: DedupCache | None = None,
        retry_max_retries: int = 3,
        retry_backoff_base_ms: int = 500,
        # --- 外部 server（合并版） ---
        server: vsoa.Server | None = None,
    ) -> None:
        self._bind_host = bind_host
        self._port = port
        self._endpoint = endpoint
        self._max_timeout_ms = max_timeout_ms
        self._topic_prefix = mqtt_topic_prefix
        self._topic_prefixes = mqtt_topic_prefixes or {}
        self._publisher = mqtt_publisher
        self._is_connected = mqtt_is_connected
        self._registry = registry
        self._dedup = dedup
        self._retry_max_retries = retry_max_retries
        self._retry_backoff_base_ms = retry_backoff_base_ms
        self._external_server = server
        self._server: vsoa.Server | None = None
        self._thread: threading.Thread | None = None
        self._running = False

    def start(self) -> None:
        """启动 VSOA RPC Server（注册 /bridge/send_command 端点）。

        如果构造时传入了外部 server（server= 参数），则直接在其上注册 handler，
        不创建线程、不调用 run()——server 生命周期由调用方管理。

        否则按独立模式创建自己的 server 并在后台线程运行（向后兼容）。
        """
        if self._external_server is not None:
            self._server = self._external_server
        else:
            self._server = vsoa.Server({"name": "bridge-downlink-rpc"})
        _self = self

        @self._server.command(self._endpoint)
        def handle_command(cli: Any, req: Any, payload: Any) -> None:
            """VSOA RPC handler。⓪ 生成 traceId 后走 §3.3 流程。"""
            # ⓪ 生成 traceId（第一步，后续所有分支都用它）
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
            logger.debug("[RPC] cmd_id=%s trace=%s received", cmd_id, trace_id)

            # ① Schema 校验（纯函数）
            is_valid, err_code = _cmd_validate(
                cmd, _self._max_timeout_ms, check_timeout=True,
            )
            if not is_valid:
                logger.warning("[RPC] cmd_id=%s trace=%s validation failed (code=%d)",
                               cmd_id, trace_id, err_code)
                _send_reply(cli, req, build_ack(cmd, err_code, trace_id=trace_id))
                return

            # ② 设备注册表检查（v3.0 新增）
            if _self._registry is not None:
                device = _self._registry.lookup(cmd.get("device_id", ""))
                if device is None:
                    logger.warning("[RPC] cmd_id=%s trace=%s device not in registry: %s",
                                   cmd_id, trace_id, cmd.get("device_id"))
                    _send_reply(cli, req, build_ack(
                        cmd, ERR_DEVICE_NOT_FOUND.code, trace_id=trace_id,
                    ))
                    return

            # ③ 幂等去重（v3.0 新增）
            if _self._dedup is not None:
                if not _self._dedup.check_and_mark(cmd.get("command_id", "")):
                    logger.warning("[RPC] cmd_id=%s trace=%s duplicate command",
                                   cmd_id, trace_id)
                    _send_reply(cli, req, build_ack(
                        cmd, ERR_CMD_DUPLICATE_ID.code, trace_id=trace_id,
                    ))
                    return

            # ④ 构造 MQTT 消息
            try:
                topic, payload_str = build_mqtt_message(
                    cmd, _self._topic_prefix, _self._topic_prefixes,
                    trace_id=trace_id,
                )
            except Exception:
                logger.error("[RPC] cmd_id=%s trace=%s build_mqtt_message 异常:\n%s",
                             cmd_id, trace_id, traceback.format_exc())
                _send_reply(cli, req, build_ack(cmd, ERR_INTERNAL.code, trace_id=trace_id))
                return

            # 检查 MQTT 连接状态
            if _self._is_connected and not _self._is_connected():
                logger.error("[RPC] cmd_id=%s trace=%s MQTT not connected", cmd_id, trace_id)
                _send_reply(cli, req, build_ack(
                    cmd, ERR_MQTT_NOT_CONNECTED.code, trace_id=trace_id,
                ))
                return

            pub = _self._publisher
            if not pub:
                logger.error("[RPC] cmd_id=%s trace=%s no publisher configured", cmd_id, trace_id)
                _send_reply(cli, req, build_ack(cmd, ERR_INTERNAL.code, trace_id=trace_id))
                return

            # ⑤ 发布 MQTT（带重试，v3.0）
            timeout_ms = cmd.get("timeout_ms", _self._max_timeout_ms)
            success, pub_code, attempts = _publish_with_retry(
                pub, topic, payload_str,
                timeout_ms=timeout_ms,
                max_retries=_self._retry_max_retries,
                retry_backoff_base_ms=_self._retry_backoff_base_ms,
            )

            if success:
                logger.info("[RPC] cmd_id=%s trace=%s device=%s/%s action=%s -> MQTT OK (attempts=%d)",
                            cmd_id, trace_id, cmd.get("device_type"), cmd.get("device_id"),
                            cmd.get("action"), attempts)
                _send_reply(cli, req, build_ack(cmd, SUCCESS, trace_id=trace_id))
            else:
                logger.error("[RPC] cmd_id=%s trace=%s MQTT FAIL (code=%d, attempts=%d)",
                             cmd_id, trace_id, pub_code, attempts)
                _send_reply(cli, req, build_ack(cmd, pub_code, trace_id=trace_id))

        # 如果是自己的 server，启动后台线程运行；否则 server 由外部管理
        if self._external_server is None:
            def run_server():
                logger.info("[RPC] Server starting on %s:%d endpoint=%s", self._bind_host, self._port, self._endpoint)
                self._running = True
                try:
                    self._server.run(self._bind_host, self._port)
                except Exception:
                    logger.error("[RPC] Server exception:\n%s", traceback.format_exc())
                finally:
                    self._running = False
                    logger.info("[RPC] Server stopped")

            self._thread = threading.Thread(target=run_server, daemon=True, name="rpc-server")
            self._thread.start()

            waited = 0
            while not self._running and waited < 50:
                time.sleep(0.1)
                waited += 1
        else:
            self._running = True
            logger.info("[RPC] /bridge/send_command endpoint registered on shared server")

    def stop(self) -> None:
        self._running = False
        if self._external_server is None:
            logger.info("[RPC] Server stop requested")
        else:
            logger.info("[RPC] RPC stop requested (server lifecycle external)")

    @property
    def is_running(self) -> bool:
        return self._running


# ---------------------------------------------------------------------------
# _publish_with_retry（v3.0 新增，替代 _publish_with_timeout）
# ---------------------------------------------------------------------------
def _publish_with_retry(
    publisher: Callable[[str, str, int], bool],
    topic: str,
    payload_str: str,
    timeout_ms: int = 10000,
    qos: int = 1,
    max_retries: int = 3,
    retry_backoff_base_ms: int = 500,
) -> tuple[bool, int, int]:
    """MQTT publish 带自动重试（指数退避）。

    每次尝试在独立线程中执行（通过 Thread+join 实现 per-attempt 超时）。
    重试间隔: backoff_base_ms * 2^attempt（指数退避）。

    Args:
        publisher: mqtt_handler.publish(topic, payload, qos) 可调用对象。
        topic: MQTT topic。
        payload_str: MQTT payload JSON 字符串。
        timeout_ms: 每次尝试的超时阈值（毫秒）。
        qos: MQTT QoS 级别（默认 1）。
        max_retries: 最大重试次数（含首次共 max_retries+1 次）。
        retry_backoff_base_ms: 退避基值（毫秒）。

    Returns:
        (success, error_code, attempts)
        - success=True  → error_code=0
        - success=False → error_code 为最后一次失败的错误码
        - attempts 为实际尝试次数（含首次）
    """
    last_error = ERR_MQTT_PUBLISH_FAILED.code

    for attempt in range(max_retries + 1):
        if attempt > 0:
            backoff = retry_backoff_base_ms * (2 ** (attempt - 1))
            time.sleep(backoff / 1000.0)

        result, err_code = _publish_single_attempt(
            publisher, topic, payload_str, qos, timeout_ms,
        )

        if result:
            return (True, SUCCESS, attempt + 1)

        last_error = err_code
        logger.debug("[RPC] MQTT publish attempt %d/%d failed (code=%d)",
                      attempt + 1, max_retries + 1, err_code)

    return (False, last_error, max_retries + 1)


def _publish_single_attempt(
    publisher: Callable[[str, str, int], bool],
    topic: str,
    payload_str: str,
    qos: int,
    timeout_ms: int,
) -> tuple[bool, int]:
    """单次 MQTT publish 尝试（带超时控制）。

    使用 threading.Thread + join(timeout) 实现 per-attempt 超时。
    超时后线程继续运行（MQTT 消息可能仍被发出），符合"不撤回"策略。
    """
    result: list[bool | None] = [None]
    exception: list[Exception | None] = [None]

    def _do_publish() -> None:
        try:
            result[0] = publisher(topic, payload_str, qos)
        except Exception as exc:
            exception[0] = exc
            result[0] = False

    t = threading.Thread(target=_do_publish, daemon=True, name="rpc-publish")
    t.start()
    t.join(timeout_ms / 1000.0)

    if t.is_alive():
        # 超时：MQTT 线程仍在运行，消息可能仍被发出
        return (False, ERR_MQTT_PUBLISH_TIMEOUT.code)

    if exception[0] is not None:
        logger.error("[RPC] MQTT publish 异常: %s", exception[0])
        return (False, ERR_MQTT_PUBLISH_FAILED.code)

    if result[0]:
        return (True, SUCCESS)
    else:
        return (False, ERR_MQTT_PUBLISH_FAILED.code)


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def _send_reply(cli: Any, req: Any, reply: dict) -> None:
    """发送 VSOA RPC reply（官方公开 API: cli.reply）。"""
    try:
        cli.reply(req.seqno, vsoa.Payload(param=reply))
    except Exception:
        logger.exception("[RPC] Failed to send reply")
