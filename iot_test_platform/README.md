# IoT Test Platform

`iot_test_platform` 是一个面向物联网联调与演示的测试平台，用来把设备数据、MQTT Broker、VSOA 服务和桥接结果统一展示在一个页面里。它更偏向"真实链路联调和运维观察"，不是单纯的实验验收页。

## 它能做什么

- 多 MQTT Broker 与 VSOA 服务接入，保留真实消息和桥接结果。
- 按 LoRa、ZigBee、协议桥接三个项目聚合设备。
- 提供 LoRa/ZigBee/WiFi 统一项目监测模板。LoRa 展示温度、空气湿度、土壤湿度、降水和逐链路质量；ZigBee 展示温度、湿度、电压、烟雾、人体红外、降水及安全联动下行控制；WiFi 展示 MQTT 摄像头静态帧、解码状态、分辨率、帧大小和上报速率。
- 单设备实时状态、历史温度趋势、信号、电量和最近通信时间。
- 温度、电量、烟雾告警及人工确认记录。
- 支持用户自定义智慧场景，将真实传感上行按 AND/OR 条件匹配后自动批量下发设备控制，并记录触发链路。
- 下行控制二次确认、真实 MQTT 发布、ACK 或超时状态与操作审计。
- 动态节点拓扑，展示设备、Broker、桥接和 VSOA 关系。
- `user`、`tester`、`admin` 三种角色及后端 API 权限校验。
- 深色/浅色主题和浏览器端偏好保存。
- 测试运维端保留原始消息、字段映射、数据模拟和性能诊断。

页面由两部分组成：

- `frontend/`：React + Vite 前端。
- `backend/`：FastAPI 后端，负责 API、WebSocket、数据存储和桥接状态。

数据默认保存在 `data/platform.db`，桥接项目根目录可通过 `BRIDGE_PROJECT_ROOT` 指定。

## 启动前准备

本项目按 **conda 的 base 环境** 使用，不再使用 `venv`。

首次使用先打开 **Anaconda Prompt** 或已激活 `base` 的 PowerShell，然后进入项目目录安装依赖：

```powershell
conda activate base
cd D:\南京翼辉暑期实习\bridge-merged\iot_test_platform
python -m pip install --upgrade pip
python -m pip install -r .\backend\requirements.txt
corepack enable
corepack pnpm --dir .\frontend install
```

建议使用：

- Python 3.12+（当前可直接使用 conda `base`）
- Node.js 20+
- pnpm（通过 `corepack` 启用）

## 一键启动

在项目根目录打开 PowerShell，执行：

```powershell
.\start_platform.ps1
```

这个脚本会同时启动：

- 后端：`0.0.0.0:8000`
- 前端：`0.0.0.0:5173`

启动后浏览器访问：

- 本机访问：`http://127.0.0.1:5173`
- 局域网访问：`http://本机局域网IP:5173`

例如，如果你电脑的局域网 IP 是 `192.168.1.23`，其他电脑就访问：

```text
http://192.168.1.23:5173
```

## 让局域网电脑访问

要让同一局域网里的其他电脑打开你的平台，重点是这三件事：

1. 用 `start_platform.ps1` 启动，而不是只跑默认的 `npm run dev`。
2. 确保前端和后端都绑定到 `0.0.0.0`，这样才会监听所有网卡。
3. 在 Windows 防火墙里放行 TCP `5173` 和 `8000` 端口。

当前仓库里 `start_platform.ps1` 已经把前后端都绑定为 `0.0.0.0`，所以只要防火墙放行，局域网内的电脑就可以直接访问。

如果你想手动启动，也可以分别运行：

```powershell
conda activate base
cd D:\南京翼辉暑期实习\bridge-merged\iot_test_platform

# 后端
python -m uvicorn backend.app:app --host 0.0.0.0 --port 8000

# 前端（新开一个终端）
corepack pnpm --dir .\frontend dev -- --host 0.0.0.0 --port 5173
```

注意：`frontend/package.json` 里的默认 `dev` 脚本只绑定 `127.0.0.1`，如果直接用它启动，局域网电脑通常访问不到。

## 默认账号

首次启动会创建演示账号：

| 角色 | 用户名 | 初始密码 |
|---|---|---|
| 普通用户 | `user` | `user123` |
| 测试运维员 | `tester` | `tester123` |
| 管理员 | `admin` | `admin123` |

正式联调前建议先用管理员页面修改初始密码。登录令牌有效期为 8 小时。

## 真实项目接入

1. 先启动需要接入的 MQTT Broker。
2. 再启动 `bridge_vsoa_mqtt` 项目，让它提供真实桥接能力。
3. 用管理员账号进入"连接配置"，添加 Broker 和订阅 Topic。
4. 连接真实 VSOA 服务。
5. 到"设备中心""消息追踪""链路转换"中查看真实数据流。
6. 如果需要做性能或运维检查，可以在运维页面跑测试任务。

历史事件、设备档案、账号、告警、命令和审计记录统一保存在当前仓库的 `data/platform.db`。后端在每次启动和正常关闭时，都会使用 SQLite 在线备份机制在 `data/backups/` 中保留最近 12 份完整快照。代码更新、普通 `git pull` 和前端重新构建不会删除这些文件。

## 常见问题

### 页面能打开，但其他电脑访问不了

- 检查是否用的是 `start_platform.ps1` 或手动 `--host 0.0.0.0`。
- 检查 Windows 防火墙是否允许 `5173` 和 `8000`。
- 检查局域网电脑访问的是你这台机器的真实 IP，不是 `127.0.0.1`。
- 如果你在公司或校园网络里，可能还需要放行本机网络配置文件中的"专用网络"入站规则。

### 前端打不开后端接口

- 确认后端已经在 `8000` 端口启动。
- 确认浏览器地址栏里打开的是平台页面，而不是直接打开某个静态文件。
- 如果前端和后端分开启动，请保持它们都在同一台机器上运行，并使用脚本推荐的端口。

## 目录说明

```text
iot_test_platform/
├── backend/            # FastAPI 后端
├── frontend/           # React + Vite 前端
├── data/               # SQLite 数据库
├── start_platform.ps1  # 一键启动脚本
└── README.md
```

`platform.db` 与备份中包含账号密码哈希、设备数据和审计记录，因此被 `.gitignore` 排除，不上传到远程 Git 仓库，也不会产生多人合并冲突。更换电脑或全新克隆仓库时，应单独迁移 `data/platform.db`；不要启动旧目录中的另一份平台副本。平台不会修改桥接组源代码。

`127.0.0.1:5173` 与局域网 IP（如 `192.168.x.x:5173`）属于不同浏览器来源，登录状态不会互通。首次使用局域网地址时需要重新登录；令牌过期后平台会自动返回登录页，不再把未授权响应误显示为"0 条历史数据"。
