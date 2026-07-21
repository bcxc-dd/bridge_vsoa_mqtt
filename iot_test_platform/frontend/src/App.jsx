import React, { useCallback, useEffect, useRef, useState } from "react";
import ReactECharts from "echarts-for-react";
import {
  Activity, ArrowDownToLine, ArrowRight, ArrowUpFromLine,
  AlertTriangle, BarChart3, Bell, Boxes, Cable, Check, ChevronDown, CloudCog, Cpu,
  Database, Download, FileCheck2, FlaskConical, Gauge, GitCompare,
  Layers3, Lightbulb, Link2, LoaderCircle, LogOut, Menu, Moon,
  MessageSquareText, Network, Play, Radio, RefreshCw, Search, Server,
  Save, Settings2, ShieldCheck, SlidersHorizontal, Sun, Trash2, UserCog,
  Waves, Wifi, WifiOff, X, Zap
} from "lucide-react";

const API = `${location.protocol}//${location.hostname}:8000`;
const WS = `${location.protocol === "https:" ? "wss" : "ws"}://${location.hostname}:8000/ws`;
const AUTH_KEY = "smart-environment-auth";
const THEME_KEY = "smart-environment-theme";
const VSOA_HISTORY_KEY = "iot-platform-vsoa-history";
const DEFAULT_VSOA_URLS = ["vsoa://127.0.0.1:3001"];
const projectName = { lora: "LoRa / LoRaWAN", zigbee: "ZigBee", generic: "通用设备" };
const nav = [
  ["overview", "环境总览", Gauge, "user"], ["devices", "设备中心", Boxes, "user"],
  ["alerts", "告警中心", Bell, "user"], ["topology", "节点拓扑", Network, "user"],
  ["stream", "消息追踪", MessageSquareText, "tester"], ["mapping", "链路转换", GitCompare, "tester"],
  ["simulator", "数据模拟", Waves, "tester"], ["performance", "性能诊断", Activity, "tester"],
  ["runs", "运维检查", FlaskConical, "tester"], ["admin", "系统管理", UserCog, "admin"]
];
const roleRank = { user: 1, tester: 2, admin: 3 };
let authToken = "";

async function api(path, options) {
  const response = await fetch(API + path, {
    headers: { "Content-Type": "application/json", ...(authToken ? { Authorization: `Bearer ${authToken}` } : {}), ...(options?.headers || {}) },
    ...options
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || "请求失败");
  }
  return response.json();
}

function loadVsoaHistory() {
  try {
    const saved = JSON.parse(localStorage.getItem(VSOA_HISTORY_KEY) || "[]");
    return [...new Set([...saved, ...DEFAULT_VSOA_URLS])].slice(0, 8);
  } catch {
    return DEFAULT_VSOA_URLS;
  }
}

function Login({ onLogin, theme, toggleTheme }) {
  const [form, setForm] = useState({ username: "user", password: "user123" });
  const [busy, setBusy] = useState(false); const [error, setError] = useState("");
  const submit = async event => {
    event.preventDefault(); setBusy(true); setError("");
    try { onLogin(await api("/api/auth/login", { method: "POST", body: JSON.stringify(form) })); }
    catch (e) { setError(e.message); }
    finally { setBusy(false); }
  };
  return <div className="login-page"><div className="login-visual"><div className="login-grid" /><div className="login-copy"><span>ACOINFO × NUAA</span><h1>智慧环境<br />设备管理平台</h1><p>统一连接 LoRa、ZigBee 与 MQTT-VSOA 协议桥接，面向真实设备状态、环境告警和安全控制。</p><div className="login-projects"><b>LoRaWAN</b><b>ZigBee</b><b>VSOA Bridge</b></div></div></div><form className="login-form" onSubmit={submit}><button type="button" className="theme-login" onClick={toggleTheme} title="切换主题">{theme === "dark" ? <Sun size={18} /> : <Moon size={18} />}</button><div><span>SECURE ACCESS</span><h2>登录平台</h2><p>使用分配的账号进入对应工作区</p></div><label>用户名<input autoFocus value={form.username} onChange={e => setForm(x => ({ ...x, username: e.target.value }))} /></label><label>密码<input type="password" value={form.password} onChange={e => setForm(x => ({ ...x, password: e.target.value }))} /></label>{error && <div className="form-error">{error}</div>}<button className="primary login-submit" disabled={busy}>{busy ? <LoaderCircle className="spin" size={18} /> : <ShieldCheck size={18} />}{busy ? "验证中" : "安全登录"}</button><small>首次登录演示账号：user / user123</small></form></div>;
}

function Pill({ online, children }) {
  return <span className={"pill " + (online ? "online" : "standby")}><i />{children}</span>;
}

function Empty({ icon: Icon, title, detail }) {
  return <div className="empty"><Icon size={28} /><strong>{title}</strong><span>{detail}</span></div>;
}

function Metric({ icon: Icon, label, value, unit, tone, hint }) {
  return <div className="metric"><span className={"metric-icon " + tone}><Icon size={18} /></span><div><label>{label}</label><strong>{value}<small>{unit}</small></strong><em>{hint}</em></div></div>;
}

function EventRow({ item, active, select }) {
  const p = item.payload || {};
  const value = p.temperature ?? p.humidity ?? p.value ?? p.error_code;
  return <button className={"event-row " + (active ? "active" : "")} onClick={select}>
    <span className={"event-dir " + item.direction}>{item.direction === "result" ? <ArrowDownToLine size={14} /> : <ArrowUpFromLine size={14} />}</span>
    <time>{new Date(item.timestamp).toLocaleTimeString("zh-CN", { hour12: false })}</time>
    <span className={"tag " + item.project}>{projectName[item.project] || item.project}</span>
    <strong>{item.device_id}</strong><code title={item.channel}>{item.channel}</code>
    <span>{value !== undefined ? String(value) : "JSON"}</span>
    <span className={"state " + item.status}>{item.status === "ok" ? "正常" : "异常"}</span>
  </button>;
}

function Drawer({ event, close }) {
  if (!event) return null;
  return <aside className="drawer">
    <header><div><span>EVENT INSPECTOR</span><strong>{event.device_id}</strong></div><button className="icon-btn" onClick={close}><X size={18} /></button></header>
    <div className="drawer-grid">
      <label>来源<strong>{event.source}</strong></label><label>方向<strong>{event.direction}</strong></label>
      <label>项目<strong>{projectName[event.project] || event.project}</strong></label><label>延迟<strong>{event.latency_ms ? event.latency_ms + " ms" : "--"}</strong></label>
    </div>
    <section><h4>通道</h4><code>{event.channel}</code></section>
    <section><h4>Payload</h4><pre>{JSON.stringify(event.payload, null, 2)}</pre></section>
    <section><h4>关联标识</h4><code>{event.trace_id || "未提供 trace_id"}</code></section>
  </aside>;
}

function Pipeline({ status }) {
  const nodes = [
    [Radio, "设备侧", "LoRa · ZigBee", true],
    [Wifi, "MQTT Broker", status.mqtt?.connected ? (status.mqtt.connected_count > 1 ? `${status.mqtt.connected_count} 个 Broker` : status.mqtt.host) : "等待连接", status.mqtt?.connected],
    [CloudCog, "协议桥接", "MQTT ↔ VSOA", status.bridge?.connected],
    [Network, "VSOA", status.vsoa?.connected ? "订阅运行中" : "等待项目服务", status.vsoa?.connected],
    [BarChart3, "管理平台", "展示 · 告警", true]
  ];
  return <div className="pipeline">{nodes.map(([Icon, name, detail, active], index) =>
    <div className="pipeline-part" key={name}>
      <div className={"pipeline-node " + (active ? "active" : "")}><span><Icon size={20} /></span><div><strong>{name}</strong><small>{detail}</small></div></div>
      {index < nodes.length - 1 && <div className={"pipe " + (nodes[index + 1][3] ? "active" : "")}><i /><ArrowRight size={15} /></div>}
    </div>
  )}</div>;
}

function temperatureOption(series, theme = "dark") {
  const points = series.points || [];
  const light = theme === "light";
  const axis = light ? "#aab9be" : "#354147";
  const label = light ? "#6b7c83" : "#7f8b91";
  const grid = light ? "#e2e9eb" : "#252e33";
  return {
    animationDuration: 300,
    grid: { top: 22, right: 18, bottom: 38, left: 48 },
    tooltip: {
      trigger: "axis",
      backgroundColor: "#161c20",
      borderColor: "#344148",
      textStyle: { color: "#eef3f4" },
      formatter: values => {
        const point = points[values[0]?.dataIndex];
        if (!point) return "";
        return `${new Date(point.timestamp).toLocaleString("zh-CN", { hour12: false })}<br/>温度&nbsp;&nbsp;<b>${point.temperature} °C</b>`;
      }
    },
    dataZoom: points.length > 80 ? [{ type: "inside", start: 0, end: 100 }, { type: "slider", height: 12, bottom: 5, borderColor: "transparent", backgroundColor: light ? "#edf2f3" : "#1c2428", dataBackground: { lineStyle: { color: light ? "#b7c8cc" : "#526168" }, areaStyle: { color: light ? "#dfe8ea" : "#263238" } }, fillerColor: light ? "rgba(8,127,117,.15)" : "rgba(57,212,194,.2)", handleStyle: { color: light ? "#5aa79f" : "#39d4c2" }, textStyle: { color: label } }] : [],
    xAxis: {
      type: "category",
      data: points.map(point => new Date(point.timestamp).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false })),
      axisLine: { lineStyle: { color: axis } },
      axisLabel: { color: label, fontSize: 10, hideOverlap: true },
      axisTick: { show: false }
    },
    yAxis: {
      type: "value", scale: true, name: "°C", nameTextStyle: { color: label, fontSize: 9 },
      splitLine: { lineStyle: { color: grid } }, axisLabel: { color: label, fontSize: 10 }
    },
    series: [{
      name: series.device_id, type: "line", data: points.map(point => point.temperature),
      smooth: .28, showSymbol: points.length < 20, symbolSize: 5,
      lineStyle: { color: light ? "#168f84" : "#39d4c2", width: 2 }, areaStyle: { color: light ? "rgba(8,127,117,.09)" : "rgba(57,212,194,.12)" }
    }]
  };
}

function Overview({ events, status, go, temperatureSeries, role, theme }) {
  const [temperatureDevice, setTemperatureDevice] = useState("");
  useEffect(() => {
    setTemperatureDevice(current => temperatureSeries.some(series => series.device_id === current)
      ? current
      : temperatureSeries[0]?.device_id || "");
  }, [temperatureSeries]);
  const selectedTemperature = temperatureSeries.find(series => series.device_id === temperatureDevice);
  const m = status.metrics || {};
  return <>
    <div className="metrics">
      <Metric icon={Activity} label="近一小时消息" value={m.messages_hour ?? 0} unit="条" tone="cyan" hint="双向数据合计" />
      <Metric icon={Cpu} label="活跃设备" value={m.active_devices ?? 0} unit="台" tone="blue" hint="已识别设备" />
      <Metric icon={Zap} label="平均桥接延迟" value={m.avg_latency_ms ?? 0} unit="ms" tone="amber" hint="VSOA 结果样本" />
      <Metric icon={ShieldCheck} label="链路成功率" value={m.success_rate ?? 100} unit="%" tone="green" hint="近一小时" />
    </div>
    <section className="panel pipeline-panel"><div className="panel-head"><div><span>SYSTEM TOPOLOGY</span><h2>设备数据链路</h2></div><Pill online={status.platform?.mode === "ready"}>{status.platform?.mode === "ready" ? "链路已连接" : "等待连接"}</Pill></div><Pipeline status={status} /></section>
    <div className="overview-grid">
      <section className="panel trend"><div className="panel-head compact"><div><span>DEVICE TELEMETRY</span><h2>设备温度趋势</h2></div>{temperatureSeries.length ? <div className="temperature-picker"><span>{temperatureSeries.length} 台设备 · 每台最多 500 点</span><select aria-label="选择温度设备" value={temperatureDevice} onChange={event => setTemperatureDevice(event.target.value)}>{temperatureSeries.map(series => <option value={series.device_id} key={series.device_id}>{series.device_id}</option>)}</select></div> : <button className="text-btn" onClick={() => go("devices")}><Boxes size={15} />查看设备</button>}</div>{selectedTemperature ? <div className="device-trend-row"><div className="device-trend-meta"><div><strong>{selectedTemperature.device_id}</strong><span>{projectName[selectedTemperature.project] || selectedTemperature.project || "通用设备"} · VSOA /device/update</span></div><div className="device-trend-kpis"><span>最新 <b>{selectedTemperature.latest_temperature} °C</b></span><span>{selectedTemperature.point_count} 个采样点</span></div></div><ReactECharts option={temperatureOption(selectedTemperature, theme)} style={{ height: 250 }} notMerge lazyUpdate /></div> : <Empty icon={BarChart3} title="等待设备温度" detail="收到包含 temperature 的真实设备数据后显示" />}</section>
      <section className="panel health"><div className="panel-head compact"><div><span>SERVICE HEALTH</span><h2>服务状态</h2></div></div>{[
        ["平台服务 API", true, `${location.hostname}:8000`, Server], ["MQTT Broker", status.mqtt?.connected, status.mqtt?.connected_count > 1 ? `${status.mqtt.connected_count} 个 Broker 已连接` : status.mqtt?.host || "未连接", Wifi],
        ["协议桥接连接", status.bridge?.connected, status.bridge?.health?.version ? `v${status.bridge.health.version} · ${status.bridge.health.devices} 台设备` : status.vsoa?.url || "未连接", Link2], ["VSOA 事件流", status.vsoa?.connected, status.vsoa?.url || "未连接", Network]
      ].map(([name, online, detail, Icon]) => <div className="health-row" key={name}><span className={online ? "active" : ""}><Icon size={17} /></span><div><strong>{name}</strong><small>{detail}</small></div><Pill online={online}>{online ? "在线" : "待机"}</Pill></div>)}</section>
    </div>
    {roleRank[role] >= roleRank.tester && <section className="panel"><div className="panel-head compact"><div><span>LATEST EVENTS</span><h2>最新链路消息</h2></div><button className="text-btn" onClick={() => go("stream")}>查看全部<ArrowRight size={15} /></button></div><EventHead /><div className="event-list mini">{events.slice(0, 6).map(e => <EventRow key={e.id} item={e} />)}</div></section>}
  </>;
}

function Topology({ devices, events, status }) {
  const realDevices = devices.filter(device => !device.device_id.startsWith("perf-"));
  const [showOffline, setShowOffline] = useState(true);
  const [selectedId, setSelectedId] = useState("");
  const [notice, setNotice] = useState(null);
  const previousStates = useRef(null);

  useEffect(() => {
    setSelectedId(current => realDevices.some(device => device.device_id === current)
      ? current
      : realDevices.find(device => device.online)?.device_id || realDevices[0]?.device_id || "");
  }, [devices]);

  useEffect(() => {
    const current = new Map(realDevices.map(device => [device.device_id, device.online]));
    if (previousStates.current) {
      let change = null;
      current.forEach((online, deviceId) => {
        const before = previousStates.current.get(deviceId);
        if (before === undefined && online) change = { deviceId, online, text: "新设备已接入" };
        else if (before !== undefined && before !== online) change = { deviceId, online, text: online ? "设备已恢复连接" : "设备连接已断开" };
      });
      previousStates.current.forEach((online, deviceId) => {
        if (online && !current.has(deviceId)) change = { deviceId, online: false, text: "设备已离开节点列表" };
      });
      if (change) setNotice({ ...change, id: Date.now() });
    }
    previousStates.current = current;
  }, [devices]);

  useEffect(() => {
    if (!notice) return undefined;
    const timer = setTimeout(() => setNotice(null), 4500);
    return () => clearTimeout(timer);
  }, [notice]);

  const displayCandidates = showOffline ? realDevices : realDevices.filter(device => device.online);
  const visibleDevices = displayCandidates.slice(0, 16);
  const positions = visibleDevices.map((device, index) => {
    const angle = -Math.PI / 2 + index * Math.PI * 2 / Math.max(visibleDevices.length, 1);
    const compact = visibleDevices.length <= 4;
    return { ...device, x: 50 + Math.cos(angle) * (compact ? 34 : 40), y: 50 + Math.sin(angle) * (compact ? 31 : 38) };
  });
  const selected = realDevices.find(device => device.device_id === selectedId);
  const telemetry = events.find(event => event.device_id === selectedId && ["temperature", "humidity", "signal", "battery"].some(key => event.payload?.[key] !== undefined))?.payload || selected?.latest_payload || {};
  const onlineCount = realDevices.filter(device => device.online).length;
  const bridgeOnline = Boolean(status.bridge?.connected && status.vsoa?.connected);
  const SelectedIcon = selected?.project === "lora" ? Radio : selected?.project === "zigbee" ? Cable : Wifi;

  return <section className="panel topology-page">
    <div className="panel-head"><div><span>LIVE NODE TOPOLOGY</span><h2>动态设备节点拓扑</h2></div><div className="topology-actions"><div className="topology-count"><strong>{onlineCount}</strong><span>/ {realDevices.length} 在线</span></div><button className={showOffline ? "active" : ""} onClick={() => setShowOffline(value => !value)}>{showOffline ? <Wifi size={15} /> : <WifiOff size={15} />}{showOffline ? "显示全部" : "仅看在线"}</button></div></div>
    <div className="topology-layout">
      <div className="topology-stage">
        <svg className="topology-wires" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">{positions.map(device => <line key={device.device_id} className={device.online && bridgeOnline ? "online" : "offline"} x1="50" y1="50" x2={device.x} y2={device.y} />)}</svg>
        <div className={"atom-core " + (bridgeOnline ? "online" : "offline")}>
          <span className="atom-orbit orbit-one"><i /></span><span className="atom-orbit orbit-two"><i /></span><span className="atom-orbit orbit-three"><i /></span>
          <span className="atom-nucleus"><Network size={26} /></span><strong>BRIDGE CORE</strong><small>{bridgeOnline ? "链路运行中" : "链路已断开"}</small>
        </div>
        {positions.map(device => {
          const Icon = device.project === "lora" ? Radio : device.project === "zigbee" ? Cable : Wifi;
          return <button title={`${device.device_id} · ${device.online ? "在线" : "离线"}`} data-connection-state={device.online ? "online" : "offline"} className={`topology-node ${device.online ? "online" : "offline"} ${selectedId === device.device_id ? "selected" : ""}`} style={{ "--node-x": `${device.x}%`, "--node-y": `${device.y}%` }} onClick={() => setSelectedId(device.device_id)} key={device.device_id}><span className="node-icon"><Icon size={19} /><i /></span><span className="node-copy"><strong>{device.device_id}</strong><small>{projectName[device.project] || device.project}</small></span><b>{device.online ? "ONLINE" : "OFFLINE"}</b></button>;
        })}
        {!positions.length && <Empty icon={Network} title="暂无可显示节点" detail="连接设备并收到消息后生成拓扑" />}
        {displayCandidates.length > 16 && <span className="topology-overflow">另有 {displayCandidates.length - 16} 个节点</span>}
      </div>
      <aside className="topology-inspector">
        <header><div><span>NODE INSPECTOR</span><strong>{selected?.device_id || "选择一个设备"}</strong></div>{selected && <Pill online={selected.online}>{selected.online ? "在线" : "离线"}</Pill>}</header>
        {selected ? <><div className="selected-node"><span className={selected.online ? "online" : ""}><SelectedIcon size={24} /></span><div><strong>{projectName[selected.project] || selected.project}</strong><small>最后上报 {new Date(selected.last_seen).toLocaleString("zh-CN", { hour12: false })}</small></div></div>
          <div className="node-metrics"><div><span>消息数量</span><strong>{selected.messages}</strong></div><div><span>异常数量</span><strong>{selected.errors}</strong></div><div><span>温度</span><strong>{telemetry.temperature ?? "--"}<small>{telemetry.temperature !== undefined ? "°C" : ""}</small></strong></div><div><span>信号</span><strong>{telemetry.signal ?? telemetry.rssi ?? "--"}<small>{telemetry.signal !== undefined || telemetry.rssi !== undefined ? "dBm" : ""}</small></strong></div></div>
          <div className="node-channels"><span>活动通道</span>{selected.channels.map(channel => <code key={channel}>{channel}</code>)}</div>
          <div className="node-payload"><span>最新遥测</span><pre>{JSON.stringify(telemetry, null, 2)}</pre></div></> : <Empty icon={Radio} title="选择设备节点" detail="点击图中的设备查看实时状态" />}
      </aside>
    </div>
    {notice && <div className={`topology-notice ${notice.online ? "online" : "offline"}`}>{notice.online ? <Wifi size={18} /> : <WifiOff size={18} />}<div><strong>{notice.text}</strong><span>{notice.deviceId}</span></div><button onClick={() => setNotice(null)} aria-label="关闭状态提示"><X size={15} /></button></div>}
  </section>;
}

function EventHead() {
  return <div className="event-head"><span /><span>时间</span><span>项目</span><span>设备</span><span>通道</span><span>值</span><span>状态</span></div>;
}

function Stream({ events, selected, setSelected }) {
  const [query, setQuery] = useState(""); const [source, setSource] = useState("all");
  const filtered = events.filter(e => (source === "all" || e.source.includes(source)) && (e.device_id + " " + e.channel + " " + JSON.stringify(e.payload)).toLowerCase().includes(query.toLowerCase()));
  return <section className="panel page-panel"><div className="panel-head"><div><span>LIVE MESSAGE STREAM</span><h2>MQTT / VSOA 实时消息流</h2></div><b className="stream-count"><i />{filtered.length} 条缓存消息</b></div>
    <div className="toolbar"><label className="search"><Search size={16} /><input value={query} onChange={e => setQuery(e.target.value)} placeholder="搜索设备、topic 或字段" /></label><div className="segments">{[["all", "全部"], ["mqtt", "MQTT"], ["vsoa", "VSOA"]].map(([id, label]) => <button className={source === id ? "active" : ""} onClick={() => setSource(id)} key={id}>{label}</button>)}</div></div>
    <EventHead /><div className="event-list full">{filtered.length ? filtered.map(e => <EventRow key={e.id} item={e} active={selected?.id === e.id} select={() => setSelected(e)} />) : <Empty icon={MessageSquareText} title="没有匹配的消息" detail="调整筛选条件或连接数据源" />}</div>
  </section>;
}

function Devices({ devices, commands, refreshCommands, role }) {
  const [project, setProject] = useState("all"); const [online, setOnline] = useState("all"); const [showSimulated, setShowSimulated] = useState(false); const [selected, setSelected] = useState(null); const [busy, setBusy] = useState(false);
  const visibleDevices = devices.filter(item => !item.simulated || (role !== "user" && showSimulated));
  const filtered = visibleDevices.filter(item => (project === "all" || item.project === project) && (online === "all" || String(item.online) === online));
  const send = async (command, parameters = {}) => {
    if (!selected || !window.confirm(`确认向设备 ${selected.name || selected.device_id} 下发“${command}”命令？`)) return;
    setBusy(true); try { await api("/api/commands", { method: "POST", body: JSON.stringify({ device_id: selected.device_id, project: selected.project, command, parameters, confirmed: true }) }); await refreshCommands(); } catch (e) { alert(e.message); } finally { setBusy(false); }
  };
  const payload = selected?.latest_payload?.data && typeof selected.latest_payload.data === "object" ? selected.latest_payload.data : selected?.latest_payload || {};
  return <div className="device-workspace"><section className="panel device-list"><div className="panel-head"><div><span>SMART DEVICE INVENTORY</span><h2>环境设备</h2></div><Pill online={visibleDevices.some(x => x.online)}>{visibleDevices.filter(x => x.online).length}/{visibleDevices.length} 在线</Pill></div><div className="toolbar device-toolbar"><div className="segments">{[["all", "全部项目"], ["lora", "LoRa"], ["zigbee", "ZigBee"], ["generic", "桥接/通用"]].map(([id, label]) => <button key={id} className={project === id ? "active" : ""} onClick={() => setProject(id)}>{label}</button>)}</div><div className="device-filter-end">{role !== "user" && <label><input type="checkbox" checked={showSimulated} onChange={e => setShowSimulated(e.target.checked)} />显示模拟设备</label>}<select value={online} onChange={e => setOnline(e.target.value)}><option value="all">全部状态</option><option value="true">仅在线</option><option value="false">仅离线</option></select></div></div>{filtered.length ? <div className="smart-device-grid">{filtered.map(item => { const data = item.latest_payload?.data && typeof item.latest_payload.data === "object" ? item.latest_payload.data : item.latest_payload || {}; return <button className={"smart-device " + (selected?.device_id === item.device_id ? "active" : "")} onClick={() => setSelected(item)} key={`${item.project}:${item.device_id}`}><header><span className={"device-signal " + (item.online ? "online" : "")}><Radio size={17} /></span><div><strong>{item.name || item.device_id}</strong><small>{projectName[item.project]} · {item.device_type}</small></div><Pill online={item.online}>{item.online ? "在线" : "离线"}</Pill></header><div className="telemetry-preview">{[["temperature", "温度", "°C"], ["humidity", "湿度", "%"], ["battery", "电量", "%"], ["signal", "信号", "dBm"]].filter(([key]) => data[key] != null).slice(0, 3).map(([key, label, unit]) => <span key={key}><small>{label}</small><strong>{String(data[key])}<i>{unit}</i></strong></span>)}</div><footer><code>{item.device_id}</code><span>{item.last_seen ? new Date(item.last_seen).toLocaleTimeString("zh-CN", { hour12: false }) : "从未上报"}</span></footer></button>; })}</div> : <Empty icon={Boxes} title="没有符合条件的设备" detail="调整项目或在线状态筛选" />}</section><aside className="panel device-inspector"><div className="panel-head"><div><span>DEVICE DETAIL</span><h2>{selected ? selected.name || selected.device_id : "选择设备"}</h2></div>{selected && <Pill online={selected.online}>{selected.online ? "实时在线" : "当前离线"}</Pill>}</div>{selected ? <><div className="inspector-meta"><span>项目<strong>{projectName[selected.project]}</strong></span><span>接入来源<strong>{selected.connection_source || selected.channels?.[0] || "--"}</strong></span><span>最后通信<strong>{selected.last_seen ? new Date(selected.last_seen).toLocaleString("zh-CN") : "--"}</strong></span></div><div className="capability-grid">{Object.entries(payload).filter(([, value]) => typeof value !== "object").slice(0, 12).map(([key, value]) => <div key={key}><span>{key}</span><strong>{String(value)}</strong></div>)}</div><div className="device-controls"><strong>安全控制</strong><div><button disabled={busy || !selected.online} onClick={() => send("turn_on", { state: true })}><Lightbulb size={16} />开启</button><button disabled={busy || !selected.online} onClick={() => send("turn_off", { state: false })}><Lightbulb size={16} />关闭</button><button disabled={busy || !selected.online} onClick={() => send("refresh", {})}><RefreshCw size={16} />刷新状态</button></div><small>每次下发前均需确认；ACK 由真实设备或桥接服务返回。</small></div><div className="command-timeline"><strong>最近控制记录</strong>{commands.filter(x => x.device_id === selected.device_id).slice(0, 6).map(item => <div key={item.id}><i className={item.status} /><span>{item.command}</span><code>{item.status}</code><small>{new Date(item.requested_at).toLocaleTimeString("zh-CN")}</small></div>)}</div></> : <Empty icon={Radio} title="从左侧选择一台设备" detail="查看实时指标、连接来源和控制记录" />}</aside></div>;
}

function Mapping({ pairs }) {
  const [selected, setSelected] = useState(null);
  useEffect(() => { if (!selected && pairs.length) setSelected(pairs[0]); }, [pairs, selected]);
  const mappings = selected?.field_mappings || [];
  const problemMappings = mappings.filter(item => item.status !== "matched");
  return <div className="mapping-layout">
    <section className="panel pair-list"><div className="panel-head"><div><span>TRACE CORRELATION</span><h2>转换记录</h2></div><b>{pairs.length} 组关联</b></div>{pairs.length ? <div className="pair-scroll">{pairs.map(pair => <button key={pair.id} className={selected?.id === pair.id ? "active" : ""} onClick={() => setSelected(pair)}><span className="pair-icon"><GitCompare size={16} /></span><div><strong>{pair.device_id}</strong><code>{pair.input.channel}</code></div><em>{pair.latency_ms} ms</em></button>)}</div> : <Empty icon={GitCompare} title="等待转换数据" detail="收到 MQTT 输入与 VSOA 输出后将自动建立关联" />}</section>
    <section className="panel mapping-workbench"><div className="panel-head"><div><span>PROTOCOL MAPPING</span><h2>MQTT → VSOA 转换观察器</h2></div>{selected && <span className={problemMappings.length || selected.mapping_error ? "mapping-warn" : "mapping-ok"}>{problemMappings.length || selected.mapping_error ? <AlertTriangle size={14} /> : <Check size={14} />}{selected.mapping_error ? "项目适配器解析失败" : problemMappings.length ? `${problemMappings.length} 个规范字段异常` : "项目字段映射正常"}</span>}</div>
      {selected ? <><div className="mapping-meta"><div><span>设备</span><strong>{selected.device_id}</strong></div><div><span>项目适配器</span><strong>{selected.adapter || projectName[selected.project]}</strong></div><div><span>桥接耗时</span><strong>{selected.latency_ms} ms</strong></div><div><span>匹配字段</span><strong>{selected.matched_fields.length}</strong></div></div><div className="payload-compare"><div><header><Wifi size={16} /><strong>MQTT 原始输入</strong><code>{selected.input.channel}</code></header><pre>{JSON.stringify(selected.input.payload, null, 2)}</pre></div><span className="compare-arrow"><ArrowRight /></span><div><header><Network size={16} /><strong>VSOA 输出</strong><code>{selected.output.channel}</code></header><pre>{JSON.stringify(selected.output.payload, null, 2)}</pre></div></div><div className="mapping-note"><strong>判定依据</strong><span>由本机项目 {selected.adapter || "adapter"} 解析原始 MQTT，再与 VSOA 规范字段比较；LoRaWAN 传输元数据不计为漏映射。</span></div>{selected.mapping_error ? <div className="form-error">{selected.mapping_error}</div> : <div className="field-map"><strong>项目规范字段映射</strong><div>{mappings.map(item => <span className={item.status === "matched" ? "" : "missing"} key={`${item.source}-${item.target}`}><code>{item.source}</code><ArrowRight size={13} /><code>{item.target}</code>{item.status === "matched" ? <Check size={13} /> : <AlertTriangle size={13} />}</span>)}</div>{selected.generated_fields?.length > 0 && <small>VSOA 生成字段：{selected.generated_fields.join(" · ")}</small>}</div>}</> : <Empty icon={Cable} title="选择一条转换记录" detail="可并排检查 topic、URL、payload 与字段映射" />}
    </section>
  </div>;
}

function Simulator({ status, project, go }) {
  const supported = project.supported_sources || [];
  const [form, setForm] = useState({ project: supported[0] || "lora", device_id: "project-test-01", interval_ms: 650, count: 40 });
  const [running, setRunning] = useState(false); const [error, setError] = useState("");
  const set = (key, value) => setForm(f => ({ ...f, [key]: value }));
  const start = async () => { setRunning(true); setError(""); try { await api("/api/simulations", { method: "POST", body: JSON.stringify(form) }); setTimeout(() => go("overview"), 900); } catch (e) { setError(e.message); } finally { setTimeout(() => setRunning(false), 1200); } };
  return <div className="sim-grid">
    <section className="panel controls"><div className="panel-head"><div><span>PROJECT DATA INJECTOR</span><h2>项目设备数据注入</h2></div><SlidersHorizontal size={20} /></div>
      <Field title="项目当前已订阅类型"><div className="segments wide">{supported.map(id => <button className={form.project === id ? "active" : ""} onClick={() => set("project", id)} key={id}>{projectName[id]}</button>)}</div></Field>
      <div className="field-row"><label>设备编号<input value={form.device_id} onChange={e => set("device_id", e.target.value)} /></label><label>发送条数<input type="number" value={form.count} onChange={e => set("count", Number(e.target.value))} /></label></div>
      <div className="field-row"><label>发送间隔 ms<input type="number" value={form.interval_ms} onChange={e => set("interval_ms", Number(e.target.value))} /></label><label>项目数据源<input value="tools/sim_device.py" disabled /></label></div>
      <div className="project-source-note"><Database size={17} /><div><strong>数据格式来自本机桥接仓库</strong><code>{project.root || "bridge_vsoa_mqtt"}</code></div></div>
      {error && <div className="form-error">{error}</div>}<button className="primary" onClick={start} disabled={running || !status.mqtt?.connected || !supported.length}>{running ? <LoaderCircle className="spin" size={18} /> : <Play size={18} />}{running ? "正在启动" : status.mqtt?.connected ? "向项目 Broker 注入" : "请先连接项目 Broker"}</button>
    </section>
    <section className="panel preview"><div className="panel-head compact"><div><span>PROJECT CONTRACT</span><h2>当前项目测试契约</h2></div><b>config.yaml</b></div>
      <div className="contract-list"><div><span>MQTT Broker</span><code>{project.mqtt?.broker}:{project.mqtt?.port}</code></div><div><span>上行 Topic</span><code>{(project.mqtt?.uplink_topics || []).join(" · ")}</code></div><div><span>VSOA Server</span><code>{project.vsoa?.local_url}</code></div><div><span>转换输出</span><code>/device/update · /bridge/event</code></div></div>
      <div className="preview-stats"><div><span>预计持续</span><strong>{(form.count * form.interval_ms / 1000).toFixed(1)} s</strong></div><div><span>发送条数</span><strong>{form.count}</strong></div><div><span>桥接状态</span><strong>{status.bridge?.connected ? "健康" : "未就绪"}</strong></div></div>
    </section>
  </div>;
}

function Field({ title, children }) { return <div className="field"><label>{title}</label>{children}</div>; }

function Performance({ runs, status, project, refresh }) {
  const supported = project.supported_sources || [];
  const [form, setForm] = useState({ project: supported[0] || "lora", device_count: 5, rate: 20, duration_seconds: 15, pattern: "steady" });
  const [selectedId, setSelectedId] = useState(null); const [busy, setBusy] = useState(false); const [error, setError] = useState("");
  const selected = runs.find(item => item.id === selectedId) || runs[0];
  const set = (key, value) => setForm(item => ({ ...item, [key]: value }));
  const start = async () => { setBusy(true); setError(""); try { const run = await api("/api/performance-runs", { method: "POST", body: JSON.stringify(form) }); setSelectedId(run.id); await refresh(); } catch (e) { setError(e.message); } finally { setBusy(false); } };
  const metrics = selected?.metrics || {};
  const series = selected?.series || [];
  const throughputOption = {
    animationDuration: 250, grid: { top: 34, right: 20, bottom: 30, left: 46 },
    legend: { top: 4, right: 8, textStyle: { color: "#849096", fontSize: 9 } },
    tooltip: { trigger: "axis", backgroundColor: "#161c20", borderColor: "#344148", textStyle: { color: "#eef3f4" } },
    xAxis: { type: "category", data: series.map(x => x.second + "s"), axisLine: { lineStyle: { color: "#354147" } }, axisLabel: { color: "#78858b", fontSize: 9 } },
    yAxis: { type: "value", name: "msg/s", nameTextStyle: { color: "#647177" }, splitLine: { lineStyle: { color: "#252e33" } }, axisLabel: { color: "#78858b", fontSize: 9 } },
    series: [
      { name: "MQTT 输入", type: "line", smooth: .25, symbol: "none", data: series.map(x => x.input), lineStyle: { color: "#56a8ff", width: 2 }, areaStyle: { color: "rgba(86,168,255,.10)" } },
      { name: "VSOA 输出", type: "line", smooth: .25, symbol: "none", data: series.map(x => x.output), lineStyle: { color: "#39d4c2", width: 2 } }
    ]
  };
  const latencyOption = {
    grid: { top: 18, right: 16, bottom: 28, left: 48 },
    xAxis: { type: "category", data: ["P50", "平均", "P95", "P99"], axisLine: { lineStyle: { color: "#354147" } }, axisLabel: { color: "#849096" } },
    yAxis: { type: "value", name: "ms", nameTextStyle: { color: "#647177" }, splitLine: { lineStyle: { color: "#252e33" } }, axisLabel: { color: "#78858b" } },
    series: [{ type: "bar", barWidth: "42%", data: [metrics.p50_latency_ms || 0, metrics.avg_latency_ms || 0, metrics.p95_latency_ms || 0, metrics.p99_latency_ms || 0], itemStyle: { color: p => ["#39d4c2", "#56a8ff", "#ffbd5a", "#ff6f76"][p.dataIndex] } }]
  };
  const sent = metrics.sent || 0; const received = metrics.received || 0; const converted = metrics.converted || 0;
  const planned = selected ? selected.config.rate * selected.config.duration_seconds : 0;
  return <div className="performance-page">
    <section className="panel perf-controls"><div className="panel-head"><div><span>PROJECT LOAD PROFILE</span><h2>桥接项目性能参数</h2></div><Pill online={status.bridge?.connected}>{status.bridge?.connected ? "项目健康" : "项目未就绪"}</Pill></div>
      <Field title="config.yaml 已订阅类型"><div className="segments wide">{supported.map(id => <button key={id} className={form.project === id ? "active" : ""} onClick={() => set("project", id)}>{projectName[id]}</button>)}</div></Field>
      <div className="field-row">{[["device_count", "并发设备数"], ["rate", "总速率 msg/s"], ["duration_seconds", "持续时间 s"]].map(([id, label]) => <label key={id}>{label}<input type="number" value={form[id]} onChange={e => set(id, Number(e.target.value))} /></label>)}</div>
      <Field title="流量模式"><div className="segments wide"><button className={form.pattern === "steady" ? "active" : ""} onClick={() => set("pattern", "steady")}><Activity size={15} />稳定流</button><button className={form.pattern === "burst" ? "active" : ""} onClick={() => set("pattern", "burst")}><Zap size={15} />突发流</button></div></Field>
      <div className="perf-estimate"><span>预计消息</span><strong>{form.rate * form.duration_seconds}</strong><span>数据模型</span><strong>项目 SimDevice</strong></div>
      {error && <div className="form-error">{error}</div>}<button className="primary" onClick={start} disabled={busy || !status.mqtt?.connected || !status.bridge?.connected || runs.some(x => x.status === "running")}>{busy ? <LoaderCircle className="spin" size={17} /> : <Play size={17} />}{status.bridge?.connected ? "启动项目性能测试" : "请先启动并连接桥接项目"}</button>
    </section>
    <section className="panel perf-live"><div className="panel-head"><div><span>LIVE BENCHMARK</span><h2>{selected ? selected.id : "等待测试任务"}</h2></div>{selected && <span className={"run-state " + selected.status}>{selected.status === "running" ? <LoaderCircle className="spin" size={14} /> : <Check size={14} />}{selected.status === "running" ? `${metrics.progress || 0}%` : "项目实测"}</span>}</div>
      {selected ? <><div className="perf-kpis"><div><span>输入吞吐</span><strong>{metrics.throughput || 0}<small>msg/s</small></strong></div><div><span>转化成功率</span><strong>{metrics.conversion_rate || 0}<small>%</small></strong></div><div><span>P95 延迟</span><strong>{metrics.p95_latency_ms || 0}<small>ms</small></strong></div><div><span>P99 延迟</span><strong>{metrics.p99_latency_ms || 0}<small>ms</small></strong></div></div><div className="perf-progress"><i style={{ width: `${metrics.progress || 0}%` }} /></div><ReactECharts option={throughputOption} style={{ height: 285 }} /></> : <Empty icon={Gauge} title="尚无项目性能数据" detail="连接项目 Broker 与 VSOA 后运行真实基准测试" />}
    </section>
    <section className="panel perf-funnel"><div className="panel-head compact"><div><span>DELIVERY FUNNEL</span><h2>链路交付漏斗</h2></div></div>{selected ? <div className="funnel-body">{[["计划发送", planned, "#56a8ff"], ["成功发布", sent, "#39d4c2"], ["平台接收", received, "#64d990"], ["VSOA 输出", converted, "#ffbd5a"]].map(([label, value, color], index) => <div key={label}><span>{label}</span><i style={{ width: `${planned ? Math.max(4, value * 100 / planned) : 4}%`, background: color }} /><strong>{value}</strong>{index > 0 && <small>{sent ? (value * 100 / sent).toFixed(1) : 0}%</small>}</div>)}</div> : <Empty icon={Layers3} title="暂无交付统计" detail="测试后显示各阶段消息数量" />}</section>
    <section className="panel perf-latency"><div className="panel-head compact"><div><span>LATENCY PERCENTILES</span><h2>延迟分位数</h2></div><b>丢失 {metrics.lost || 0} · 重复 {metrics.duplicates || 0}</b></div><ReactECharts option={latencyOption} style={{ height: 260 }} /></section>
    <section className="panel perf-history"><div className="panel-head compact"><div><span>BENCHMARK HISTORY</span><h2>项目性能历史</h2></div><b>{runs.length} 次测试</b></div><div className="perf-table"><div className="perf-table-head"><span>任务</span><span>来源</span><span>负载</span><span>吞吐</span><span>P95</span><span>转化率</span></div>{runs.map(run => <button key={run.id} className={selected?.id === run.id ? "active" : ""} onClick={() => setSelectedId(run.id)}><code>{run.id}</code><span>本机项目</span><span>{run.config.device_count} 台 · {run.config.rate}/s</span><strong>{run.metrics.throughput || 0}</strong><strong>{run.metrics.p95_latency_ms || 0} ms</strong><span>{run.metrics.conversion_rate || 0}%</span></button>)}</div></section>
  </div>;
}

function Runs({ runs, refresh }) {
  const [scenario, setScenario] = useState("设备上行链路诊断"); const [busy, setBusy] = useState(false); const [codeBusy, setCodeBusy] = useState(false); const [selectedId, setSelectedId] = useState(null); const [error, setError] = useState("");
  const selected = runs.find(run => run.id === selectedId) || null;
  const create = async () => { setBusy(true); setError(""); try { const run = await api("/api/test-runs", { method: "POST", body: JSON.stringify({ experiment: "OPS", scenario }) }); setSelectedId(run.id); await refresh(); setTimeout(refresh, 1800); } catch (e) { setError(e.message); } finally { setBusy(false); } };
  const runCodeTests = async () => { setCodeBusy(true); setError(""); try { const run = await api("/api/project-tests", { method: "POST", body: JSON.stringify({ scope: "all" }) }); setSelectedId(run.id); await refresh(); setTimeout(refresh, 2200); } catch (e) { setError(e.message); } finally { setCodeBusy(false); } };
  return <section className="panel page-panel"><div className="panel-head"><div><span>OPERATIONS DIAGNOSTICS</span><h2>链路运维检查</h2></div><div className="run-actions"><button className="text-btn" onClick={runCodeTests} disabled={codeBusy}>{codeBusy ? <LoaderCircle className="spin" size={16} /> : <Cpu size={16} />}运行桥接单元测试</button><button className="primary compact" onClick={create} disabled={busy}>{busy ? <LoaderCircle className="spin" size={17} /> : <Play size={17} />}检查真实链路</button></div></div>
    <div className="scenario-bar"><label>诊断场景<select value={scenario} onChange={e => setScenario(e.target.value)}><option>设备上行链路诊断</option><option>协议字段映射诊断</option><option>下行控制回执诊断</option><option>异常数据可观测性诊断</option></select></label><div><FileCheck2 size={17} /><span>这是运维诊断，不对应任何实验编号或实验验收。</span></div></div>{error && <div className="form-error">{error}</div>}
    <div className="run-summary"><div><ShieldCheck /><span>被测代码</span><strong>bridge_vsoa_mqtt/src</strong></div><div><Database /><span>测试来源</span><strong>项目 tests · 真实消息记录</strong></div><div><Layers3 /><span>覆盖范围</span><strong>单元测试 · 联调 · 性能</strong></div></div>
    <div className="run-table"><div className="run-head"><span>任务编号</span><span>类型</span><span>诊断场景</span><span>开始时间</span><span>耗时</span><span>检查项</span><span>状态</span></div>{runs.length ? runs.map(run => <button className={"run-row " + (selected?.id === run.id ? "active" : "")} key={run.id} onClick={() => setSelectedId(run.id)}><code>{run.id}</code><span className="tag lora">运维</span><strong>{run.scenario}</strong><span>{new Date(run.started_at).toLocaleString("zh-CN")}</span><span>{run.duration_ms ? run.duration_ms + " ms" : "--"}</span><span>{run.passed}/{run.passed + run.failed || "--"}</span><b className={"run-state " + run.status}>{run.status === "passed" ? <Check size={14} /> : run.status === "failed" ? <AlertTriangle size={14} /> : <LoaderCircle className="spin" size={14} />}{run.status === "passed" ? "正常" : run.status === "failed" ? "异常" : run.status === "running" ? "执行中" : "等待"}</b></button>) : <Empty icon={FlaskConical} title="暂无运维诊断" detail="按需运行真实链路检查" />}</div>
    {selected && <div className="run-detail"><header><div><span>RESULT DETAIL</span><strong>{selected.id} 检查明细</strong></div><a href={`${API}/api/test-runs/${selected.id}/report.csv`}><Download size={15} />导出 CSV</a></header><div>{selected.details?.length ? selected.details.map(item => <article key={item.name} className={item.ok ? "pass" : "fail"}>{item.ok ? <Check size={15} /> : <AlertTriangle size={15} />}<div><strong>{item.name}</strong><span>{item.detail}</span></div></article>) : <span className="run-wait">任务执行完成后显示检查项</span>}</div></div>}
  </section>;
}

function Alerts({ alerts, refresh }) {
  const acknowledge = async id => { await api(`/api/alerts/${id}/acknowledge`, { method: "POST" }); await refresh(); };
  const active = alerts.filter(x => x.status === "active");
  return <section className="panel page-panel"><div className="panel-head"><div><span>ENVIRONMENT ALERTS</span><h2>环境与设备告警</h2></div><div className="alert-summary"><b>{active.length}</b><span>条待处理</span></div></div><div className="alert-list">{alerts.length ? alerts.map(item => <article key={item.id} className={`alert-item ${item.severity} ${item.status}`}><span className="alert-icon"><AlertTriangle size={18} /></span><div><header><strong>{item.message}</strong><span>{projectName[item.project] || item.project}</span></header><p>{item.device_id} · {item.alert_type}</p><small>{new Date(item.created_at).toLocaleString("zh-CN")}</small></div>{item.status === "active" ? <button onClick={() => acknowledge(item.id)}><Check size={15} />确认处理</button> : <span className="alert-done">{item.acknowledged_by} 已确认</span>}</article>) : <Empty icon={ShieldCheck} title="当前没有告警" detail="温度、电量和烟雾异常会自动显示在这里" />}</div></section>;
}

function Admin({ users, audits, profiles, refresh }) {
  const [userForm, setUserForm] = useState({ username: "", display_name: "", password: "", role: "user", active: true });
  const [deviceForm, setDeviceForm] = useState({ device_id: "", name: "", project: "lora", device_type: "environment_sensor", capabilities: "[]", thresholds: '{"temperature_high":35,"battery_low":20}', connection_source: "" });
  const [error, setError] = useState("");
  const saveUser = async event => { event.preventDefault(); setError(""); try { await api("/api/admin/users", { method: "POST", body: JSON.stringify(userForm) }); setUserForm({ username: "", display_name: "", password: "", role: "user", active: true }); await refresh(); } catch (e) { setError(e.message); } };
  const saveDevice = async event => { event.preventDefault(); setError(""); try { await api("/api/device-profiles", { method: "POST", body: JSON.stringify({ ...deviceForm, capabilities: JSON.parse(deviceForm.capabilities), thresholds: JSON.parse(deviceForm.thresholds) }) }); await refresh(); } catch (e) { setError(e.message); } };
  return <div className="admin-layout"><section className="panel"><div className="panel-head"><div><span>ACCOUNT MANAGEMENT</span><h2>用户与角色</h2></div><b>{users.length} 个账号</b></div><div className="admin-users">{users.map(item => <div key={item.username}><span className={`role-dot ${item.role}`} /><strong>{item.display_name}</strong><code>{item.username}</code><span>{item.role}</span><Pill online={item.active}>{item.active ? "启用" : "停用"}</Pill></div>)}</div><form className="admin-form" onSubmit={saveUser}><h3>新增或修改账号</h3><input placeholder="用户名" value={userForm.username} onChange={e => setUserForm(x => ({ ...x, username: e.target.value }))} /><input placeholder="显示名称" value={userForm.display_name} onChange={e => setUserForm(x => ({ ...x, display_name: e.target.value }))} /><input type="password" placeholder="密码（修改时可留空）" value={userForm.password} onChange={e => setUserForm(x => ({ ...x, password: e.target.value }))} /><select value={userForm.role} onChange={e => setUserForm(x => ({ ...x, role: e.target.value }))}><option value="user">普通用户</option><option value="tester">测试运维员</option><option value="admin">管理员</option></select><button className="primary"><Save size={15} />保存账号</button></form></section><section className="panel"><div className="panel-head"><div><span>DEVICE PROFILES</span><h2>设备接入档案</h2></div><b>{profiles.length} 个档案</b></div><div className="profile-list">{profiles.map(item => <button key={item.device_id} onClick={() => setDeviceForm({ ...item, capabilities: JSON.stringify(item.capabilities), thresholds: JSON.stringify(item.thresholds) })}><strong>{item.name}</strong><code>{item.device_id}</code><span>{projectName[item.project]}</span></button>)}</div><form className="admin-form device-profile-form" onSubmit={saveDevice}><h3>设备档案与能力</h3><input placeholder="设备编号" value={deviceForm.device_id} onChange={e => setDeviceForm(x => ({ ...x, device_id: e.target.value }))} /><input placeholder="设备名称" value={deviceForm.name} onChange={e => setDeviceForm(x => ({ ...x, name: e.target.value }))} /><select value={deviceForm.project} onChange={e => setDeviceForm(x => ({ ...x, project: e.target.value }))}><option value="lora">LoRa</option><option value="zigbee">ZigBee</option><option value="generic">桥接/通用</option></select><input placeholder="设备类型" value={deviceForm.device_type} onChange={e => setDeviceForm(x => ({ ...x, device_type: e.target.value }))} /><textarea placeholder="能力 JSON" value={deviceForm.capabilities} onChange={e => setDeviceForm(x => ({ ...x, capabilities: e.target.value }))} /><textarea placeholder="阈值 JSON" value={deviceForm.thresholds} onChange={e => setDeviceForm(x => ({ ...x, thresholds: e.target.value }))} /><button className="primary"><Save size={15} />保存设备档案</button></form></section><section className="panel audit-panel"><div className="panel-head"><div><span>AUDIT TRAIL</span><h2>操作审计</h2></div><b>最近 {audits.length} 条</b></div><div className="audit-list">{audits.map(item => <div key={item.id}><span>{new Date(item.timestamp).toLocaleString("zh-CN")}</span><strong>{item.username}</strong><code>{item.action}</code><small>{item.resource}</small></div>)}</div></section>{error && <div className="form-error admin-error">{error}</div>}</div>;
}

function Connections({ status, project, profiles, vsoaProfiles, close, refresh, refreshProfiles, refreshVsoaProfiles }) {
  const projectTopics = project.mqtt?.uplink_topics || [];
  const defaults = status.mqtt?.topics?.length ? status.mqtt.topics : [...projectTopics, `${project.mqtt?.downlink_topic_prefix || "bridge/downlink"}/#`];
  const [form, setForm] = useState({ name: status.mqtt?.name || "本机项目 Broker", host: status.mqtt?.host || project.mqtt?.broker || "", port: status.mqtt?.port || project.mqtt?.port || 1883, client_id: status.mqtt?.client_id || "", username: status.mqtt?.username || "", password: "", qos: status.mqtt?.qos ?? project.mqtt?.qos ?? 1, topics: defaults.join("\n") });
  const [url, setUrl] = useState(status.vsoa?.url || project.vsoa?.local_url || "vsoa://127.0.0.1:3001"); const [vsoaName, setVsoaName] = useState("本机桥接项目"); const [vsoaHistory, setVsoaHistory] = useState(loadVsoaHistory); const [selectedProfile, setSelectedProfile] = useState(""); const [selectedVsoaProfile, setSelectedVsoaProfile] = useState(""); const [busy, setBusy] = useState(""); const [error, setError] = useState(""); const [diagnostic, setDiagnostic] = useState(null);
  const set = (key, value) => setForm(x => ({ ...x, [key]: value }));
  const payload = () => ({ ...form, port: Number(form.port), qos: Number(form.qos), topics: form.topics.split("\n").map(x => x.trim()).filter(Boolean) });
  const rememberVsoaUrl = value => {
    const normalized = value.trim();
    if (!normalized) return;
    setVsoaHistory(current => {
      const next = [normalized, ...current.filter(item => item !== normalized)].slice(0, 8);
      try { localStorage.setItem(VSOA_HISTORY_KEY, JSON.stringify(next)); } catch { /* Browser storage may be unavailable. */ }
      return next;
    });
  };
  const connect = async type => { setBusy(type); setError(""); try { if (type === "mqtt") await api("/api/mqtt/connect", { method: "POST", body: JSON.stringify(payload()) }); else { await api("/api/vsoa/connect", { method: "POST", body: JSON.stringify({ url: url.trim() }) }); rememberVsoaUrl(url); } setTimeout(refresh, 700); } catch (e) { setError(e.message); } finally { setBusy(""); } };
  const disconnectBroker = async name => { setBusy(`disconnect-${name}`); setError(""); try { await api("/api/mqtt/connections/" + encodeURIComponent(name), { method: "DELETE" }); await refresh(); } catch (e) { setError(e.message); } finally { setBusy(""); } };
  const diagnose = async () => { setBusy("diagnose"); setError(""); try { setDiagnostic(await api("/api/mqtt/diagnose", { method: "POST", body: JSON.stringify(payload()) })); } catch (e) { setError(e.message); } finally { setBusy(""); } };
  const save = async () => { setBusy("save"); try { await api("/api/connection-profiles", { method: "POST", body: JSON.stringify(payload()) }); setSelectedProfile(form.name); await refreshProfiles(); } catch (e) { setError(e.message); } finally { setBusy(""); } };
  const applyProfile = name => { setSelectedProfile(name); const item = profiles.find(x => x.name === name); if (item) setForm({ ...form, ...item, password: "", topics: (item.topics || defaults).join("\n") }); };
  const removeProfile = async name => { await api("/api/connection-profiles/" + encodeURIComponent(name), { method: "DELETE" }); setSelectedProfile(""); await refreshProfiles(); };
  const saveVsoaProfile = async () => { setBusy("vsoa-save"); setError(""); try { await api("/api/vsoa-connection-profiles", { method: "POST", body: JSON.stringify({ name: vsoaName, url: url.trim() }) }); rememberVsoaUrl(url); setSelectedVsoaProfile(vsoaName); await refreshVsoaProfiles(); } catch (e) { setError(e.message); } finally { setBusy(""); } };
  const applyVsoaProfile = name => { setSelectedVsoaProfile(name); const item = vsoaProfiles.find(profile => profile.name === name); if (item) { setVsoaName(item.name); setUrl(item.url); } };
  const removeVsoaProfile = async name => { await api("/api/vsoa-connection-profiles/" + encodeURIComponent(name), { method: "DELETE" }); setSelectedVsoaProfile(""); await refreshVsoaProfiles(); };
  return <div className="modal-bg"><div className="modal connection-modal"><header><div><span>DATA CONNECTIONS</span><strong>数据源连接与诊断</strong></div><button className="icon-btn" onClick={close}><X size={18} /></button></header>
    <div className="profile-strip"><select value={selectedProfile} onChange={e => applyProfile(e.target.value)}><option value="">载入连接档案</option>{profiles.map(item => <option key={item.name}>{item.name}</option>)}</select>{selectedProfile && <button title={`删除 ${selectedProfile}`} onClick={() => removeProfile(selectedProfile)}><Trash2 size={14} /></button>}</div>
    <div className="connection"><div className="connection-title"><Wifi size={19} /><div><strong>MQTT Broker 连接</strong><span>可同时添加 LoRa、ZigBee 等多个 Broker</span></div><Pill online={status.mqtt?.connected}>{status.mqtt?.connected ? `${status.mqtt.connected_count || 1} 个已连接` : status.mqtt?.connecting ? "连接中" : "未连接"}</Pill></div>
      {(status.mqtt?.connections || []).length > 0 && <div className="broker-connections">{status.mqtt.connections.map(item => <div key={item.name}><span className={item.connected ? "online" : ""}><i /></span><strong>{item.name}</strong><code>{item.host}:{item.port}</code><small>{item.topics.length} 个 Topic</small><button title={`断开 ${item.name}`} onClick={() => disconnectBroker(item.name)} disabled={busy === `disconnect-${item.name}`}><X size={14} /></button></div>)}</div>}
      <div className="connection-grid"><label>档案名称<input value={form.name} onChange={e => set("name", e.target.value)} /></label><label>Broker 地址<input value={form.host} onChange={e => set("host", e.target.value)} /></label><label>端口<input type="number" value={form.port} onChange={e => set("port", e.target.value)} /></label><label>QoS<select value={form.qos} onChange={e => set("qos", e.target.value)}><option value="0">0</option><option value="1">1</option><option value="2">2</option></select></label><label>用户名<input value={form.username} onChange={e => set("username", e.target.value)} placeholder="可选" /></label><label>密码<input type="password" value={form.password} onChange={e => set("password", e.target.value)} placeholder="不会保存到档案" /></label><label className="span-2">Client ID<input value={form.client_id} onChange={e => set("client_id", e.target.value)} placeholder="留空自动生成" /></label><label className="span-2">订阅 Topic，每行一个<textarea value={form.topics} onChange={e => set("topics", e.target.value)} /></label></div>
      <div className="connection-actions"><button onClick={save}><Save size={15} />保存档案</button><button onClick={diagnose}><Activity size={15} />{busy === "diagnose" ? "诊断中" : "连接诊断"}</button><button className="accent" onClick={() => connect("mqtt")}><Link2 size={15} />添加 Broker</button></div>
      {diagnostic && <div className="diagnostic">{diagnostic.steps.map(step => <div key={step.name} className={step.ok ? "ok" : "bad"}>{step.ok ? <Check size={14} /> : <X size={14} />}<strong>{step.name}</strong><span>{step.detail}</span></div>)}</div>}
    </div>
    <div className="connection compact-connection"><div className="connection-title"><Network size={19} /><div><strong>VSOA 连接</strong><span>连接组员或本机的 VSOA 服务</span></div><Pill online={status.vsoa?.connected}>{status.vsoa?.connected ? "已连接" : "未连接"}</Pill></div><div className="profile-strip vsoa-profile-strip"><select value={selectedVsoaProfile} onChange={e => applyVsoaProfile(e.target.value)}><option value="">载入 VSOA 连接档案</option>{vsoaProfiles.map(item => <option key={item.name}>{item.name}</option>)}</select>{selectedVsoaProfile && <button title={`删除 ${selectedVsoaProfile}`} onClick={() => removeVsoaProfile(selectedVsoaProfile)}><Trash2 size={14} /></button>}</div><div className="connection-fields vsoa-profile-fields"><input value={vsoaName} onChange={e => setVsoaName(e.target.value)} placeholder="档案名称" /><input list="vsoa-url-history" value={url} onChange={e => setUrl(e.target.value)} placeholder="vsoa://host:port" /><datalist id="vsoa-url-history">{vsoaHistory.map(item => <option value={item} key={item} />)}</datalist><button title="保存 VSOA 档案" onClick={saveVsoaProfile}><Save size={16} /></button><button onClick={() => connect("vsoa")}><Link2 size={16} />连接</button></div></div>{error && <div className="form-error">{error}</div>}<div className="modal-note"><ShieldCheck size={17} />平台只观察真实项目：Broker、Topic 和 VSOA 默认值均来自本机 config.yaml。</div>
  </div></div>;
}

export default function App() {
  const savedAuth = (() => { try { return JSON.parse(localStorage.getItem(AUTH_KEY) || "null"); } catch { return null; } })();
  const [session, setSession] = useState(savedAuth); authToken = session?.token || "";
  const [theme, setTheme] = useState(localStorage.getItem(THEME_KEY) || "dark");
  const [page, setPage] = useState("overview"); const [menu, setMenu] = useState(false);
  const [events, setEvents] = useState([]); const [temperatureSeries, setTemperatureSeries] = useState([]); const [runs, setRuns] = useState([]); const [performanceRuns, setPerformanceRuns] = useState([]); const [devices, setDevices] = useState([]); const [pairs, setPairs] = useState([]); const [profiles, setProfiles] = useState([]); const [vsoaProfiles, setVsoaProfiles] = useState([]); const [alerts, setAlerts] = useState([]); const [commands, setCommands] = useState([]); const [users, setUsers] = useState([]); const [audits, setAudits] = useState([]); const [deviceProfiles, setDeviceProfiles] = useState([]); const [selected, setSelected] = useState(null);
  const [status, setStatus] = useState({ platform: { mode: "incomplete" }, mqtt: {}, vsoa: {}, bridge: {}, metrics: {} }); const [project, setProject] = useState({}); const [clock, setClock] = useState(new Date()); const [connections, setConnections] = useState(false);
  const role = session?.user?.role || "user"; const visibleNav = nav.filter(item => roleRank[role] >= roleRank[item[3]]);
  const toggleTheme = () => setTheme(current => current === "dark" ? "light" : "dark");
  useEffect(() => { document.documentElement.dataset.theme = theme; localStorage.setItem(THEME_KEY, theme); }, [theme]);
  const refreshProfiles = useCallback(async () => { if (role === "admin") setProfiles(await api("/api/connection-profiles")); }, [role]);
  const refreshVsoaProfiles = useCallback(async () => { if (role === "admin") setVsoaProfiles(await api("/api/vsoa-connection-profiles")); }, [role]);
  const refreshCommands = useCallback(async () => setCommands(await api("/api/commands")), []);
  const refreshAlerts = useCallback(async () => setAlerts(await api("/api/alerts")), []);
  const refreshAdmin = useCallback(async () => { if (role !== "admin") return; const [u, a, d] = await Promise.all([api("/api/admin/users"), api("/api/admin/audit-logs"), api("/api/device-profiles")]); setUsers(u); setAudits(a); setDeviceProfiles(d); }, [role]);
  const refresh = useCallback(async () => {
    if (!authToken) return;
    const base = await Promise.all([api("/api/status"), api("/api/project"), api("/api/events?limit=160"), api("/api/temperature-series?limit_per_device=500"), api("/api/devices"), api("/api/alerts"), api("/api/commands")]);
    setStatus(base[0]); setProject(base[1]); setEvents(base[2]); setTemperatureSeries(base[3]); setDevices(base[4]); setAlerts(base[5]); setCommands(base[6]);
    if (roleRank[role] >= roleRank.tester) { const [r, pr, p] = await Promise.all([api("/api/test-runs"), api("/api/performance-runs"), api("/api/transformations")]); setRuns(r); setPerformanceRuns(pr); setPairs(p); }
    if (role === "admin") { const [c, v, u, a, d] = await Promise.all([api("/api/connection-profiles"), api("/api/vsoa-connection-profiles"), api("/api/admin/users"), api("/api/admin/audit-logs"), api("/api/device-profiles")]); setProfiles(c); setVsoaProfiles(v); setUsers(u); setAudits(a); setDeviceProfiles(d); }
  }, [role, session?.token]);
  const refreshLiveState = useCallback(async () => { const [s, d] = await Promise.all([api("/api/status"), api("/api/devices")]); setStatus(s); setDevices(d); }, []);
  useEffect(() => { if (!session) return; refresh().catch(() => {}); const timer = setInterval(() => setClock(new Date()), 1000); return () => clearInterval(timer); }, [refresh, session]);
  useEffect(() => { if (!session) return; const timer = setInterval(() => refreshLiveState().catch(() => {}), 5000); return () => clearInterval(timer); }, [refreshLiveState, session]);
  useEffect(() => { if (!session) return; let ws, retry, sync; const open = () => { ws = new WebSocket(`${WS}?token=${encodeURIComponent(session.token)}`); ws.onmessage = message => { const p = JSON.parse(message.data); if (p.type === "event") { setEvents(x => [p.data, ...x].slice(0, 200)); clearTimeout(sync); sync = setTimeout(refresh, 700); } if (p.type === "metrics") setStatus(x => ({ ...x, metrics: p.data })); if (p.type === "run") setRuns(x => [p.data, ...x.filter(r => r.id !== p.data.id)]); if (p.type === "performance") setPerformanceRuns(x => [p.data, ...x.filter(r => r.id !== p.data.id)]); }; ws.onclose = () => { retry = setTimeout(open, 1800); }; }; open(); return () => { clearTimeout(retry); clearTimeout(sync); ws?.close(); }; }, [refresh, session]);
  const login = value => { authToken = value.token; localStorage.setItem(AUTH_KEY, JSON.stringify(value)); setSession(value); };
  const logout = () => { localStorage.removeItem(AUTH_KEY); authToken = ""; setSession(null); setPage("overview"); };
  if (!session) return <Login onLogin={login} theme={theme} toggleTheme={toggleTheme} />;
  const title = nav.find(item => item[0] === page)?.[1];
  return <div className="shell">
    <aside className={"sidebar " + (menu ? "open" : "")}><div className="brand"><span><Activity size={22} /></span><div><strong>SMART IOT</strong><small>ENVIRONMENT PLATFORM</small></div></div><nav>{visibleNav.map(([id, label, Icon]) => <button className={page === id ? "active" : ""} onClick={() => { setPage(id); setMenu(false); }} key={id}><Icon size={18} /><span>{label}</span>{page === id && <i />}</button>)}</nav><footer><div><span>CONNECTION</span><strong>{status.platform?.mode === "ready" ? "ONLINE" : "WAIT"}</strong></div>{role === "admin" && <button onClick={() => setConnections(true)}><Settings2 size={17} />连接配置</button>}<small>{session.user.display_name} · {role}<br />{project.version ? `Bridge v${project.version}` : "智慧环境平台"}</small></footer></aside>
    <main><header className="topbar"><button className="menu-btn" onClick={() => setMenu(!menu)}><Menu size={20} /></button><div className="title"><span>智慧环境设备管理平台</span><strong>{title}</strong></div><div className="top-actions"><div className="clock"><span>{clock.toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" })}</span><strong>{clock.toLocaleTimeString("zh-CN", { hour12: false })}</strong></div>{role === "admin" && <button className="connection-btn" onClick={() => setConnections(true)}>{status.platform?.mode === "ready" ? <Wifi size={17} /> : <WifiOff size={17} />}<span>{status.platform?.mode === "ready" ? "链路在线" : "链路待接入"}</span><ChevronDown size={15} /></button>}<button className="icon-btn" onClick={toggleTheme} title="切换深浅主题">{theme === "dark" ? <Sun size={17} /> : <Moon size={17} />}</button><button className="icon-btn" onClick={refresh} title="刷新"><RefreshCw size={17} /></button><button className="icon-btn" onClick={logout} title="退出登录"><LogOut size={17} /></button></div></header>
      <div className="content">{page === "overview" && <Overview events={events} status={status} go={setPage} temperatureSeries={temperatureSeries} role={role} theme={theme} />}{page === "topology" && <Topology devices={devices} events={events} status={status} />}{page === "stream" && <Stream events={events} selected={selected} setSelected={setSelected} />}{page === "devices" && <Devices devices={devices} commands={commands} refreshCommands={refreshCommands} role={role} />}{page === "alerts" && <Alerts alerts={alerts} refresh={refreshAlerts} />}{page === "mapping" && <Mapping pairs={pairs} />}{page === "simulator" && <Simulator status={status} project={project} go={setPage} />}{page === "performance" && <Performance runs={performanceRuns} status={status} project={project} refresh={async () => setPerformanceRuns(await api("/api/performance-runs"))} />}{page === "runs" && <Runs runs={runs} refresh={async () => setRuns(await api("/api/test-runs"))} />}{page === "admin" && <Admin users={users} audits={audits} profiles={deviceProfiles} refresh={refreshAdmin} />}</div>
    </main><Drawer event={selected} close={() => setSelected(null)} />{connections && role === "admin" && <Connections status={status} project={project} profiles={profiles} vsoaProfiles={vsoaProfiles} close={() => setConnections(false)} refresh={refresh} refreshProfiles={refreshProfiles} refreshVsoaProfiles={refreshVsoaProfiles} />}
  </div>;
}
