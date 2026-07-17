---
AIGC:
    Label: "1"
    ContentProducer: 001191110102MACQD9K64018705
    ProduceID: 3317688578606600_0/project_7659229021292560694-files/L4/LoRaWAN_MQTT后端接入说明_docx版.md
    ReservedCode1: ""
    ContentPropagator: 001191110102MACQD9K64028705
    PropagateID: 3317688578606600#1784094252125
    ReservedCode2: ""
---
# LoRaWAN MQTT 数据接口说明

## 一、MQTT 连接信息

| 参数 | 值 |
|------|------|
| 协议 | MQTT 3.1.1 |
| Broker 地址 | 192.168.137.118 |
| 端口 | 1883 |
| 用户名 | 无（匿名连接） |
| 密码 | 无 |
| ClientID | 建议自定义，如 backend_service_01 |
| QoS | 建议 QoS 1 |

说明：系统目前提供两种数据上行方式。两种数据最终均进入 MQTT Broker: 192.168.137.118:1883，后端只需连接该 MQTT Broker 即可获取全部数据。

### 1.1 LoRaWAN 链路

```text
EoRa-S3-400TB
      |
      | LoRaWAN
      ↓
E870 LoRaWAN Hub
      |
      ↓
ChirpStack
      |
      ↓
MQTT Broker
```

图1 LoRaWAN 链路

### 1.2 WiFi MQTT 链路

```text
EoRa-S3-400TB
      |
      | WiFi MQTT
      ↓
PC Mosquitto
      |
      | MQTT Bridge
      ↓
MQTT Broker
```

图2 WiFi MQTT 链路

### 1.3 数据桥接说明（mqtt_bridge.py）

由于 S3 WiFi MQTT 数据直接发送至 PC 本地 Mosquitto（192.168.3.219），而 T113 服务器上的 MQTT Broker（192.168.137.118）运行在不同网段，两者无法直接互通。

因此需要通过 mqtt_bridge.py 将 PC 本地的 S3 WiFi 数据转发至 T113 统一 Broker，确保后端只需连接单一接入点即可获取全部数据。

#### 1.3.1 桥接流程

```text
S3 WiFi MQTT 数据
      |
      ↓
PC 本地 Mosquitto
(192.168.3.219:1883)
      |
      | mqtt_bridge.py 转发
      ↓
T113 统一 MQTT Broker
(192.168.137.118:1883)
      |
      ↓
后端统一订阅
```

图3 数据桥接流程

#### 1.3.2 mqtt_bridge.py 配置

| 参数 | 值 |
|------|------|
| 源 Broker（本地） | 192.168.3.219:1883 |
| 目标 Broker（T113） | 192.168.137.118:1883 |
| 转发 Topic | s3/eora-s3-400tb-001/data |
| 运行位置 | PC 端 |

#### 1.3.3 输出示例

```text
Remote MQTT connected: 192.168.137.118
MQTT Bridge running...
Local MQTT connected: 192.168.3.219
Subscribed: s3/eora-s3-400tb-001/data
----------------------
Received:
{"device_id":"eora-s3-400tb-001","dev_eui":"0000000000000925","boot_id":1704463124,"seq":31,"send_time_ms":2676691,"wifi_retry_count":0,"temperature":26.1,"humidity":55.1,"link":"wifi"}
Forward success
```

## 二、订阅 Topic

### 2.1 Topic 列表

| Topic | 说明 | QoS |
|-------|------|-----|
| application/+/device/+/event/up | LoRaWAN 所有设备上行数据 | 1 |
| application/{applicationID}/device/{devEUI}/event/up | LoRaWAN 指定设备数据 | 1 |
| s3/eora-s3-400tb-001/data | S3 WiFi 上行数据 | 1 |

### 2.2 LoRaWAN Topic 结构说明

```text
application/{applicationID}/device/{devEUI}/event/up
```

| 字段 | 含义 | 示例值 |
|------|------|--------|
| applicationID | ChirpStack 应用 ID | 3ed92fba-9e06-4b8e-ad41-54927a0fa89d |
| devEUI | 设备唯一标识 | 0000000000000925 |
| event/up | 上行数据事件 | 固定值 |

示例：

```text
application/3ed92fba-9e06-4b8e-ad41-54927a0fa89d/device/0000000000000925/event/up
```

### 2.3 WiFi MQTT Topic 结构说明

```text
s3/eora-s3-400tb-001/data
```

| 字段 | 含义 | 示例值 |
|------|------|--------|
| s3 | 设备类型标识 | 固定值 |
| eora-s3-400tb-001 | 设备 ID | EoRa-S3-400TB |
| data | 数据上报事件 | 固定值 |

## 三、MQTT 消息格式

### 3.1 LoRaWAN MQTT 消息格式

ChirpStack 上报完整 JSON：

```json
{
  "time": "2026-07-14T06:41:07.200699727+00:00",
  "deviceInfo": {
    "devEui": "0000000000000925"
  },
  "devAddr": "01f45da6",
  "rxInfo": [
    {
      "gatewayId": "0000000000000923",
      "rssi": -24,
      "snr": 14.0
    }
  ],
  "data": "AEGcSB0jAD7kbQABCQIrAQ=="
}
```

### 3.2 LoRaWAN 关键字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| time | string (ISO8601) | 数据接收时间（UTC） |
| deviceInfo.devEui | string | 设备 DevEUI |
| devAddr | string | 设备网络地址 |
| rxInfo[].gatewayId | string | 接收网关 ID |
| rxInfo[].rssi | int | 信号强度 (dBm) |
| rxInfo[].snr | float | 信噪比 |
| data | string (Base64) | LoRa 应用层 Payload（需解码） |

### 3.3 WiFi MQTT 消息格式

S3 通过 WiFi 直接发送 JSON 数据：

```json
{
  "device_id": "eora-s3-400tb-001",
  "dev_eui": "0000000000000925",
  "boot_id": 1704463124,
  "seq": 31,
  "send_time_ms": 2676691,
  "wifi_retry_count": 0,
  "temperature": 26.1,
  "humidity": 55.1,
  "link": "wifi"
}
```

### 3.4 WiFi 关键字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| device_id | string | 设备 ID |
| dev_eui | string | 设备唯一标识 |
| boot_id | uint32 | 设备启动编号 |
| seq | uint32 | WiFi 数据包序号 |
| send_time_ms | uint32 | 设备运行时间 (ms) |
| wifi_retry_count | uint8 | WiFi 应用层重传次数 |
| temperature | float | 温度 (℃) |
| humidity | float | 湿度 (%) |
| link | string | 数据链路类型 |

其中 link = "wifi" 表示该数据通过 WiFi MQTT 上传。

## 四、Payload 解析协议

### 4.1 数据概览

data 字段经 Base64 解码后得到 16 字节二进制 Payload。

### 4.2 字段定义

| Byte 偏移 | 字段名 | 类型 | 长度 | 说明 |
|-----------|--------|------|------|------|
| 0-1 | packetSeq | uint16 (大端) | 2 | 数据包序号 |
| 2-5 | bootId | uint32 (大端) | 4 | 设备启动编号 |
| 6-9 | sendTimeMs | uint32 (大端) | 4 | 设备运行时间 (ms) |
| 10 | retryCount | uint8 | 1 | LoRaWAN 应用层重传次数 |
| 11-12 | temperature | int16 (大端) | 2 | 温度原始值，÷10（℃） |
| 13-14 | humidity | uint16 (大端) | 2 | 湿度原始值，÷10（%） |
| 15 | flags | uint8 | 1 | 状态标志位 |

### 4.3 flags 位定义

| Bit | 名称 | 含义 |
|-----|------|------|
| bit 0 | joined | 1 = 设备已成功入网 |
| bit 3 | application_retry | 1 = 应用层重传数据 |

### 4.4 解析公式

```text
(1) 温度(℃) = temperature / 10.0
(2) 湿度(%)  = humidity / 10.0
```

## 五、解析示例

### 5.1 原始数据

```text
Base64: AEGcSB0jAD7kbQABCQIrAQ==
```

### 5.2 Base64 解码

```text
HEX: 00 41 9C 48 1D 23 00 3E E4 6D 00 01 09 02 2B 01
```

### 5.3 逐字段解析

| 字段 | HEX | 十进制 | 解析结果 |
|------|-----|--------|----------|
| packetSeq | 00 41 | 65 | 第65包 |
| bootId | 9C 48 1D 23 | 2621447459 | - |
| sendTimeMs | 00 3E E4 6D | 4121709 | ~68.7分钟 |
| retryCount | 00 | 0 | 无重传 |
| temperature | 01 09 | 265 | 26.5 ℃ |
| humidity | 02 2B | 555 | 55.5 % |
| flags | 01 | 1 (bit0=1) | 已入网 |

## 六、MQTT 验证代码

### 6.1 文件

```text
mqtt_receiver.py
```

### 6.2 功能

程序支持两种数据入口：

#### 6.2.1 LoRaWAN：

1. MQTT 订阅 application/+/device/+/event/up
2. JSON 解析
3. Base64 解码
4. Payload 解析
5. 温湿度输出

#### 6.2.2 WiFi：

1. MQTT 订阅 s3/eora-s3-400tb-001/data
2. JSON 解析
3. 读取温湿度字段
4. 输出设备状态

### 6.3 输出示例

```text
正在连接MQTT...
MQTT连接成功
订阅Topic:
  application/+/device/+/event/up
  s3/eora-s3-400tb-001/data

========================
Topic:
application/3ed92fba-9e06-4b8e-ad41-54927a0fa89d/device/0000000000000925/event/up

========== LoRaWAN数据 ==========
DevEUI: 0000000000000925
设备: Ebyte Test Device 470

时间:
2026-07-15T03:04:53.001179935+00:00

网关:
Gateway: e4b323fffef78b8c
RSSI: -34
SNR: 14.0

Base64:
ACRlmAsUAC3jGwABCgIsAQ==

HEX:
002465980b14002de31b00010a022c01

======= LoRa解析结果 =======
温度: 26.6 ℃
湿度: 55.6 %
序号: 36
Boot ID: 0x65980b14
LoRaWAN重传: 0
已入网: True
应用层重传: False
============================

========================
Topic:
s3/eora-s3-400tb-001/data

========== WiFi数据 ==========
设备: eora-s3-400tb-001
DevEUI: 0000000000000925
序号: 36
Boot ID: 1704463124
发送时间: 3007259 ms
温度: 26.6 ℃
湿度: 55.6 %
WiFi重传: 0
链路: wifi
============================
```

