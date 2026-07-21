# 智慧环境设备管理平台

面向实际设备使用场景的物联网业务平台，统一接入 LoRa/LoRaWAN、ZigBee 与 MQTT-VSOA 协议桥接。平台关注设备状态、环境数据、告警和安全控制，不承担 16 个实验的逐项验收。

## 主要能力

- 多 MQTT Broker 与 VSOA 服务接入，保留真实消息和桥接结果。
- 按 LoRa、ZigBee、协议桥接三个项目聚合设备。
- 单设备实时状态、历史温度趋势、信号、电量和最近通信时间。
- 温度、电量、烟雾告警及人工确认记录。
- 下行控制二次确认、真实 MQTT 发布、ACK 或超时状态与操作审计。
- 动态节点拓扑，展示设备、Broker、桥接和 VSOA 关系。
- `user`、`tester`、`admin` 三种角色及后端 API 权限校验。
- 深色/浅色主题和浏览器端偏好保存。
- 测试运维端保留原始消息、字段映射、数据模拟和性能诊断。

详细产品边界见 [docs/product_scope.md](docs/product_scope.md)，设备接入格式见 [docs/device_integration_contract.md](docs/device_integration_contract.md)。

## 一键启动

首次使用先安装依赖：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r .\backend\requirements.txt
corepack pnpm --dir .\frontend install
```

在 PowerShell 中运行：

```powershell
.\start_platform.ps1
```

本机访问 `http://127.0.0.1:5173`。局域网成员使用 `http://本机局域网IP:5173`，Windows 防火墙需允许 TCP 5173 和 8000 端口。

首次启动会创建演示账号：

| 角色 | 用户名 | 初始密码 |
|---|---|---|
| 普通用户 | `user` | `user123` |
| 测试运维员 | `tester` | `tester123` |
| 管理员 | `admin` | `admin123` |

正式联调前必须使用管理员页面修改初始密码。登录令牌有效期为 8 小时。

## 真实项目接入

1. 启动需要接入的 MQTT Broker。
2. 启动 `bridge_vsoa_mqtt` 项目，而不是由平台模拟桥接结果。
3. 管理员在“连接配置”中添加一个或多个 Broker，并填写实际订阅 Topic。
4. 管理员连接真实 VSOA 服务。
5. 在设备中心检查设备归属、遥测值与在线状态。
6. 测试运维员在消息追踪和链路转换页面排查 MQTT 到 VSOA 的过程。

历史事件、设备档案、账号、告警、命令和审计记录保存在 `data/platform.db`。平台不会修改桥接组源代码。

平台既可以放在 `bridge_vsoa_mqtt/iot_test_platform` 中，也可以与 `bridge_vsoa_mqtt` 保持同级。特殊部署可通过环境变量 `BRIDGE_PROJECT_ROOT` 指定桥接项目根目录。
