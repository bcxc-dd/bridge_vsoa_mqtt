import React, { useCallback, useEffect, useRef, useState } from "react";
import ReactECharts from "echarts-for-react";
import {
  Activity, ArrowDownToLine, ArrowRight, ArrowUpFromLine,
  AlertTriangle, BarChart3, Bell, Boxes, Cable, Camera, Check, ChevronDown, CloudCog, CloudRain, CloudSun, Cpu,
  Database, Download, FileCheck2, FlaskConical, Gauge, GitCompare,
  Flame, Layers3, Lightbulb, Link2, LoaderCircle, LogOut, Menu, Moon,
  Droplets, House, MessageSquareText, Network, Play, Radio, RefreshCw, Search, Server,
  Save, Settings2, ShieldCheck, SlidersHorizontal, Sprout, Sun, Thermometer, Trash2, UserCog, Pencil, FolderOpen,
  PersonStanding, Plus, Power, Volume2, Waves, Wifi, WifiOff, X, Zap
} from "lucide-react";

const API = `${location.protocol}//${location.hostname}:8000`;
const WS = `${location.protocol === "https:" ? "wss" : "ws"}://${location.hostname}:8000/ws`;
const AUTH_KEY = "smart-environment-auth";
const AUTH_EXPIRED_EVENT = "smart-environment-auth-expired";
const THEME_KEY = "smart-environment-theme";
const VSOA_HISTORY_KEY = "iot-platform-vsoa-history";
const DEFAULT_VSOA_URLS = ["vsoa://127.0.0.1:3001", "vsoa://192.168.3.216:3000"];
const projectName = { lora: "LoRa / LoRaWAN", zigbee: "ZigBee", wifi: "WiFi", generic: "通用设备" };
const isSceneTriggerEvent = event => event?.channel === "/scene/trigger" || event?.device_id === "trigger";
const nav = [
  ["overview", "环境总览", Gauge, "user"], ["lora", "项目监测", Radio, "user"], ["devices", "设备中心", Boxes, "user"],
  ["scenes", "场景联动", Layers3, "user"],
  ["alerts", "告警中心", Bell, "user"],
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
    if (response.status === 401 && path !== "/api/auth/login") {
      localStorage.removeItem(AUTH_KEY);
      authToken = "";
      window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT));
    }
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
  return <div className="login-page"><div className="login-visual">< img className="login-logo" src="/Yihui-Logo.svg" alt="Yihui Logo" /><div className="login-grid" /><div className="login-copy"><span>ACOINFO × NUAA</span><h1>智慧物联网<br />设备管理平台</h1><p>统一连接 LoRa、ZigBee 与 MQTT-VSOA 协议桥接，面向真实设备状态、环境告警和安全控制。</p ><div className="login-projects"><b>LoRaWAN</b><b>ZigBee</b><b>VSOA Bridge</b></div></div></div><form className="login-form" onSubmit={submit}><button type="button" className="theme-login" onClick={toggleTheme} title="切换主题">{theme === "dark" ? <Sun size={18} /> : <Moon size={18} />}</button><div><span>SECURE ACCESS</span><h2>登录平台</h2><p>使用分配的账号进入对应工作区</p ></div><label>用户名<input autoFocus value={form.username} onChange={e => setForm(x => ({ ...x, username: e.target.value }))} /></label><label>密码<input type="password" value={form.password} onChange={e => setForm(x => ({ ...x, password: e.target.value }))} /></label>{error && <div className="form-error">{error}</div>}<button className="primary login-submit" disabled={busy}>{busy ? <LoaderCircle className="spin" size={18} /> : <ShieldCheck size={18} />}{busy ? "验证中" : "安全登录"}</button><small>首次登录演示账号：user / user123</small></form></div>;}

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
  const value = p.temperature ?? p.humidity ?? p.value ?? p.error_code ?? (p.signal !== undefined ? `${p.signal} dBm` : undefined) ?? (p.binary_length !== undefined ? `${p.binary_length} B` : undefined);
  const isDownlink = item.direction === "downlink" || item.direction === "result";
  return <button className={"event-row " + (active ? "active" : "")} onClick={select}>
    <span className={"event-dir " + item.direction}>{isDownlink ? <ArrowDownToLine size={14} /> : <ArrowUpFromLine size={14} />}</span>
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

const loraMetrics = [
  ["temperature", "温度", "°C", Thermometer, "#39d4c2"],
  ["humidity", "空气湿度", "%", Droplets, "#56a8ff"],
  ["soil_moisture", "土壤湿度", "%", Sprout, "#64d990"],
  ["rainfall", "降水水平", "mm", CloudRain, "#ffbd5a"],
];
const TELEMETRY_CHART_LIMIT = 180;

const zigbeeMetrics = [
  ["temperature", "温度", "°C", Thermometer, "#39d4c2"],
  ["humidity", "湿度", "%", Droplets, "#56a8ff"],
  ["voltage", "电压", "V", Zap, "#ffbd5a"],
  ["smoke", "烟雾", "", Flame, "#ff7b68"],
  ["presence", "人体红外", "", PersonStanding, "#b59cff"],
  ["rainfall", "降水", "mm", CloudRain, "#64d990"],
];

const environmentProjects = {
  lora: { title: "LoRa 智慧环境实时监测", eyebrow: "LORA FIELD OPERATIONS", description: "环境遥测、现场图像与无线链路质量统一查看", metrics: loraMetrics },
  zigbee: { title: "ZigBee 智慧环境实时监测", eyebrow: "ZIGBEE HOME OPERATIONS", description: "环境感知、人体红外与安全联动控制统一查看", metrics: zigbeeMetrics },
  wifi: { title: "WiFi 图像设备实时监测", eyebrow: "WIFI CAMERA OPERATIONS", description: "查看 MQTT 实时图像、帧参数与传输链路状态", metrics: [] },
};

const metricVisuals = {
  temperature: [Thermometer, "#39d4c2"], humidity: [Droplets, "#56a8ff"],
  soil_moisture: [Sprout, "#64d990"], rainfall: [CloudRain, "#ffbd5a"],
  pressure: [Gauge, "#7cc9ff"], illuminance: [Sun, "#ffd166"],
  co2: [CloudSun, "#73d6a2"], pm2_5: [CloudCog, "#9fb7ff"], pm10: [CloudCog, "#7fa3dc"],
  voc: [Waves, "#b59cff"], wind_speed: [Waves, "#5cc8e8"], noise: [Volume2, "#ff9f7a"],
  uv_index: [Sun, "#ffbd5a"], water_level: [Droplets, "#45b8e8"],
  voltage: [Zap, "#ffbd5a"], smoke: [Flame, "#ff7b68"], presence: [PersonStanding, "#b59cff"],
};

const thresholdMetricCatalog = {
  temperature: ["温度", "°C", ["temperature", "temperature_c", "temp", "temp_c", "air_temperature"]],
  humidity: ["空气湿度", "%", ["humidity", "humidity_percent", "air_humidity", "relative_humidity"]],
  soil_moisture: ["土壤湿度", "%", ["soil_moisture", "soilHumidity", "soil_humidity", "moisture"]],
  rainfall: ["降水水平", "mm", ["rainfall", "rainfall_mm", "rain_level", "precipitation"]],
  voltage: ["电压", "V", ["voltage", "voltage_v", "battery_voltage", "supply_voltage"]],
  battery: ["电量", "%", ["battery", "battery_percent", "battery_level"]],
  smoke: ["烟雾", "%", ["smoke", "smoke_level", "smoke_relative_percent", "smoke_alarm"]],
  presence: ["人体红外", "", ["presence", "human_presence", "motion_detected", "motion", "infrared"]],
  illuminance: ["光照", "lux", ["illuminance", "illumination", "light_level", "lux"]],
  pressure: ["气压", "hPa", ["pressure", "air_pressure", "barometric_pressure"]],
  co2: ["二氧化碳", "ppm", ["co2", "co2_ppm", "carbon_dioxide"]],
  pm2_5: ["PM2.5", "μg/m³", ["pm2_5", "pm25", "pm2.5"]],
  pm10: ["PM10", "μg/m³", ["pm10"]],
  voc: ["VOC", "ppb", ["voc", "tvoc"]],
  noise: ["环境噪声", "dB", ["noise", "noise_level", "sound_level"]],
  water_level: ["水位", "cm", ["water_level", "water_depth"]],
  signal: ["信号强度", "dBm", ["signal", "rssi", "rssi_dbm"]],
  snr: ["信噪比", "dB", ["snr", "lora_snr"]],
};

const thresholdOperatorLabels = {
  gt: "大于", gte: "大于等于", lt: "小于", lte: "小于等于", eq: "等于", neq: "不等于",
};

function findNestedMetric(payload, aliases) {
  if (!payload || typeof payload !== "object") return undefined;
  const queue = [payload]; const seen = new Set(queue);
  while (queue.length) {
    const current = queue.shift();
    for (const alias of aliases) {
      if (["number", "boolean"].includes(typeof current[alias])) return current[alias];
    }
    Object.values(current).forEach(value => {
      const children = Array.isArray(value) ? value : [value];
      children.forEach(child => {
        if (child && typeof child === "object" && !seen.has(child)) { seen.add(child); queue.push(child); }
      });
    });
  }
  return undefined;
}

function deviceThresholdMetrics(device) {
  const payload = device?.latest_payload || {};
  const configured = new Set((device?.thresholds?.rules || []).map(rule => rule.field));
  const reported = new Map((device?.available_metrics || []).map(metric => [metric.field, metric]));
  const known = Object.entries(thresholdMetricCatalog).flatMap(([field, [label, unit, aliases]]) => {
    const historical = reported.get(field);
    const value = historical?.value ?? findNestedMetric(payload, aliases);
    return value !== undefined || configured.has(field)
      ? [{ field, label: historical?.label || label, unit: historical?.unit || unit, value, dataType: historical?.data_type || (typeof value === "boolean" ? "boolean" : "number") }]
      : [];
  });
  const custom = [...reported.values()].filter(metric => !thresholdMetricCatalog[metric.field]).map(metric => ({
    field: metric.field,
    label: metric.label || metric.field,
    unit: metric.unit || "",
    value: metric.value,
    dataType: metric.data_type || (typeof metric.value === "boolean" ? "boolean" : "number"),
  }));
  return [...known, ...custom];
}

function deviceMetricDefinitions(device, fallbackMetrics) {
  if (device?.metrics?.length) return device.metrics.map(metric => {
    const [Icon, color] = metricVisuals[metric.field] || [Activity, "#39d4c2"];
    return [metric.field, metric.label || metric.field, metric.unit || "", Icon, color, metric.data_type || "number"];
  });
  return fallbackMetrics;
}

const isCameraDevice = device => Boolean(device && (String(device.device_type).toLowerCase().includes("camera") || device.latest_image));

function CameraFeed({ cameras, selectedCamera, setSelectedId }) {
  const [imageState, setImageState] = useState({ source: "", status: "loading" });
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [saveDirectory, setSaveDirectory] = useState(() => localStorage.getItem("camera-save-directory") || "");
  const [saveResult, setSaveResult] = useState(null);
  const imageStatus = imageState.source === selectedCamera?.latest_image ? imageState.status : "loading";
  const frame = selectedCamera?.latest_frame || {};
  const saveImage = async () => {
    if (!selectedCamera?.latest_image) return;
    setSaveResult({ busy: true, text: "正在保存图像" });
    try {
      const result = await api(`/api/cameras/${encodeURIComponent(selectedCamera.device_id)}/save`, { method: "POST", body: JSON.stringify({ save_directory: saveDirectory }) });
      setSaveResult({ ok: true, text: `已保存：${result.saved_path}` });
    } catch (error) { setSaveResult({ ok: false, text: error.message }); }
  };
  const saveSettings = () => { localStorage.setItem("camera-save-directory", saveDirectory); setSettingsOpen(false); setSaveResult({ ok: true, text: saveDirectory ? `保存目录：${saveDirectory}` : "已使用平台默认图像目录" }); };
  return <section className="panel camera-module"><div className="panel-head compact"><div><span>CAMERA FEED</span><h2>摄像头实时图像</h2></div><div className="camera-head-actions">{cameras.length > 0 && <select value={selectedCamera?.device_id || ""} onChange={event => setSelectedId(event.target.value)}>{cameras.map(device => <option value={device.device_id} key={device.device_id}>{device.name}</option>)}</select>}<button title="设置保存地址" onClick={() => setSettingsOpen(value => !value)}><Settings2 size={16} /></button><button className="camera-save" disabled={!selectedCamera?.latest_image || saveResult?.busy} onClick={saveImage}><Save size={16} />保存图像</button></div></div>{settingsOpen && <div className="camera-save-settings"><FolderOpen size={17} /><label>服务器保存目录<input value={saveDirectory} onChange={event => setSaveDirectory(event.target.value)} placeholder="留空使用 data/camera_captures" /></label><button onClick={saveSettings}>应用</button></div>}{saveResult && <div className={`camera-save-result ${saveResult.ok ? "ok" : saveResult.busy ? "busy" : "error"}`}>{saveResult.text}</div>}{selectedCamera?.latest_image ? <figure><div className="camera-stage">{imageStatus === "error" ? <Empty icon={AlertTriangle} title="图像解码失败" detail="已收到 camera 数据，但当前 Base64 或图片格式无法被浏览器解码" /> : <img src={selectedCamera.latest_image} alt={`${selectedCamera.name} 最新现场画面`} onLoad={() => setImageState({ source: selectedCamera.latest_image, status: "ready" })} onError={() => setImageState({ source: selectedCamera.latest_image, status: "error" })} />}<span className={`camera-decode-state ${imageStatus}`}>{imageStatus === "ready" ? "JPEG 已解码" : imageStatus === "error" ? "解码失败" : "正在解码"}</span></div><div className="camera-frame-meta"><span><b>{frame.format || "--"}</b>格式</span><span><b>{frame.width && frame.height ? `${frame.width} × ${frame.height}` : "--"}</b>分辨率</span><span><b>{frame.bytes != null ? `${frame.bytes} B` : "--"}</b>帧大小</span><span><b>{frame.fps != null ? `${frame.fps} FPS` : "--"}</b>上报速率</span></div><figcaption><span><i />MQTT 最新接收画面</span><code>{frame.hub_ip || frame.topic || "camera"}</code><time>{new Date(selectedCamera.last_seen).toLocaleString("zh-CN", { hour12: false })}</time></figcaption></figure> : <Empty icon={Camera} title="等待摄像头图像" detail="支持 image_b64、image_base64、image_url、image、photo 字段" />}</section>;
}

function telemetryOption(points, field, label, unit, color, theme) {
  const values = points.filter(point => point[field] != null).slice(-TELEMETRY_CHART_LIMIT);
  const light = theme === "light";
  return {
    animationDuration: 260,
    grid: { top: 24, right: 16, bottom: 30, left: 42 },
    tooltip: { trigger: "axis", valueFormatter: value => `${value} ${unit}` },
    xAxis: { type: "category", boundaryGap: false, data: values.map(point => new Date(point.timestamp).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false })), axisLabel: { color: light ? "#718188" : "#7d898f", fontSize: 9, hideOverlap: true }, axisLine: { lineStyle: { color: light ? "#d8e1e4" : "#283137" } }, axisTick: { show: false } },
    yAxis: { type: "value", scale: true, name: unit, nameTextStyle: { color: light ? "#718188" : "#7d898f", fontSize: 9 }, axisLabel: { color: light ? "#718188" : "#7d898f", fontSize: 9 }, splitLine: { lineStyle: { color: light ? "#e7edef" : "#222b30" } } },
    series: [{ name: label, type: "line", data: values.map(point => point[field]), smooth: .25, symbol: "circle", showSymbol: values.length < 18, symbolSize: 4, lineStyle: { color, width: 2 }, itemStyle: { color }, areaStyle: { color, opacity: light ? .08 : .12 } }]
  };
}

function EnvironmentDashboard({ theme }) {
  const [project, setProject] = useState("lora");
  const [dashboard, setDashboard] = useState({ devices: [], links: [] });
  const [selectedId, setSelectedId] = useState("");
  const [busy, setBusy] = useState(true);
  const config = environmentProjects[project];
  const refreshDashboard = useCallback(async () => {
    setBusy(true);
    try { setDashboard(await api(`/api/${project}-dashboard?limit_per_device=500`)); }
    finally { setBusy(false); }
  }, [project]);
  useEffect(() => { setDashboard({ devices: [], links: [] }); setSelectedId(""); }, [project]);
  useEffect(() => { refreshDashboard(); const timer = setInterval(refreshDashboard, 5000); return () => clearInterval(timer); }, [refreshDashboard]);
  useEffect(() => {
    setSelectedId(current => dashboard.devices.some(device => device.device_id === current)
      ? current
      : dashboard.devices.find(device => !isCameraDevice(device) && device.points?.length)?.device_id || dashboard.devices[0]?.device_id || "");
  }, [dashboard.devices]);
  const selected = dashboard.devices.find(device => device.device_id === selectedId);
  const sensors = dashboard.devices.filter(device => !isCameraDevice(device));
  const cameras = dashboard.devices.filter(isCameraDevice);
  const selectedIsCamera = isCameraDevice(selected);
  const selectedSensor = selected && !selectedIsCamera ? selected : null;
  const selectedCamera = selectedIsCamera ? selected : null;
  const availableMetrics = selectedSensor ? deviceMetricDefinitions(selectedSensor, config.metrics).filter(([field]) => selectedSensor.latest[field] != null || selectedSensor.points.some(point => point[field] != null)) : [];
  return <div className="lora-dashboard">
    <section className="panel lora-command"><div><span>{config.eyebrow}</span><h2>{config.title}</h2><p>{config.description}</p></div><div className="project-monitor-actions"><div className="segments" aria-label="选择项目">{[["lora", "LoRa"], ["zigbee", "ZigBee"], ["wifi", "WiFi"]].map(([id, label]) => <button key={id} className={project === id ? "active" : ""} onClick={() => setProject(id)}>{label}</button>)}</div><div className="lora-live"><i /><strong>{dashboard.devices.filter(device => device.online).length}</strong><span>/ {dashboard.devices.length} 设备在线</span><button onClick={refreshDashboard} disabled={busy} title={`刷新 ${projectName[project]} 数据`}><RefreshCw size={16} className={busy ? "spin" : ""} /></button></div></div></section>
    <section className="panel lora-device-strip"><div className="panel-head compact"><div><span>DEVICE SELECTOR</span><h2>设备选择</h2></div><b>{dashboard.devices.length} 台已识别设备</b></div><div className="lora-device-list">{dashboard.devices.map(device => <button className={selectedId === device.device_id ? "active" : ""} onClick={() => setSelectedId(device.device_id)} key={device.device_id}><span className={device.online ? "online" : ""}>{isCameraDevice(device) ? <Camera size={18} /> : <Radio size={18} />}</span><div><strong>{device.name}</strong><small>{device.device_type} · {device.online ? "在线" : "离线"}</small></div><ChevronDown size={15} /></button>)}</div></section>
    <div className="lora-primary-grid single-primary">
      {selectedSensor && config.metrics.length > 0 && <section className="panel environment-module"><div className="panel-head compact"><div><span>ENVIRONMENT TELEMETRY</span><h2>{selectedSensor.name} · 实时数据</h2></div><select value={selectedSensor.device_id} onChange={event => setSelectedId(event.target.value)}>{sensors.map(device => <option value={device.device_id} key={device.device_id}>{device.name}</option>)}</select></div>{availableMetrics.length ? <><div className={`lora-metric-grid metrics-${availableMetrics.length}`}>{availableMetrics.map(([field, label, unit, Icon]) => { const value = selectedSensor.latest[field]; const display = field === "presence" && value != null ? (value ? "有人经过" : "无人") : field === "smoke" && typeof value === "boolean" ? (value ? "检测到" : "正常") : value ?? "--"; return <div key={field}><span><Icon size={18} /></span><small>{label}</small><strong>{display}<i>{value != null ? unit : "等待数据"}</i></strong></div>; })}</div><div className={`lora-chart-grid charts-${availableMetrics.length}`}>{availableMetrics.map(([field, label, unit, , color]) => <article key={field}><header><strong>{label}</strong><span>{selectedSensor.points.filter(point => point[field] != null).length} 个采样点</span></header><ReactECharts option={telemetryOption(selectedSensor.points, field, label, unit, color, theme)} style={{ height: 205 }} notMerge lazyUpdate /></article>)}</div></> : <Empty icon={BarChart3} title="当前设备暂无可绘制数据" detail="收到温度、湿度、土壤湿度或降雨等数值后自动生成曲线" />}</section>}
      {selectedIsCamera && <CameraFeed cameras={cameras} selectedCamera={selectedCamera} setSelectedId={setSelectedId} />}
      {!selected && <section className="panel environment-module"><Empty icon={Radio} title={`尚未识别 ${projectName[project]} 设备`} detail="收到设备上报后自动加入项目监测" /></section>}
    </div>
    <section className="panel link-quality-module"><div className="panel-head compact"><div><span>LINK QUALITY LEDGER</span><h2>逐链路质量分析</h2></div><b>基于真实 {projectName[project]} 上行记录</b></div><div className="link-quality-head"><span>设备</span><span>当前 RSSI</span><span>平均 RSSI</span><span>当前 SNR</span><span>接收报文</span><span>推算丢包</span><span>最后通信</span></div><div className="link-quality-list">{dashboard.links.map(link => <button onClick={() => setSelectedId(link.device_id)} key={link.device_id}><span><i className={link.online ? "online" : ""} /><strong>{link.name}</strong><small>{link.device_id}</small></span><b className={link.rssi != null && link.rssi >= -90 ? "good" : "weak"}>{link.rssi != null ? `${link.rssi} dBm` : "--"}</b><span>{link.avg_rssi != null ? `${link.avg_rssi} dBm` : "--"}</span><span>{link.snr != null ? `${link.snr} dB` : "--"}</span><span>{link.packets}</span><span title={link.loss_rate == null ? "设备消息缺少连续序号，不能准确计算" : `${link.missing_packets} 个序号缺口`}>{link.loss_rate != null ? `${link.loss_rate}%` : "无法计算"}</span><time>{link.last_seen ? new Date(link.last_seen).toLocaleString("zh-CN", { hour12: false }) : "--"}</time></button>)}</div>{!dashboard.links.length && <Empty icon={Activity} title="暂无链路记录" detail={`${projectName[project]} 上行到达 Broker 后显示`} />}</section>
  </div>;
}

function friendlyEventText(event) {
  const payload = event?.payload || {};
  const data = payload.data && typeof payload.data === "object" ? payload.data : payload;
  const name = event?.device_id || "设备";
  if (event?.direction === "downlink" || event?.direction === "result") return `${name} 的控制命令已发送`;
  if (data.motion_detected === true || data.presence === true || data.pir === true) return `${name} 检测到人员经过`;
  if (data.smoke === true || data.smoke_alarm === true) return `${name} 检测到烟雾告警`;
  if (data.image_b64 || data.image_base64 || data.image_url || data.type === "camera") return `${name} 更新了现场图像`;
  if (data.temperature != null && data.humidity != null) return `${name} 更新了温度和湿度`;
  if (data.temperature != null) return `${name} 更新了温度`;
  if (data.humidity != null || data.soil_moisture != null || data.rainfall != null) return `${name} 更新了环境数据`;
  return `${name} 上报了新数据`;
}

function userAdvice(alert) {
  if (alert.alert_type?.startsWith("threshold:")) return alert.severity === "critical" ? "请尽快检查设备与现场环境" : "建议查看当前数据并确认设备状态";
  if (alert.alert_type === "temperature_high") return "建议通风或检查温控设备";
  if (alert.alert_type === "battery_low") return "建议尽快更换电池或检查供电";
  if (alert.alert_type === "smoke") return "请立即检查现场并确认安全";
  if (alert.alert_type?.includes("offline")) return "请检查设备电源和网络连接";
  return "建议进入告警中心查看详情";
}

function Overview({ events, status, go, role, devices, alerts }) {
  const visibleEvents = events.filter(event => !isSceneTriggerEvent(event));
  const recentActivities = visibleEvents
    .filter(event => event.source === "mqtt" || ["uplink", "downlink"].includes(event.direction))
    .filter((event, index, items) => items.findIndex(other =>
      other.device_id === event.device_id
      && other.direction === event.direction
      && other.channel === event.channel
      && Math.abs(new Date(other.timestamp) - new Date(event.timestamp)) < 3000
    ) === index)
    .slice(0, 4);
  const activeAlerts = alerts.filter(alert => alert.status === "active");
  const realDevices = devices.filter(device => device.device_id !== "trigger" && !device.simulated);
  const onlineDevices = realDevices.filter(device => device.online);
  const offlineDevices = realDevices.filter(device => !device.online);
  const attentionCount = activeAlerts.length + offlineDevices.length;
  const hasAttention = attentionCount > 0;
  const attentionItems = [
    ...activeAlerts.slice(0, 3).map(alert => ({
      id: alert.id,
      tone: alert.severity === "critical" ? "critical" : "warning",
      title: alert.message,
      detail: userAdvice(alert),
    })),
    ...(activeAlerts.length < 3 && offlineDevices.length ? [{
      id: "offline-devices",
      tone: "offline",
      title: `${offlineDevices.length} 台设备当前离线`,
      detail: "仍可下发控制命令，实际执行结果以设备恢复后的 ACK 为准",
    }] : []),
  ].slice(0, 3);
  const environmentTitle = activeAlerts.length
    ? `有 ${activeAlerts.length} 项情况需要关注`
    : offlineDevices.length
      ? `${offlineDevices.length} 台设备需要检查`
    : onlineDevices.length
      ? "当前设备运行平稳"
      : "正在等待设备接入";
  const environmentDetail = activeAlerts.length
    ? "平台已按优先级整理需要处理的环境与设备状态。"
    : offlineDevices.length
      ? "部分设备暂时没有上报数据，请检查供电、网络或设备位置。"
    : onlineDevices.length
      ? `${onlineDevices.length} 台设备正在持续上报，目前没有未处理告警。`
      : "设备开始上报后，这里会自动给出环境总结和处理建议。";
  const m = status.metrics || {};
  return <>
    {role === "user" && <section className="home-welcome"><div><span>HOME ENVIRONMENT</span><h1>家中环境，一眼掌握</h1><p>设备状态和环境变化会在这里持续更新。</p></div><div className={status.platform?.mode === "ready" ? "ready" : "waiting"}><CloudSun size={25} /><span>家庭设备</span><strong>{status.platform?.mode === "ready" ? "连接正常" : "等待接入"}</strong></div></section>}
    <div className={`metrics ${role === "user" ? "user-metrics" : ""}`}>
      <Metric icon={Activity} label="近一小时消息" value={m.messages_hour ?? 0} unit="条" tone="cyan" hint="双向数据合计" />
      <Metric icon={Cpu} label="活跃设备" value={m.active_devices ?? 0} unit="台" tone="blue" hint="已识别设备" />
      {role !== "user" && <Metric icon={Zap} label="平均桥接延迟" value={m.avg_latency_ms ?? 0} unit="ms" tone="amber" hint="VSOA 结果样本" />}
      {role !== "user" && <Metric icon={ShieldCheck} label="链路成功率" value={m.success_rate ?? 100} unit="%" tone="green" hint="近一小时" />}
    </div>
    <section className="panel pipeline-panel"><div className="panel-head"><div><span>SYSTEM TOPOLOGY</span><h2>设备数据链路</h2></div><Pill online={status.platform?.mode === "ready"}>{status.platform?.mode === "ready" ? "链路已连接" : "等待连接"}</Pill></div><Pipeline status={status} /></section>
    <Topology devices={devices} events={events} status={status} embedded />
    <div className={`overview-grid ${role === "user" ? "user-overview-grid" : ""}`}>
      <section className="panel home-brief"><div className="panel-head compact"><div><span>TODAY AT HOME</span><h2>今日环境与生活建议</h2></div><button className="text-btn" onClick={() => go("alerts")}>{activeAlerts.length ? `${activeAlerts.length} 条待处理` : "查看告警"}<ArrowRight size={15} /></button></div>
      <div className={`home-brief-summary ${hasAttention ? "attention" : onlineDevices.length ? "good" : "waiting"}`}><span>{hasAttention ? <AlertTriangle size={24} /> : onlineDevices.length ? <CloudSun size={24} /> : <WifiOff size={24} />}</span><div><strong>{environmentTitle}</strong><p>{environmentDetail}</p></div><dl><div><dt>在线设备</dt><dd>{onlineDevices.length}</dd></div><div><dt>需要关注</dt><dd>{attentionCount}</dd></div></dl></div>
        <div className="home-brief-columns"><section><header><strong>需要关注</strong><button onClick={() => go("alerts")}>全部</button></header><div className="home-attention-list">{attentionItems.length ? attentionItems.map(item => <button className={item.tone} onClick={() => go("alerts")} key={item.id}><span>{item.tone === "critical" ? <Flame size={17} /> : item.tone === "offline" ? <WifiOff size={17} /> : <AlertTriangle size={17} />}</span><div><strong>{item.title}</strong><small>{item.detail}</small></div><ChevronDown size={15} /></button>) : <div className="home-all-clear"><ShieldCheck size={20} /><div><strong>暂时没有需要处理的事项</strong><small>保持当前设备和场景设置即可</small></div></div>}</div></section>
          <section><header><strong>最近动态</strong><button onClick={() => go(role === "user" ? "devices" : "stream")}>更多</button></header><div className="home-activity-list">{recentActivities.map(event => <div key={event.id}><span className={event.direction}><Activity size={15} /></span><div><strong>{friendlyEventText(event)}</strong><small>{new Date(event.timestamp).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false })} · {projectName[event.project] || "设备"}</small></div></div>)}{!recentActivities.length && <div className="home-all-clear"><Radio size={20} /><div><strong>等待设备动态</strong><small>收到真实上报后自动更新</small></div></div>}</div></section></div>
        <footer className="home-quick-actions"><button onClick={() => go("devices")}><Boxes size={16} /><span>设备控制</span></button><button onClick={() => go("lora")}><Radio size={16} /><span>查看环境数据</span></button><button onClick={() => go("scenes")}><Layers3 size={16} /><span>智慧场景</span></button><button onClick={() => go("alerts")}><Bell size={16} /><span>处理告警</span></button></footer>
      </section>
      <section className="panel health"><div className="panel-head compact"><div><span>SERVICE HEALTH</span><h2>服务状态</h2></div></div>{[
        ["平台服务 API", true, `${location.hostname}:8000`, Server], ["MQTT Broker", status.mqtt?.connected, status.mqtt?.connected_count > 1 ? `${status.mqtt.connected_count} 个 Broker 已连接` : status.mqtt?.host || "未连接", Wifi],
        ["协议桥接连接", status.bridge?.connected, status.bridge?.health?.version ? `v${status.bridge.health.version} · ${status.bridge.health.devices} 台设备` : status.vsoa?.url || "未连接", Link2], ["VSOA 事件流", status.vsoa?.connected, status.vsoa?.url || "未连接", Network]
      ].map(([name, online, detail, Icon]) => <div className="health-row" key={name}><span className={online ? "active" : ""}><Icon size={17} /></span><div><strong>{name}</strong><small>{detail}</small></div><Pill online={online}>{online ? "在线" : "待机"}</Pill></div>)}</section>
    </div>
    {roleRank[role] >= roleRank.tester && <section className="panel"><div className="panel-head compact"><div><span>LATEST EVENTS</span><h2>最新链路消息</h2></div><button className="text-btn" onClick={() => go("stream")}>查看全部<ArrowRight size={15} /></button></div><EventHead /><div className="event-list mini">{visibleEvents.slice(0, 6).map(e => <EventRow key={e.id} item={e} />)}</div></section>}
  </>;
}

function Topology({ devices, events, status, embedded = false }) {
  const realDevices = devices.filter(device => device.device_id !== "trigger" && !device.device_id.startsWith("perf-"));
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

  return <section className={`panel topology-page ${embedded ? "topology-embedded" : ""}`}>
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
  const [project, setProject] = useState("all");
  const [history, setHistory] = useState(events); const [loadingHistory, setLoadingHistory] = useState(false); const [hasMore, setHasMore] = useState(true);
  useEffect(() => { setHistory(current => { const known = new Set(current.map(item => item.id)); return [...events.filter(item => !known.has(item.id)), ...current]; }); }, [events]);
  const loadHistory = async () => {
    setLoadingHistory(true);
    try {
      const older = await api(`/api/events?limit=500&offset=${history.length}`);
      setHistory(current => { const known = new Set(current.map(item => item.id)); return [...current, ...older.filter(item => !known.has(item.id))]; });
      setHasMore(older.length === 500);
    } finally { setLoadingHistory(false); }
  };
  const visibleHistory = history.filter(e => !isSceneTriggerEvent(e));
  const filtered = visibleHistory.filter(e => (source === "all" || e.source.includes(source)) && (project === "all" || e.project === project) && (e.device_id + " " + e.channel + " " + JSON.stringify(e.payload)).toLowerCase().includes(query.toLowerCase()));
  return <section className="panel page-panel"><div className="panel-head"><div><span>LIVE & HISTORICAL MESSAGES</span><h2>MQTT / VSOA 实时与历史消息</h2></div><b className="stream-count"><i />已加载 {history.length} 条记录</b></div>
    <div className="toolbar"><label className="search"><Search size={16} /><input value={query} onChange={e => setQuery(e.target.value)} placeholder="搜索设备、topic 或字段" /></label><div className="segments">{[["all", "全部"], ["mqtt", "MQTT"], ["vsoa", "VSOA"]].map(([id, label]) => <button className={source === id ? "active" : ""} onClick={() => setSource(id)} key={id}>{label}</button>)}</div><div className="segments">{[["all", "全部项目"], ["lora", "LoRa"], ["zigbee", "ZigBee"], ["wifi", "WiFi"], ["generic", "通用"]].map(([id, label]) => <button className={project === id ? "active" : ""} onClick={() => setProject(id)} key={id}>{label}</button>)}</div></div>
    <EventHead /><div className="event-list full">{filtered.length ? filtered.map(e => <EventRow key={e.id} item={e} active={selected?.id === e.id} select={() => setSelected(e)} />) : <Empty icon={MessageSquareText} title="没有匹配的消息" detail="调整筛选条件或连接数据源" />}</div>
    {hasMore && <div className="history-loader"><button onClick={loadHistory} disabled={loadingHistory}><RefreshCw size={15} className={loadingHistory ? "spin" : ""} />{loadingHistory ? "正在读取历史记录" : "加载更早记录"}</button></div>}
  </section>;
}

function Devices({ devices, commands, refreshCommands, refreshDevices, role }) {
  const [project, setProject] = useState("all"); const [online, setOnline] = useState("all"); const [showSimulated, setShowSimulated] = useState(false); const [selectedId, setSelectedId] = useState(""); const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState(false); const [annotation, setAnnotation] = useState({ display_name: "", note: "" }); const [annotationResult, setAnnotationResult] = useState("");
  const [thresholdRules, setThresholdRules] = useState([]); const [thresholdResult, setThresholdResult] = useState("");
  const visibleDevices = devices.filter(item => item.device_id !== "trigger" && (!item.simulated || (role !== "user" && showSimulated)));
  const filtered = visibleDevices.filter(item => (project === "all" || item.project === project) && (online === "all" || String(item.online) === online));
  const selected = devices.find(item => `${item.project}:${item.device_id}` === selectedId) || null;
  useEffect(() => {
    if (!selected) return;
    setAnnotation({ display_name: selected.name || selected.device_id, note: selected.note || "" });
    setThresholdRules(selected.thresholds?.rules || []);
    setEditing(false); setAnnotationResult(""); setThresholdResult("");
  }, [selectedId]);
  const send = async (command, parameters = {}, label = command) => {
    if (!selected || !window.confirm(`确认向设备 ${selected.name || selected.device_id} 下发“${label}”命令？`)) return;
    setBusy(true); try { await api("/api/commands", { method: "POST", body: JSON.stringify({ device_id: selected.device_id, project: selected.project, command, parameters, confirmed: true }) }); await refreshCommands(); } catch (e) { alert(e.message); } finally { setBusy(false); }
  };
  const saveAnnotation = async () => {
    if (!selected || !annotation.display_name.trim()) return;
    setBusy(true); setAnnotationResult("");
    try {
      await api(`/api/devices/${encodeURIComponent(selected.device_id)}/annotation`, { method: "PATCH", body: JSON.stringify(annotation) });
      await refreshDevices(); setEditing(false); setAnnotationResult("名称与备注已保存，并同步用于所有页面");
    } catch (error) { setAnnotationResult(error.message); }
    finally { setBusy(false); }
  };
  const thresholdMetrics = deviceThresholdMetrics(selected);
  const addThresholdRule = () => {
    const metric = thresholdMetrics[0];
    if (!metric) return;
    setThresholdRules(current => [...current, {
      field: metric.field,
      operator: metric.dataType === "boolean" ? "eq" : "gt",
      value: metric.dataType === "boolean" ? true : Number(metric.value ?? 0),
      severity: "warning",
      enabled: true,
    }]);
  };
  const updateThresholdRule = (index, key, value) => setThresholdRules(current => current.map((rule, position) => {
    if (position !== index) return rule;
    if (key !== "field") return { ...rule, [key]: value };
    const metric = thresholdMetrics.find(item => item.field === value);
    return {
      ...rule,
      field: value,
      operator: metric?.dataType === "boolean" ? "eq" : rule.operator,
      value: metric?.dataType === "boolean" ? true : Number(metric?.value ?? 0),
    };
  }));
  const saveThresholds = async () => {
    if (!selected) return;
    setBusy(true); setThresholdResult("");
    try {
      await api(`/api/devices/${encodeURIComponent(selected.device_id)}/thresholds`, {
        method: "PUT",
        body: JSON.stringify({ rules: thresholdRules }),
      });
      await refreshDevices();
      setThresholdResult(thresholdRules.length ? "告警规则已保存，后续真实上报将按新阈值判断" : "已关闭该设备的全部阈值告警");
    } catch (error) { setThresholdResult(error.message); }
    finally { setBusy(false); }
  };
  const directActuators = actuatorOptions(selected).filter(key => key !== "camera");
  return <div className="device-workspace">
    <section className="panel device-list">
      <div className="panel-head"><div><span>SMART DEVICE INVENTORY</span><h2>环境设备</h2></div><Pill online={visibleDevices.some(x => x.online)}>{visibleDevices.filter(x => x.online).length}/{visibleDevices.length} 在线</Pill></div>
      <div className="toolbar device-toolbar"><div className="segments">{[["all", "全部项目"], ["lora", "LoRa"], ["zigbee", "ZigBee"], ["wifi", "WiFi"], ["generic", "桥接/通用"]].map(([id, label]) => <button key={id} className={project === id ? "active" : ""} onClick={() => setProject(id)}>{label}</button>)}</div><div className="device-filter-end">{role !== "user" && <label><input type="checkbox" checked={showSimulated} onChange={e => setShowSimulated(e.target.checked)} />显示模拟设备</label>}<select value={online} onChange={e => setOnline(e.target.value)}><option value="all">全部状态</option><option value="true">仅在线</option><option value="false">仅离线</option></select></div></div>
      {filtered.length ? <div className="smart-device-grid">{filtered.map(item => { const data = item.latest_payload?.data && typeof item.latest_payload.data === "object" ? item.latest_payload.data : item.latest_payload || {}; const key = `${item.project}:${item.device_id}`; return <button className={"smart-device " + (selectedId === key ? "active" : "")} onClick={() => setSelectedId(key)} key={key}><header><span className={"device-signal " + (item.online ? "online" : "")}><Radio size={17} /></span><div><strong>{item.name || item.device_id}</strong><small>{projectName[item.project]} · {item.device_type}</small></div><Pill online={item.online}>{item.online ? "在线" : "离线"}</Pill></header><div className="telemetry-preview">{[["temperature", "温度", "°C"], ["humidity", "湿度", "%"], ["battery", "电量", "%"], ["signal", "信号", "dBm"]].filter(([field]) => data[field] != null).slice(0, 3).map(([field, label, unit]) => <span key={field}><small>{label}</small><strong>{String(data[field])}<i>{unit}</i></strong></span>)}</div><footer><code>{item.device_id}</code><span>{item.last_seen ? new Date(item.last_seen).toLocaleTimeString("zh-CN", { hour12: false }) : "从未上报"}</span></footer></button>; })}</div> : <Empty icon={Boxes} title="没有符合条件的设备" detail="调整项目或在线状态筛选" />}
    </section>
    <aside className="panel device-inspector">
      <div className="panel-head"><div><span>DEVICE DETAIL</span><h2>{selected ? selected.name || selected.device_id : "选择设备"}</h2></div>{selected && <Pill online={selected.online}>{selected.online ? "实时在线" : "当前离线"}</Pill>}</div>
      {selected ? <>
        <div className="device-annotation"><header><strong>显示名称与备注</strong><button title="编辑设备名称和备注" onClick={() => setEditing(value => !value)}><Pencil size={15} />{editing ? "取消" : "编辑"}</button></header>{editing ? <div><label>设备显示名称<input value={annotation.display_name} onChange={event => setAnnotation(current => ({ ...current, display_name: event.target.value }))} /></label><label>设备备注<textarea value={annotation.note} onChange={event => setAnnotation(current => ({ ...current, note: event.target.value }))} placeholder="例如：客厅窗边温湿度传感器" /></label><button className="primary" disabled={busy || !annotation.display_name.trim()} onClick={saveAnnotation}><Save size={15} />保存</button></div> : <p>{selected.note || "暂无备注，可为设备补充位置或用途说明。"}</p>}{annotationResult && <small>{annotationResult}</small>}</div>
        <div className="inspector-meta"><span>项目<strong>{projectName[selected.project]}</strong></span><span>接入来源<strong>{selected.connection_source || selected.channels?.[0] || "--"}</strong></span><span>最后通信<strong>{selected.last_seen ? new Date(selected.last_seen).toLocaleString("zh-CN") : "--"}</strong></span></div>
        <section className="device-thresholds"><header><div><strong>告警阈值</strong><small>按这台设备实际上报的数据设置</small></div><button disabled={!thresholdMetrics.length} onClick={addThresholdRule}><Plus size={14} />添加规则</button></header>
          {thresholdMetrics.length ? <><div className="threshold-current">{thresholdMetrics.map(metric => <span key={metric.field}><small>{metric.label}</small><strong>{metric.value === undefined ? "--" : String(metric.value)}{metric.unit}</strong></span>)}</div>
            <div className="threshold-rule-list">{thresholdRules.map((rule, index) => { const metric = thresholdMetrics.find(item => item.field === rule.field) || thresholdMetrics[0]; const booleanMetric = metric?.dataType === "boolean"; return <div className="threshold-rule" key={`${rule.field}-${index}`}><label className="threshold-enabled"><input type="checkbox" checked={rule.enabled !== false} onChange={event => updateThresholdRule(index, "enabled", event.target.checked)} /><span>启用</span></label><select value={rule.field} onChange={event => updateThresholdRule(index, "field", event.target.value)}>{thresholdMetrics.map(item => <option value={item.field} key={item.field}>{item.label}</option>)}</select><select value={booleanMetric ? (["eq", "neq"].includes(rule.operator) ? rule.operator : "eq") : rule.operator} onChange={event => updateThresholdRule(index, "operator", event.target.value)}>{Object.entries(thresholdOperatorLabels).filter(([key]) => !booleanMetric || ["eq", "neq"].includes(key)).map(([key, label]) => <option value={key} key={key}>{label}</option>)}</select>{booleanMetric ? <select value={String(Boolean(rule.value))} onChange={event => updateThresholdRule(index, "value", event.target.value === "true")}><option value="true">触发 / 是</option><option value="false">正常 / 否</option></select> : <label className="threshold-value"><input type="number" step="any" value={rule.value} onChange={event => updateThresholdRule(index, "value", Number(event.target.value))} /><span>{metric?.unit}</span></label>}<select value={rule.severity || "warning"} onChange={event => updateThresholdRule(index, "severity", event.target.value)}><option value="warning">提醒</option><option value="critical">严重</option></select><button title="删除规则" onClick={() => setThresholdRules(current => current.filter((_, position) => position !== index))}><Trash2 size={14} /></button></div>})}</div>
            <footer><span>{thresholdRules.length ? `已配置 ${thresholdRules.length} 条规则` : "当前不产生阈值告警"}</span><button className="primary" disabled={busy} onClick={saveThresholds}><Save size={14} />保存告警规则</button></footer>{thresholdResult && <p className="threshold-result">{thresholdResult}</p>}</> : <div className="threshold-empty"><ShieldCheck size={18} /><span>这台设备尚未上报可设置阈值的环境数据</span></div>}
        </section>
        <div className="device-controls"><strong>板载执行器直接控制</strong><div className="actuator-control-list">{directActuators.length ? directActuators.map(actuator => <section className="actuator-control-row" key={actuator}><span><Lightbulb size={15} /><b>{actuatorCatalog[actuator]?.label || actuator}</b></span><div>{actuatorActionOptions(selected, actuator).map(actionKey => { const definition = sceneActionCatalog[actionKey]; return <button disabled={busy} onClick={() => send(definition.action, definition.params, definition.label)} key={actionKey}>{definition.label}</button>; })}</div></section>) : <p>当前设备没有已配置的下行执行器</p>}</div><small>命令会直接发布到 MQTT Broker；设备离线时仍可下发，是否实际执行以设备后续 ACK 为准。</small></div>
        <div className="command-timeline"><strong>最近控制记录</strong>{commands.filter(x => x.device_id === selected.device_id).slice(0, 6).map(item => <div key={item.id}><i className={item.status} /><span>{item.command}</span><code>{item.status}</code><small>{new Date(item.requested_at).toLocaleTimeString("zh-CN")}</small></div>)}</div>
      </> : <Empty icon={Radio} title="从左侧选择一台设备" detail="查看实时指标、连接来源和控制记录" />}
    </aside>
  </div>;
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

const fallbackSceneSensors = [
  ["temperature", "温度", "°C", "float"], ["humidity", "空气湿度", "%", "float"],
  ["soil_moisture", "土壤湿度", "%", "float"], ["precipitation", "降水", "mm", "float"],
  ["illuminance", "光照", "lux", "float"], ["smoke", "烟雾", "", "bool"],
  ["pir", "人体红外", "", "bool"], ["voltage", "电压", "V", "float"],
].map(([sensor_id, label, unit, data_type]) => ({ sensor_id, label, unit, data_type }));

const sceneActionCatalog = {
  relay_on: { label: "继电器开启", action: "set", params: { relay: "on" } },
  relay_off: { label: "继电器关闭", action: "set", params: { relay: "off" } },
  led_on: { label: "LED 开启", action: "set", params: { led: "on" } },
  led_off: { label: "LED 关闭", action: "set", params: { led: "off" } },
  led_blink: { label: "LED 闪烁", action: "set", params: { led: "blink" } },
  buzzer_on: { label: "蜂鸣器开启", action: "set", params: { buzzer: "on" } },
  buzzer_off: { label: "蜂鸣器关闭", action: "set", params: { buzzer: "off" } },
  buzzer_beep: { label: "蜂鸣器连续鸣响", action: "set", params: { buzzer: "beep" } },
  motor_on: { label: "电机开启", action: "set", params: { motor: "on" } },
  motor_off: { label: "电机关闭", action: "set", params: { motor: "off" } },
  motor_angle: { label: "转动到固定角度", action: "set", params: { motor: "rotate" } },
  device_on: { label: "设备开启", action: "set", params: { state: true } },
  device_off: { label: "设备关闭", action: "set", params: { state: false } },
  camera_save: { label: "保存图像", action: "capture", params: { camera: "save" } },
};

const actuatorCatalog = {
  relay: { label: "继电器", actions: ["relay_on", "relay_off"] },
  led: { label: "LED 灯", actions: ["led_on", "led_off", "led_blink"] },
  buzzer: { label: "蜂鸣器", actions: ["buzzer_on", "buzzer_off", "buzzer_beep"] },
  motor: { label: "电机", actions: ["motor_on", "motor_off"] },
  device: { label: "整块设备", actions: ["device_on", "device_off"] },
  camera: { label: "摄像头", actions: ["camera_save"] },
};

function actuatorOptions(device) {
  if (!device) return [];
  const text = `${device?.name || ""} ${device?.device_type || ""} ${device?.device_id || ""}`.toLowerCase();
  const deviceId = String(device.device_id).trim().toLowerCase();
  if (device?.project === "zigbee" && deviceId === "0xb25b") return ["relay"];
  if (device?.project === "zigbee" && deviceId === "0xc38f") return ["led"];
  const isLoraSensor = device?.project === "lora" && !text.includes("camera") && !text.includes("摄像");
  const isEora = ["generic", "wifi"].includes(device?.project) && String(device.device_id).toLowerCase().replaceAll("-", "_") === "eora_s3_400tb_001";
  if (isLoraSensor) return ["led", "motor"];
  if (isEora) return ["led", "motor"];
  if (device?.project === "wifi" || text.includes("camera") || text.includes("摄像")) return ["camera"];
  return [];
}

function actuatorActionOptions(device, actuator) {
  const deviceId = String(device?.device_id || "").trim().toLowerCase();
  if (device?.project === "zigbee" && deviceId === "0xc38f" && actuator === "led") return ["led_on", "led_off"];
  const isEora = (device?.project === "lora")
    || (["generic", "wifi"].includes(device?.project) && String(device.device_id).toLowerCase().replaceAll("-", "_") === "eora_s3_400tb_001");
  if (isEora && actuator === "led") return ["led_on", "led_off"];
  return actuatorCatalog[actuator]?.actions || [];
}

function inferActionForm(action) {
  const params = action.params || {};
  const actuator = action.action === "capture" || params.camera ? "camera" : params.led !== undefined ? "led" : params.buzzer !== undefined ? "buzzer" : params.motor !== undefined || params.angle !== undefined ? "motor" : "device";
  let actionKey = actuatorCatalog[actuator].actions.find(key => {
    const expected = sceneActionCatalog[key].params;
    return Object.entries(expected).every(([field, value]) => params[field] === value);
  }) || actuatorCatalog[actuator].actions[0];
  if (actuator === "motor" && params.angle !== undefined) actionKey = "motor_angle";
  return { ...action, actuator, action_key: actionKey, angle: Number(params.angle ?? 90), save_directory: params.save_directory || "" };
}

function blankScene(devices = []) {
  const source = devices.find(item => !item.simulated);
  const target = devices.find(item => ["zigbee", "wifi", "lora"].includes(item.project) && !item.simulated);
  return {
    scene_id: "", name: "", description: "", condition_logic: "and", enabled: true,
    duration_seconds: 30, cooldown_seconds: 60, schedule_start: "", schedule_end: "", time_mode: "permanent",
    conditions: [{ device_id: source?.device_id || "", sensor: "temperature", operator: "gt", value: 35, trigger_mode: "level", hold_seconds: 0 }],
    actions: [{ device_type: target?.project || "zigbee", device_id: target?.device_id || "", actuator: "", action_key: "", save_directory: "" }],
  };
}

function sceneToForm(scene) {
  return {
    ...scene,
    schedule_start: scene.schedule_start || "", schedule_end: scene.schedule_end || "",
    time_mode: scene.schedule_start || scene.schedule_end ? "scheduled" : "permanent",
    conditions: scene.conditions.map(condition => ({ device_id: "", hold_seconds: 0, ...condition })),
    actions: scene.actions.map(inferActionForm),
  };
}

function Scenes({ devices }) {
  const [scenes, setScenes] = useState([]); const [sensors, setSensors] = useState(fallbackSceneSensors); const [triggers, setTriggers] = useState([]);
  const [selectedId, setSelectedId] = useState(""); const [form, setForm] = useState(() => blankScene(devices)); const [busy, setBusy] = useState(false); const [error, setError] = useState("");
  const sources = devices.filter(item => !item.simulated);
  const targets = devices.filter(item => ["lora", "zigbee", "wifi", "generic"].includes(item.project) && !item.simulated);
  const refreshScenes = useCallback(async () => {
    setError("");
    const [sceneResult, sensorResult, triggerResult] = await Promise.allSettled([api("/api/scenes"), api("/api/scenes/sensors"), api("/api/scene-triggers?limit=50")]);
    if (sceneResult.status === "fulfilled") setScenes(sceneResult.value); else setError(sceneResult.reason.message);
    if (sensorResult.status === "fulfilled" && sensorResult.value.length) setSensors(sensorResult.value);
    if (triggerResult.status === "fulfilled") setTriggers(triggerResult.value);
  }, []);
  useEffect(() => { refreshScenes(); }, [refreshScenes]);
  const selectScene = scene => { setSelectedId(scene.scene_id); setForm(sceneToForm(scene)); setError(""); };
  const updateCondition = (index, key, value) => setForm(current => ({ ...current, conditions: current.conditions.map((item, position) => {
    if (position !== index) return item;
    if (key === "device_id") return { ...item, device_id: value };
    if (key === "sensor") {
      const definition = sensors.find(sensor => sensor.sensor_id === value);
      const isBool = definition?.data_type === "bool";
      return { ...item, sensor: value, operator: isBool ? "eq" : item.operator, value: isBool ? true : item.value, trigger_mode: value === "pir" ? "edge" : item.trigger_mode, hold_seconds: value === "pir" ? 0 : item.hold_seconds };
    }
    return { ...item, [key]: value };
  }) }));
  const updateAction = (index, key, value) => setForm(current => ({ ...current, actions: current.actions.map((item, position) => {
    if (position !== index) return item;
    if (key === "device_id") { const device = targets.find(candidate => candidate.device_id === value); return { ...item, device_id: value, device_type: device?.project || item.device_type, actuator: "", action_key: "", save_directory: "" }; }
    if (key === "actuator") {
      const device = targets.find(candidate => candidate.device_id === item.device_id);
      return { ...item, actuator: value, action_key: actuatorActionOptions(device, value)[0] || "", save_directory: "" };
    }
    return { ...item, [key]: value };
  }) }));
  const save = async event => {
    event.preventDefault(); setError("");
    if (form.enabled && !window.confirm("保存后该场景将处于启用状态，条件满足时会自动向真实设备下发命令。确认继续？")) return;
    const payload = {
      ...form,
      schedule_start: form.time_mode === "permanent" ? null : form.schedule_start || null,
      schedule_end: form.time_mode === "permanent" ? null : form.schedule_end || null,
      conditions: form.conditions.map(item => ({ ...item, value: sensors.find(sensor => sensor.sensor_id === item.sensor)?.data_type === "bool" ? String(item.value) === "true" || item.value === true : Number(item.value), hold_seconds: item.trigger_mode === "level" ? Number(item.hold_seconds || 0) : 0 })),
      actions: form.actions.map(item => { const definition = sceneActionCatalog[item.action_key]; return { device_type: item.device_type, device_id: item.device_id, action: definition.action, params: { ...definition.params, ...(item.action_key === "camera_save" && item.save_directory ? { save_directory: item.save_directory } : {}) } }; }),
    };
    delete payload.time_mode;
    setBusy(true);
    try { const saved = await api(form.scene_id ? `/api/scenes/${encodeURIComponent(form.scene_id)}` : "/api/scenes", { method: form.scene_id ? "PUT" : "POST", body: JSON.stringify(payload) }); await refreshScenes(); setSelectedId(saved.scene_id); setForm(sceneToForm(saved)); }
    catch (problem) { setError(problem.message); } finally { setBusy(false); }
  };
  const toggle = async scene => {
    const enable = !scene.enabled;
    if (enable && !window.confirm(`启用“${scene.name}”后，满足条件会自动控制真实设备。确认启用？`)) return;
    setBusy(true); try { await api(`/api/scenes/${encodeURIComponent(scene.scene_id)}/${enable ? "enable" : "disable"}`, { method: "POST" }); await refreshScenes(); } catch (problem) { setError(problem.message); } finally { setBusy(false); }
  };
  const remove = async scene => {
    if (!scene?.scene_id || !window.confirm(`确认永久删除场景“${scene.name}”？`)) return;
    setBusy(true); try { await api(`/api/scenes/${encodeURIComponent(scene.scene_id)}`, { method: "DELETE" }); if (selectedId === scene.scene_id) { setSelectedId(""); setForm(blankScene(targets)); } await refreshScenes(); } catch (problem) { setError(problem.message); } finally { setBusy(false); }
  };
  const sensorLabel = id => sensors.find(item => item.sensor_id === id)?.label || id;
  const deviceLabel = id => devices.find(item => item.device_id === id)?.name || id || "任意设备";
  return <div className="scene-layout">
    <section className="panel scene-list"><div className="panel-head"><div><span>SMART AUTOMATIONS</span><h2>智慧场景</h2></div><button className="scene-add" onClick={() => { setSelectedId(""); setForm(blankScene(targets)); }}><Plus size={15} />新建</button></div><div className="scene-list-scroll">{scenes.map(scene => <article className={selectedId === scene.scene_id ? "active" : ""} key={scene.scene_id}><button className="scene-select" onClick={() => selectScene(scene)}><span className={scene.enabled ? "enabled" : "disabled"}><Power size={15} /></span><div><strong>{scene.name}</strong><small>{scene.conditions.map(item => `${deviceLabel(item.device_id)} · ${sensorLabel(item.sensor)} ${item.operator} ${String(item.value)}`).join(scene.condition_logic === "and" ? " 且 " : " 或 ")}</small></div><time>{scene.last_triggered_at ? `最近 ${new Date(scene.last_triggered_at).toLocaleString("zh-CN")}` : "尚未触发"}</time></button><button className={`scene-toggle ${scene.enabled ? "on" : ""}`} disabled={busy} onClick={() => toggle(scene)}>{scene.enabled ? "已启用" : "已停用"}</button><button className="scene-delete" title={`删除 ${scene.name}`} disabled={busy} onClick={() => remove(scene)}><Trash2 size={15} /></button></article>)}</div>{!scenes.length && <Empty icon={Layers3} title="还没有场景规则" detail="创建规则后由桥接进程监听真实上行数据" />}</section>
    <form className="panel scene-editor" onSubmit={save}><div className="panel-head"><div><span>SCENE BUILDER</span><h2>{form.scene_id ? "编辑场景" : "新建场景"}</h2></div><label className="scene-enabled"><input type="checkbox" checked={form.enabled} onChange={event => setForm(current => ({ ...current, enabled: event.target.checked }))} />启用</label></div>
      <div className="scene-basic"><label>场景名称<input required value={form.name} onChange={event => setForm(current => ({ ...current, name: event.target.value }))} placeholder="例如：人员经过蜂鸣提醒" /></label><label>说明<input value={form.description} onChange={event => setForm(current => ({ ...current, description: event.target.value }))} placeholder="场景用途和触发结果" /></label></div>
      <section className="scene-section"><header><div><span>IF</span><strong>触发条件</strong></div><select value={form.condition_logic} onChange={event => setForm(current => ({ ...current, condition_logic: event.target.value }))}><option value="and">满足全部条件 AND</option><option value="or">满足任一条件 OR</option></select></header>{form.conditions.map((condition, index) => { const definition = sensors.find(sensor => sensor.sensor_id === condition.sensor); const isBool = definition?.data_type === "bool"; return <div className={`scene-rule-row ${condition.trigger_mode === "level" ? "with-hold" : ""}`} key={`${index}-${condition.device_id}-${condition.sensor}`}><select value={condition.device_id} onChange={event => updateCondition(index, "device_id", event.target.value)}><option value="">选择来源设备</option>{sources.map(device => <option value={device.device_id} key={`${device.project}-${device.device_id}`}>{device.name || device.device_id}</option>)}</select><select value={condition.sensor} onChange={event => updateCondition(index, "sensor", event.target.value)}>{sensors.map(sensor => <option value={sensor.sensor_id} key={sensor.sensor_id}>{sensor.label}{sensor.unit ? ` (${sensor.unit})` : ""}</option>)}</select><select className={isBool ? "disabled-operator" : ""} disabled={isBool} value={isBool ? "eq" : condition.operator} onChange={event => updateCondition(index, "operator", event.target.value)}><option value="gt">大于</option><option value="gte">大于等于</option><option value="lt">小于</option><option value="lte">小于等于</option><option value="eq">等于</option><option value="neq">不等于</option></select>{isBool ? <select value={String(condition.value)} onChange={event => updateCondition(index, "value", event.target.value === "true")}><option value="true">是 / 触发</option><option value="false">否 / 正常</option></select> : <input type="number" step="any" value={condition.value} onChange={event => updateCondition(index, "value", event.target.value)} />}<select value={condition.trigger_mode} onChange={event => updateCondition(index, "trigger_mode", event.target.value)}><option value="level">持续满足</option><option value="edge">首次满足</option></select>{condition.trigger_mode === "level" && <label className="hold-seconds"><input type="number" min="0" max="86400" value={condition.hold_seconds || 0} onChange={event => updateCondition(index, "hold_seconds", Number(event.target.value))} /><span>持续秒数</span></label>}<button type="button" title="删除条件" disabled={form.conditions.length === 1} onClick={() => setForm(current => ({ ...current, conditions: current.conditions.filter((_, position) => position !== index) }))}><Trash2 size={14} /></button></div>; })}<button type="button" className="add-row" onClick={() => setForm(current => ({ ...current, conditions: [...current.conditions, { device_id: sources[0]?.device_id || "", sensor: "temperature", operator: "gt", value: 35, trigger_mode: "level", hold_seconds: 0 }] }))}><Plus size={14} />添加条件</button></section>
      <section className="scene-section"><header><div><span>THEN</span><strong>执行动作</strong></div><small>依次选择板卡、板载执行器和动作；互斥动作将被系统拦截</small></header>{form.actions.map((action, index) => { const target = targets.find(device => device.device_id === action.device_id); const actuators = actuatorOptions(target); const actionKeys = actuatorActionOptions(target, action.actuator); const hasParameter = action.action_key === "camera_save"; return <div className={`scene-action-row ${hasParameter ? "has-parameter" : ""}`} key={index}><select required aria-label="目标设备" value={action.device_id} onChange={event => updateAction(index, "device_id", event.target.value)}><option value="">1. 选择目标设备</option>{targets.map(device => <option value={device.device_id} key={`${device.project}-${device.device_id}`}>{device.name || device.device_id} · {projectName[device.project]}</option>)}</select><select required aria-label="板载执行器" disabled={!action.device_id} value={action.actuator} onChange={event => updateAction(index, "actuator", event.target.value)}><option value="">2. 选择板载执行器</option>{actuators.map(key => <option value={key} key={key}>{actuatorCatalog[key].label}</option>)}</select><select required aria-label="执行动作" disabled={!action.actuator} value={action.action_key} onChange={event => updateAction(index, "action_key", event.target.value)}><option value="">3. 选择执行动作</option>{actionKeys.map(key => <option value={key} key={key}>{sceneActionCatalog[key].label}</option>)}</select>{action.action_key === "camera_save" && <label className="scene-save-path"><FolderOpen size={15} /><input value={action.save_directory || ""} onChange={event => updateAction(index, "save_directory", event.target.value)} placeholder="图像保存目录（留空为默认目录）" /></label>}<button type="button" title="删除动作" disabled={form.actions.length === 1} onClick={() => setForm(current => ({ ...current, actions: current.actions.filter((_, position) => position !== index) }))}><Trash2 size={14} /></button></div>; })}<button type="button" className="add-row" onClick={() => { const target = targets[0]; setForm(current => ({ ...current, actions: [...current.actions, { device_type: target?.project || "zigbee", device_id: target?.device_id || "", actuator: "", action_key: "", save_directory: "" }] })); }}><Plus size={14} />添加动作</button></section>
      <section className="scene-lifecycle"><label>动作保持时间（秒）<input type="number" min="0" value={form.duration_seconds} onChange={event => setForm(current => ({ ...current, duration_seconds: Number(event.target.value) }))} /></label><label>冷却期（秒）<input type="number" min="0" value={form.cooldown_seconds} onChange={event => setForm(current => ({ ...current, cooldown_seconds: Number(event.target.value) }))} /></label><label>时间设置<select value={form.time_mode} onChange={event => setForm(current => ({ ...current, time_mode: event.target.value }))}><option value="permanent">永久开启</option><option value="scheduled">指定时间</option></select></label>{form.time_mode === "scheduled" && <><label>生效时间<input value={form.schedule_start} onChange={event => setForm(current => ({ ...current, schedule_start: event.target.value }))} placeholder="HH:MM 或 ISO 时间" /></label><label>失效时间<input value={form.schedule_end} onChange={event => setForm(current => ({ ...current, schedule_end: event.target.value }))} placeholder="HH:MM 或 ISO 时间" /></label></>}</section>
      {error && <div className="form-error scene-error"><AlertTriangle size={16} />{error}</div>}<footer className="scene-editor-actions"><button className="primary" disabled={busy || !targets.length}><Save size={15} />{busy ? "正在同步" : "保存到桥接"}</button></footer>{!targets.length && <small className="scene-device-warning">请先接入至少一台设备，才能配置真实动作。</small>}</form>
    <section className="panel scene-history"><div className="panel-head compact"><div><span>AUTOMATION HISTORY</span><h2>最近触发记录</h2></div><b>{triggers.length} 条</b></div><div>{triggers.map(item => <article key={item.id}><span><Check size={15} /></span><div><strong>{item.scene_name}</strong><small>由 {item.device_id} 触发 · {Object.entries(item.conditions_snapshot).map(([key, value]) => `${sensorLabel(key)} ${String(value)}`).join(" · ")}</small></div><time>{new Date(item.triggered_at).toLocaleString("zh-CN")}</time><code>{item.trace_id || "--"}</code></article>)}</div>{!triggers.length && <Empty icon={Activity} title="暂无场景触发" detail="条件实际满足并成功下发动作后才会记录" />}</section>
  </div>;
}

function Alerts({ alerts, refresh }) {
  const acknowledge = async id => { await api(`/api/alerts/${id}/acknowledge`, { method: "POST" }); await refresh(); };
  const active = alerts.filter(x => x.status === "active");
  return <section className="panel page-panel"><div className="panel-head"><div><span>ENVIRONMENT ALERTS</span><h2>环境与设备告警</h2></div><div className="alert-summary"><b>{active.length}</b><span>条待处理</span></div></div><div className="alert-list">{alerts.length ? alerts.map(item => <article key={item.id} className={`alert-item ${item.severity} ${item.status}`}><span className="alert-icon"><AlertTriangle size={18} /></span><div><header><strong>{item.message}</strong><span>{projectName[item.project] || item.project}</span></header><p>{item.device_id} · {item.alert_type}</p><small>{new Date(item.created_at).toLocaleString("zh-CN")}</small></div>{item.status === "active" ? <button onClick={() => acknowledge(item.id)}><Check size={15} />确认处理</button> : <span className="alert-done">{item.acknowledged_by} 已确认</span>}</article>) : <Empty icon={ShieldCheck} title="当前没有告警" detail="设备数据达到用户配置的阈值后会显示在这里" />}</div></section>;
}

function Admin({ users, audits, refresh }) {
  const [userForm, setUserForm] = useState({ username: "", display_name: "", password: "", role: "user", active: true });
  const [error, setError] = useState("");
  const saveUser = async event => { event.preventDefault(); setError(""); try { await api("/api/admin/users", { method: "POST", body: JSON.stringify(userForm) }); setUserForm({ username: "", display_name: "", password: "", role: "user", active: true }); await refresh(); } catch (e) { setError(e.message); } };
  return <div className="admin-layout admin-accounts-only"><section className="panel"><div className="panel-head"><div><span>ACCOUNT MANAGEMENT</span><h2>用户与角色</h2></div><b>{users.length} 个账号</b></div><div className="admin-users">{users.map(item => <div key={item.username}><span className={`role-dot ${item.role}`} /><strong>{item.display_name}</strong><code>{item.username}</code><span>{item.role}</span><Pill online={item.active}>{item.active ? "启用" : "停用"}</Pill></div>)}</div><form className="admin-form" onSubmit={saveUser}><h3>新增或修改账号</h3><input placeholder="用户名" value={userForm.username} onChange={e => setUserForm(x => ({ ...x, username: e.target.value }))} /><input placeholder="显示名称" value={userForm.display_name} onChange={e => setUserForm(x => ({ ...x, display_name: e.target.value }))} /><input type="password" placeholder="密码（修改时可留空）" value={userForm.password} onChange={e => setUserForm(x => ({ ...x, password: e.target.value }))} /><select value={userForm.role} onChange={e => setUserForm(x => ({ ...x, role: e.target.value }))}><option value="user">普通用户</option><option value="tester">测试运维员</option><option value="admin">管理员</option></select><button className="primary"><Save size={15} />保存账号</button></form></section><section className="panel audit-panel"><div className="panel-head"><div><span>AUDIT TRAIL</span><h2>操作审计</h2></div><b>最近 {audits.length} 条</b></div><div className="audit-list">{audits.map(item => <div key={item.id}><span>{new Date(item.timestamp).toLocaleString("zh-CN")}</span><strong>{item.username}</strong><code>{item.action}</code><small>{item.resource}</small></div>)}</div></section>{error && <div className="form-error admin-error">{error}</div>}</div>;
}

function Connections({ status, project, profiles, vsoaProfiles, close, refresh, refreshProfiles, refreshVsoaProfiles }) {
  const projectTopics = project.mqtt?.uplink_topics || [];
  const defaults = status.mqtt?.topics?.length ? status.mqtt.topics : [...projectTopics, `${project.mqtt?.downlink_topic_prefix || "bridge/downlink"}/#`];
  const [form, setForm] = useState({ name: status.mqtt?.name || "本机项目 Broker", host: status.mqtt?.host || project.mqtt?.broker || "", port: status.mqtt?.port || project.mqtt?.port || 1883, client_id: status.mqtt?.client_id || "", username: status.mqtt?.username || "", password: "", qos: status.mqtt?.qos ?? project.mqtt?.qos ?? 1, topics: defaults.join("\n") });
  const [url, setUrl] = useState(status.vsoa?.url || project.vsoa?.local_url || "vsoa://127.0.0.1:3001"); const [vsoaName, setVsoaName] = useState("本机桥接项目"); const [vsoaHistory, setVsoaHistory] = useState(loadVsoaHistory); const [selectedProfile, setSelectedProfile] = useState(""); const [selectedVsoaProfile, setSelectedVsoaProfile] = useState(""); const [busy, setBusy] = useState(""); const [error, setError] = useState(""); const [diagnostic, setDiagnostic] = useState(null);
  useEffect(() => {
    Promise.allSettled([refreshProfiles(), refreshVsoaProfiles()]);
  }, [refreshProfiles, refreshVsoaProfiles]);
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
  const [events, setEvents] = useState([]); const [runs, setRuns] = useState([]); const [performanceRuns, setPerformanceRuns] = useState([]); const [devices, setDevices] = useState([]); const [pairs, setPairs] = useState([]); const [profiles, setProfiles] = useState([]); const [vsoaProfiles, setVsoaProfiles] = useState([]); const [alerts, setAlerts] = useState([]); const [commands, setCommands] = useState([]); const [users, setUsers] = useState([]); const [audits, setAudits] = useState([]); const [selected, setSelected] = useState(null);
  const [status, setStatus] = useState({ platform: { mode: "incomplete" }, mqtt: {}, vsoa: {}, bridge: {}, metrics: {} }); const [project, setProject] = useState({}); const [clock, setClock] = useState(new Date()); const [connections, setConnections] = useState(false);
  const role = session?.user?.role || "user"; const visibleNav = nav.filter(item => roleRank[role] >= roleRank[item[3]]);
  const toggleTheme = () => setTheme(current => current === "dark" ? "light" : "dark");
  useEffect(() => { document.documentElement.dataset.theme = theme; localStorage.setItem(THEME_KEY, theme); }, [theme]);
  useEffect(() => {
    const expireSession = () => { setSession(null); setPage("overview"); };
    window.addEventListener(AUTH_EXPIRED_EVENT, expireSession);
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, expireSession);
  }, []);
  const refreshProfiles = useCallback(async () => { if (role === "admin") setProfiles(await api("/api/connection-profiles")); }, [role]);
  const refreshVsoaProfiles = useCallback(async () => { if (role === "admin") setVsoaProfiles(await api("/api/vsoa-connection-profiles")); }, [role]);
  const refreshCommands = useCallback(async () => setCommands(await api("/api/commands")), []);
  const refreshAlerts = useCallback(async () => setAlerts(await api("/api/alerts")), []);
  const refreshAdmin = useCallback(async () => { if (role !== "admin") return; const [u, a] = await Promise.all([api("/api/admin/users"), api("/api/admin/audit-logs")]); setUsers(u); setAudits(a); }, [role]);
  const refresh = useCallback(async () => {
    if (!authToken) return;
    const base = await Promise.all([api("/api/status"), api("/api/project"), api("/api/events?limit=160"), api("/api/devices"), api("/api/alerts"), api("/api/commands")]);
    setStatus(base[0]); setProject(base[1]); setEvents(base[2]); setDevices(base[3]); setAlerts(base[4]); setCommands(base[5]);
    if (roleRank[role] >= roleRank.tester) { const [r, pr, p] = await Promise.all([api("/api/test-runs"), api("/api/performance-runs"), api("/api/transformations")]); setRuns(r); setPerformanceRuns(pr); setPairs(p); }
    if (role === "admin") {
      const [c, v, u, a] = await Promise.allSettled([api("/api/connection-profiles"), api("/api/vsoa-connection-profiles"), api("/api/admin/users"), api("/api/admin/audit-logs")]);
      if (c.status === "fulfilled") setProfiles(c.value);
      if (v.status === "fulfilled") setVsoaProfiles(v.value);
      if (u.status === "fulfilled") setUsers(u.value);
      if (a.status === "fulfilled") setAudits(a.value);
    }
  }, [role, session?.token]);
  const refreshLiveState = useCallback(async () => { const [s, d] = await Promise.all([api("/api/status"), api("/api/devices")]); setStatus(s); setDevices(d); }, []);
  useEffect(() => {
    if (!session) return undefined;
    let retry;
    const load = () => refresh().catch(() => { retry = setTimeout(load, 2000); });
    load();
    const timer = setInterval(() => setClock(new Date()), 1000);
    return () => { clearInterval(timer); clearTimeout(retry); };
  }, [refresh, session]);
  useEffect(() => { if (!session) return; const timer = setInterval(() => refreshLiveState().catch(() => {}), 5000); return () => clearInterval(timer); }, [refreshLiveState, session]);
  useEffect(() => {
    if (!connections || role !== "admin") return;
    Promise.allSettled([refreshProfiles(), refreshVsoaProfiles()]);
  }, [connections, role, refreshProfiles, refreshVsoaProfiles]);
  useEffect(() => { if (!session) return; let ws, retry; const open = () => { ws = new WebSocket(`${WS}?token=${encodeURIComponent(session.token)}`); ws.onmessage = message => { const p = JSON.parse(message.data); if (p.type === "event" && !isSceneTriggerEvent(p.data)) setEvents(x => [p.data, ...x].slice(0, 200)); if (p.type === "metrics") setStatus(x => ({ ...x, metrics: p.data })); if (p.type === "run") setRuns(x => [p.data, ...x.filter(r => r.id !== p.data.id)]); if (p.type === "performance") setPerformanceRuns(x => [p.data, ...x.filter(r => r.id !== p.data.id)]); }; ws.onclose = () => { retry = setTimeout(open, 1800); }; }; open(); return () => { clearTimeout(retry); ws?.close(); }; }, [session]);
  const login = value => { authToken = value.token; localStorage.setItem(AUTH_KEY, JSON.stringify(value)); setSession(value); };
  const logout = () => { localStorage.removeItem(AUTH_KEY); authToken = ""; setSession(null); setPage("overview"); };
  if (!session) return <Login onLogin={login} theme={theme} toggleTheme={toggleTheme} />;
  const title = nav.find(item => item[0] === page)?.[1];
  return <div className="shell">
    <aside className={"sidebar " + (menu ? "open" : "")}><div className="brand"><span><img src="/acoinfo-logo.png" alt="翼辉信息 ACOINFO" /></span><div><strong>ACOINFO IOTNEX</strong><small>Intelligent Connection</small></div></div><nav>{visibleNav.map(([id, label, Icon]) => <button className={page === id ? "active" : ""} onClick={() => { setPage(id); setMenu(false); }} key={id}><Icon size={18} /><span>{label}</span>{page === id && <i />}</button>)}</nav><footer><div><span>HOME STATUS</span><strong>{status.platform?.mode === "ready" ? "ONLINE" : "WAIT"}</strong></div>{role === "admin" && <button onClick={() => setConnections(true)}><Settings2 size={17} />连接配置</button>}<small>{session.user.display_name} · {role}<br />{project.version ? `Bridge v${project.version}` : "智慧家居平台"}</small></footer></aside>
    <main><header className="topbar"><button className="menu-btn" onClick={() => setMenu(!menu)}><Menu size={20} /></button><div className="title"><span>智慧家居环境平台</span><strong>{title}</strong></div><div className="top-actions"><div className="clock"><span>{clock.toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" })}</span><strong>{clock.toLocaleTimeString("zh-CN", { hour12: false })}</strong></div>{role === "admin" && <button className="connection-btn" onClick={() => setConnections(true)}>{status.platform?.mode === "ready" ? <Wifi size={17} /> : <WifiOff size={17} />}<span>{status.platform?.mode === "ready" ? "链路在线" : "链路待接入"}</span><ChevronDown size={15} /></button>}<button className="icon-btn" onClick={toggleTheme} title="切换深浅主题">{theme === "dark" ? <Sun size={17} /> : <Moon size={17} />}</button><button className="icon-btn" onClick={refresh} title="刷新"><RefreshCw size={17} /></button><button className="icon-btn" onClick={logout} title="退出登录"><LogOut size={17} /></button></div></header>
      <div className="content">{page === "overview" && <Overview events={events} status={status} go={setPage} role={role} devices={devices} alerts={alerts} />}{page === "lora" && <EnvironmentDashboard theme={theme} />}{page === "stream" && <Stream events={events} selected={selected} setSelected={setSelected} />}{page === "devices" && <Devices devices={devices} commands={commands} refreshCommands={refreshCommands} refreshDevices={refreshLiveState} role={role} />}{page === "scenes" && <Scenes devices={devices} />}{page === "alerts" && <Alerts alerts={alerts} refresh={refreshAlerts} />}{page === "mapping" && <Mapping pairs={pairs} />}{page === "simulator" && <Simulator status={status} project={project} go={setPage} />}{page === "performance" && <Performance runs={performanceRuns} status={status} project={project} refresh={async () => setPerformanceRuns(await api("/api/performance-runs"))} />}{page === "runs" && <Runs runs={runs} refresh={async () => setRuns(await api("/api/test-runs"))} />}{page === "admin" && <Admin users={users} audits={audits} refresh={refreshAdmin} />}</div>
    </main><Drawer event={isSceneTriggerEvent(selected) ? null : selected} close={() => setSelected(null)} />{connections && role === "admin" && <Connections status={status} project={project} profiles={profiles} vsoaProfiles={vsoaProfiles} close={() => setConnections(false)} refresh={refresh} refreshProfiles={refreshProfiles} refreshVsoaProfiles={refreshVsoaProfiles} />}
  </div>;
}
