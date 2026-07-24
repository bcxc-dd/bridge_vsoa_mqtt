# MQTT ↔ VSOA 桥接组件 + IoT 测试平台

> **版本:** v2.1 | **日期:** 2026-07-24
> **作者:** 方宏波（下行 + IoT 平台）、辛澳翔（上行）、卢静旭（测试框架）
> **状态:** 开发中 — 端到端验证通过

---

## 目录

- [1. 项目简介](#1-项目简介)
- [2. 架构概览](#2-架构概览)
- [3. 环境准备](#3-环境准备)
- [4. 快速复现](#4-快速复现)
- [5. 目录结构](#5-目录结构)
- [6. 数据模型](#6-数据模型)
- [7. VSOA 接口](#7-vsoa-接口)
- [8. IoT 测试平台](#8-iot-测试平台)
- [9. 运行测试](#9-运行测试)
- [10. 配置说明](#10-配置说明)
- [11. 常见问题](#11-常见问题)

---

## 1. 项目简介

本仓库包含两大组件：

| 组件 | 路径 | 说明 |
|------|------|------|
| **MQTT-VSOA 桥接** | `src/` | 独立服务进程，在 MQTT 设备（LoRa / Zigbee / WiFi）与 VSOA 服务端之间双向通信 |
| **IoT 测试平台** | `iot_test_platform/` | Web 面板，统一展示设备数据、MQTT Broker、VSOA 服务和桥接结果 |

```
┌──────────────┐     MQTT      ┌────────────────┐     VSOA      ┌──────────────┐
│  LoRa 设备   │ ◄──────────►  │   桥接组件      │ ◄──────────►  │  VSOA 服务端  │
│  Zigbee 设备 │              │                │               │  (业务层)     │
│  WiFi 摄像头  │               │  上行: MQTT→VSOA│              │              │
└──────────────┘               │  下行: VSOA→MQTT│              └──────────────┘
                               └───────┬────────┘
                                       │ HTTP API
                               ┌───────▼────────┐
                               │  IoT 测试平台   │  ← Web 浏览器访问
                               │  (Web 面板)     │
                               └────────────────┘
```

**上行（Uplink）：** MQTT 设备数据 → adapter 解析 → 设备注册表 → VSOA 查询接口 + 发布通知  
**下行（Downlink）：** VSOA 命令 → 校验 → 注册表检查 → 幂等去重 → MQTT 控制消息下发  
**场景联动（Scene）：** 上行传感数据 → 条件匹配 → 冷却/边沿判断 → MQTT 下行动作

---

## 2. 架构概览

### 端口分配

| 端口 | 协议 | 用途 |
|:----:|------|------|
| **3001** | VSOA | bridge VSOA Server：上行 RPC 查询 + 下行 RPC 命令 + ACK/事件发布 |
| **3000** | VSOA | 业务层 VSOA Server，订阅 `/ctrl/cmd`（Pub/Sub 下行命令） |
| **1883** | MQTT | 连接 MQTT Broker（订阅上行 topic + 发布下行 topic） |
| **9090** | TCP | JSON Lines 注入（离线测试，无需真实 MQTT Broker） |
| **8000** | HTTP | IoT 测试平台后端（FastAPI） |
| **5173** | HTTP | IoT 测试平台前端（Vite 开发服务器） |

### 双通道下行

| | RPC | Pub/Sub |
|--|------|---------|
| **调用方式** | `client.fetch("/bridge/send_command", ...)` | `client.datagram("/ctrl/cmd", ...)` |
| **阻塞特性** | 同步阻塞，等待回执 | 异步，发完即返回 |
| **回执方式** | `fetch()` 同步返回 | bridge publish `/ctrl/ack` 异步通知 |
| **超时支持** | ✅ per-command timeout | ❌ |
| **适用场景** | 单设备精准控制，需要即时结果 | 批量下发、状态同步 |

### Payload 透传

桥接只提取路由必需的 `device_id` 和 `device_type`，其余所有传感器数据放入 `raw: dict` 原样透传。新增传感器类型**无需改 bridge 代码**。

---

## 3. 环境准备

### 必需软件

| 软件 | 版本要求 | 用途 |
|------|---------|------|
| Python | 3.8.10+（推荐 3.12+） | bridge 主服务 + 平台后端 |
| Node.js | 20+ | 平台前端 |
| pnpm | 通过 corepack 启用 | 前端包管理 |

### Python 环境：二选一

支持两种 Python 环境管理方式，根据个人习惯任选其一。

#### 方案 A：venv（推荐，无需额外安装）

```powershell
# 在项目根目录创建虚拟环境
python -m venv .venv

# 激活（Windows PowerShell）
.\.venv\Scripts\Activate.ps1

# 激活（Windows CMD）
.\.venv\Scripts\activate.bat

# 激活（Linux / macOS）
source .venv/bin/activate
```

#### 方案 B：conda（如果你已安装 Anaconda/Miniconda）

```powershell
conda activate base
# 或创建专用环境：
# conda create -n bridge python=3.12
# conda activate bridge
```

### 安装依赖

无论使用 venv 还是 conda，**激活环境后**，以下步骤完全一致。

**1. Bridge 桥接依赖**

```powershell
# 在项目根目录执行
pip install -r requirements-py38.txt
```

需要安装的包：
- `paho-mqtt==2.1.0` — MQTT 客户端
- `PyYAML==6.0.3` — YAML 配置解析

> **注意：** VSOA Python SDK（v1.0.4）已内置在 `vsoa/` 目录下，不需要 pip 安装。项目经过 Python 3.8 类型注解兼容处理，协议逻辑保持不变，许可证见 `vsoa/LICENSE`。**必须从仓库根目录启动**以确保 `vsoa/` 优先加载。

**2. IoT 测试平台依赖**

```powershell
cd iot_test_platform

# 后端
pip install -r backend/requirements.txt

# 前端
corepack enable
corepack pnpm --dir frontend install
```

后端需要：`fastapi`、`uvicorn[standard]`、`paho-mqtt`、`pyyaml`

---

## 4. 快速复现

### 第一步：确认 MQTT Broker 可用

项目的 MQTT Broker 地址配置在 `config.yaml` 中。默认：
- 主 Broker（LoRa）：`192.168.200.221:1883`
- Zigbee Broker：`192.168.200.169:1883`

如果使用其他 Broker，请先修改 `config.yaml` 中的 `mqtt.broker` 和 `mqtt.project_brokers`。

### 第二步：启动 Bridge 主服务

```powershell
cd D:\南京翼辉暑期实习\bridge-merged
python -m src.main --config config.yaml
```

看到以下日志表示启动成功：
```
[INFO] Bridge main starting...
[INFO] [REGISTRY] DeviceRegistry(0 devices)
[INFO] MQTT connected to 192.168.200.221:1883
[INFO] VSOA Server listening on 0.0.0.0:3001
```

> **Linux/LoRaWAN 主机**可用 `sh run_bridge_py38.sh`

### 第三步：启动 IoT 测试平台

**新开一个终端**，在项目根目录执行：

```powershell
cd iot_test_platform
.\start_platform.ps1
```

启动后浏览器访问：
- **本机：** `http://127.0.0.1:5173`
- **局域网：** 脚本会自动检测并显示局域网 IP 地址

首次启动会创建默认账号：

| 角色 | 用户名 | 密码 |
|------|--------|------|
| 普通用户 | `user` | `user123` |
| 测试运维 | `tester` | `tester123` |
| 管理员 | `admin` | `admin123` |

> **提示：** 如需同一局域网内其他电脑访问，请以**管理员身份**运行 `start_platform.ps1`，脚本会自动放行 Windows 防火墙 TCP 5173 和 8000 端口。

### 第四步：端到端验证

用 4 个终端做完整链路验证：

| 终端 | 命令 | 作用 |
|------|------|------|
| 1 | `python src/main.py` | Bridge 主服务 |
| 2 | `python mqtt_monitor.py` | GUI 监视器 |
| 3 | `python tools/sim_device.py` | 模拟 LoRa/Zigbee 设备上报 |
| 4 | `python tools/verify_e2e.py` | 自动化验证脚本 |

### 如果代码有更新

```powershell
git pull
# 如果有冲突，解决后：
# 重启 bridge 主服务和测试平台后端！
```

**关键提示：** `git pull` 后旧的后端进程（PID 可查）还在运行旧代码。需要先停掉旧进程再重启：

```powershell
# 查找占用 8000 端口的进程
netstat -ano | findstr ":8000.*LISTENING"
# 杀掉对应 PID（例如 3408）
taskkill /PID 3408 /F
# 重新启动平台
.\start_platform.ps1
```

---

## 5. 目录结构

```
bridge-merged/
├── config.yaml                  # 统一配置文件
├── scenes.yaml                  # 场景联动规则
├── requirements-py38.txt        # Python 依赖
├── run_bridge_py38.sh           # Linux 启动脚本
├── README.md
├── doc/
│   ├── spec.md                  # 技术规格说明书
│   └── task.md                  # 开发任务清单
├── src/
│   ├── main.py                  # 统一入口：同时启动上行 + 下行 + 场景引擎
│   ├── config.py                # 类型化配置加载（数据类）
│   ├── error_codes.py           # 错误码（1xxx 上行 + 2xxx 下行 + 3xxx 场景）
│   ├── trace_id.py              # traceId 生成器（br-{random}-{timestamp}）
│   ├── device_registry.py       # 统一设备注册表（线程安全）
│   ├── mqtt_handler.py          # 统一 MQTT 客户端（支持多 Broker 路由）
│   ├── uplink/
│   │   ├── vsoa_server.py       # 上行 VSOA 查询端点 + 发布通知
│   │   ├── tcp_inject.py        # TCP 9090 JSON Lines 注入（离线测试）
│   │   ├── camera_reassembler.py # LoRaWAN HCv3 图片分片重组
│   │   └── adapters/
│   │       ├── base.py          # Adapter 抽象基类 + 测量值别名表
│   │       ├── lora.py          # LoRa/LoRaWAN/ChirpStack 适配器
│   │       ├── zigbee.py        # Zigbee2MQTT 适配器
│   │       ├── wifi.py          # WiFi/EoRa 设备适配器
│   │       └── generic.py       # 通用适配器（兜底）
│   ├── downlink/
│   │   ├── rpc_server.py        # 下行 RPC handler（同步回执 + per-command 超时）
│   │   ├── pubsub_handler.py    # 下行 Pub/Sub 订阅 + ACK 发布
│   │   ├── business_server.py   # 业务 VSOA Server（端口 3000，按需启动）
│   │   ├── command.py           # 命令校验 + MQTT 消息构造（纯函数，两通道共用）
│   │   └── dedup.py             # 幂等去重缓存（command_id, TTL 过期淘汰）
│   └── scene_engine/
│       ├── engine.py            # 条件匹配、冷却、边沿与自动停止
│       ├── models.py            # 场景规则模型和校验
│       ├── sensors.py           # 8 种标准传感器定义与归一化读取
│       ├── persistence.py       # scenes.yaml 原子持久化
│       └── rpc_api.py           # /scene/* VSOA 管理接口
├── vsoa/                        # 内置 VSOA Python SDK v1.0.4（Py3.8 兼容）
│   ├── server.py                # VSOA Server（@server.command + publish）
│   ├── client.py                # VSOA Client（connect + fetch + subscribe）
│   ├── parser.py                # 协议解析
│   └── ...
├── iot_test_platform/           # IoT 测试平台
│   ├── start_platform.ps1       # 一键启动脚本（后端 + 前端）
│   ├── backend/
│   │   ├── app.py               # FastAPI 后端（~3000 行）
│   │   └── requirements.txt     # 后端依赖
│   ├── frontend/
│   │   ├── src/App.jsx          # React 单页应用（~3000 行）
│   │   ├── src/styles.css       # 样式（深色/浅色主题）
│   │   └── package.json         # 前端依赖
│   └── data/                    # SQLite 数据库 + 备份（gitignore）
├── tools/
│   ├── sim_device.py            # MQTT 设备模拟器
│   ├── send_downlink.py         # 下行命令发送脚本
│   ├── verify_e2e.py            # 端到端验证脚本
│   ├── mqtt_monitor.py          # CLI MQTT 监视器
│   ├── mqtt_test.py             # MQTT 连接测试
│   ├── start_terminals.ps1      # 一键启动多终端（开发用）
│   └── python38_smoke.py        # Python 3.8 兼容性冒烟测试
├── tests/
│   ├── downlink/                # 下行单元测试 + 集成测试 + MQTT 订阅验证
│   ├── uplink/                  # 上行单元测试 + 集成测试（含 conftest fixtures）
│   ├── platform/                # IoT 测试平台功能测试
│   └── scene_engine/            # 场景引擎单元测试
└── logs/                        # 运行时日志
```

> ⚠️ **已废弃：** 根目录 `mqtt_bridge.py`、`mqtt_receiver.py` 是旧版脚本，不再使用。`src/downlink/main.py` 和 `src/uplink/main.py` 是合并前的独立入口，已不可独立运行。统一入口为 `src/main.py`。

---

## 6. 数据模型

### 6.1 Payload 透传（raw: dict）

桥接只提取路由必需的 `device_id` 和 `device_type`，其余传感器数据全部放入 `raw` 字典透传：

```python
@dataclass
class UplinkReport:
    device_id: str      # 路由必需：设备标识
    type: str            # 路由必需：设备类型（由 adapter 从 raw keys 推断）
    raw: dict[str, Any]  # 所有传感器数据原样透传
```

### 6.2 测量值别名表

Adapter 将常见测量值映射到规范名称（写入 `raw`），未知字段原样透传：

| 规范名称 | 别名 |
|---------|------|
| `temperature` | temp, temperature |
| `humidity` | humidity, moisture |
| `pressure` | pressure, barometric_pressure |
| `battery` | battery, battery_level |
| `signal` | rssi, signal, linkquality |
| `snr` | snr, loRaSNR |
| `soil_moisture` | soil_moisture, soil_humidity |
| `precipitation` | precipitation, rain, rainfall |
| `illuminance` | illuminance, light, lux, brightness |
| `smoke` | smoke, smoke_alarm, gas |
| `pir` | pir, motion, presence, infrared |
| `voltage` | voltage, volt, battery_voltage |

### 6.3 LoRaWAN HCv3 图片分片

桥接在 MQTT 上行管道内处理 FPort 2 的 HCv3 图片分片：CRC16 校验、按 `DevEUI + image_seq` 分组、乱序重排、整图 CRC32 与 JPEG 标记校验。完整图片以 `image_b64` + `image_mime=image/jpeg` 输出。FPort 3 回复 ACK_OK / RETX_REQUEST。

---

## 7. VSOA 接口

### RPC 查询端点（端口 3001）

| URL | 用途 |
|------|------|
| `/bridge/health` | 健康检查 |
| `/adapter/list` | 适配器列表 |
| `/uplink/schema` | 上行数据模型字段说明 |
| `/device/list` | 所有设备摘要列表 |
| `/device/all/data` | 所有设备完整数据 |
| `/device/{id}/data` | 单设备完整数据 |
| `/device/{id}/status` | 单设备状态 |
| `/bridge/send_command` | **下行命令入口（同步 RPC 回执）** |
| `/scene/list`, `/scene/add`, `/scene/delete` 等 | 场景管理 |

### VSOA 发布通知

| URL | 触发时机 |
|------|------|
| `/device/update` | 设备注册/更新 |
| `/bridge/event` | 上行消息处理完成 |
| `/ctrl/ack` | Pub/Sub 下行命令处理完成 |
| `/scene/trigger` | 场景触发或自动停止 |

---

## 8. IoT 测试平台

### 功能概览

- 多 MQTT Broker 与 VSOA 服务接入，保留真实消息和桥接结果
- 按 LoRa / ZigBee / WiFi 三个项目聚合设备，各自展示专属指标面板
- 单设备实时状态、历史温度趋势、信号、电量
- 温度/电量/烟雾告警及人工确认
- 用户自定义智慧场景：AND/OR 条件匹配 → 自动批量下发设备控制
- 下行控制二次确认、ACK/超时状态与操作审计
- 动态节点拓扑（设备 → Broker → 桥接 → VSOA）
- `user` / `tester` / `admin` 三种角色权限
- 深色/浅色主题

### 局域网访问

`127.0.0.1:5173` 与局域网 IP（如 `192.168.x.x:5173`）属于不同浏览器来源，登录状态不会互通。首次使用局域网地址需要**重新登录**。

### 默认账号

首次启动会自动创建。正式联调前建议用管理员页面修改初始密码。登录令牌有效期为 8 小时。

### 数据持久化

历史事件、设备档案、账号、告警、命令和审计记录保存在 `iot_test_platform/data/platform.db`。后端启动和正常关闭时会在 `data/backups/` 保留最近 12 份 SQLite 完整快照。`*.db` 文件已加入 `.gitignore`，不会被 git 追踪。

---

## 9. 运行测试

```powershell
cd D:\南京翼辉暑期实习\bridge-merged

# 全部单元测试
python -m pytest tests/ -v

# 按模块
python -m pytest tests/downlink/ -v    # 下行
python -m pytest tests/uplink/ -v      # 上行
python -m pytest tests/platform/ -v    # 平台
python -m pytest tests/scene_engine/ -v # 场景引擎

# Python 3.8 兼容性验证
python tools/python38_smoke.py
```

---

## 10. 配置说明

### config.yaml 主要段落

| 段落 | 说明 |
|------|------|
| `vsoa.server` | VSOA Server 监听地址与端口（3001） |
| `vsoa.business_server` | 业务层 VSOA Server（3000），`auto_start: true` 可由 bridge 自动启动 |
| `vsoa.pubsub_client` | Pub/Sub 客户端：连接业务层 + 订阅 `/ctrl/cmd` |
| `vsoa.reconnect` | VSOA 断连重试参数 |
| `mqtt` | MQTT Broker 地址 + 上行订阅 topic + 下行发布前缀 |
| `mqtt.project_brokers` | 不同项目的独立 Broker（如 zigbee 使用独立 Broker） |
| `uplink` | TCP 注入端口（9090）、设备上限（64）、适配器列表 |
| `uplink.camera` | LoRaWAN HCv3 摄像头分片重组参数 |
| `downlink.command` | 超时、去重（TTL 300s）、重试（最多 3 次） |
| `chirpstack` | ChirpStack 下行格式（enabled + confirmed + fPort） |
| `scene_engine` | 场景规则文件路径、默认冷却期（60s）、规则上限（100） |
| `logging` | 日志级别（INFO）与输出文件 |

---

## 11. 常见问题

### Bridge 相关

**Q: 启动报 `No module named 'vsoa'`**
> 必须从仓库根目录启动，让项目内置的 `vsoa/` 优先加载。不要用 pip 安装 vsoa 包。

**Q: MQTT 连接超时**
> 检查 `config.yaml` 中的 `mqtt.broker` 地址是否正确，确保网络可达。

**Q: 下行命令返回 2203（设备不在注册表）**
> 设备需要先上行注册后才能接受下行命令。确保目标设备至少上报过一次数据。

### IoT 平台相关

**Q: `start_platform.ps1` 中文乱码**
> 脚本使用 UTF-8 with BOM 编码。如果出现乱码，用记事本另存为 UTF-8 with BOM 格式。

**Q: 页面 404 / API 返回 404**
> 可能原因：
> 1. **最可能：** `git pull` 后旧后端进程还在运行。先 `netstat -ano | findstr ":8000"` 找到 PID，`taskkill /PID <id> /F` 杀掉，再重新启动。
> 2. 前端和后端不在同一台机器上运行 — 前端会自动连接浏览器地址栏的主机名对应的后端，跨机器使用会有问题。
> 3. 检查后端是否成功启动（观察终端输出或 `backend.log`）。

**Q: 局域网其他电脑访问不了**
> 1. 确认使用 `start_platform.ps1` 启动（绑定 `0.0.0.0`）
> 2. 以管理员身份运行一次放行防火墙 TCP 5173 和 8000
> 3. 访问时使用真实的局域网 IP，不是 `127.0.0.1`

**Q: 前端能打开但看不到数据**
> 1. 先登录（默认账号见上方）
> 2. 确认 Bridge 主服务已启动且在运行
> 3. 确认有实际设备数据上报（可在 bridge 终端观察日志）

---

## 开发状态

| 项目 | 状态 |
|------|:--:|
| 上行管道 (MQTT→VSOA) | ✅ |
| 下行 RPC (VSOA→MQTT) | ✅ |
| 下行 Pub/Sub (VSOA→MQTT) | ✅ |
| Payload 透传（协议级抽象） | ✅ |
| 设备注册表（上行注册 + 下行查询） | ✅ |
| 幂等去重 | ✅ |
| ChirpStack 下行 | ✅ |
| VSOA 断连自动重连 | ✅ |
| traceId 全链路追踪 | ✅ |
| 场景联动引擎 | ✅ |
| IoT 测试平台 | ✅ |
| LoRaWAN HCv3 摄像头分片重组 | ✅ |
| 对接真实设备 | 📌 待进行 |

---

## 相关文档

- `doc/spec.md` — 技术规格说明书 v2.0
- `doc/task.md` — 开发任务清单
- `doc/api.md` — 业务层接口文档
- `config.yaml` — 完整配置文件（含注释）
- `overall_experiment_rules.md` — 统一规范与硬件文档
- `LoRaWAN_MQTT后端接入说明.md` — LoRaWAN MQTT 集成说明
