"""
命令校验与转换模块

RPC 和 Pub/Sub 两通道的共用核心。所有函数均为纯函数：
  - 不持有也不修改任何共享状态
  - 不修改入参
  - 不访问全局变量
  - 相同的输入永远产生相同的输出

对应 spec.md §4 (命令 Schema) / §5 (MQTT Topic)。
"""

from __future__ import annotations

import base64
import json
from typing import Any

from src.error_codes import (
    SUCCESS,
    ERR_CMD_INVALID_JSON,
    ERR_CMD_MISSING_FIELD,
    ERR_CMD_UNKNOWN_DEVICE_TYPE,
    ERR_CMD_UNKNOWN_ACTION,
    ERR_CMD_TIMEOUT_EXCEEDED,
    ERR_INTERNAL,
    lookup,
)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
VALID_DEVICE_TYPES = frozenset({"lora", "zigbee"})
VALID_ACTIONS = frozenset({"set", "get", "reset", "config"})

# 必填字段列表（timeout_ms 除外，其为选填）
_REQUIRED_FIELDS = ("command_id", "device_type", "device_id", "action", "params")

# 默认超时（ms），当命令未指定 timeout_ms 且调用方也未传入 max_timeout 时使用
_DEFAULT_MAX_TIMEOUT_MS = 60000


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------
def validate(cmd: Any, max_timeout_ms: int = _DEFAULT_MAX_TIMEOUT_MS,
             check_timeout: bool = True) -> tuple[bool, int]:
    """校验命令 payload 是否符合 schema。

    Args:
        cmd: 命令 dict（或可被当作 dict 的对象）。
        max_timeout_ms: 允许的最大 timeout_ms（取自 config）。
        check_timeout: 是否校验 timeout_ms 上限。Pub/Sub 通道传 False
                       （异步语义，timeout_ms 无意义）。

    Returns:
        (is_valid, error_code)。is_valid=True 时 error_code=SUCCESS(0)。
    """
    # --- 2001: 非法 JSON / 非 dict ---
    if cmd is None or not isinstance(cmd, dict):
        return (False, ERR_CMD_INVALID_JSON.code)

    # --- 2002: 缺必填字段 ---
    for field in _REQUIRED_FIELDS:
        if field not in cmd or cmd[field] is None:
            return (False, ERR_CMD_MISSING_FIELD.code)

    # --- 2003: 非法 device_type ---
    device_type = cmd.get("device_type")
    if not isinstance(device_type, str) or device_type not in VALID_DEVICE_TYPES:
        return (False, ERR_CMD_UNKNOWN_DEVICE_TYPE.code)

    # --- 2004: 非法 action ---
    action = cmd.get("action")
    if not isinstance(action, str) or action not in VALID_ACTIONS:
        return (False, ERR_CMD_UNKNOWN_ACTION.code)

    # --- 2005: timeout_ms 超限（仅 check_timeout=True 时校验） ---
    if check_timeout:
        timeout_ms = cmd.get("timeout_ms")
        if timeout_ms is not None:
            if not isinstance(timeout_ms, (int, float)) or timeout_ms < 0:
                return (False, ERR_CMD_TIMEOUT_EXCEEDED.code)
            if timeout_ms > max_timeout_ms:
                return (False, ERR_CMD_TIMEOUT_EXCEEDED.code)

    # --- 全部通过 ---
    return (True, SUCCESS)


# ---------------------------------------------------------------------------
# build_mqtt_message
# ---------------------------------------------------------------------------
def build_mqtt_message(cmd: dict, topic_prefix: str,
                       topic_prefixes: dict | None = None,
                       trace_id: str = "",
                       chirpstack: dict | None = None,
                       device_info: Any = None) -> tuple[str, str]:
    """将合法命令转换为 MQTT (topic, payload) 元组。

    调用方必须确保 cmd 已通过 validate() 校验。

    Args:
        cmd: 已校验的命令 dict。
        topic_prefix: 默认 MQTT topic 前缀（如 "bridge/downlink"）。
        topic_prefixes: per-device-type 前缀覆盖 dict（可选）。
                        key=device_type，value=前缀。无覆盖时回退 topic_prefix。
        trace_id: 链路追踪 ID（v3.0 新增）。空字符串表示不注入。
        chirpstack: ChirpStack 下行配置 dict（方案 B）。
                    格式: {"enabled": true, "confirmed": true, "fPort": 1, "application_id": ""}
        device_info: DeviceInfo 对象（方案 B，含 dev_eui / app_id）。

    Returns:
        (topic, payload) 元组，payload 为 JSON 字符串（ChirpStack 模式为含 Base64 data 的 JSON）。

    >>> build_mqtt_message(
    ...     {"command_id":"c1","device_type":"lora","device_id":"n1",
    ...      "action":"set","params":{"led":"on"},"timestamp":"2026-07-06T00:00:00Z"},
    ...     "bridge/downlink")
    ('bridge/downlink/lora/n1/set', '...')

    >>> build_mqtt_message(
    ...     {"command_id":"c2","device_type":"lora","device_id":"n1",
    ...      "action":"set","params":{"led":"on"},"timestamp":"2026-07-06T00:00:00Z"},
    ...     "bridge/downlink", {"lora": "lora/cmd"})
    ('lora/cmd/n1/set', '...')

    # ChirpStack 模式（方案 B）
    >>> build_mqtt_message(
    ...     {"command_id":"c3","device_type":"lora","device_id":"n1",
    ...      "action":"set","params":{"led":"on"}},
    ...     "bridge/downlink",
    ...     chirpstack={"enabled": True, "confirmed": True, "fPort": 1, "application_id": ""},
    ...     device_info=type('D', (), {"dev_eui": "70B3D5E500000001", "app_id": "my-app"})())
    ('application/my-app/device/70B3D5E500000001/command/down', '{"devEui":"70B3D5E500000001","confirmed":true,"fPort":1,"data":"eyJsZWQiOiAib24ifQ==","command_id":"c3","trace_id":""}')
    """
    device_type = cmd["device_type"]
    device_id = cmd["device_id"]
    action = cmd["action"]

    # --- 方案 B: ChirpStack 下行模式 ---
    if _use_chirpstack_mode(chirpstack, device_info):
        return _build_chirpstack_message(cmd, device_info, chirpstack, trace_id)

    # --- 默认模式 ---
    # 构造 MQTT topic
    if topic_prefixes and device_type in topic_prefixes:
        # per-device-type 覆盖：前缀已隐含 device_type，不重复
        topic = f"{topic_prefixes[device_type]}/{device_id}/{action}"
    else:
        # 默认格式：包含 device_type 段
        topic = f"{topic_prefix}/{device_type}/{device_id}/{action}"

    # 构造 MQTT payload（不含 device_type/device_id，它们在 topic 中已体现）
    payload_obj: dict[str, Any] = {
        "command_id": cmd["command_id"],
        "action": action,
        "params": cmd["params"],
    }
    if "timestamp" in cmd:
        payload_obj["timestamp"] = cmd["timestamp"]
    else:
        from datetime import datetime, timezone
        payload_obj["timestamp"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # v3.0: 注入 traceId（非空时）
    if trace_id:
        payload_obj["trace_id"] = trace_id

    payload = json.dumps(payload_obj, ensure_ascii=False)
    return (topic, payload)


# ---------------------------------------------------------------------------
# build_ack
# ---------------------------------------------------------------------------
def build_ack(cmd: dict, error_code: int, ack_level: str = "bridge",
              trace_id: str = "") -> dict:
    """构造 ACK 回执 payload（RPC 和 Pub/Sub 两通道共用）。

    Args:
        cmd: 原始命令 dict（用于提取 command_id / device_type / device_id）。
        error_code: 结果错误码（0=SUCCESS，非 0=见 error_codes）。
        ack_level: "bridge"（MQTT 发布结果）或 "device"（设备执行结果，预留）。
        trace_id: 链路追踪 ID（v3.0 新增）。空字符串表示不注入。

    Returns:
        ACK payload dict，格式见 spec §7.3。

    >>> build_ack({"command_id":"c1","device_type":"lora","device_id":"n1"}, 0)
    {'command_id': 'c1', 'error_code': 0, 'error_msg': 'ok', 'device_type': 'lora', 'device_id': 'n1', 'ack_level': 'bridge', 'trace_id': ''}
    """
    # 查找错误码对应的可读消息
    if error_code == SUCCESS:
        error_msg = "ok"
    else:
        err = lookup(error_code)
        error_msg = err.message if err else f"unknown error ({error_code})"

    ack: dict[str, Any] = {
        "command_id":  cmd.get("command_id", ""),
        "error_code":  error_code,
        "error_msg":   error_msg,
        "device_type": cmd.get("device_type", ""),
        "device_id":   cmd.get("device_id", ""),
        "ack_level":   ack_level,
    }
    # v3.0: 注入 traceId（包含空字符串，始终有此字段以便下游解析）
    ack["trace_id"] = trace_id

    return ack


# ---------------------------------------------------------------------------
# ChirpStack 下行模式（方案 B）
# ---------------------------------------------------------------------------

def _use_chirpstack_mode(chirpstack: dict | None, device_info: Any) -> bool:
    """判断是否应使用 ChirpStack 下行格式。

    条件:
      1. chirpstack 配置 enabled=True
      2. device_info 存在且包含 dev_eui
    """
    if not chirpstack or not chirpstack.get("enabled"):
        return False
    if device_info is None:
        return False
    dev_eui = getattr(device_info, "dev_eui", None)
    return bool(dev_eui)


def _build_chirpstack_message(
    cmd: dict, device_info: Any, chirpstack: dict, trace_id: str,
) -> tuple[str, str]:
    """构造 ChirpStack 下行 MQTT 消息（方案 B）。

    Topic:   application/{app_id}/device/{dev_eui}/command/down
    Payload: {"devEui": "...", "confirmed": true, "fPort": 1,
               "data": "<base64 of params JSON>", "command_id": "...", "trace_id": "..."}
    """
    dev_eui = getattr(device_info, "dev_eui", "")
    app_id = (
        getattr(device_info, "app_id", "")
        or chirpstack.get("application_id", "")
    )

    # Topic: ChirpStack 下行格式
    topic = f"application/{app_id}/device/{dev_eui}/command/down"

    # Payload: params JSON → Base64
    params_json = json.dumps(cmd.get("params", {}), ensure_ascii=False)
    data_b64 = base64.b64encode(params_json.encode("utf-8")).decode("ascii")

    payload_obj: dict[str, Any] = {
        "devEui": dev_eui,
        "confirmed": chirpstack.get("confirmed", True),
        "fPort": chirpstack.get("fPort", 1),
        "data": data_b64,
        "command_id": cmd.get("command_id", ""),
        "device_id": cmd.get("device_id", ""),
    }

    # 注入 traceId
    if trace_id:
        payload_obj["trace_id"] = trace_id

    payload = json.dumps(payload_obj, ensure_ascii=False)
    return (topic, payload)
