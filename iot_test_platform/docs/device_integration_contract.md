# 设备统一接入约定

## 遥测上报

设备业务字段统一放在 `data` 中，公共字段不得绑定具体传感器类型。

LoRa 环境设备的标准业务字段为 `temperature`、`humidity`、`soil_moisture` 和 `rainfall`。

WiFi 摄像头消息可发布到 `bridge/uplink/{hub_id}/camera/frame`。平台兼容 `image_b64`、`image_base64`、`image_url`、`image` 和 `photo`；Base64 帧建议同时提供 `format`、`width`、`height`、`bytes`、`fps` 与 `hub_ip`。当前 EoRa Hub 使用 `image_b64` 传输 JPEG 静态帧。平台只解码并展示收到的静态图片，不直接连接摄像头，也不处理视频流。

ZigBee 环境设备的标准业务字段为 `temperature`、`humidity`、`voltage`、`smoke`、`presence` 和 `rainfall`。其中 `presence` 为布尔值，`true` 表示检测到人员经过；`smoke` 可使用布尔告警值或数值浓度。平台同时兼容 `pir`、`motion`、`infrared`、`battery_voltage` 等常见别名。

ZigBee 下行控制统一使用以下业务参数：蜂鸣器开启/关闭为 `{"buzzer":"on|off","trigger":"presence"}`；LED 闪烁/停止为 `{"led":"blink|off","trigger":"temperature_or_smoke"}`。平台只负责将用户确认后的命令交给真实桥接链路，执行结果必须以设备 ACK 为准。

## 场景联动约定

场景条件使用规范字段 `temperature`、`humidity`、`soil_moisture`、`precipitation`、`illuminance`、`smoke`、`pir` 和 `voltage`。适配器负责将设备别名归一化后写入 `DeviceInfo.raw`，场景引擎与平台展示使用同一份值。

人体红外 `pir` 默认采用上升沿触发，其他指标默认采用持续电平触发并受冷却期限制。场景动作复用标准下行命令和 MQTT topic，不绕过桥接设备校验。动作持续时间结束后，桥接引擎自动将 `on`、`blink`、`start` 分别映射为 `off`、`off`、`stop`。

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

LoRa 环境传感器与 EoRa WiFi 传感器的 LED、电机直接控制使用同一 MQTT 合约。设备是否在线不影响命令发布：

```text
Topic: bridge/downlink/generic/eora_s3_400tb_001/cmd
```

```json
{"cmd":"led","value":"on"}
```

`cmd` 支持 `led`、`motor`，`value` 支持 `on`、`off`。平台只要求 MQTT Broker 已连接；Broker 接收命令后记录为已发送，不能据此声明设备已经执行。设备最终状态仍以设备 ACK 或后续状态上报为准。

其他设备控制继续调用真实桥接项目的 VSOA RPC `/bridge/send_command`：

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
