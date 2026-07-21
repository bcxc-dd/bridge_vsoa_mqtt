# 设备统一接入约定

## 遥测上报

设备业务字段统一放在 `data` 中，公共字段不得绑定具体传感器类型。

```json
{
  "protocol": "lora",
  "device_id": "env-001",
  "timestamp": 1784000000,
  "data": {
    "temperature": 26.5,
    "humidity": 61,
    "battery": 82,
    "smoke": false
  }
}
```

必须提供 `device_id`；建议提供 `protocol` 和 `timestamp`。平台允许增加新的 `data` 字段，未知字段原样保存并在设备详情中展示。

## 设备档案

设备档案包含设备编号、名称、所属项目、设备类型、连接来源、能力列表和告警阈值。能力类型使用：

- `telemetry`：只读遥测，例如温度、湿度、电量。
- `switch`：开关控制，例如照明、继电器。
- `setpoint`：可设定数值，例如目标温度。
- `alarm`：告警状态，例如烟雾或故障。

## 下行控制

平台先调用真实桥接项目的 VSOA RPC `/bridge/send_command`：

```json
{
  "command_id": "全局唯一命令号",
  "trace_id": "全局唯一追踪号",
  "device_type": "zigbee",
  "device_id": "light-001",
  "action": "set",
  "params": { "state": true },
  "timestamp": "ISO-8601 时间"
}
```

桥接项目校验命令后，按其现有代码生成 `bridge/downlink/{device_type}/{device_id}/{action}` MQTT Topic。每条控制命令必须由用户确认；MQTT 发布成功仍不代表设备执行成功。

## ACK 与状态

设备或桥接服务应向 VSOA `/ctrl/ack` 返回相同 `trace_id`：

```json
{
  "trace_id": "与控制命令一致",
  "device_id": "light-001",
  "success": true,
  "message": "command applied"
}
```

平台根据 ACK 将命令标记为 `acknowledged` 或 `failed`；未收到 ACK 时保持 `sent`，不得显示为执行成功。

## 在线与丢包判断

设备 120 秒内有消息视为在线。可靠丢包率需要发送端提供单调递增序号；没有序号时，平台只统计应用层已观察到的发送、接收、重复和转换数量。
