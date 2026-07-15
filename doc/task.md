# MQTT ↔ VSOA 桥接组件 — 开发任务清单

> **版本:** v1.0（合并版）  
> **日期:** 2026-07-14  
> **对应 spec:** `doc/spec.md` v1.0  
> **策略:** 测试先行（方案A）— 先迁移测试确保通过，再逐模块合并/适配  
> **进度:** 16/17 完成，132 单元测试通过，5 集成测试待任务 17 修复

---

## 任务总览

| 阶段 | 任务数 | 说明 |
|------|:--:|------|
| 一：项目骨架与源码迁移 | 1 | 目录创建 + 源码拷贝 + import 路径修正 |
| 二：测试迁移 | 2 | 上行/下行测试迁移，全部通过 |
| 三：共用模块合并 | 5 | config → error_codes → trace_id → device_registry → mqtt_handler |
| 四：上行模块适配 | 3 | adapters → vsoa_server → tcp_inject |
| 五：下行模块适配 | 4 | command → dedup → rpc_server → pubsub_handler |
| 六：集成 | 2 | 统一入口 + 全量集成验证 |

---

## 阶段一：项目骨架与源码迁移

### 任务 1 — 创建目录结构并迁移源码

- **描述：** 按照 spec §13 目录结构创建 `bridge-merged/` 下所有目录和 `__init__.py`，将 `bridge/src/downlink/` 和 `bridge-uplink/src/` 下所有源码文件拷贝到对应位置。修正所有模块间的 import 路径（从 `from device_registry import ...` 改为 `from src.device_registry import ...` 或相对导入），确保 `python -c "import src.main"` 无 ImportError。
- **产出：**
  - `bridge-merged/src/` 完整目录结构
  - 所有源码文件就位，import 路径修正
  - `config.yaml` 初版（可直接拷贝 §12.1 模板）
- **验收标准：**
  1. `python -c "from src.config import load_config; print(load_config('config.yaml'))"` 无报错
  2. `python -c "from src.device_registry import DeviceRegistry; r = DeviceRegistry(64); print(r.count)"` 输出 `0`
  3. `python -c "from src.error_codes import SUCCESS; print(SUCCESS.code)"` 输出 `0`
  4. `python -c "from src.uplink.adapters import select_adapter; print(len(select_adapter.__doc__ or ''))"` 无报错
- **状态：** 📌 待开始

---

## 阶段二：测试迁移（测试先行）

### 任务 2 — 上行测试迁移

- **描述：** 将 `bridge-uplink/tests/` 下所有测试文件迁移到 `bridge-merged/tests/uplink/`。修正测试文件中的 import 路径（`from device_registry import ...` → `from src.device_registry import ...`），修正 `conftest.py` 中的 fixture。运行 `pytest tests/uplink/ -v`，确保全部通过。
- **产出：**
  - `tests/uplink/conftest.py` — 适配后的 fixture
  - `tests/uplink/test_adapters.py` — 适配后通过
  - `tests/uplink/test_registry.py` — 适配后通过
  - `tests/uplink/test_integration.py` — 适配后通过
- **验收标准：**
  1. `cd bridge-merged && python -m pytest tests/uplink/ -v` 全部通过
  2. 测试数量与原 `bridge-uplink/tests/` 一致（无丢失、无新增）
- **状态：** 📌 待开始

### 任务 3 — 下行测试迁移

- **描述：** 将 `bridge/tests/downlink/` 下所有测试文件迁移到 `bridge-merged/tests/downlink/`。修正 import 路径（`from command import ...` → `from src.downlink.command import ...`）。运行 `pytest tests/downlink/ -v`，确保全部通过。
- **产出：**
  - `tests/downlink/test_command.py` — 适配后通过
  - `tests/downlink/test_registry.py` — 适配后通过
  - `tests/downlink/test_dedup.py` — 适配后通过
  - `tests/downlink/test_integration.py` — 适配后通过
- **验收标准：**
  1. `cd bridge-merged && python -m pytest tests/downlink/ -v` 全部通过
  2. 测试数量与原 `bridge/tests/downlink/` 一致（无丢失、无新增）
- **状态：** 📌 待开始

---

## 阶段三：共用模块合并

### 任务 4 — 合并 config.py

- **描述：** 将 `bridge-uplink/src/config.py`（dataclass 方式）与 `bridge/src/downlink/config.py`（dict 代理方式）合并为 `src/config.py`。采用 dataclass 方式，结构对齐 spec §12.1。下行原有 `_ConfigProxy` dict 访问方式改为 dataclass 属性访问（`config["mqtt"]["broker"]` → `config.mqtt.broker`）。**更新所有引用 `config.py` 的源码和测试文件的 import 路径及 API 调用方式。**

  **config.yaml 合并：** 对比 `bridge/config.yaml` 和 `bridge-uplink/config.yaml`，按 spec §12.2 对应表合并为统一 `config.yaml`。具体操作：
  - 以 spec §12.1 模板为骨架
  - 从 `bridge/config.yaml` 提取：`vsoa.pubsub_client`、`vsoa.reconnect`、`downlink.command.*`
  - 从 `bridge-uplink/config.yaml` 提取：`uplink.*`、`mqtt.uplink_topics`
  - 共用部分（`mqtt.broker`/`port`/`keepalive`/`reconnect`、`logging`）合并，冲突项以 spec §12.1 模板为准
  - 移除废弃项（`vsoa.rpc_server.endpoint`、`vsoa.server.name`/`version`、`device_registry.source`）
- **产出：**
  - `src/config.py` — 统一 dataclass 配置（`BridgeConfig`，含 `VsoaConfig` / `MqttConfig` / `UplinkConfig` / `DownlinkConfig` / `LoggingConfig`）
  - 所有受影响的源码文件（import 路径 + dict 访问 → 属性访问）
  - 所有受影响的测试文件（import 路径 + dict 访问 → 属性访问）
- **验收标准：**
  1. `python -c "from src.config import load_config; c = load_config('config.yaml'); print(c.mqtt.broker, c.vsoa.server.port, c.uplink.max_devices, c.downlink.command.default_timeout_ms)"` 输出配置值
  2. 原 `src/downlink/config.py` 已删除，原 `src/uplink/config.py`（如独立存在）已删除
  3. `pytest tests/ -v` 全部通过（无 ImportError，无 dict 访问报错）
- **状态：** 📌 待开始

### 任务 5 — 合并 error_codes.py

- **描述：** 将 `bridge/src/downlink/error_codes.py`（下行 2xxx）扩展，新增上行错误码 1001-1003（`ERR_UP_DEVICE_NOT_FOUND`、`ERR_UP_INVALID_URL`、`ERR_UP_REGISTRY_FULL`），形成统一 `src/error_codes.py`。保持 `ErrorCode` dataclass + `lookup()` 接口。**更新所有引用 `error_codes.py` 的源码和测试文件的 import 路径**（`from src.downlink.error_codes import ...` → `from src.error_codes import ...`）。
- **产出：**
  - `src/error_codes.py` — 含完整 1xxx + 2xxx + 9xxx 错误码
  - 原 `src/downlink/error_codes.py` 已删除
  - 所有受影响的源码和测试文件（import 路径更新）
  - 上行 `vsoa_server.py` 中错误返回改用数字码（`1001` 替代 `{"error": "Device not found"}`）
- **验收标准：**
  1. `python -c "from src.error_codes import ERR_UP_DEVICE_NOT_FOUND; print(ERR_UP_DEVICE_NOT_FOUND.code)"` 输出 `1001`
  2. `python -c "from src.error_codes import lookup; print(lookup(1001).name)"` 输出 `ERR_UP_DEVICE_NOT_FOUND`
  3. `pytest tests/ -v` 全部通过（错误码相关用例无 ImportError）
- **状态：** 📌 待开始

### 任务 6 — 抽取 trace_id.py

- **描述：** 将 `rpc_server.py` 和 `pubsub_handler.py` 中重复的 `_generate_trace_id()` 函数抽取到 `src/trace_id.py`，作为共用模块。两处原有函数替换为 `from src.trace_id import generate_trace_id`。**更新 `rpc_server.py` 和 `pubsub_handler.py` 的 import，同时在各自模块内移除 `import secrets` 和 `import time`（如仅用于 traceId 生成）。**
- **产出：**
  - `src/trace_id.py` — `generate_trace_id() → str`，格式 `br-{8位hex}-{毫秒时间戳}`
  - `src/downlink/rpc_server.py` 和 `src/downlink/pubsub_handler.py` — 移除本地 `_generate_trace_id()`，改为 import
- **验收标准：**
  1. `python -c "from src.trace_id import generate_trace_id; tid = generate_trace_id(); print(tid.startswith('br-'), len(tid) > 20)"` 输出 `True True`
  2. 快速连续生成 1000 个 traceId 无碰撞
  3. `pytest tests/ -v` 全部通过（rpc_server 和 pubsub_handler 的 trace_id 相关用例不受影响）
- **状态：** 📌 待开始

### 任务 7 — 合并 device_registry.py

- **描述：** 将 `bridge-uplink/src/device_registry.py`（上行 `DeviceData` + `upsert()`）与 `bridge/src/downlink/device_registry.py`（下行 `DeviceInfo` + `lookup()`）合并为 `src/device_registry.py`。合并后 `DeviceInfo` 同时包含上行传感器字段（temperature/humidity/signal 等）和下行预留字段（mqtt_topic_template/dev_eui）。`DeviceRegistry` 同时提供 `upsert()` 和 `lookup()` 接口。线程安全（`threading.Lock`）。**更新所有引用 `device_registry.py` 的源码和测试文件的 import 路径**（`from src.downlink.device_registry import ...` / `from src.uplink.device_registry import ...` → `from src.device_registry import ...`）。**适配 `DeviceInfo` 字段变化**（合并后字段增加，测试中构造 `DeviceInfo` 的位置可能需要适配）。
- **产出：**
  - `src/device_registry.py` — 统一 `DeviceInfo` + `DeviceRegistry` 类
  - 原 `src/downlink/device_registry.py` 和 `src/uplink/device_registry.py` 已删除
  - 所有受影响的源码文件（import 路径 + DeviceInfo 字段适配）
  - 所有受影响的测试文件（import 路径 + DeviceInfo 构造适配）
- **验收标准：**
  1. `registry.upsert(report)` 插入新设备 → 返回 `(DeviceInfo, True)`
  2. `registry.upsert(report)` 更新已有设备 → 返回 `(DeviceInfo, False)`，`report_count` 递增
  3. `registry.lookup("存在的设备")` 返回 `DeviceInfo`
  4. `registry.lookup("不存在的设备")` 返回 `None`
  5. 注册表满（64 个设备）时 `upsert()` 返回 `(None, False)`
  6. 线程安全：两个线程并发 upsert/lookup 无异常
  7. `pytest tests/ -v` 全部通过（无 ImportError，注册表相关用例全部通过）
- **状态：** 📌 待开始

### 任务 8 — 合并 mqtt_handler.py

- **描述：** 将 `bridge-uplink/src/mqtt_handler.py`（上行 subscriber，`UplinkMqttHandler`）与 `bridge/src/downlink/mqtt_handler.py`（下行 publisher，`MQTTHandler`）合并为 `src/mqtt_handler.py`。统一 `paho-mqtt` 客户端，同时支持 `subscribe(topics)` 和 `publish(topic, payload, qos)`。保留线程安全（publish 加锁）、自动重连、on_message 回调。**更新所有引用 `mqtt_handler.py` 的源码和测试文件的 import 路径**（`from src.downlink.mqtt_handler import ...` / `from src.uplink.mqtt_handler import ...` → `from src.mqtt_handler import ...`）。

  **on_message 分发路由：** 统一客户端的 `on_message` 回调按 topic 前缀路由：
  - 匹配 `bridge/uplink/`、`lora/+/up`、`zigbee/+/report` → 上行处理管道（adapter → registry.upsert → VSOA 通知）
  - 其他 topic → 当前只打 warning 日志（下行方向不需要 subscribe，下行命令走 VSOA RPC/PubSub 入口）
  
  路由通过注册回调函数实现：`MQTTHandler` 接受一个可选的 `on_uplink_message` 回调，由 `main.py` 在初始化时注入上行处理管道函数。
- **产出：**
  - `src/mqtt_handler.py` — 统一 `MQTTHandler` 类（subscribe + publish）
  - 原 `src/downlink/mqtt_handler.py` 和 `src/uplink/mqtt_handler.py` 已删除
  - 所有受影响的源码和测试文件（import 路径更新）
- **验收标准：**
  1. `mqtt.connect()` → 连接成功，订阅 7 个上行 topic
  2. `mqtt.publish(topic, payload)` → 发布成功
  3. on_message 回调收到 MQTT 消息 → 触发上行处理管道
  4. 断开后自动重连 → 重新订阅所有 topic
  5. 线程安全：两个线程并发 publish 无异常
  6. `pytest tests/ -v` 全部通过（无 ImportError，MQTT 相关用例全部通过）
- **状态：** 📌 待开始

---

## 阶段四：上行模块适配

### 任务 9 — 适配 uplink/adapters/

- **描述：** 修正 `src/uplink/adapters/` 下所有模块的 import 路径（`from device_registry import ...` → `from src.device_registry import ...`、`from config import ...` → `from src.config import ...`）。适配 `DeviceInfo` 新字段（合并后字段名/类型如有变化需调整）。`select_adapter()` 和三个 adapter 解析逻辑不变。
- **产出：**
  - `src/uplink/adapters/__init__.py` / `base.py` / `lora.py` / `zigbee.py` / `generic.py` — import 路径适配后
- **验收标准：**
  1. `python -c "from src.uplink.adapters import select_adapter, ADAPTERS; print(len(ADAPTERS))"` 输出 `3`
  2. 上行 adapter 测试（`tests/uplink/test_adapters.py`）全部通过
- **状态：** 📌 待开始

### 任务 10 — 适配 uplink/vsoa_server.py

- **描述：** 将上行 VSOA Server 适配为使用统一配置和统一设备注册表。7 个查询端点保持不变，`/device/update` 和 `/bridge/event` 发布保持不变。改用 `src/error_codes.py` 数字错误码（1001/1002 替代 `{"error": "..."}` 字符串）。改用 `src/device_registry.py` 合并后的 `DeviceRegistry`。

  > ⚠️ **Breaking Change：** `/device/{id}/data` 和 `/device/{id}/status` 的错误响应格式从 `{"error": "Device not found"}`（字符串）变为 `{"error_code": 1001, "error_msg": "device not found: {id}"}`（数字码）。所有消费此接口的上游 VSOA 客户端需要适配错误处理逻辑。此项变更在最终的 `doc/api.md` 中需明确文档化。
- **产出：**
  - `src/uplink/vsoa_server.py` — 适配后，使用统一 config / registry / error_codes
- **验收标准：**
  1. 7 个 RPC 查询端点正常响应（`/bridge/health`、`/device/list` 等）
  2. `/device/{id}/data` 不存在设备 → 返回数字错误码 `1001`（不再是字符串 `{"error": "Device not found"}`）
  3. `/device/update` 发布正常
  4. 上行 VSOA 相关测试全部通过
- **状态：** 📌 待开始

### 任务 11 — 适配 uplink/tcp_inject.py

- **描述：** 修正 `src/uplink/tcp_inject.py` 的 import 路径。TCP 9090 JSON Lines 注入逻辑不变，适配统一配置（`config.uplink.tcp_inject_port` 等）。
- **产出：**
  - `src/uplink/tcp_inject.py` — import 路径适配后
- **验收标准：**
  1. TCP 9090 启动 → `echo '{"cmd":"ping"}' | nc 127.0.0.1 9090` 收到响应
  2. `{"cmd":"mqtt_message","topic":"bridge/uplink/lora/test/data","payload":{...}}` 注入 → 触发上行管道
  3. 上行集成测试（TCP 注入相关用例）全部通过
- **状态：** 📌 待开始

---

## 阶段五：下行模块适配

### 任务 12 — 适配 downlink/command.py

- **描述：** 修正 `src/downlink/command.py` 的 import 路径（`error_codes` → `src.error_codes`）。`validate()`、`build_mqtt_message()`、`build_ack()` 三个纯函数逻辑不变。`build_ack()` 和 `build_mqtt_message()` 的 `trace_id` 参数保持不变。
- **产出：**
  - `src/downlink/command.py` — import 路径适配后
- **验收标准：**
  1. `python -c "from src.downlink.command import validate; v, e = validate({'command_id':'c1','device_type':'lora','device_id':'d1','action':'set','params':{}}, 60000); print(v, e)"` 输出 `True 0`
  2. 下行 command 测试（`tests/downlink/test_command.py`）全部通过
- **状态：** 📌 待开始

### 任务 13 — 适配 downlink/dedup.py

- **描述：** 修正 `src/downlink/dedup.py` 的 import 路径。`DedupCache` 类逻辑完全不变。
- **产出：**
  - `src/downlink/dedup.py` — import 路径适配后
- **验收标准：**
  1. `python -c "from src.downlink.dedup import DedupCache; d = DedupCache(10, 100); print(d.check_and_mark('id1'), d.check_and_mark('id1'))"` 输出 `True False`
  2. 下行 dedup 测试（`tests/downlink/test_dedup.py`）全部通过
- **状态：** 📌 待开始

### 任务 14 — 适配 downlink/rpc_server.py

- **描述：** 将下行 RPC Server 适配为使用统一架构：
  - import 路径修正（`src.downlink.command`、`src.downlink.dedup`、`src.device_registry`、`src.error_codes`、`src.trace_id`）
  - `DeviceRegistry` 改用合并版（`registry.lookup()` 接口不变，但底层来自上行自动填充）
  - `MQTTHandler` 改用合并版（publish 接口不变）
  - `_publish_with_retry()` 逻辑保持不变
  - RPC handler 处理流程保持不变（spec §4.2 的 ⓪~⑤ 步）
- **产出：**
  - `src/downlink/rpc_server.py` — 适配后
- **验收标准：**
  1. `RpcServer.start()` → VSOA Server（3001）启动，`/bridge/send_command` 端点可访问
  2. RPC 命令 → validate → registry.lookup → dedup.check_and_mark → MQTT publish → cli.reply(ACK)
  3. `device_id` 不在注册表 → 返回 ACK 含 `error_code=2203`
  4. MQTT publish 失败 → 自动重试（指数退避），全部失败返回对应错误码
  5. 下行 RPC 相关集成测试全部通过
- **状态：** 📌 待开始

### 任务 15 — 适配 downlink/pubsub_handler.py

- **描述：** 将下行 Pub/Sub Handler 适配为使用统一架构：
  - import 路径修正
  - `DeviceRegistry` 改用合并版、`MQTTHandler` 改用合并版
  - **核心变更：ACK 发布不再使用独立 VSOA Server（端口 3009）**，改为调用统一 VSOA Server（端口 3001）的 `publish()` 方法。需要在 `PubSubHandler` 中注入统一 Server 的 publish 引用
  - VSOA 断连自动重连逻辑保持不变（`run_forever()` + 指数退避）
  - 命令处理流程保持不变（spec §4.2）

  **前置验证（开发前执行）：** 上行 `UplinkVsoaServer.publish()` 已验证 `vsoa.Server.publish()` 可从 handler 外部调用（`bridge-uplink/src/vsoa_server.py:163-172`）。本任务开发前需额外验证：在统一 VSOA Server 实例上，RPC handler（`@server.command`）和外部 `server.publish()` 调用可共存于同一 Server 实例、同一端口，互不干扰。验证方法：写一个最小脚本，启动一个 `vsoa.Server`，同时注册一个 `@server.command` handler 并在另一个线程调用 `server.publish()`，确认两者均正常工作。
- **产出：**
  - `src/downlink/pubsub_handler.py` — 适配后，ACK 走统一 VSOA Server
- **验收标准：**
  1. Pub/Sub Client 连接业务层 VSOA Server（3000），订阅 `/ctrl/cmd`
  2. 收到 `/ctrl/cmd` 消息 → 处理 → 通过统一 VSOA Server（3001）publish ACK 到 `/ctrl/ack`
  3. `device_id` 不在注册表 → ACK 含 `error_code=2203`
  4. VSOA 断开 → 自动重连（指数退避）→ 重新订阅 `/ctrl/cmd`
  5. 端口 3009 不再被监听（`netstat -an | grep 3009` 无结果）
  6. 下行 Pub/Sub 相关集成测试全部通过
- **状态：** 📌 待开始

---

## 阶段六：集成

### 任务 16 — 创建统一 main.py

- **描述：** 创建 `src/main.py` 统一入口，按 spec §14.1 启动序列依次启动所有组件：
  1. 加载统一 `config.yaml`
  2. 配置日志
  3. 初始化 `DeviceRegistry`
  4. 初始化 `DedupCache`
  5. 创建 `MQTTHandler`（统一客户端）→ 连接 Broker → 订阅 7 个上行 topic
  6. 创建上行查询端点 + 下行 RPC handler → 注册到统一 VSOA Server
  7. 启动 VSOA Server（端口 3001）
  8. 创建 VSOA Pub/Sub Client → 连接业务层（3000）→ 订阅 `/ctrl/cmd`
  9. 启动 TCP 9090 注入服务器
  10. 注入依赖（MQTT publish 引用 → RPC Server / PubSub Handler；VSOA Server publish 引用 → PubSub Handler / 上行通知）
  11. 打印启动 banner（格式见 spec §14.2）
  12. 进入 VSOA Client 事件循环
  13. SIGINT/SIGTERM 优雅关闭（spec §14.3）
- **产出：**
  - `src/main.py` — 统一入口
- **验收标准：**
  1. `cd bridge-merged && python src/main.py` → 服务启动，banner 中显示所有端口和端点
  2. banner 不出现端口 3009
  3. `Ctrl+C` → 优雅关闭（日志中出现 "bridge stopped"）
  4. 启动后 `python tools/verify.py`（需更新指向 3001）→ 可查询上行数据 + 发送下行命令
- **状态：** 📌 待开始

### 任务 17 — 集成测试验证

- **描述：** 运行全量测试 + 手动端到端验证，确保合并后功能完整。
  - 全量测试：`pytest tests/ -v`
  - 手动验证上行：启动 bridge → 通过 TCP 9090 或 MQTT 注入一条 LoRa/Zigbee/Generic 数据 → VSOA 查询 `/device/list` 和 `/device/{id}/data` 确认数据正确
  - 手动验证下行 RPC：`verify.py` 发送 RPC 命令 → 确认 ACK 返回正确 → MQTT subscriber 确认 MQTT payload 正确
  - 手动验证下行 Pub/Sub：业务层 publish `/ctrl/cmd` → subscribe `/ctrl/ack` 确认收到 ACK
  - 手动验证注册表共用：上行先上报设备 → 下行直接发命令（不配置 devices.yaml）→ 确认命令放行（非 2203）
  - 手动验证端口 3009 不存在
- **产出：**
  - 全部测试通过报告
  - 手动验证记录（日志截图或 verify 脚本输出）
- **验收标准：**
  1. `pytest tests/ -v` → 全部通过（含上行 + 下行）
  2. 上行端到端：MQTT/TCP 注入 → VSOA 查询可见设备数据
  3. 下行端到端：RPC 命令 → ACK 返回 → MQTT topic 可见下行消息
  4. 下行端到端：Pub/Sub 命令 → `/ctrl/ack` 收到 ACK
  5. 注册表共用：上行注册的设备，下行可直接控制（无 devices.yaml）
  6. 端口 3009 未监听
  7. 无回归：原上行和下行各自功能全部正常
- **状态：** 📌 待开始

---

## 任务依赖关系

```
任务 1 (骨架+源码迁移)
 ├─► 任务 2 (上行测试迁移)  ──► 任务 4-11 中涉及的测试验证
 └─► 任务 3 (下行测试迁移)  ──► 任务 4-15 中涉及的测试验证

任务 1 ──► 任务 4 (合并 config)       ──► 任务 5-15 依赖统一配置
任务 1 ──► 任务 5 (合并 error_codes)  ──► 任务 10 (vsoa_server 用数字码)
任务 1 ──► 任务 6 (抽取 trace_id)     ──► 任务 14, 15
任务 1 ──► 任务 7 (合并 registry)     ──► 任务 9, 10, 14, 15
任务 1 ──► 任务 8 (合并 mqtt)         ──► 任务 11, 14, 15, 16

任务 7 ──► 任务 9 (adapters)
任务 4,5,7 ──► 任务 10 (vsoa_server)
任务 4,8 ──► 任务 11 (tcp_inject)

任务 5 ──► 任务 12 (command)
任务 1 ──► 任务 13 (dedup)
任务 4,5,6,7,8 ──► 任务 14 (rpc_server)
任务 4,5,6,7,8 ──► 任务 15 (pubsub_handler)

任务 8,10,14,15,11 ──► 任务 16 (main.py)
所有任务 ──► 任务 17 (集成验证)
```

> **可并行项：** 任务 2 和 3 可并行；任务 4/5/6/7/8 完成后的上行适配（9-11）与下行适配（12-15）可并行。
