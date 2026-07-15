# MQTT ↔ VSOA 桥接组件（合并版）

> **版本:** v1.1  
> **作者:** 方宏波（下行）、辛澳翔（上行）  
> **日期:** 2026-07-15  
> **状态:** 开发中 — 162 单元测试通过，端到端验证通过

---

## 1. 项目简介

本组件是 **MQTT ↔ VSOA 双向桥接系统**，作为独立服务进程运行，在 MQTT 设备（LoRa / Zigbee / Generic 终端）与 VSOA 服务端之间建立双向通信通道。

```
┌──────────────┐     MQTT      ┌────────────────┐     VSOA      ┌──────────────┐
│  LoRa 设备   │ ◄──────────►  │   桥接组件      │ ◄──────────►  │  VSOA 服务端  │
│  Zigbee 设备  │              │                │               │  (业务层)     │
│  Generic 设备 │               │  上行: MQTT→VSOA│              │              │
└──────────────┘               │  下行: VSOA→MQTT│              └──────────────┘
                               └────────────────┘
```

**上行（Uplink）：** MQTT 设备数据 → adapter 解析 → 设备注册表 → VSOA 查询接口 + 发布通知  
**下行（Downlink）：** VSOA 命令 → 校验 → 注册表检查 → 幂等去重 → MQTT 控制消息下发

本仓库是 `bridge/`（下行 v3.0）与 `bridge-uplink/`（上行 v2.0）的**合并版本**，统一了配置、设备注册表、MQTT 客户端和 VSOA Server。

---

## 2. 目录结构

```
bridge-merged/
├── config.yaml                  # 统一配置文件
├── doc/
│   ├── spec.md                  # 技术规格说明书 v1.0
│   └── task.md                  # 开发任务清单 (17 个任务)
├── src/
│   ├── main.py                  # 统一入口：同时启动上行 + 下行
│   ├── config.py                # 统一配置加载（dataclass）
│   ├── error_codes.py           # 统一错误码（1xxx 上行 + 2xxx 下行）
│   ├── trace_id.py              # traceId 生成器（共用）
│   ├── device_registry.py       # 合并设备注册表（upsert + lookup，线程安全）
│   ├── mqtt_handler.py          # 统一 MQTT 客户端（subscribe + publish）
│   ├── uplink/
│   │   ├── vsoa_server.py       # 上行 VSOA 查询端点 + 发布通知
│   │   ├── tcp_inject.py        # TCP 9090 JSON Lines 注入（离线测试）
│   │   └── adapters/
│   │       ├── base.py          # Adapter 抽象基类 + UplinkReport 数据模型
│   │       ├── lora.py          # LoRa/LoRaWAN 适配器
│   │       ├── zigbee.py        # Zigbee2MQTT 适配器
│   │       └── generic.py       # 通用适配器（兜底）
│   └── downlink/
│       ├── rpc_server.py        # 下行 RPC handler（同步回执 + 自动重试）
│       ├── pubsub_handler.py    # 下行 Pub/Sub 订阅 + ACK + VSOA 重连
│       ├── command.py           # 命令校验 + MQTT 消息/ACK 构造（纯函数）
│       └── dedup.py             # 幂等去重缓存
├── tools/
│   ├── mqtt_monitor.py          # MQTT 全量监视（上行 + 下行）
│   ├── sim_device.py            # MQTT 设备模拟器（LoRa + Zigbee）
│   └── verify_e2e.py            # 端到端验证脚本（RPC + Pub/Sub + 注册表）
├── tests/
│   ├── uplink/
│   │   ├── test_adapters.py     # 适配器单元测试
│   │   ├── test_registry.py     # 注册表单元测试
│   │   └── test_integration.py  # 上行集成测试（需 live 服务，CI 中 skip）
│   └── downlink/
│       ├── test_command.py      # 命令校验单元测试
│       ├── test_dedup.py        # 幂等去重单元测试
│       ├── test_registry.py     # 注册表单元测试
│       ├── test_integration.py  # 下行集成测试（需 live 服务，CI 中 skip）
│       ├── mqtt_sub.py          # MQTT 订阅验证工具（仅下行）
│       └── verify.py            # 手动验证脚本（旧版，仅下行）
└── logs/
    └── bridge.log               # 运行时日志
```

---

## 3. 端口分配

| 端口 | 方向 | 协议 | 用途 |
|:----:|------|------|------|
| **3001** | 入站 | VSOA | bridge VSOA Server：上行 RPC 查询 + 下行 RPC 命令 + ACK/事件发布 |
| **3000** | 出站 | VSOA | 连接业务层 VSOA Server，订阅 `/ctrl/cmd`（Pub/Sub 命令通道） |
| **1883** | 出站 | MQTT | 连接 MQTT Broker（订阅 7 个上行 topic + 发布下行 topic） |
| **9090** | 入站 | TCP | JSON Lines 注入（离线测试，模拟 MQTT 上行消息） |

> ⚠️ 合并版已移除端口 3009（原下行独立 ACK Server）。ACK 发布统一走 3001 的 VSOA Server。

---

## 4. VSOA 接口

### RPC 查询端点（端口 3001）

| URL | 方法 | 用途 |
|------|:--:|------|
| `/bridge/health` | GET | 健康检查（服务名、状态、运行时间、设备数） |
| `/adapter/list` | GET | 适配器列表 |
| `/uplink/schema` | GET | 上行数据模型字段说明 |
| `/device/list` | GET | 所有设备摘要列表 |
| `/device/all/data` | GET | 所有设备完整数据 |
| `/device/{id}/data` | GET | 单设备完整数据 |
| `/device/{id}/status` | GET | 单设备状态 |
| `/bridge/send_command` | RPC | **下行命令入口**（同步回执） |

### 发布通知（Pub/Sub，端口 3001）

| URL | 触发时机 |
|------|------|
| `/device/update` | 每次设备注册/更新 |
| `/bridge/event` | 每次上行消息处理完成 |
| `/ctrl/ack` | 每次 Pub/Sub 下行命令处理完成（ACK 回执） |

---

## 5. 核心设计

### 5.1 双通道下行

| | RPC 通道 | Pub/Sub 通道 |
|--|----------|-------------|
| **调用方式** | `client.fetch("/bridge/send_command", ...)` | `server.publish("/ctrl/cmd", ...)` |
| **回执方式** | `fetch()` 同步返回 ACK | bridge publish `/ctrl/ack` 异步 ACK |
| **MQTT 重试** | ✅ 自动重试（指数退避） | 单次 best-effort |
| **适用场景** | 需同步确认的控制指令 | 高吞吐、发后即忘 |

### 5.2 设备注册表（合并）

上行 MQTT 消息自动注册设备，下行命令直接查询校验。**不再需要手动维护 `devices.yaml` 白名单。**

```
上行: MQTT → adapter.parse() → registry.upsert(report) → 设备自动注册
下行: VSOA 命令 → registry.lookup(device_id) → 存在则放行，不存在则 2203
```

### 5.3 下行命令处理流程

```
收到命令 cmd
  ├─ ⓪ 生成 traceId
  ├─ ① validate(cmd)              ← Schema 校验
  ├─ ② registry.lookup(device_id) ← 设备注册表检查
  ├─ ③ dedup.check_and_mark()     ← 幂等去重
  ├─ ④ build_mqtt_message()       ← 构造 MQTT topic + payload
  └─ ⑤ MQTT publish + 回执/ACK
```

### 5.4 上行处理管道

```
MQTT topic + payload
  ├─ ① select_adapter(topic, payload)   ← 按优先级匹配 adapter
  ├─ ② adapter.parse(topic, payload)    ← 规范化为 UplinkReport
  ├─ ③ registry.upsert(report)          ← 写入设备注册表
  └─ ④ vsoa.publish 通知               ← /device/update + /bridge/event
```

### 5.5 traceId 全链路追踪

格式 `br-{8位hex}-{毫秒时间戳}`（例：`br-a3f8c2d1-1720435200000`），注入到 ACK、MQTT payload 和日志中，贯穿全链路。

---

## 6. 快速开始

### 环境要求

- Python ≥ 3.10
- VSOA Python SDK v1.0.4
- paho-mqtt

### 安装依赖

```bash
pip install paho-mqtt pyyaml
# VSOA SDK 按内部文档安装
```

### 启动服务

```bash
cd bridge-merged
python src/main.py                    # 正常模式（连接 MQTT Broker）
python src/main.py --no-mqtt          # 离线模式（仅 TCP 9090 + VSOA）
python src/main.py --config my.yaml   # 使用自定义配置
```

### 运行测试

```bash
cd bridge-merged
python -m pytest tests/ -v            # 全部测试（162 单元通过，11 集成 skip）
python -m pytest tests/downlink/ -v   # 仅下行测试
python -m pytest tests/uplink/ -v     # 仅上行测试
```

### 端到端手动验证（4 终端）

模拟真实环境：通过公网 MQTT Broker 和本地 VSOA 验证双向桥接全链路。需要 4 个终端窗口。

```
┌──────────┬─────────────────────────────────────────────────────────────┐
│ 终端 1   │ MQTT 全量监视 — 观察上行 + 下行 MQTT 消息                    │
│ 终端 2   │ bridge 主服务 — 日志显示 adapter 匹配 / 设备注册 / RPC 处理  │
│ 终端 3   │ 验证主控 — VSOA 视角的 ACK + 测试结果                        │
│ 终端 4   │ 设备模拟器 — MQTT publish 模拟 LoRa / Zigbee 设备上报        │
└──────────┴─────────────────────────────────────────────────────────────┘
```

#### 启动顺序

**终端 1 — MQTT 全量监视**

订阅所有上下行 MQTT topic，能看到 Broker 上经过的每条消息。

```bash
cd bridge-merged
python tools/mqtt_monitor.py
```

**终端 2 — bridge 主服务**

```bash
cd bridge-merged
python src/main.py
```

确认启动 banner 中所有 `[OK]` 行出现：
- `[OK] MQTT connected` — 已连接公网 Broker
- `[OK] Unified VSOA Server listening on 127.0.0.1:3001` — VSOA 已监听
- `[OK] PubSub subscribed: ['/ctrl/cmd']` — 已订阅业务层命令

> **注意：** bridge 启动时如果没有业务层 VSOA Server (3000) 在运行，PubSub 连接会重试最多 10 次（约 30s）。终端 3 必须在 bridge 的 PubSub 连接耗尽重试前启动业务层 Server。**建议：终端 2 启动后立刻启动终端 3。**

**终端 4 — 设备模拟器**

模拟 LoRa 和 Zigbee 设备通过 MQTT 上报传感器数据。

```bash
cd bridge-merged
python tools/sim_device.py
```

在交互提示符下注册预设设备：
```
sim> auto
```

这会上报 lora-node-01 和 zb-sensor-01 两个设备，bridge 收到后会触发上行管道。

**终端 3 — 端到端验证**

```bash
cd bridge-merged
python tools/verify_e2e.py
```

脚本自动完成：
1. 启动业务层 VSOA Server (port 3000)，bridge 的 PubSub Client 连上来订阅 `/ctrl/cmd`
2. 连接 bridge VSOA Server (port 3001) — RPC + ACK/事件 订阅
3. 显示健康检查和当前设备列表
4. **提示你去终端 4 注册设备** — 输入 `auto` 后按 Enter
5. 自动运行 5 阶段测试套件

#### 验证阶段

```
阶段1: 上行 — MQTT 设备上报 → adapter 解析 → 注册表 upsert → VSOA 查询
阶段2: 下行 RPC — fetch("/bridge/send_command") → 同步 ACK (code=0, 含 traceId)
阶段3: 下行 Pub/Sub — publish("/ctrl/cmd") → bridge 处理 → /ctrl/ack 异步 ACK
阶段4: 注册表共用 — 上行注册后下行直接可用 (无 devices.yaml)，未注册 → 2203
阶段5: 边界情况 — 校验失败(2002/2003)、幂等去重(2006)、traceId 格式
```

#### 全链路数据流

```
[终端4: sim_device]
  │  MQTT publish ── bridge/uplink/lora/lora-node-01/data
  ▼
[公网 MQTT Broker broker.emqx.io]
  │  ┌─ 终端1 (mqtt_monitor) 可观测到原始 MQTT 消息
  ▼
[终端2: bridge ── 上行管道]
  │  ① MQTT on_message 收到上行数据
  │  ② lora_adapter.parse() → UplinkReport
  │  ③ registry.upsert(report) → 设备自动注册
  │  ④ _vsoa_publish("/device/update", ...) ──────────────────┐
  │     _vsoa_publish("/bridge/event", ...)  ──────────────────┤
  ▼                                                             │
[终端2: bridge ── VSOA Server (3001)]                           │
  │                                                             ▼
  │  终端3 (verify_e2e) 订阅了以上 3 个 URL: ◄──────────────────┘
  │    · /device/update  — 收到设备完整数据 (含温湿度)
  │    · /bridge/event   — 收到 "data_received" 事件
  │    · /ctrl/ack       — 收到 PubSub 命令的 ACK 回执
  │
  ├── 下行 RPC ──────────────────────────────────────────────
  │  终端3: fetch("/bridge/send_command", cmd) ──▶ bridge:3001
  │        ◀── cli.reply(seqno, ACK) 同步返回
  │
  └── 下行 Pub/Sub ──────────────────────────────────────────
      终端3: biz_server.publish("/ctrl/cmd", cmd) ──▶ bridge 订阅
      终端3: ◀── bridge publish /ctrl/ack (通过 3001)
```

#### 交叉验证清单

验证通过时，各终端应观察到：

| 终端 | 应看到 |
|------|--------|
| **终端1** (MQTT 监视) | `[UPLINK] bridge/uplink/lora/lora-node-01/data`、`[UPLINK] bridge/uplink/zigbee/zb-sensor-01/data`、`[DOWNLINK] bridge/downlink/lora/lora-node-01/set` (含 trace_id) |
| **终端2** (bridge 日志) | `[UPLINK] registered source=lora ... device=lora-node-01`、`[RPC] ... -> MQTT OK`、`[PubSub] ... -> MQTT OK`、`[PubSub] ACK sent: cmd_id=...` |
| **终端3** (验证结果) | `✓ 设备注册表非空`、`✓ RPC LoRa set → code=0`、`✓ Pub/Sub LoRa set → ACK code=0`、`✓ 幂等去重: 第2次 code=2006` |
| **终端4** (设备模拟器) | `[UPLINK(LoRa)] bridge/uplink/lora/lora-node-01/data`、`[UPLINK(Zigbee)] bridge/uplink/zigbee/zb-sensor-01/data` |

#### 预期结果示例

终端 3 全部通过时的输出：

```
  ✓ 设备注册表非空 — 当前 2 个设备
  ✓ lora-node-01 已注册
  ✓ zb-sensor-01 已注册
  ✓ 单设备数据查询成功 (lora-node-01) — source=lora type=multi
  ✓ RPC LoRa set → code=0 — code=0 trace=br-...
  ✓   ACK 含 trace_id (br-格式) — br-...
  ✓   ack_level='bridge' — ack_level=bridge
  ✓ RPC Zigbee config → code=0
  ✓ Pub/Sub LoRa set → ACK code=0 — ...       ← 验证 PubSub ACK 可送达
  ✓   Pub/Sub ACK 含 trace_id
  ✓ Pub/Sub bad cmd → ACK code=2003
  ✓ 未注册设备 → code=2203 (ERR_DEVICE_NOT_FOUND)
  ✓ 非法 device_type → code=2003
  ✓ 缺少必填字段 → code=2002
  ✓ 幂等去重: 第2次 code=2006
  ✓ traceId 格式: br-{8位hex}-{毫秒时间戳}

  通过: 17  /  失败: 0  /  总计: 17
```

### 离线模式验证（无需 MQTT Broker）

如果无法连接公网 MQTT Broker，使用 `--no-mqtt` 模式，通过 TCP 9090 注入模拟上行：

```bash
# 终端 2: 离线模式启动 bridge
python src/main.py --no-mqtt

# 终端 4: 通过 TCP 9090 注入上行数据
echo '{"cmd":"mqtt_message","topic":"bridge/uplink/lora/test-01/data","payload":{"temperature":23.5,"humidity":60}}' | nc 127.0.0.1 9090

# 终端 3: VSOA RPC 命令仍然可用
python tools/verify_e2e.py
```

> 注意：`--no-mqtt` 模式下 MQTT 下行 publish 无法真正发出，但 RPC ACK、校验、注册表、去重等全部正常工作。

---

## 7. 配置说明

参见 `config.yaml`，主要配置段：

| 段 | 说明 |
|------|------|
| `bridge` | 组件名与版本 |
| `vsoa.server` | VSOA Server 绑定地址与端口（3001） |
| `vsoa.pubsub_client` | 业务层 VSOA 连接 + `/ctrl/cmd` 订阅 + `/ctrl/ack` 发布 |
| `vsoa.reconnect` | VSOA 断连重试参数（指数退避） |
| `mqtt` | MQTT Broker 连接参数 + 上行订阅 topic + 下行发布前缀 |
| `uplink` | TCP 注入端口、设备数上限、适配器列表 |
| `downlink.command` | 超时、去重、重试参数 |
| `logging` | 日志级别与输出 |

---

## 8. 开发状态

| 项目 | 状态 |
|------|:--:|
| 单元测试（162 条） | ✅ 全部通过 |
| 集成测试（11 条） | ⏸️ 需 live 服务（CI 中 skip） |
| 统一入口 main.py | ✅ 已实现 |
| 合并配置 | ✅ 已实现 |
| 合并错误码 | ✅ 已实现（1xxx + 2xxx） |
| 合并设备注册表 | ✅ 已实现（upsert + lookup） |
| 合并 MQTT Handler | ✅ 已实现 |
| 端口 3009 移除 | ✅ 已完成 |
| VSOA 断连重连 | ✅ 已实现 |
| VSOA publish (ACK/事件) | ✅ 已修复 (v1.1: vsoa.URL bug) |
| RPC 通道端到端 | ✅ 验证通过 |
| Pub/Sub 通道端到端 | ✅ 验证通过 |
| 注册表共用 | ✅ 验证通过 |
| 对接真实设备 | 📌 待进行 |

详见 `doc/task.md`（17 个任务清单）和 `doc/spec.md`（技术规格说明书）。

---

## 9. 相关文档

- `doc/spec.md` — 技术规格说明书 v1.0（架构、接口、数据模型、错误码）
- `doc/task.md` — 开发任务清单（17 个任务，含验收标准）
- `../overall_experiment_rules(1).md` — 总体实验规范（上行数据模型、MQTT Topic）
- `../bridge/doc/api.md` — 业务层接口文档
