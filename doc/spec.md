# MQTT ↔ VSOA 桥接组件 — 技术规格说明书

> **版本:** v1.0（合并版）  
> **作者:** 方宏波、辛澳翔  
> **日期:** 2026-07-14  
> **状态:** 待评审  
> **来源:** 合并 `bridge/`（下行 v3.0）与 `bridge-uplink/`（上行 v2.0），依据 `overall_experiment_rules(1).md` 统一规范

---

## 1. 概述

### 1.1 项目定位

本组件是 MQTT ↔ VSOA 双向桥接系统，作为独立服务进程，在 MQTT 设备（LoRa/Zigbee/Generic 终端）与 VSOA 服务端之间建立双向通信通道：

```
┌──────────────┐     MQTT      ┌────────────────┐     VSOA      ┌──────────────┐
│  LoRa 设备   │ ◄──────────►  │   桥接组件      │ ◄──────────►  │  VSOA 服务端  │
│  Zigbee 设备  │              │                │               │  (业务层)     │
│  Generic 设备│               │  上行: MQTT→VSOA│              │              │
└──────────────┘               │  下行: VSOA→MQTT│              └──────────────┘
                               └────────────────┘
```

- **上行（Uplink）：** MQTT 设备数据 → adapter 解析 → 设备注册表 → VSOA 查询接口 + 发布通知
- **下行（Downlink）：** VSOA 命令 → 校验 → 注册表检查 → 幂等去重 → MQTT 控制消息下发

### 1.2 设计原则

1. **独立服务进程** — 故障隔离、独立测试、不嵌入业务层 VSOA Server
2. **单端口对外** — 合并上行查询与下行 RPC 到一个 VSOA Server（端口 3001）
3. **设备注册表共用** — 上行自动发现设备，下行直接查询，不再维护静态白名单
4. **双通道下行** — RPC（同步回执）+ Pub/Sub（异步 ACK），覆盖不同调用场景
5. **协议转换透明** — 业务层只看到 VSOA 接口，设备侧只看到 MQTT topic

### 1.3 术语定义

| 术语 | 含义 |
|------|------|
| **上行** | MQTT → VSOA 方向，设备数据上报到业务层 |
| **下行** | VSOA → MQTT 方向，控制命令下发到设备 |
| **RPC 通道** | 业务层 `client.fetch("/bridge/send_command", ...)` → bridge handler → `cli.reply(seqno, result)`。同步阻塞，一行拿到回执 |
| **Pub/Sub 通道** | 业务层 `server.publish("/ctrl/cmd", ...)` → bridge `client.subscribe()` → on_message。异步消息，bridge publish ACK 到 `/ctrl/ack` |
| **回执 (ACK)** | RPC 通过 `fetch()` 返回值同步传回；Pub/Sub 通过 bridge publish `/ctrl/ack` 异步传回 |
| **traceId** | bridge 内部生成的链路追踪标识，格式 `br-{8位hex}-{毫秒时间戳}`，贯穿全链路 |
| **设备注册表** | 内存设备表，上行 `upsert()` 自动填充，下行 `lookup()` 查询校验 |
| **幂等去重** | 相同 `command_id` 在 TTL 内重复发送时拒绝执行，返回 2006 |
| **适配器 (Adapter)** | 将异构 MQTT payload 规范化为统一 `UplinkReport` 的协议转换层 |

---

## 2. 架构设计

### 2.1 总体架构

```
                         bridge (独立进程)
                         ┌────────────────────────────────────────────────────────────┐
                         │                                                            │
                         │  ┌──────────────────────────────────────────────────────┐  │
                         │  │ VSOA Server (port 3001)                                │  │
                         │  │                                                        │  │
                         │  │  ┌─ RPC: /bridge/send_command    (下行命令入口)        │  │
                         │  │  ├─ RPC: /bridge/health           (健康检查)           │  │
                         │  │  ├─ RPC: /adapter/list            (适配器列表)         │  │
                         │  │  ├─ RPC: /uplink/schema           (上行schema)         │  │
                         │  │  ├─ RPC: /device/list             (设备列表)           │  │
                         │  │  ├─ RPC: /device/all/data         (全部设备数据)       │  │
                         │  │  ├─ RPC: /device/{id}/data        (单设备数据)         │  │
                         │  │  ├─ RPC: /device/{id}/status      (单设备状态)         │  │
                         │  │  ├─ Pub: /device/update           (设备更新通知)       │  │
                         │  │  ├─ Pub: /bridge/event            (桥接事件通知)       │  │
                         │  │  └─ Pub: /ctrl/ack                (下行ACK回执)        │  │
                         │  └──────────────────────────────────────────────────────┘  │
                         │                                                            │
                         │  ┌──────────────────────────────────────────────────────┐  │
                         │  │ VSOA Client (→ 业务层 VSOA Server :3000)              │  │
                         │  │   subscribe: /ctrl/cmd  (Pub/Sub 命令入口)            │  │
                         │  │   → on_message → 下行处理流程 → publish ACK          │  │
                         │  │   断连自动重连（指数退避）                              │  │
                         │  └──────────────────────────────────────────────────────┘  │
                         │                                                            │
                         │  ┌──────────────────────────────────────────────────────┐  │
                         │  │ MQTT Handler (统一客户端)                              │  │
                         │  │   subscribe: 7个上行topic + publish: 下行topic         │  │
                         │  │   线程安全，自动重连                                    │  │
                         │  └──────────────────────────────────────────────────────┘  │
                         │                                                            │
                         │  ┌──────────────────────────────────────────────────────┐  │
                         │  │ TCP Inject Server (port 9090)                         │  │
                         │  │   离线测试：JSON Lines → 模拟 MQTT 上行消息            │  │
                         │  └──────────────────────────────────────────────────────┘  │
                         │                                                            │
                         │  ┌─ 共用模块 ────────────────────────────────────────────┐ │
                         │  │ DeviceRegistry  CommandValidator  DedupCache          │ │
                         │  │ ErrorCodes      ConfigLoader     traceId Generator    │ │
                         │  └──────────────────────────────────────────────────────┘  │
                         └────────────────────────────────────────────────────────────┘
```

### 2.2 端口分配

| 端口 | 方向 | 协议 | 用途 |
|:----:|------|------|------|
| **3001** | 入站 | VSOA | bridge VSOA Server：上行 RPC 查询 + 下行 RPC 命令 + ACK/事件 发布 |
| **3000** | 出站 | VSOA | 连接业务层 VSOA Server，订阅 `/ctrl/cmd`（Pub/Sub 命令通道） |
| **1883** | 出站 | MQTT | 连接 MQTT Broker（订阅上行 topic + 发布下行 topic） |
| **9090** | 入站 | TCP | JSON Lines 注入（离线测试，模拟 MQTT 上行消息） |

> ⚠️ **v3.0→合并版变化：** 端口 3009（原下行独立 ACK Server）已移除。ACK 发布统一走 3001 的 VSOA Server。

### 2.3 VSOA 通信方式选择

沿用下行 spec v3.0 §3.1 的分析，选用 RPC + Pub/Sub，不选用 Datagram 和 Stream。

| # | 通信方式 | 选用 | 理由 |
|---|---------|:---:|------|
| 1 | RPC | ✅ | 同步回执，天然匹配"发命令→等结果"语义 |
| 2 | Pub/Sub | ✅ | 异步高吞吐、发后即忘、可选 ACK |
| 3 | Datagram | ❌ | 无回复通道，错误码无处送达 |
| 4 | Stream | ❌ | 离散命令不需要连续字节流，MQTT 也无流概念 |

### 2.4 VSOA Payload 处理策略

只桥接 `param`（结构化数据），不转发 `data`（二进制块）。理由见下行 spec v3.0 §3.2。

---

## 3. 上行数据流（MQTT → VSOA）

### 3.1 处理管道

```
MQTT topic + payload
  │
  ├─ ① select_adapter(topic, payload)    ← 按优先级匹配 adapter
  │     └─ 无匹配 → generic_adapter（兜底）
  │
  ├─ ② adapter.parse(topic, payload)     ← 规范化为 UplinkReport
  │     └─ 解析失败 → 记录 warning，丢弃
  │
  ├─ ③ registry.upsert(report)           ← 写入设备注册表
  │     └─ 注册表满 → 丢弃（记录 warning）
  │
  └─ ④ vsoa.publish 通知                 ← 发布到 VSOA
        ├─ /device/update  (单设备完整数据)
        └─ /bridge/event   (事件摘要)
```

### 3.2 适配器（Adapter）

三个适配器，按优先级匹配：

| 优先级 | Adapter | 匹配条件 | 数据源 |
|:--:|------|------|------|
| 1 | `lora_adapter` | topic 含 `lora`，或 payload 含 `devEUI`/`fPort`/`rxInfo` | `bridge/uplink/lora/+/data`、`lora/+/up` |
| 2 | `zigbee_adapter` | topic 含 `zigbee`，或 payload 含 `ieeeAddr`/`linkquality` | `bridge/uplink/zigbee/+/data`、`zigbee/+/report` |
| 3 | `generic_adapter` | 兜底（始终匹配） | `bridge/uplink/generic/+/data`、`+/status`、`+/error` |

### 3.3 统一上行数据模型：UplinkReport

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

字段说明与别名映射见 `overall_experiment_rules(1).md` §3。

### 3.4 VSOA 上行接口

#### RPC 查询 URL（全部 GET，通过端口 3001）

| URL | 用途 | 返回 |
|------|------|------|
| `/bridge/health` | 健康检查 | 服务名、状态、运行时间、设备数、版本、端口 |
| `/adapter/list` | 适配器列表 | 各 adapter 的 name + source |
| `/uplink/schema` | 上行 schema | `uplink_report.v2` 字段说明 |
| `/device/list` | 设备摘要列表 | 所有设备的 id/type/status/source/adapter/last_update/report_count |
| `/device/all/data` | 全部设备完整数据 | 所有设备的 `to_json()` |
| `/device/{id}/data` | 单设备数据 | 设备完整 JSON，不存在返回 `{"error":"Device not found"}` |
| `/device/{id}/status` | 单设备状态 | 同 `/device/{id}/data` |

#### 发布通知 URL（Pub/Sub，通过端口 3001）

| 发布 URL | 触发时机 | 消息体 |
|------|------|------|
| `/device/update` | 每次 `registry.upsert()` 成功 | 该设备完整 JSON |
| `/bridge/event` | 每次上行消息处理完成 | `{"event","device_id","source","adapter","timestamp"}` |

---

## 4. 下行数据流（VSOA → MQTT）

### 4.1 双通道架构

```
业务层 VSOA Client                业务层 VSOA Server
  │ fetch("/bridge/                  │ publish("/ctrl/cmd")
  │   send_command")                 │
  │                                  │
  ▼                                  ▼
┌──────────────────────────────────────────────────┐
│              bridge VSOA Server (3001)            │
│  @server.command("/bridge/send_command")          │
│  → 同步处理 → cli.reply(seqno, result)            │
│                                                   │
│  server.publish("/ctrl/ack", Payload(ack))        │
│  (ACK 回执发布，替代原端口 3009)                   │
└──────────────────────────────────────────────────┘
                                    ▲
                                    │ subscribe
                              ┌─────┴──────────────┐
                              │ bridge VSOA Client   │
                              │ → 业务层 :3000       │
                              │ subscribe /ctrl/cmd  │
                              │ on_message → 处理    │
                              └────────────────────┘
```

### 4.2 两通道命令处理流程（统一）

```
收到命令 cmd（来自 RPC 或 Pub/Sub）
  │
  ├─ ⓪ 生成 traceId                        ← 第一步生成，后续所有分支共用
  │
  ├─ ① validate(cmd)                        ← Schema 校验（纯函数）
  │     └─ 失败 → build_ack(cmd, err, trace_id) + reply/ACK
  │     （Pub/Sub 通道 check_timeout=False，跳过 timeout_ms 校验）
  │
  ├─ ② registry.lookup(cmd["device_id"])    ← 设备注册表检查
  │     └─ 未找到 → build_ack(cmd, 2203, trace_id) + reply/ACK
  │     （设备由上行自动注册，不再依赖静态 devices.yaml）
  │
  ├─ ③ dedup.check_and_mark(cmd["command_id"])  ← 幂等去重
  │     └─ 重复 → build_ack(cmd, 2006, trace_id) + reply/ACK
  │
  ├─ ④ build_mqtt_message(cmd, ...)         ← 构造 MQTT topic + payload
  │
  └─ ⑤ [RPC]    _publish_with_retry(...)    ← 自动重试（指数退避）
       [PubSub]  mqtt.publish() → build_ack() → publish ACK（单次 best-effort）
```

### 4.3 两通道差异

| | RPC 通道 | Pub/Sub 通道 |
|--|----------|-------------|
| **业务层调用** | `client.fetch("/bridge/send_command", payload=Payload(param=cmd))` | `server.publish("/ctrl/cmd", payload=Payload(param=cmd))` |
| **bridge 入口** | VSOA Server `@server.command()` handler | VSOA Client `on_message` 回调 |
| **Schema 校验** | `validate(cmd, check_timeout=True)` | `validate(cmd, check_timeout=False)` |
| **设备注册表** | ✅ | ✅ |
| **幂等去重** | ✅ | ✅ |
| **traceId** | ✅ | ✅ |
| **回执方式** | `cli.reply(seqno, result)` → `fetch()` 同步返回 | bridge `publish("/ctrl/ack", Payload(ack))` → 业务层 subscribe 接收 |
| **MQTT 重试** | ✅ 自动重试 N 次（指数退避） | 单次 best-effort |

### 4.4 两层回执模型

**bridge 的"成功"语义是：MQTT Broker 已确认收到该消息（QoS 1 PUBACK），不是设备已执行命令。**

| 层面 | 含义 | 回执通道 | 时间尺度 |
|------|------|----------|:--:|
| **bridge 层** | MQTT Broker 已 PUBACK | RPC `fetch()` 同步返回 / Pub/Sub ACK | 毫秒级 |
| **设备层** | 设备已收到并执行命令 | 设备上行 MQTT publish → 上行链路 → 业务层 | LoRa 秒~分钟级 / Zigbee 毫秒级 |

`ack_level` 字段区分：
- `"bridge"` — 当前实现，MQTT Broker 已确认
- `"device"` — 预留，设备执行回执（通过上行链路异步送达）

---

## 5. 命令 Schema 规范

### 5.1 下行命令

```json
{
  "command_id":  "cmd-20260708-001",
  "device_type": "lora",
  "device_id":   "lora-node-01",
  "action":      "set",
  "params":      {"led": "on"},
  "timeout_ms":  10000,
  "timestamp":   "2026-07-08T10:00:00Z"
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|:---:|------|
| `command_id` | string | ✅ | 命令唯一标识（幂等去重的 key） |
| `device_type` | string | ✅ | `"lora"` \| `"zigbee"`（可扩展 `"generic"`） |
| `device_id` | string | ✅ | 目标设备 ID（需在设备注册表中存在） |
| `action` | string | ✅ | `"set"` \| `"get"` \| `"reset"` \| `"config"` |
| `params` | object | ✅ | 命令参数，至少为 `{}` |
| `timeout_ms` | integer | ❌ | RPC 通道超时（毫秒），默认 10000，最大 60000。Pub/Sub 忽略 |
| `timestamp` | string | ❌ | ISO 8601，缺省自动生成 |

### 5.2 下行 MQTT Payload

bridge 转换后发布到 MQTT 的消息体：

```json
{
  "command_id": "cmd-20260708-001",
  "action":     "set",
  "params":     {"led": "on"},
  "timestamp":  "2026-07-08T10:00:00Z",
  "trace_id":   "br-a3f8c2d1-1720435200000"
}
```

### 5.3 ACK 回执

RPC 同步返回和 Pub/Sub ACK 发布使用相同格式：

```json
{
  "command_id":   "cmd-20260708-001",
  "error_code":   0,
  "error_msg":    "ok",
  "device_type":  "lora",
  "device_id":    "lora-node-01",
  "ack_level":    "bridge",
  "trace_id":     "br-a3f8c2d1-1720435200000"
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `command_id` | string | 匹配原始命令 |
| `error_code` | int | `0` = 成功，非 `0` = 失败（见 §8） |
| `error_msg` | string | 人类可读错误描述 |
| `device_type` | string | 目标设备类型 |
| `device_id` | string | 目标设备 ID |
| `ack_level` | string | `"bridge"` = MQTT 发布结果；`"device"` = 设备执行结果（预留） |
| `trace_id` | string | 全链路追踪 ID |

---

## 6. MQTT Topic 规范

### 6.1 上行 Topic

按 `overall_experiment_rules(1).md` §4.2 统一规范：

```
bridge/uplink/{source}/{device_id}/{action}
```

| 层级 | 说明 | 允许值 |
|------|------|------|
| `bridge` | 桥接系统固定前缀 | `bridge` |
| `uplink` | 方向 | `uplink` |
| `{source}` | 数据来源 | `lora`、`zigbee`、`generic` |
| `{device_id}` | 设备 ID | 如 `lora_env_01` |
| `{action}` | 消息类型 | `data`、`status`、`error` |

**规范订阅（7 个 topic）：**

| Topic | Adapter | 用途 |
|------|------|------|
| `bridge/uplink/lora/+/data` | `lora_adapter` | LoRa/LoRaWAN 数据 |
| `lora/+/up` | `lora_adapter` | LoRaWAN 网关兼容 topic |
| `bridge/uplink/zigbee/+/data` | `zigbee_adapter` | Zigbee 数据 |
| `zigbee/+/report` | `zigbee_adapter` | Zigbee2MQTT 兼容 topic |
| `bridge/uplink/generic/+/data` | `generic_adapter` | 通用设备数据 |
| `bridge/uplink/generic/+/status` | `generic_adapter` | 通用设备状态 |
| `bridge/uplink/generic/+/error` | `generic_adapter` | 通用错误上报 |

### 6.2 下行 Topic

```
bridge/downlink/{device_type}/{device_id}/{action}
```

示例：
```
bridge/downlink/lora/lora-node-01/set
bridge/downlink/zigbee/zb-sensor-01/config
```

支持 per-device-type 前缀覆盖：

```yaml
mqtt:
  downlink_topic_prefix: "bridge/downlink"
  downlink_topic_prefixes:
    lora:   "lora/cmd"
    zigbee: "zigbee/cmd"
```

覆盖后：
```
lora/cmd/lora-node-01/set
zigbee/cmd/zb-sensor-01/config
```

设备级 topic 模板（`mqtt_topic_template`）预留，待 LoRaWAN 网关真实下行 topic 格式确认后激活。

---

## 7. 设备注册表

### 7.1 合并方案

合并后设备注册表兼具上行自动发现和下行查询校验两种职责。**上行 `upsert()` 自动注册设备，下行 `lookup()` 直接查询，不再需要手动维护 `devices.yaml` 白名单。**

```
上行 MQTT 消息 → adapter.parse() → registry.upsert(report) → 设备自动注册
下行 VSOA 命令 → registry.lookup(device_id) → 存在则放行，不存在则 2203
```

### 7.2 数据模型

```python
@dataclass
class DeviceInfo:
    """设备注册表中的设备条目（合并上行自动填充 + 下行校验需求）。"""
    # --- 核心标识 ---
    device_id: str                      # 唯一标识
    type: str                           # "lora" | "zigbee" | "generic" | "multi"
    status: str                         # "online" | "offline" | "error"

    # --- 上行填充 ---
    name: str                           # 人类可读名称
    source: str                         # "lora" | "zigbee" | "generic" | "mqtt"
    adapter: str                        # 使用的适配器名称
    timestamp: int                      # 最后上报时间（Unix epoch 毫秒）
    registered_at: int                  # 首次注册时间
    report_count: int                   # 上报次数
    last_topic: str                     # 最后收到的 MQTT topic

    # --- 传感器数据（可选） ---
    temperature: float | None
    humidity: float | None
    pressure: float | None
    unit: str | None
    battery: int | None
    signal: int | None
    snr: float | None

    # --- 下行预留 ---
    mqtt_topic_template: str | None     # 设备级 topic 模板（预留）
    dev_eui: str | None                 # LoRaWAN DevEUI（预留）
```

### 7.3 DeviceRegistry 接口

```python
class DeviceRegistry:
    """线程安全的设备注册表（合并上行写入 + 下行查询）。"""

    def __init__(self, max_devices: int = 64):
        """初始化注册表，设置最大容量。"""

    def upsert(self, report: UplinkReport) -> tuple[DeviceInfo | None, bool]:
        """插入或更新设备。返回 (设备条目, 是否新注册)。注册表满时返回 (None, False)。"""

    def lookup(self, device_id: str) -> DeviceInfo | None:
        """按 device_id 查询。O(1)。"""

    def list_all(self) -> list[DeviceInfo]:
        """列出所有设备。"""

    def list_by_type(self, device_type: str) -> list[DeviceInfo]:
        """按类型过滤。"""

    @property
    def count(self) -> int:
        """已注册设备总数。"""
```

### 7.4 合并前后对比

| | 合并前 | 合并后 |
|------|------|------|
| **上行注册表** | 独立 `DeviceRegistry`，自动填充 | → 合并为一个 |
| **下行注册表** | 独立 `DeviceRegistry`，静态 YAML 白名单 | → 同上，上行自动注册后下行即可查询 |
| **devices.yaml** | 手动维护白名单 | **不再需要**（或降级为启动预装的可选数据源） |
| **下行校验** | `lookup(device_id)` 查 YAML | `lookup(device_id)` 查合并注册表 |

> ⚠️ **过渡策略：** 如果启动时没有任何上行数据（设备注册表为空），所有下行命令都会返回 2203。这是预期行为——设备必须先上报，桥接才知道它的存在。如需启动预装设备，可保留 `devices.yaml` 作为注册表的初始化数据源（`seed_file`），但不再是校验的唯一依据。

---

## 8. 错误码规范

### 8.1 码段分配

| 码段 | 范围 | 负责方 |
|------|------|:------:|
| `0` | 0 | 成功 |
| `1xxx` | 1000–1999 | 上行 |
| `2xxx` | 2000–2999 | 下行 |
| `9xxx` | 9000–9999 | 共用（预留） |

### 8.2 上行错误码（1xxx）

| 错误码 | 名称 | 说明 | 状态 |
|:------:|------|------|:--:|
| `0` | `OK` | 成功 | ✅ |
| `1001` | `ERR_UP_DEVICE_NOT_FOUND` | 查询的 device_id 在注册表中不存在 | ✅ v1.0 |
| `1002` | `ERR_UP_INVALID_URL` | VSOA URL 格式不正确（如 `/device/x/y/z`） | ✅ v1.0 |
| `1003` | `ERR_UP_REGISTRY_FULL` | 设备注册表已满（达到 max_devices） | ✅ v1.0 |

> 注意：设备侧上报的 error payload 字段（`DEVICE_OFFLINE`、`RS485_TIMEOUT` 等）是 MQTT payload 中的字符串，不属于 bridge VSOA 错误码体系。这些字符串由设备小组定义，bridge 原样存入设备注册表 `status` 字段和错误 payload。

### 8.3 下行错误码（2xxx）

#### 2xxx — 命令校验错误

| 错误码 | 名称 | 说明 | 状态 |
|:------:|------|------|:--:|
| `2001` | `ERR_CMD_INVALID_JSON` | 命令 payload 不是合法 JSON | ✅ |
| `2002` | `ERR_CMD_MISSING_FIELD` | 缺少必填字段 | ✅ |
| `2003` | `ERR_CMD_UNKNOWN_DEVICE_TYPE` | `device_type` 不在枚举值中 | ✅ |
| `2004` | `ERR_CMD_UNKNOWN_ACTION` | `action` 不在枚举值中 | ✅ |
| `2005` | `ERR_CMD_TIMEOUT_EXCEEDED` | `timeout_ms` 超过 `max_timeout_ms` 限制 | ✅ |
| `2006` | `ERR_CMD_DUPLICATE_ID` | `command_id` 在 TTL 窗口内重复 | ✅ |

#### 21xx — MQTT 发布错误

| 错误码 | 名称 | 说明 | 状态 |
|:------:|------|------|:--:|
| `2101` | `ERR_MQTT_NOT_CONNECTED` | MQTT Broker 未连接 | ✅ |
| `2102` | `ERR_MQTT_PUBLISH_FAILED` | MQTT publish 调用失败（含重试耗尽） | ✅ |
| `2103` | `ERR_MQTT_QOS_NOT_SUPPORTED` | QoS 级别不被支持 | ✅ |

#### 22xx — MQTT 发布结果 / 设备错误

| 错误码 | 名称 | 说明 | 状态 |
|:------:|------|------|:--:|
| `2201` | `ERR_MQTT_PUBLISH_TIMEOUT` | MQTT publish 等待 PUBACK 超时（含重试耗尽） | ✅ |
| `2202` | `ERR_MQTT_PUBLISH_REJECTED` | MQTT publish 被 Broker 拒绝 | 预留 |
| `2203` | `ERR_DEVICE_NOT_FOUND` | 目标设备不在注册表中 | ✅ |
| `2204` | `ERR_DEVICE_OFFLINE` | 目标设备当前离线 | 预留 |

#### 23xx — 内部错误

| 错误码 | 名称 | 说明 | 状态 |
|:------:|------|------|:--:|
| `2301` | `ERR_INTERNAL` | 未分类的内部错误 | ✅ |
| `2302` | `ERR_CONFIG_INVALID` | 配置文件校验失败 | ✅ |
| `2303` | `ERR_VSOA_DISCONNECTED` | VSOA 连接断开 | ✅ |
| `2304` | `ERR_QUEUE_FULL` | 待处理命令队列已满 | ✅ |

---

## 9. traceId 全链路追踪

### 9.1 生成规则

```
格式: br-{8位hex随机}-{毫秒时间戳}
示例: br-a3f8c2d1-1720435200000
```

- `br` — bridge 前缀
- `8位hex随机` — `secrets.token_hex(4)`，碰撞概率极低
- `毫秒时间戳` — `int(time.time() * 1000)`

### 9.2 注入位置

| 注入位置 | 说明 |
|----------|------|
| bridge 日志 | `[trace=br-xxx]` 前缀 |
| 下行 ACK | `trace_id` 字段 |
| 下行 MQTT payload | `trace_id` 字段，设备上行时回传可串联全链路 |

### 9.3 全链路串联

```
业务层 → VSOA → bridge(traceId生成) → MQTT → 设备
                                                 │
设备上行 MQTT(带回trace_id) → bridge上行 → VSOA → 业务层
```

设备侧如支持回传 `trace_id`，在上行 MQTT payload 中携带 `trace_id` 字段即可串联全链路。

---

## 10. 幂等去重

### 10.1 行为规范

| 场景 | 行为 |
|------|------|
| 新 `command_id` | 标记，放行 |
| 相同 `command_id` 在 TTL 内重复 | 拒绝，返回 2006 |
| 相同 `command_id` 已过 TTL | 视为新命令（旧条目惰性淘汰） |
| 超出 `max_size` | 淘汰最老条目 |

### 10.2 配置

```yaml
downlink:
  command:
    dedup:
      enabled: true
      ttl_seconds: 300          # 幂等窗口
      max_size: 10000           # 最大缓存条目
```

---

## 11. 重连与重试机制

### 11.1 MQTT 重连

- 自动重连，默认无限重试
- 重连间隔可配置

### 11.2 VSOA Pub/Sub Client 重连

| 阶段 | 策略 |
|------|------|
| **首次连接** | 启动时循环尝试，最多 `max_retries` 次，全部失败则退出 |
| **后续断连** | `run_forever()` 内部检测断开 → 指数退避重连 → 重新订阅 |

退避公式：`interval_ms * backoff_multiplier^attempt`

```yaml
vsoa:
  reconnect:
    enabled: true
    interval_ms: 3000
    max_retries: 10
    backoff_multiplier: 2.0
```

### 11.3 VSOA RPC Server

被动监听端口，无需重连。端口被占用时启动失败。

### 11.4 下行 MQTT Publish 重试

仅 RPC 通道启用，Pub/Sub 通道为单次 best-effort。

```
attempt 0: 立即执行  (timeout 10s)
   等待 500ms
attempt 1: 重试      (timeout 10s)
   等待 1000ms
attempt 2: 重试      (timeout 10s)
   等待 2000ms
attempt 3: 重试      (timeout 10s) → 全部失败 → 返回 (False, error_code, 4)
```

```yaml
downlink:
  command:
    retry:
      max_retries: 3            # 最多重试次数（含首次共 4 次）
      backoff_base_ms: 500      # 退避基值
```

---

## 12. 配置文件规范

### 12.1 完整 config.yaml

```yaml
# ============================================================
# MQTT ↔ VSOA 桥接组件 — 配置文件
# ============================================================

# ---------- 桥接组件信息 ----------
bridge:
  name: "MQTT-VSOA Bridge"
  version: "1.0.0"

# ---------- VSOA 连接 ----------
vsoa:
  # --- VSOA Server（bridge 内嵌，端口 3001）---
  server:
    bind_host: "127.0.0.1"
    port: 3001

  # --- Pub/Sub Client（连接业务层 VSOA Server，订阅 /ctrl/cmd）---
  pubsub_client:
    server_url: "vsoa://127.0.0.1:3000"
    subscribe_urls:
      - "/ctrl/cmd"
    ack_publish_url: "/ctrl/ack"

  # --- VSOA 断连重试 ---
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
  client_id: "bridge-v1"
  qos: 1
  retained: false
  reconnect:
    enabled: true
    interval_ms: 3000
    max_retries: 0                        # 0 = 无限重试

  # --- 上行订阅 topic ---
  uplink_topics:
    - "bridge/uplink/lora/+/data"
    - "bridge/uplink/zigbee/+/data"
    - "bridge/uplink/generic/+/data"
    - "bridge/uplink/generic/+/status"
    - "bridge/uplink/generic/+/error"
    - "lora/+/up"
    - "zigbee/+/report"

  # --- 下行发布 topic ---
  downlink_topic_prefix: "bridge/downlink"
  downlink_topic_prefixes: {}
    # lora:   "lora/cmd"
    # zigbee: "zigbee/cmd"

# ---------- 上行处理 ----------
uplink:
  tcp_inject_port: 9090                   # TCP 离线注入端口
  max_devices: 64                         # 内存设备表最大容量
  max_json_len: 8192                      # 单条 JSON 最大长度（字节）
  max_topic_len: 192                      # Topic 最大长度（字节）
  max_device_id_len: 64                   # 设备 ID 最大长度（字节）
  adapters: ["lora", "zigbee", "generic"] # 启用的适配器列表

# ---------- 下行命令处理 ----------
downlink:
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

# ---------- 日志 ----------
logging:
  level: "INFO"
  format: "[%(asctime)s] [%(levelname)s] %(message)s"
  date_format: "%Y-%m-%d %H:%M:%S"
  file: "logs/bridge.log"
```

### 12.2 合并前后 config 对应关系

| 合并后路径 | 来源 |
|------|------|
| `vsoa.server` | 合并自 `bridge-uplink/vsoa.server` + `bridge/vsoa.rpc_server` |
| `vsoa.pubsub_client` | 来自 `bridge/vsoa.pubsub_client` |
| `vsoa.reconnect` | 来自 `bridge/vsoa.reconnect`（合并版统一退避参数） |
| `mqtt.broker` ~ `mqtt.reconnect` | 合并自两边的共用 MQTT 连接参数 |
| `mqtt.uplink_topics` | 来自 `bridge-uplink/mqtt.topics` |
| `mqtt.downlink_topic_prefix` + `mqtt.downlink_topic_prefixes` | 来自 `bridge/mqtt.topic_prefix` + `bridge/mqtt.topic_prefixes` |
| `uplink.*` | 来自 `bridge-uplink/uplink.*` |
| `downlink.command.*` | 来自 `bridge/command.*` |
| `logging` | 合并自两边（`file` 改为 `bridge.log`） |

**移除的配置项（合并后不再需要）：**
- `bridge/vsoa.rpc_server.endpoint` — 合并版 `/bridge/send_command` 固定内置
- `bridge-uplink/vsoa.server.name` / `version` — 合并版用 `bridge.name` / `bridge.version`
- `bridge/device_registry.source` + `devices.yaml` — 设备注册表改为上行自动填充

---

## 13. 目录结构

```
bridge-merged/
├── config.yaml                  # 合并后统一配置文件
├── doc/
│   ├── spec.md                  # 本文档
│   ├── api.md                   # 接口文档（业务层开发者参考）
│   ├── mqtt_topic_spec.md       # MQTT Topic 规范
│   └── schema.md                # 上行数据模型文档
├── src/
│   ├── __init__.py
│   ├── main.py                  # 统一入口：同时启动上行 + 下行
│   ├── config.py                # 统一配置加载（dataclass）
│   ├── error_codes.py           # 统一错误码（1xxx + 2xxx + 9xxx）
│   ├── trace_id.py              # traceId 生成器（共用）
│   ├── device_registry.py       # 合并设备注册表（upsert + lookup）
│   ├── mqtt_handler.py          # 统一 MQTT 客户端（subscribe + publish）
│   ├── uplink/
│   │   ├── __init__.py
│   │   ├── vsoa_server.py       # 上行 VSOA 查询端点 + 发布通知
│   │   ├── tcp_inject.py        # TCP 9090 JSON Lines 注入
│   │   └── adapters/
│   │       ├── __init__.py      # select_adapter()
│   │       ├── base.py          # Adapter ABC + UplinkReport + 解析辅助
│   │       ├── lora.py          # LoRa/LoRaWAN 适配器
│   │       ├── zigbee.py        # Zigbee2MQTT 适配器
│   │       └── generic.py       # 通用适配器（兜底）
│   └── downlink/
│       ├── __init__.py
│       ├── rpc_server.py        # 下行 RPC handler + _publish_with_retry
│       ├── pubsub_handler.py    # 下行 Pub/Sub 订阅 + ACK + VSOA 重连
│       ├── command.py           # 命令校验 + MQTT 消息/ACK 构造（纯函数）
│       └── dedup.py             # 幂等去重缓存
├── tests/
│   ├── conftest.py
│   ├── uplink/
│   │   ├── test_adapters.py
│   │   ├── test_registry.py
│   │   └── test_integration.py
│   └── downlink/
│       ├── test_command.py
│       ├── test_registry.py
│       ├── test_dedup.py
│       └── test_integration.py
├── tools/
│   ├── mqtt_simulator.py        # MQTT 设备模拟器
│   ├── verify.py                # VSOA RPC 验证客户端
│   └── verify_e2e.py            # 端到端验证脚本
└── logs/
    └── bridge.log               # 运行时日志
```

### 13.1 模块关系

```
main.py
  ├─► config.py  ←── config.yaml
  │
  ├─► device_registry.py         ← 上行 upsert + 下行 lookup
  ├─► mqtt_handler.py            ← 上行 subscribe + 下行 publish（统一客户端，线程安全）
  │
  ├─► uplink/vsoa_server.py      ← 注册 7 个查询端点 + /device/update + /bridge/event 发布
  │     └─► device_registry.py
  │
  ├─► uplink/tcp_inject.py       ← TCP 9090 注入
  │     └─► uplink/adapters/     ← 适配器管道
  │
  ├─► downlink/rpc_server.py     ← /bridge/send_command handler
  │     ├─► command.py           ← validate + build_mqtt_message + build_ack
  │     ├─► dedup.py             ← check_and_mark
  │     ├─► device_registry.py   ← lookup
  │     └─► trace_id.py          ← generate_trace_id
  │
  └─► downlink/pubsub_handler.py ← /ctrl/cmd 订阅 + /ctrl/ack ACK + VSOA 重连
        ├─► command.py
        ├─► dedup.py
        ├─► device_registry.py
        └─► trace_id.py
```

### 13.2 合并变更清单

| 模块 | 变更类型 | 具体改动 |
|------|:--:|------|
| `main.py` | **合并** | 统一入口，同时启动上行+下行 |
| `config.py` | **合并** | 统一 dataclass 配置，加载合并后 config.yaml |
| `error_codes.py` | **合并** | 1xxx 上行 + 2xxx 下行合并到一个文件 |
| `device_registry.py` | **合并** | 合并上行 `DeviceData` + 下行 `DeviceInfo` 为统一 `DeviceInfo`，同时支持 `upsert()` 和 `lookup()` |
| `mqtt_handler.py` | **合并** | 统一客户端，同时支持 subscribe（上行 7 个 topic）+ publish（下行 topic） |
| `uplink/vsoa_server.py` | **改** | 改用 merge 后统一 VSOA Server（端口 3001），不再独立启动 |
| `uplink/tcp_inject.py` | 保留 | 不变 |
| `uplink/adapters/*` | 保留 | 不变，适配 `DeviceInfo` 新字段 |
| `downlink/rpc_server.py` | **改** | 复用统一 VSOA Server，`registry` 改为合并注册表，移除独立 `_publisher` 注入 |
| `downlink/pubsub_handler.py` | **改** | ACK 发布改用统一 VSOA Server（不再独立端口 3009），`registry` 改为合并注册表 |
| `downlink/command.py` | 保留 | 不变（纯函数，无状态） |
| `downlink/dedup.py` | 保留 | 不变 |
| `devices.yaml` | **移除** | 设备注册表改为上行自动填充 |
| `config.yaml` | **合并** | 见 §12.1 |

---

## 14. 启动流程

### 14.1 启动序列

```
main.py 启动:
  1. 加载 config.yaml
  2. 配置日志
  3. 初始化 DeviceRegistry（max_devices）
  4. 初始化 DedupCache（如启用）
  5. 创建 MQTTHandler（统一客户端）
  6. 连接 MQTT Broker → 订阅 7 个上行 topic
  7. 创建上行 VSOA 查询端点 + 下行 RPC handler → 注册到统一 VSOA Server
  8. 启动 VSOA Server（端口 3001，后台线程）
  9. 创建 VSOA Pub/Sub Client → 连接业务层 VSOA Server（:3000）
     → 订阅 /ctrl/cmd（首次连接重试 max_retries 次）
  10. 启动 TCP 9090 注入服务器（可选）
  11. 注入依赖（MQTT publish 函数 → RPC Server / PubSub Handler）
  12. 打印启动 banner
  13. 进入 VSOA Client 事件循环（带自动重连）
```

### 14.2 启动 Banner

```
================================================
[INFO] MQTT-VSOA Bridge v1.0.0 started
[INFO] VSOA Server          : 127.0.0.1:3001
[INFO]   RPC: /bridge/health, /adapter/list, /uplink/schema
[INFO]   RPC: /device/list, /device/all/data, /device/{id}/data
[INFO]   RPC: /bridge/send_command (downlink)
[INFO]   Pub: /device/update, /bridge/event, /ctrl/ack
[INFO] MQTT connected        : broker.emqx.io:1883
[INFO]   Uplink sub topics   : 7
[INFO]   Downlink pub prefix : bridge/downlink
[INFO] PubSub subscribed     : /ctrl/cmd → /ctrl/ack
[INFO] TCP inject            : 0.0.0.0:9090
[INFO] Device registry       : max 64 devices
================================================
```

### 14.3 优雅关闭

```
SIGINT/SIGTERM:
  1. PubSub Client 断开并停止
  2. TCP Inject Server 停止
  3. MQTT 断开
  4. VSOA Server 停止
  5. 记录 "bridge stopped" 日志
```

---

## 15. 验收标准

### 15.1 上行

| # | 验收项 | 通过条件 |
|---|--------|----------|
| 1 | MQTT 订阅 | 7 个 topic 订阅成功，日志可见 |
| 2 | Adapter 匹配 | LoRa/Zigbee/Generic payload 分别匹配到对应 adapter |
| 3 | 设备自动注册 | 新 device_id 首次上报 → registry.upsert() → 设备注册 |
| 4 | 设备更新 | 已有 device_id 再次上报 → registry.upsert() → report_count 递增 |
| 5 | VSOA 查询 | `/device/list`、`/device/{id}/data` 返回正确数据 |
| 6 | VSOA 通知 | `/device/update`、`/bridge/event` 发布正常 |
| 7 | TCP 9090 注入 | 离线模式下 JSON Lines 注入功能正常 |
| 8 | adapter parse 失败 | 非法 payload → 记录 warning，不崩溃 |

### 15.2 下行

| # | 验收项 | 通过条件 |
|---|--------|----------|
| 1 | RPC 命令 | `fetch("/bridge/send_command")` → 同步返回 ACK |
| 2 | Pub/Sub 命令 | `publish("/ctrl/cmd")` → subscribe `/ctrl/ack` 收到 ACK |
| 3 | 命令校验 | 缺字段 → 2002；错误 device_type → 2003；错误 action → 2004 |
| 4 | 设备注册表 | 已注册 device_id → 放行；未注册 → 2203 |
| 5 | 幂等去重 | 相同 command_id 在 TTL 内第 2 次 → 2006 |
| 6 | traceId | ACK、MQTT payload、日志 三者包含同一 trace_id |
| 7 | RPC 重试 | MQTT publish 失败 → 自动重试，全部失败 → 返回错误码 |
| 8 | VSOA 重连 | 业务层 VSOA Server 重启 → bridge 自动重连并恢复订阅 |

### 15.3 合并特有

| # | 验收项 | 通过条件 |
|---|--------|----------|
| 1 | 单进程双方向 | 一个 main.py 同时处理上行和下行 |
| 2 | 端口统一 | 只有 3001 一个 VSOA 端口对外，3009 不存在 |
| 3 | 注册表共用 | 上行自动注册的设备，下行命令可直接使用（无需手动配置 devices.yaml） |
| 4 | 配置统一 | 一个 config.yaml 覆盖上下行所有配置 |

---

## 16. 待决议事项

| # | 事项 | 状态 |
|---|------|:--:|
| 1 | 架构方案 | ✅ 合并版 spec 已确认 |
| 2 | 设备注册表合并 | ✅ 上行 upsert + 下行 lookup |
| 3 | ACK Server 合并（去 3009） | ✅ 统一走 3001 |
| 4 | MQTT topic 规范统一 | ✅ 依 `overall_experiment_rules(1).md` |
| 5 | 错误码 1xxx 段 | ✅ v1.0 定义 1001-1003 |
| 6 | 设备在线状态跟踪（is_online） | 📌 预留 — 需上行侧在线检测 |
| 7 | 设备级 topic 模板渲染 | 📌 等待 LoRaWAN 网关型号确认 |
| 8 | payload 转换层（JSON→hex） | 📌 等待网关 payload 格式确认 |
| 9 | 统一测试框架 | 📌 卢静旭负责 |
| 10 | 与辛澳翔联调 | 📌 spec 对齐后进行 |
