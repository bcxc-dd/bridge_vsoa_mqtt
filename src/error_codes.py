"""
MQTT ↔ VSOA 桥接组件 — 统一错误码常量定义

对应 spec.md §8，码段分配：
  - 1xxx: 上行错误（1001-1003）
  - 2xxx: 下行错误（2001-2304）
  - 9xxx: 共用（预留）

使用方式:
    from src.error_codes import SUCCESS, ERR_CMD_MISSING_FIELD
    ERR_CMD_MISSING_FIELD.code    # 2002
    ERR_CMD_MISSING_FIELD.name    # "ERR_CMD_MISSING_FIELD"
    ERR_CMD_MISSING_FIELD.message # "缺少必填字段"
"""

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# 成功
# ---------------------------------------------------------------------------
SUCCESS = 0


# ---------------------------------------------------------------------------
# ErrorCode 数据类
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ErrorCode:
    """不可变错误码对象。"""
    code: int
    name: str
    message: str

    def __repr__(self) -> str:
        return f"ErrorCode({self.code}, '{self.name}', '{self.message}')"


# ---------------------------------------------------------------------------
# 1xxx — 上行错误
# ---------------------------------------------------------------------------
ERR_UP_DEVICE_NOT_FOUND = ErrorCode(1001, "ERR_UP_DEVICE_NOT_FOUND", "查询的 device_id 在注册表中不存在")
ERR_UP_INVALID_URL      = ErrorCode(1002, "ERR_UP_INVALID_URL",      "VSOA URL 格式不正确")
ERR_UP_REGISTRY_FULL    = ErrorCode(1003, "ERR_UP_REGISTRY_FULL",    "设备注册表已满，无法注册新设备")

# ---------------------------------------------------------------------------
# 2xxx — 命令校验错误
# ---------------------------------------------------------------------------
ERR_CMD_INVALID_JSON       = ErrorCode(2001, "ERR_CMD_INVALID_JSON",       "命令 payload 不是合法 JSON")
ERR_CMD_MISSING_FIELD      = ErrorCode(2002, "ERR_CMD_MISSING_FIELD",      "缺少必填字段")
ERR_CMD_UNKNOWN_DEVICE_TYPE = ErrorCode(2003, "ERR_CMD_UNKNOWN_DEVICE_TYPE", "device_type 不在枚举值中（非 lora/zigbee）")
ERR_CMD_UNKNOWN_ACTION     = ErrorCode(2004, "ERR_CMD_UNKNOWN_ACTION",     "action 不在枚举值中（非 set/get/reset/config）")
ERR_CMD_TIMEOUT_EXCEEDED   = ErrorCode(2005, "ERR_CMD_TIMEOUT_EXCEEDED",   "timeout_ms 超过 max_timeout_ms 限制")
ERR_CMD_DUPLICATE_ID       = ErrorCode(2006, "ERR_CMD_DUPLICATE_ID",       "command_id 重复")  # v3.0 实现

# ---------------------------------------------------------------------------
# 21xx — MQTT 发布错误
# ---------------------------------------------------------------------------
ERR_MQTT_NOT_CONNECTED     = ErrorCode(2101, "ERR_MQTT_NOT_CONNECTED",     "MQTT Broker 未连接")
ERR_MQTT_PUBLISH_FAILED    = ErrorCode(2102, "ERR_MQTT_PUBLISH_FAILED",    "MQTT publish 调用失败")
ERR_MQTT_QOS_NOT_SUPPORTED = ErrorCode(2103, "ERR_MQTT_QOS_NOT_SUPPORTED", "请求的 QoS 级别不被 Broker 支持")

# ---------------------------------------------------------------------------
# 22xx — MQTT 发布结果 / 设备错误（2201 第2周实现，2203 v3.0 实现，2202/2204 预留）
# ---------------------------------------------------------------------------
ERR_MQTT_PUBLISH_TIMEOUT  = ErrorCode(2201, "ERR_MQTT_PUBLISH_TIMEOUT",  "MQTT publish 等待 Broker PUBACK 超时")
ERR_MQTT_PUBLISH_REJECTED = ErrorCode(2202, "ERR_MQTT_PUBLISH_REJECTED", "MQTT publish 被 Broker 拒绝")  # 预留
ERR_DEVICE_NOT_FOUND     = ErrorCode(2203, "ERR_DEVICE_NOT_FOUND",      "目标设备不在注册表中")           # v3.0 实现
ERR_DEVICE_OFFLINE       = ErrorCode(2204, "ERR_DEVICE_OFFLINE",        "目标设备当前离线")               # 预留（第3周）

# ---------------------------------------------------------------------------
# 23xx — 内部错误
# ---------------------------------------------------------------------------
ERR_INTERNAL           = ErrorCode(2301, "ERR_INTERNAL",           "未分类的内部错误")
ERR_CONFIG_INVALID     = ErrorCode(2302, "ERR_CONFIG_INVALID",     "配置文件校验失败")
ERR_VSOA_DISCONNECTED  = ErrorCode(2303, "ERR_VSOA_DISCONNECTED",  "VSOA 连接断开")
ERR_QUEUE_FULL         = ErrorCode(2304, "ERR_QUEUE_FULL",         "待处理命令队列已满")


# ---------------------------------------------------------------------------
# 查找表（code → ErrorCode）
# ---------------------------------------------------------------------------
_CODE_TABLE: dict[int, ErrorCode] = {}
for _v in list(globals().values()):
    if isinstance(_v, ErrorCode):
        _CODE_TABLE[_v.code] = _v


def lookup(code: int) -> ErrorCode | None:
    """通过错误码数字查找对应的 ErrorCode 对象。

    Returns:
        ErrorCode 对象，若未找到则返回 None。
    """
    return _CODE_TABLE.get(code)


# ---------------------------------------------------------------------------
# 便捷工具
# ---------------------------------------------------------------------------
def is_success(error_code: int) -> bool:
    """判断错误码是否表示成功。"""
    return error_code == SUCCESS
