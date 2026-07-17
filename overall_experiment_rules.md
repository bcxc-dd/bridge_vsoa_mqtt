# 统一规范与接口规则

版本：v1.0  
日期：2026-07-08  

---

## 1. 硬件设备与实验边界

### 1.1 硬件材料

| 设备 | 建议角色 | 推荐接入方式 |
|---|---|---|
| ESP32-C6 开发板（2.16 寸 AMOLED 触摸屏） | WiFi/BLE 节点、控制面板、数据显示终端 | 当前按通用 MQTT 设备接入，topic 使用 `bridge/uplink/generic/{device_id}/data` |
| ESP32-S3 + SX1268/SX1262 LoRa 开发板 | LoRa 传感节点或下行控制节点 | LoRa/LoRaWAN 网关转 MQTT，topic 使用 `bridge/uplink/lora/{device_id}/data` |
| WiFi/蓝牙/LoRa 无线射频模块与 LoRaWAN Hub | 设备侧射频链路与 LoRaWAN 汇聚 | Hub 或脚本负责转换为 UTF-8 JSON MQTT payload |
| 工业级 LoRaWAN 网关（470/868/915M，WiFi/以太网） | LoRaWAN 网关 | 优先发布到 `bridge/uplink/lora/{device_id}/data`，兼容 `lora/{device_id}/up` |
| CC2530 ZigBee 开发板/物联网无线控制套件 | Zigbee 传感或控制节点 | 网关或串口脚本发布到 `bridge/uplink/zigbee/{device_id}/data` |
| CC2530 核心板/2.4G 无线智能家居模块 | Zigbee/2.4G 节点 | 按 Zigbee 或通用 MQTT 接入 |
| ZigBee 3.0 转 RS485 无线透传模块（E180-DTU 系列） | RS485/Modbus 透传 | 必须先解析寄存器，再发布结构化 JSON |
| 青萍温湿度检测仪/WiFi 版 | 温湿度传感源 | 当前按通用 MQTT 设备接入 |
| 青萍空气检测仪 Lite（CO2/PM2.5/PM10/HomeKit） | 空气质量传感源 | 当前按通用 MQTT 设备接入，字段可扩展 `co2`、`pm25`、`pm10` |

### 1.2 系统角色

| 角色 | 负责内容 | 输出/输入边界 |
|---|---|---|
| 真实设备小组 | 设备采集、串口/网关解析、MQTT 发布 | 输出统一 topic 和 UTF-8 JSON payload |
| 上行 bridge | MQTT 订阅、payload 解析、设备表 upsert、VSOA 查询服务 | 输入 MQTT，输出 VSOA RPC URL 与发布 URL |
| 下行 bridge | VSOA 命令接收、命令校验、设备白名单、MQTT 控制下发 | 输入 VSOA RPC 或发布/订阅命令，输出 MQTT downlink |
| ADP/VSOA SDK 接入方 | 查询设备数据、订阅设备更新、发送控制命令 | 按本文 VSOA URL 和命令 schema 调用 |

### 1.3 总链路

```text
上行：
真实设备/网关/脚本
  -> MQTT Broker
  -> 上行 bridge adapter
  -> uplink_report.v2
  -> VSOA RPC URL 查询 / VSOA 发布 URL 通知

下行：
VSOA 应用/ADP/SDK
  -> 下行 bridge RPC 或发布/订阅 URL
  -> 命令校验 + 设备注册表 + 幂等去重 + traceId
  -> MQTT Broker
  -> 真实设备/网关/执行器
```

注意：现有文档中上行 bridge 与下行 bridge 的示例 VSOA RPC 端口都写为 `3001`。如果两者在同一台机器同时运行，必须在联调前明确端口分配，不能两个进程同时占用同一端口。

### 1.4 协议术语边界

本文严格区分 MQTT 与 VSOA 的术语，避免把两套协议混用。

| 协议 | 路由/资源标识 | 消息承载 | 调用模式 |
|---|---|---|---|
| MQTT | Topic | Payload，完全由业务自定义 | 仅发布/订阅 |
| VSOA | URL 路径 | 请求/发布消息体或返回体，由协议与 SDK 承载必要元数据 | RPC 调用与发布/订阅 |

规则：

| 场景 | 规范说法 |
|---|---|
| MQTT 路由 | 使用 `topic` |
| MQTT 业务数据 | 使用 `payload` |
| VSOA RPC 资源 | 使用 `URL`、`路径` 或 `RPC URL` |
| VSOA 发布订阅资源 | 使用 `发布 URL`、`订阅 URL` |
| VSOA 消息内容 | 使用 `消息体`、`请求体`、`返回体` 或 `参数体` |

除引用具体 SDK API 名称外，本文不把 `topic` 或 `payload` 作为 VSOA 资源/消息类型名。

---

## 2. 通用命名规则

### 2.1 设备 ID

所有组必须为每个物理设备确定一个稳定的逻辑 `device_id`。

| 类型 | 推荐 ID 来源 | 示例 |
|---|---|---|
| LoRaWAN 节点 | 优先 `deviceName`，没有则用 `devEUI` | `lora_env_01` |
| Zigbee 节点 | 优先 `friendly_name`，没有则用 `ieeeAddr` | `zigbee_env_01` |
| RS485/Modbus 仪表 | 资产编号、站点编号、从站地址组合 | `rs485_meter_01` |
| WiFi/BLE/通用设备 | 设备资产编号或 `bridge_{sensor_type}_{number}` | `bridge_air_01` |

约束：

| 项 | 要求 |
|---|---|
| 长度 | 建议小于 64 字节 |
| 字符 | 建议使用小写字母、数字、下划线 |
| 一致性 | topic 中 `{device_id}` 与 payload 中设备 ID 尽量一致 |
| 稳定性 | 同一物理设备重启、换网关、换脚本后 ID 不变 |
| 禁止事项 | 不要让同一物理设备在不同消息里使用多个 ID |

### 2.2 时间戳

| 项 | 规则 |
|---|---|
| 上行数据 | 使用 Unix epoch 毫秒，例如 `1783329001000` |
| Zigbee `last_seen` | 允许作为时间戳别名，但建议统一转为毫秒 |
| 下行命令 | 使用 ISO 8601 字符串，例如 `2026-07-08T10:00:00Z` |
| 设备无 RTC | 可以由网关或 bridge 使用接收时间补齐，但必须在字段说明中注明 |

### 2.3 traceId

下行 bridge 为每条控制命令生成 `trace_id`，格式：

```text
br-{8位hex随机}-{毫秒时间戳}
```

示例：

```text
br-a3f8c2d1-1720435200000
```

`trace_id` 必须进入 ACK 消息体、下行 MQTT payload 和 bridge 日志。其中 ACK 消息体由 VSOA RPC 返回或发布/订阅接口发送。设备如果支持回传，应在后续上行数据中带回同一个 `trace_id`，用于串联 VSOA -> bridge -> MQTT -> 设备 -> MQTT -> VSOA 全链路。

VSOA 协议自身的元数据由协议和 SDK 承载，不需要业务侧手动封装。这里保留 `trace_id` 是为了跨 MQTT、网关和真实设备排障，属于业务追踪字段。

---

## 3. schema.md：数据模型规范

### 3.1 统一上行模型：`uplink_report.v2`

上行 bridge 内部必须把各类 MQTT payload 转换为统一模型：

```json
{
  "device_id": "lora_env_01",
  "name": "lora_env_01",
  "type": "multi",
  "status": "online",
  "source": "lora",
  "adapter": "lora_adapter",
  "timestamp": 1783329001000,
  "temperature": 23.6,
  "humidity": 56.2,
  "pressure": 101.3,
  "unit": "celsius",
  "battery": 92,
  "signal": -57,
  "snr": 8.2
}
```

字段说明：

| 字段 | 类型 | 必填 | 说明 |
|---|---|:---:|---|
| `device_id` | string | 是 | 设备唯一 ID |
| `timestamp` | int64 | 是 | Unix epoch 毫秒 |
| `name` | string | 否 | 人类可读名称，默认可与 `device_id` 相同 |
| `type` | string | 建议 | `temperature`、`humidity`、`pressure`、`multi`、`status`、`air_quality` |
| `status` | string | 建议 | `online`、`offline`、`error`，默认 `online` |
| `source` | string | 转换生成 | `lora`、`zigbee`、`generic`、`mqtt` |
| `adapter` | string | 转换生成 | `lora_adapter`、`zigbee_adapter`、`generic_adapter` |
| `temperature` | number | 条件 | 温度，单位默认 `celsius` |
| `humidity` | number | 条件 | 湿度，单位默认 `%` |
| `pressure` | number | 条件 | 压力，可用 `kpa` 或 `hpa`，需注明 |
| `co2` | number | 条件 | CO2 浓度，单位 `ppm` |
| `pm25` | number | 条件 | PM2.5，单位 `ug/m3` |
| `pm10` | number | 条件 | PM10，单位 `ug/m3` |
| `unit` | string | 否 | 单位或主测量值单位 |
| `battery` | int | 否 | 电量百分比，0-100 |
| `signal` | int | 否 | LoRa RSSI 或 Zigbee linkquality |
| `snr` | number | 否 | LoRa SNR |
| `raw` | object | 否 | 原始解析辅助信息，不作为主要业务字段 |

至少需要满足：`device_id`、`timestamp`、`status` 或至少一个有效测量字段。

### 3.2 当前可识别字段别名

| 语义 | 可识别字段 |
|---|---|
| 设备 ID | `device_id`、`id`、`deviceName`、`devEUI`、`dev_eui`、`friendly_name`、`ieeeAddr`、`ieee_addr`，或从 topic 提取 |
| 时间戳 | `timestamp`、`last_seen` |
| 温度 | `temperature`、`temp` |
| 湿度 | `humidity`、`hum` |
| 压力 | `pressure`、`barometer` |
| 电量 | `battery`、`battery_level` |
| 信号 | `signal`、`rssi`、`linkquality` |
| LoRa SNR | `snr`、`loRaSNR` |

其他小组如果字段名不在上表中，必须提供字段映射表，或先在网关脚本中转换为标准字段。

### 3.3 LoRa/LoRaWAN MQTT 上行示例

MQTT topic 示例：

```text
bridge/uplink/lora/lora_env_01/data
```

MQTT payload 示例：

```json
{
  "devEUI": "24e124136d000001",
  "deviceName": "lora_env_01",
  "applicationName": "factory-lora",
  "timestamp": 1783329001000,
  "fPort": 2,
  "rxInfo": [
    {
      "rssi": -57,
      "loRaSNR": 8.2
    }
  ],
  "object": {
    "temperature": 23.6,
    "humidity": 56.2,
    "battery": 92
  }
}
```

规则：

| 项 | 要求 |
|---|---|
| 设备 ID | `deviceName` 或 `devEUI` 至少提供一个，优先使用 `deviceName` |
| 信号质量 | 建议保留 `rxInfo[].rssi` 与 `rxInfo[].loRaSNR` |
| 业务字段 | 优先放在 `object` 中，字段名使用标准名 |
| base64 | 如果只有 `data` 或 `frm_payload`，必须提供解码规则，或在平台侧先解码成结构化 JSON |

### 3.4 Zigbee MQTT 上行示例

MQTT topic 示例：

```text
bridge/uplink/zigbee/zigbee_env_01/data
```

MQTT payload 示例：

```json
{
  "ieeeAddr": "0x00124b0024c00001",
  "friendly_name": "zigbee_env_01",
  "last_seen": 1783329002000,
  "linkquality": 154,
  "battery": 85,
  "temperature": 25.1,
  "humidity": 60.4,
  "status": "online"
}
```

规则：

| 项 | 要求 |
|---|---|
| 设备 ID | `friendly_name` 或 `ieeeAddr` 至少提供一个，优先使用 `friendly_name` |
| 时间 | `last_seen` 建议使用毫秒级时间戳 |
| 信号 | `linkquality` 存入 `signal`，但它不是 dBm |
| 业务字段 | 直接使用 `temperature`、`humidity`、`pressure` 等标准字段 |

### 3.5 RS485/工业数据 MQTT 上行示例

RS485、Modbus 或私有串口协议的原始二进制帧不允许直接作为业务 payload。必须先在网关脚本中完成寄存器解析，再发布结构化 JSON。

MQTT topic 示例：

```text
bridge/uplink/zigbee/rs485_meter_01/data
```

MQTT payload 示例：

```json
{
  "device_id": "rs485_meter_01",
  "name": "workshop_meter_01",
  "type": "multi",
  "status": "online",
  "timestamp": 1783329003000,
  "temperature": 28.4,
  "humidity": 51.2,
  "pressure": 101.1,
  "battery": 100,
  "signal": 146,
  "raw": {
    "protocol": "modbus-rtu",
    "slave_id": 1,
    "registers": {
      "40001": 284,
      "40002": 512
    },
    "scale": {
      "temperature": 0.1,
      "humidity": 0.1
    }
  }
}
```

规则：

| 项 | 要求 |
|---|---|
| 寄存器 | 必须提供地址、倍率、单位、字节序、符号位说明 |
| 原始数据 | 可放入 `raw`，用于排查 |
| 主业务字段 | 必须转换为标准字段，如 `temperature`、`humidity`、`pressure` |
| 异常 | CRC 错误、超时、超阈值必须发布错误 payload |

### 3.6 错误/告警 MQTT 上行示例

MQTT topic 示例：

```text
bridge/uplink/generic/rs485_meter_01/error
```

MQTT payload 示例：

```json
{
  "device_id": "rs485_meter_01",
  "type": "status",
  "status": "error",
  "timestamp": 1783329004000,
  "error_code": "RS485_TIMEOUT",
  "error_msg": "No response from slave 1 within 1000 ms",
  "severity": "warning"
}
```

---

## 4. MQTT topic 命名规范与统一 topic

### 4.1 MQTT 连接规则

| 项 | 默认值/要求 |
|---|---|
| 协议 | MQTT 3.1.1 over TCP |
| 默认 Broker | `tcp://broker.emqx.io:1883` |
| 认证 | 默认空用户名/密码；专用 broker 必须提供 username/password |
| Client ID | 每个发布端必须唯一，建议包含小组、设备类型和时间戳 |
| Payload 编码 | UTF-8 JSON |
| Retained | 必须为 `false`，除非专项测试明确要求 |
| QoS | 高频数据 `0` 或 `1`；状态/错误 `1`；命令下发默认 `1` |
| TLS/WebSocket | 当前不是默认能力，若真实平台只支持 `8883` 或 WebSocket，需要提前改造 |

### 4.2 统一上行 topic 格式

```text
bridge/uplink/{source}/{device_id}/{action}
```

| 层级 | 说明 | 允许值/示例 |
|---|---|---|
| `bridge` | MQTT 命名空间固定前缀，表示由桥接系统管理/消费 | `bridge` |
| `uplink` | 固定方向 | `uplink` |
| `{source}` | 数据来源 | `lora`、`zigbee`、`generic` |
| `{device_id}` | 设备 ID | `lora_env_01` |
| `{action}` | 消息类型 | `data`、`status`、`error` |

必须优先使用：

```text
bridge/uplink/lora/{device_id}/data
bridge/uplink/zigbee/{device_id}/data
bridge/uplink/generic/{device_id}/data
bridge/uplink/generic/{device_id}/status
bridge/uplink/generic/{device_id}/error
```

规范订阅：

| Topic | Adapter | 用途 |
|---|---|---|
| `bridge/uplink/lora/+/data` | `lora_adapter` | LoRa/LoRaWAN 数据 |
| `lora/+/up` | `lora_adapter` | LoRaWAN 网关兼容 topic |
| `bridge/uplink/zigbee/+/data` | `zigbee_adapter` | Zigbee 数据 |
| `zigbee/+/report` | `zigbee_adapter` | Zigbee2MQTT 风格兼容 topic |
| `bridge/uplink/generic/+/data` | `generic_adapter` | 通用设备数据 |
| `bridge/uplink/generic/+/status` | `generic_adapter` | 通用设备状态 |
| `bridge/uplink/generic/+/error` | `generic_adapter` | 通用错误上报 |


禁止：

| 不允许写法 | 原因 |
|---|---|
| `test/data`、`demo/topic` 等随机 topic | bridge 默认不会订阅 |
| topic 中包含中文、空格或超长字符串 | 当前 topic 缓冲为 192 字节 |
| 同一设备随机切换 topic 层级 | 会导致解析和排查困难 |
| 同一物理设备多个 `device_id` | VSOA 中会形成多个设备记录 |

### 4.3 统一下行 topic 格式

默认格式：

```text
bridge/downlink/{device_type}/{device_id}/{action}
```

示例：

```text
bridge/downlink/lora/lora-node-01/set
bridge/downlink/zigbee/zb-sensor-01/config
```

允许通过配置做设备类型级覆盖：

```yaml
mqtt:
  topic_prefix: "bridge/downlink"
  topic_prefixes:
    lora: "lora/cmd"
    zigbee: "zigbee/cmd"
```

覆盖后示例：

```text
lora/cmd/lora-node-01/set
zigbee/cmd/zb-sensor-01/config
```

设备级 topic 模板字段 `mqtt_topic_template` 已在下行设备注册表中预留，但当前不作为默认规则启用。等 LoRaWAN 网关真实下行 topic 格式确认后再激活。

---

## 5. MQTT payload 与 VSOA 消息体 schema

### 5.1 上行数据 payload 基本规则

所有上行 payload 必须是 UTF-8 JSON 对象。不得直接发布裸二进制串口帧。

最小可接受示例：

```json
{
  "device_id": "device_01",
  "timestamp": 1783329001000,
  "temperature": 23.6,
  "humidity": 56.2,
  "battery": 92,
  "status": "online"
}
```

如果 payload 不含 `device_id`，topic 中必须能提取 `{device_id}`。如果 payload 不含 `timestamp`，bridge 可以用接收时间补齐，但该设备小组必须在交付说明中注明。

### 5.2 VSOA 下行命令消息体

VSOA 应用通过 RPC URL 或发布 URL 发送给下行 bridge 的统一命令消息体：

```json
{
  "command_id": "cmd-20260708-001",
  "device_type": "lora",
  "device_id": "lora-node-01",
  "action": "set",
  "params": {
    "led": "on"
  },
  "timeout_ms": 10000,
  "timestamp": "2026-07-08T10:00:00Z"
}
```

字段说明：

| 字段 | 类型 | 必填 | 说明 |
|---|---|:---:|---|
| `command_id` | string | 是 | 命令唯一 ID，用于幂等去重 |
| `device_type` | string | 是 | `lora` 或 `zigbee`，后续可扩展 `bridge`、`wifi` |
| `device_id` | string | 是 | 目标设备 ID，必须存在于下行 `devices.yaml` |
| `action` | string | 是 | `set`、`get`、`reset`、`config` |
| `params` | object | 是 | 命令参数 |
| `timeout_ms` | integer | 否 | RPC 通道超时时间，缺省取配置 |
| `timestamp` | string | 否 | ISO 8601，缺省由 bridge 补齐 |

下行 bridge 转换后发布到 MQTT 的 payload：

```json
{
  "command_id": "cmd-20260708-001",
  "action": "set",
  "params": {
    "led": "on"
  },
  "timestamp": "2026-07-08T10:00:00Z",
  "trace_id": "br-a3f8c2d1-1720435200000"
}
```

### 5.3 VSOA 下行 ACK 返回体

RPC 通道通过 `fetch()` 同步返回 ACK 返回体；发布/订阅通道由 bridge 发布到 VSOA URL `/ctrl/ack`，订阅方接收 ACK 消息体。

```json
{
  "command_id": "cmd-20260708-001",
  "error_code": 0,
  "error_msg": "ok",
  "device_type": "lora",
  "device_id": "lora-node-01",
  "ack_level": "bridge",
  "trace_id": "br-a3f8c2d1-1720435200000"
}
```

---

## 6. 上行数据转 VSOA 服务接口

### 6.1 服务基本信息

| 项 | 当前默认值 |
|---|---|
| 服务名 | `VSOA Uplink Bridge` |
| 版本 | `2.0.0` |
| VSOA RPC 地址 | `127.0.0.1:3001`，真机联调按实际 IP/端口调整 |
| TCP 离线注入地址 | `127.0.0.1:9090` |
| 支持 adapter | `lora_adapter`、`zigbee_adapter`、`generic_adapter` |

### 6.2 VSOA RPC 查询 URL

| URL | 用途 |
|---|---|
| `GET /bridge/health` | 查询 bridge 健康状态、MQTT 连接状态、设备数 |
| `GET /adapter/list` | 查询当前支持的 adapter 和 MQTT 订阅 topic |
| `GET /uplink/schema` | 查询上行 schema 摘要 |
| `GET /device/list` | 查询已注册设备列表 |
| `GET /device/all/data` | 查询全部设备完整数据 |
| `GET /device/{device_id}/data` | 查询单设备数据 |
| `GET /device/{device_id}/status` | 查询单设备状态 |

单设备查询返回示例：

```json
{
  "device_id": "lora_env_01",
  "name": "lora_env_01",
  "type": "multi",
  "status": "online",
  "source": "lora",
  "adapter": "lora_adapter",
  "timestamp": 1783329001000,
  "registered_at": 1783329001000,
  "temperature": 23.6,
  "humidity": 56.2,
  "battery": 92,
  "signal": -57,
  "snr": 8.2,
  "report_count": 1,
  "last_topic": "bridge/uplink/lora/lora_env_01/data"
}
```

其中 `last_topic` 是最后一次收到的 MQTT topic，不是 VSOA 路径。

未找到设备时：

```json
{"error":"Device not found"}
```

### 6.3 VSOA 发布/订阅 URL

发布/订阅接口不使用 MQTT 式 topic。下表中的路径是 VSOA 发布 URL，订阅方按 URL 订阅；消息内容放在 VSOA 消息体中，协议与 SDK 负责承载必要元数据。

| 发布 URL | 触发时机 | 消息体 |
|---|---|---|
| `/device/update` | 任一设备 upsert 成功 | 单设备完整 JSON |
| `/bridge/event` | 上行消息被接收并转换 | 事件 JSON |

ADP/VSOA SDK 如需实时监听，应订阅以上两个发布 URL。


---

## 7. 统一命令接口

### 7.1 VSOA RPC 命令接口

用于同步控制命令：

```text
client.fetch("/bridge/send_command", body=cmd)
```

上面是协议层伪代码：`/bridge/send_command` 是 VSOA RPC URL，`cmd` 是请求消息体。具体 SDK 可能使用 `param`、`body` 或封装类承载消息体。

调用方不应把该消息体理解为 MQTT payload。

bridge 处理后通过 RPC 返回 ACK 返回体。调用方必须根据 `error_code` 判断命令是否被 bridge 接收并发布到 MQTT。

### 7.2 VSOA 发布/订阅命令接口

用于异步控制命令：

```text
业务层发布 URL: /ctrl/cmd
bridge ACK 发布 URL: /ctrl/ack
```

命令消息体仍然使用 5.2 的统一命令结构。发布/订阅通道不检查 `timeout_ms`，也不做 MQTT publish 多次重试，语义为 best-effort。

### 7.3 下行命令处理流程

```text
收到命令 cmd
  -> 生成 trace_id
  -> validate(cmd)
  -> 检查 device_id 是否存在于 devices.yaml
  -> 检查 command_id 幂等去重
  -> 构造 MQTT topic 和 MQTT payload
  -> 发布 MQTT
  -> 返回或发布 ACK
```

两种通道差异：

| 项 | RPC 通道 | 发布/订阅通道 |
|---|---|---|
| 调用方式 | `fetch("/bridge/send_command")`，请求消息体为命令 JSON | 发布到 VSOA URL `/ctrl/cmd`，消息体为命令 JSON |
| ACK | RPC 同步返回 ACK 返回体 | 发布到 VSOA URL `/ctrl/ack`，消息体为 ACK JSON |
| 超时检查 | 检查 `timeout_ms` | 忽略 `timeout_ms` |
| MQTT publish 重试 | 启用 | 单次 best-effort |
| 注册表检查 | 启用 | 启用 |
| 幂等去重 | 启用 | 启用 |
| traceId | 启用 | 启用 |

### 7.4 幂等去重规则

| 场景 | 行为 |
|---|---|
| 新 `command_id` | 标记并放行 |
| 相同 `command_id` 在 TTL 内重复 | 拒绝执行，返回 `2006` |
| 相同 `command_id` 超过 TTL | 视为新命令 |
| 缓存超过 `max_size` | 淘汰最老条目 |

默认 TTL 为 300 秒。

### 7.5 下行设备注册表

下行 bridge 使用独立 `devices.yaml` 作为设备白名单。命令中的 `device_id` 不在白名单时，直接返回 `2203`，不会发布 MQTT。

```yaml
devices:
  lora-node-01:
    type: lora
    description: "LoRa 测试节点 01"
    dev_eui: "24e124136d000001"
    # mqtt_topic_template: "application/1/device/{dev_eui}/command/down"

  zb-sensor-01:
    type: zigbee
    description: "Zigbee 传感器节点 01"
```

---

## 8. 配置文件规范

### 8.1 `config.yaml` 总模板

```yaml
# ---------- VSOA 连接 ----------
vsoa:
  rpc_server:
    bind_host: "127.0.0.1"
    port: 3001
    endpoint: "/bridge/send_command"

  pubsub_client:
    server_url: "vsoa://127.0.0.1:3000"
    subscribe_urls:
      - "/ctrl/cmd"
    ack_publish_url: "/ctrl/ack"

  reconnect:
    enabled: true
    interval_ms: 3000
    max_retries: 10
    backoff_multiplier: 2.0

# ---------- MQTT 连接 ----------
mqtt:
  broker: "broker.emqx.io"
  port: 1883
  username: ""
  password: ""
  keepalive: 60
  client_id: "bridge-downlink"
  topic_prefix: "bridge/downlink"
  topic_prefixes:
    # lora: "lora/cmd"
    # zigbee: "zigbee/cmd"
  qos: 1
  retained: false
  reconnect:
    enabled: true
    interval_ms: 3000
    max_retries: 0

# ---------- 命令处理 ----------
command:
  default_timeout_ms: 10000
  max_timeout_ms: 60000
  pending_queue_size: 100
  dedup:
    enabled: true
    ttl_seconds: 300
    max_size: 10000
  retry:
    max_retries: 3
    backoff_base_ms: 500

# ---------- 设备注册表 ----------
device_registry:
  source: "devices.yaml"
  auto_reload: false

# ---------- 上行 bridge 限制 ----------
uplink:
  tcp_inject_port: 9090
  max_devices: 64
  max_json_len: 8192
  max_topic_len: 192
  max_device_id_len: 64

# ---------- 日志 ----------
logging:
  level: "INFO"
  format: "[%(asctime)s] [%(levelname)s] %(message)s"
  date_format: "%Y-%m-%d %H:%M:%S"
  file: "logs/bridge.log"
```

### 8.2 其他小组必须提交的配置/交付信息

| 类别 | 必须提供 |
|---|---|
| 设备信息 | 设备型号、设备数量、设备 ID 对照表、安装位置 |
| 协议信息 | LoRaWAN/Zigbee/RS485/Modbus 参数、频段、串口参数 |
| MQTT 信息 | Broker、端口、认证、topic、QoS、retained、发布周期 |
| Payload 样例 | 至少 3 条真实 payload，包括正常、状态、异常或边界值 |
| 字段解释 | 每个字段含义、单位、倍率、取值范围、是否可为空 |
| 解码规则 | LoRa base64、Modbus 寄存器、私有帧格式、字节序 |
| 时间规则 | timestamp 来源、单位、时区、设备无 RTC 时如何处理 |
| 错误规则 | 离线、超时、CRC 错误、超阈值如何表示 |
| 验收方法 | 用哪个 topic、哪个设备 ID、哪个 VSOA URL 验证成功 |

设备 ID 对照表模板：

| 物理设备 | 推荐 `device_id` | 协议原始 ID | MQTT topic | 备注 |
|---|---|---|---|---|
| LoRa 节点 1 | `lora_env_01` | `devEUI=24e124136d000001` | `bridge/uplink/lora/lora_env_01/data` | 温湿度 |
| Zigbee 节点 1 | `zigbee_env_01` | `ieeeAddr=0x00124b0024c00001` | `bridge/uplink/zigbee/zigbee_env_01/data` | 温湿度 |
| RS485 仪表 1 | `rs485_meter_01` | `slave_id=1` | `bridge/uplink/zigbee/rs485_meter_01/data` | 透传 |

字段映射表模板：

| 原始字段/寄存器 | 转换后字段 | 类型 | 单位 | 倍率 | 示例 | 说明 |
|---|---|---|---|---:|---|---|
| `object.temperature` | `temperature` | number | `celsius` | 1 | `23.6` | LoRa 解码后温度 |
| `linkquality` | `signal` | int | quality | 1 | `154` | Zigbee 链路质量 |
| `40001` | `temperature` | number | `celsius` | 0.1 | `284 -> 28.4` | RS485 寄存器 |

---

## 9. 错误处理与重连机制

### 9.1 MQTT 错误处理

| 场景 | 处理规则 |
|---|---|
| Broker 无法连接 | 先用 MQTTX 或 mosquitto 验证 broker 可达，再检查地址、DNS、防火墙、认证 |
| CONNACK 失败 | 检查 username/password、client_id 冲突、TLS/协议版本 |
| SUBSCRIBE 失败 | 检查 broker ACL、topic 权限、连接稳定性 |
| 反复 disconnected | 检查网络和 broker 日志；bridge 按配置间隔重连 |
| MQTTX 能看到消息但 bridge 无日志 | topic 不在订阅范围，必须改成统一 topic 或扩展订阅 |
| 公共 broker 消息混杂 | 使用专用 broker 或项目专属 topic 前缀 |

### 9.2 VSOA 重连

| 组件 | 规则 |
|---|---|
| VSOA 发布/订阅 Client | 断连后按 `interval_ms * backoff_multiplier^attempt` 指数退避重连，并重新订阅 |
| RPC Server | 被动监听端口，不需要重连；端口被占用时启动失败 |
| 首次连接 | 启动阶段最多尝试 `max_retries` 次，全部失败则退出 |
| 后续断连 | `run_forever()` 内自动重连，超过最大次数记录 fatal 并退出 |

### 9.3 下行 publish 重试

RPC 通道启用 MQTT publish 重试：

```text
attempt 0: 立即执行
attempt 1: 等待 500ms 后重试
attempt 2: 等待 1000ms 后重试
attempt 3: 等待 2000ms 后重试
```

默认 `max_retries=3`，表示首次加 3 次重试，共最多 4 次尝试。全部失败后返回最后一次错误码。

### 9.4 payload/解析错误

| 场景 | 处理规则 |
|---|---|
| JSON 非法 | 现场必须保存原始 MQTT payload；该数据不进入 VSOA 设备表 |
| 缺少设备 ID | 从 topic 提取；仍无法提取则拒绝并记录 parse_failed |
| 字段名不匹配 | 优先要求设备小组改字段；必要时扩展 adapter |
| payload 超过 8192 字节 | 减小 payload、拆字段或调整 `MAX_JSON_LEN` |
| topic 超过 192 字节 | 缩短 topic，避免中文和冗余层级 |
| 原始二进制 | 必须先转 hex/base64 或结构化 JSON，不能直接作为业务 payload |

### 9.5 现场排障顺序

1. 先运行本地上行测试，确认 bridge、adapter、VSOA 链路可用。
2. 使用 TCP 9090 注入真实 payload 拷贝，先验证 parser。
3. 再启用 MQTT subscriber，确认 bridge 已连接 broker 并订阅成功。
4. 用 MQTTX 订阅 `bridge/uplink/#`、`lora/+/up`、`zigbee/+/report`，确认真实 topic 和 payload。
5. 对齐 topic、payload、设备 ID 后，看 bridge 是否出现 `converted source=... adapter=...`。
6. 用 VSOA 查询 `/bridge/health`、`/adapter/list`、`/device/list`、`/device/{id}/data`。
7. 涉及 RS485 时，先离线验证寄存器和字节序，再接入 Zigbee 透传。

---

## 10. 错误码表

### 10.1 码段分配

| 码段 | 范围 | 负责方/用途 |
|---|---|---|
| `0` | 0 | 成功 |
| `1xxx` | 1000-1999 | 上行侧预留 |
| `2xxx` | 2000-2999 | 下行命令与 MQTT 发布 |
| `9xxx` | 9000-9999 | 共同预留 |

### 10.2 下行错误码

| 错误码 | 名称 | 说明 |
|---:|---|---|
| `0` | `OK` | 成功 |
| `2001` | `ERR_CMD_INVALID_JSON` | 命令消息体不是合法 JSON |
| `2002` | `ERR_CMD_MISSING_FIELD` | 缺少必填字段 |
| `2003` | `ERR_CMD_UNKNOWN_DEVICE_TYPE` | `device_type` 不在枚举值中 |
| `2004` | `ERR_CMD_UNKNOWN_ACTION` | `action` 不在枚举值中 |
| `2005` | `ERR_CMD_TIMEOUT_EXCEEDED` | `timeout_ms` 超过 `max_timeout_ms` |
| `2006` | `ERR_CMD_DUPLICATE_ID` | `command_id` 在 TTL 窗口内重复 |
| `2101` | `ERR_MQTT_NOT_CONNECTED` | MQTT Broker 未连接 |
| `2102` | `ERR_MQTT_PUBLISH_FAILED` | MQTT publish 调用失败或重试耗尽 |
| `2103` | `ERR_MQTT_QOS_NOT_SUPPORTED` | QoS 级别不被支持 |
| `2201` | `ERR_MQTT_PUBLISH_TIMEOUT` | MQTT publish 等待 PUBACK 超时或重试耗尽 |
| `2202` | `ERR_MQTT_PUBLISH_REJECTED` | MQTT publish 被 Broker 拒绝 |
| `2203` | `ERR_DEVICE_NOT_FOUND` | 目标设备不在下行注册表 |
| `2204` | `ERR_DEVICE_OFFLINE` | 目标设备离线，预留，需上行在线状态 |
| `2301` | `ERR_INTERNAL` | 未分类内部错误 |
| `2302` | `ERR_CONFIG_INVALID` | 配置文件校验失败 |
| `2303` | `ERR_VSOA_DISCONNECTED` | VSOA 连接断开 |
| `2304` | `ERR_QUEUE_FULL` | 待处理命令队列已满 |

### 10.3 上行/现场错误字符串

上行错误当前主要通过 error payload 的 `error_code` 字段表达：

| 错误码 | 含义 |
|---|---|
| `DEVICE_OFFLINE` | 设备离线 |
| `SENSOR_TIMEOUT` | 传感器采集超时 |
| `RS485_TIMEOUT` | RS485 从站无响应 |
| `RS485_CRC_ERROR` | RS485/Modbus CRC 错误 |
| `PAYLOAD_SCHEMA_ERROR` | payload 字段不符合规范 |
| `VALUE_OUT_OF_RANGE` | 数值超出阈值 |

### 10.4 VSOA 状态码

| 状态码 | 含义 |
|---|---|
| `0` | 成功 |
| `VSOA_STATUS_ARGUMENTS` | 参数错误 |
| `VSOA_STATUS_INVALID_URL` | 资源不存在或 URL 不匹配 |

---

## 11. 验收标准

### 11.1 MQTT 层

| 检查项 | 通过标准 |
|---|---|
| topic | 在当前 bridge 订阅范围内 |
| payload | UTF-8 JSON，可被解析 |
| retained | false |
| device_id | topic 或 payload 至少一处可提取 |
| timestamp | 毫秒级时间戳，或已说明由 bridge 补齐 |
| 数值字段 | 使用标准字段名或已提供字段映射 |

### 11.2 Bridge 层

日志应出现：

```text
[MQTT] RX topic=...
[REGISTRY] registered/updated ...
[UPLINK] converted source=... adapter=... device=...
```

不应出现：

```text
[UPLINK] adapter=... parse_failed: ... missing device id
[BRIDGE] input line too long, dropped
[REGISTRY] Device limit reached (64)
```

### 11.3 VSOA 层

必须能查询：

```text
GET /bridge/health
GET /adapter/list
GET /device/list
GET /device/{device_id}/data
```

查询结果必须包含：

| 字段 | 期望 |
|---|---|
| `device_id` | 与约定 ID 一致 |
| `source` | `lora`、`zigbee` 或 `generic` |
| `adapter` | 对应 adapter |
| `timestamp` | 接近真实采集或上报时间 |
| 业务字段 | 至少包含一个有效测量值或明确状态 |
| `report_count` | 连续上报时递增 |
| `last_topic` | 与真实 topic 一致 |

### 11.4 下行命令层

| 验收项 | 通过标准 |
|---|---|
| 设备注册表 | `device_id` 存在时放行，不存在时返回 `2203` |
| 幂等去重 | 相同 `command_id` 在 TTL 内第二次发送返回 `2006` |
| traceId | ACK、MQTT payload、日志均包含同一 `trace_id` |
| RPC 重试 | MQTT publish 失败时按配置重试，全部失败后返回对应错误码 |
| 发布/订阅 ACK | 向 VSOA URL `/ctrl/cmd` 发布命令后，能在订阅 URL `/ctrl/ack` 收到 ACK |

