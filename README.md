# MQTT ↔ VSOA 桥接组件

> **版本:** v2.0  
> **作者:** 方宏波（下行）、辛澳翔（上行）  
> **日期:** 2026-07-20  
> **状态:** 开发中 — 端到端验证通过

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

---

## 2. 目录结构

```
bridge-merged/
├── config.yaml                  # 统一配置文件
├── mqtt_monitor.py              # GUI 监视器（Tkinter 界面，见 §3.2）
├── README.md
├── doc/
│   ├── spec.md                  # 技术规格说明书
│   └── task.md                  # 开发任务清单
├── src/
│   ├── main.py                  # 统一入口：同时启动上行 + 下行
│   ├── config.py                # 统一配置加载
│   ├── error_codes.py           # 统一错误码（1xxx 上行 + 2xxx 下行）
│   ├── trace_id.py              # traceId 生成器
│   ├── device_registry.py       # 合并设备注册表（线程安全）
│   ├── mqtt_handler.py          # 统一 MQTT 客户端
│   ├── uplink/
│   │   ├── vsoa_server.py       # 上行 VSOA 查询端点 + 发布通知
│   │   ├── tcp_inject.py        # TCP 9090 JSON Lines 注入（离线测试）
│   │   └── adapters/
│   │       ├── base.py          # Adapter 抽象基类
│   │       ├── lora.py          # LoRa/LoRaWAN 适配器
│   │       ├── zigbee.py        # Zigbee2MQTT 适配器
│   │       └── generic.py       # 通用适配器（兜底）
│   └── downlink/
│       ├── rpc_server.py        # 下行 RPC handler（同步回执）
│       ├── pubsub_handler.py    # 下行 Pub/Sub 订阅 + ACK
│       ├── command.py           # 命令校验 + MQTT 消息构造（纯函数）
│       └── dedup.py             # 幂等去重缓存
├── tools/
│   ├── mqtt_monitor.py          # MQTT CLI 监视器（终端版，轻量替代）
│   ├── mqtt_test.py             # MQTT 连接测试（订阅全部 topic）
│   ├── sim_device.py            # MQTT 设备模拟器
│   ├── send_downlink.py         # 下行命令发送脚本（RPC + Pub/Sub）
│   ├── vsoa_monitor.py          # VSOA 事件监视器（ACK + 设备注册）
│   ├── verify_e2e.py            # 端到端验证脚本
│   ├── start_terminals.ps1      # 一键启动多终端（开发用）
│   └── _test_publish.py         # VSOA publish 线程安全测试
├── tests/
│   ├── downlink/
│   │   ├── test_command.py      # 命令校验单元测试
│   │   ├── test_dedup.py        # 幂等去重单元测试
│   │   ├── test_registry.py     # 设备注册表单元测试
│   │   ├── test_integration.py  # 下行集成测试
│   │   ├── mqtt_sub.py          # MQTT 订阅验证
│   │   └── verify.py            # 手动验证脚本
│   └── uplink/
│       ├── conftest.py          # pytest fixtures
│       ├── test_adapters.py     # 适配器单元测试
│       ├── test_registry.py     # 设备注册表单元测试
│       └── test_integration.py  # 上行集成测试
└── logs/
    └── bridge.log               # 运行时日志
```

> ⚠️ **已废弃文件：** `src/downlink/main.py` 和 `src/uplink/main.py` 是合并前的独立入口，依赖的模块已移至 `src/` 级别，无法独立运行。统一入口为 `src/main.py`。根目录 `mqtt_bridge.py`、`mqtt_receiver.py` 为旧版脚本，已不再使用。

---

## 3. 架构

### 3.1 bridge/main.py（桥接服务进程）

独立服务进程，负责所有桥接逻辑。对外暴露：

| 端口 | 协议 | 用途 |
|:----:|------|------|
| **3001** | VSOA | bridge VSOA Server：上行 RPC 查询 + 下行 RPC 命令 + ACK/事件发布 |
| **3000** | VSOA | 连接业务层 VSOA Server，订阅 `/ctrl/cmd`（Pub/Sub 下行命令） |
| **1883** | MQTT | 连接 MQTT Broker（订阅上行 topic + 发布下行 topic） |
| **9090** | TCP | JSON Lines 注入（离线测试） |

### 3.2 mqtt_monitor.py（GUI 监视器，根目录）

**纯展示 + 下行命令发送客户端**，所有桥接逻辑委托给 `src/main.py`。

> 另提供 `tools/mqtt_monitor.py`（CLI 终端版），功能较轻量，仅做 MQTT 订阅打印，不含 GUI 和 VSOA 交互。

```
mqtt_monitor.py (GUI, 根目录)
  │
  ├─ MQTT Client ──→ broker（订阅展示，不处理）
  │
  ├─ VSOA Client ──→ bridge:3001 RPC /bridge/send_command（下行）
  │
  ├─ VSOA Client ──→ business_server:3000 datagram /ctrl/cmd（下行）
  │                     └── bridge 订阅 /ctrl/cmd → 处理 → MQTT
  │
  └─ VSOA 事件监听 ──→ 订阅 bridge:3001 的 VSOA 发布
                         · /device/update — 上行设备注册/更新
                         · /bridge/event  — 上行数据到达
                         · /ctrl/ack      — 下行 Pub/Sub ACK 回执
```

**GUI 不实现任何桥接逻辑**：没有 DeviceRegistry、没有上行 adapter、没有 MQTT 发布。所有转换由 bridge 完成，GUI 通过订阅 bridge 的 VSOA 发布来证明桥接成功。

---

## 4. VSOA 接口

### RPC 查询端点（端口 3001）

| URL | 方法 | 用途 |
|------|:--:|------|
| `/bridge/health` | command | 健康检查 |
| `/adapter/list` | command | 适配器列表 |
| `/uplink/schema` | command | 上行数据模型字段说明 |
| `/device/list` | command | 所有设备摘要列表 |
| `/device/all/data` | command | 所有设备完整数据 |
| `/device/{id}/data` | command | 单设备完整数据 |
| `/device/{id}/status` | command | 单设备状态 |
| `/bridge/send_command` | command | **下行命令入口（同步 RPC 回执）** |

### VSOA 发布通知

| URL | 触发时机 | 方向 |
|------|------|:--:|
| `/device/update` | 每次设备注册/更新 | 上行 |
| `/bridge/event` | 每次上行消息处理完成 | 上行 |
| `/ctrl/ack` | 每次 Pub/Sub 下行命令处理完成 | 下行 |

---

## 5. 双通道下行

| | RPC | Pub/Sub |
|--|------|---------|
| **调用方式** | `client.fetch("/bridge/send_command", ...)` | `client.datagram("/ctrl/cmd", ...)` |
| **阻塞特性** | 同步阻塞，等待回执 | 异步，发完即返回 |
| **回执方式** | `fetch()` 同步返回 | bridge publish `/ctrl/ack` 异步通知 |
| **超时支持** | ✅ per-command timeout | ❌ |
| **适用场景** | 单设备精准控制，需要即时结果 | 批量下发、状态同步 |

---

## 6. 快速开始

### 环境要求

- Python ≥ 3.10
- VSOA Python SDK v1.0.4
- paho-mqtt、pyyaml

```bash
pip install paho-mqtt pyyaml
# VSOA SDK 按内部文档安装
```

### 启动

**终端 1 — bridge 主服务**

```bash
cd bridge-merged
python src/main.py
```

**终端 2 — GUI 监视器**

```bash
cd bridge-merged
python mqtt_monitor.py
```

> 也可以使用 CLI 版轻量监视器：`python tools/mqtt_monitor.py`

### GUI 使用

1. **MQTT 消息标签页** — 实时展示所有 MQTT 上下行消息
2. **VSOA 桥接事件标签页** — 展示 bridge 发布的 VSOA 通知，证明桥接正常工作
3. **RPC 发送** — 填写 JSON 命令 → 同步拿到回执（error_code + trace_id）
4. **发布 /ctrl/cmd** — Pub/Sub 异步下行，回执出现在 VSOA 事件标签页

### 运行测试

```bash
cd bridge-merged
python -m pytest tests/ -v            # 全部单元测试
python -m pytest tests/downlink/ -v   # 仅下行测试
python -m pytest tests/uplink/ -v     # 仅上行测试
```

### 端到端验证

```bash
# 终端 1: bridge
python src/main.py

# 终端 2: GUI 监视器
python mqtt_monitor.py

# 终端 3: 设备模拟器（模拟 LoRa/Zigbee 上报）
python tools/sim_device.py

# 终端 4: 自动化验证脚本
python tools/verify_e2e.py
```

---

## 7. 配置说明

参见 `config.yaml`：

| 段 | 说明 |
|------|------|
| `vsoa.server` | VSOA Server 监听地址与端口（3001） |
| `vsoa.business_server` | 业务层 VSOA Server 配置（3000，GUI 可自动启动） |
| `vsoa.pubsub_client` | Pub/Sub 客户端：连接业务层 + 订阅 `/ctrl/cmd` |
| `vsoa.reconnect` | VSOA 断连重试参数 |
| `mqtt` | MQTT Broker 连接 + 上行订阅 topic + 下行发布前缀 |
| `uplink` | TCP 注入端口、设备上限、适配器列表 |
| `downlink.command` | 超时、去重、重试参数 |
| `chirpstack` | ChirpStack 下行格式（enabled + confirmed + fPort） |
| `logging` | 日志级别与输出 |

---

## 8. 开发状态

| 项目 | 状态 |
|------|:--:|
| 单元测试 | ✅ 全部通过 |
| 上行管道 (MQTT→VSOA) | ✅ |
| 下行 RPC (VSOA→MQTT) | ✅ |
| 下行 Pub/Sub (VSOA→MQTT) | ✅ |
| 设备注册表（上行自动注册 + 下行查询） | ✅ |
| 幂等去重 | ✅ |
| ChirpStack 下行格式 | ✅ |
| VSOA 断连自动重连 | ✅ |
| traceId 全链路追踪 | ✅ |
| GUI 监视器（mqtt_monitor.py） | ✅ |
| VSOA 事件监听（上下行桥接验证） | ✅ |
| 对接真实设备 | 📌 待进行 |

---

## 9. 相关文档

- `doc/spec.md` — 技术规格说明书
- `doc/task.md` — 开发任务清单
- `doc/api.md` — 业务层接口文档
- `config.yaml` — 完整配置文件
