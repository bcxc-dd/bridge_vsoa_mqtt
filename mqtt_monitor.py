"""MQTT real-time message monitor — pure GUI demo client.

All bridge logic (MQTT↔VSOA conversion, device registry, dedup, retry)
is delegated to bridge/main.py.

Run:
    python mqtt_monitor.py

Requires bridge/main.py running for downlink:
  - RPC:  vsoa://127.0.0.1:3001  /bridge/send_command
  - Pub/Sub: business VSOA Server :3000  (auto-started by this GUI if needed)
             → bridge subscribes /ctrl/cmd → MQTT
"""

from __future__ import annotations

import argparse
import json
import queue
import sys
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any, Callable
from urllib.parse import urlparse

try:
    import paho.mqtt.client as mqtt
except ImportError:
    mqtt = None  # type: ignore[assignment]

try:
    import vsoa
except ImportError:
    vsoa = None  # type: ignore[assignment]


DEFAULT_HOST = "192.168.3.219"
DEFAULT_PORT = 1883
DEFAULT_TOPICS = (
    "bridge/uplink/lora/+/data",
    "bridge/downlink/#",
    "s3/eora-s3-400tb-001/data",
)
PUBLIC_BROKER_HOST = "broker.emqx.io"
PUBLIC_BROKER_PORT = 1883
PUBLIC_BROKER_TOPICS = (
    "bridge/uplink/+/rs485_meter_01/#",
    "bridge/uplink/lora/+/data",
    "bridge/uplink/zigbee/+/data",
    "bridge/uplink/generic/+/data",
    "bridge/uplink/generic/+/status",
    "bridge/uplink/generic/+/error",
    "lora/+/up",
    "zigbee/+/report",
)
DEFAULT_CONFIG = Path(__file__).resolve().parent / "config.yaml"
BRIDGE_ROOT = Path(__file__).resolve().parent

if str(BRIDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(BRIDGE_ROOT))


# ---------------------------------------------------------------------------
# data classes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ReceivedMessage:
    """A single MQTT message displayed in the main table."""
    received_at: str
    host: str
    gateway: str
    topic: str
    qos: int
    payload: str


@dataclass(frozen=True)
class PublicBrokerMessage:
    received_at: str
    topic: str
    qos: int
    retained: bool
    payload: str


# ---------------------------------------------------------------------------
# LocalVsoaServer
# ---------------------------------------------------------------------------

class LocalVsoaServer:
    """Auto-start a business VSOA server when the configured one is absent.

    Thin shell — only forwards datagrams via publish so that bridge's PubSub
    client (which subscribes /ctrl/cmd) can see them.  Does NOT handle
    /ctrl/cmd itself — that's bridge's job.
    """

    LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}

    def __init__(
        self,
        server_url: str,
        bind_host: str,
        bind_port: int,
        auto_start: bool,
        status_callback: Callable[[str], None],
    ) -> None:
        if vsoa is None:
            raise RuntimeError("缺少 vsoa")
        parsed = urlparse(server_url)
        self.server_url = server_url
        self.host = parsed.hostname or ""
        self.bind_host = bind_host
        self.bind_port = bind_port
        self.auto_start = auto_start
        self.status_callback = status_callback
        self.server = None
        self.thread: threading.Thread | None = None

    def _can_connect(self, timeout: float = 0.5) -> bool:
        client = vsoa.Client()
        try:
            return client.connect(self.server_url, timeout=timeout) == vsoa.Client.CONNECT_OK
        except Exception:
            return False
        finally:
            client.close()

    def start_if_needed(self) -> bool:
        if self._can_connect():
            self.status_callback("检测到已有 VSOA Server，直接复用")
            return True
        if not self.auto_start:
            self.status_callback("自动启动已关闭，等待外部 VSOA Server")
            return False
        if self.host not in self.LOOPBACK_HOSTS:
            self.status_callback("目标为远程地址，等待外部 VSOA Server")
            return False
        if not self.bind_host or not 1 <= self.bind_port <= 65535:
            self.status_callback("VSOA Server 监听地址或端口无效")
            return False

        server = vsoa.Server({"name": "MQTT Monitor Business Server"})
        self.server = server

        def on_data(cli, url, payload, quick) -> None:
            try:
                server.publish(url, payload)
            except Exception as exc:
                self.status_callback(f"VSOA 转发失败：{exc}")

        server.ondata = on_data

        def run_server() -> None:
            try:
                server.run(self.bind_host, self.bind_port)
            except Exception as exc:
                self.status_callback(f"VSOA Server 启动失败：{exc}")

        self.thread = threading.Thread(target=run_server, name="vsoa-business", daemon=True)
        self.thread.start()

        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if self._can_connect(timeout=0.2):
                self.status_callback(f"VSOA Server 已监听 {self.bind_host}:{self.bind_port}")
                return True
            time.sleep(0.1)
        self.status_callback("VSOA Server 启动超时")
        return False

    def publish_control_command(self, data: dict[str, Any], target_url: str | None = None) -> bool:
        """Send a command datagram to /ctrl/cmd.  Bridge handles the rest."""
        command = dict(data)
        payload = vsoa.Payload(param=command)
        destination = target_url or self.server_url
        client = vsoa.Client()
        try:
            if client.connect(destination, timeout=2.0) != vsoa.Client.CONNECT_OK:
                return False
            return bool(client.datagram("/ctrl/cmd", payload=payload, quick=False))
        except Exception:
            return False
        finally:
            client.close()


# ---------------------------------------------------------------------------
# VsoaEventListener — subscribe to bridge's uplink VSOA publications
# ---------------------------------------------------------------------------

class VsoaEventListener:
    """Subscribe to bridge's VSOA publications to verify uplink bridge.

    Bridge publishes after each uplink MQTT→VSOA conversion:
      - /device/update  — device registered or updated
      - /bridge/event    — data_received event
    """

    def __init__(
        self,
        server_url: str,
        event_callback: Callable[[str, dict[str, Any]], None],
        status_callback: Callable[[str], None],
    ) -> None:
        self.server_url = server_url
        self.event_callback = event_callback
        self.status_callback = status_callback
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self._connected = False

    def start(self) -> None:
        if self.thread is not None:
            return
        self.thread = threading.Thread(target=self._run, name="vsoa-listener", daemon=True)
        self.thread.start()

    def _run(self) -> None:
        while not self.stop_event.is_set():
            client = vsoa.Client()
            try:
                if client.connect(self.server_url, timeout=3.0) != vsoa.Client.CONNECT_OK:
                    self.status_callback("VSOA 监听: 连接失败，3s 后重试")
                    self.stop_event.wait(3.0)
                    continue

                # callback for received VSOA messages
                def on_message(cli, url, payload, quick):
                    try:
                        data = dict(payload.param) if payload and hasattr(payload, "param") and payload.param else {}
                        self.event_callback(url, data)
                    except Exception:
                        pass

                client.onmessage = on_message
                client.subscribe("/device/update")
                client.subscribe("/bridge/event")
                self._connected = True
                self.status_callback(f"VSOA 监听已连接 {self.server_url}")

                # VSOA event loop blocks here until disconnected
                client.run()
            except Exception as exc:
                self.status_callback(f"VSOA 监听异常: {exc}")
            finally:
                self._connected = False
                try:
                    client.close()
                except Exception:
                    pass
                if not self.stop_event.is_set():
                    self.status_callback("VSOA 监听断开，3s 后重连...")
                    self.stop_event.wait(3.0)

    @property
    def connected(self) -> bool:
        return self._connected

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=3.0)


# ---------------------------------------------------------------------------
# display helpers
# ---------------------------------------------------------------------------

def _parse_lora_payload(payload: bytes) -> dict[str, Any] | None:
    if len(payload) != 16:
        return None
    flags = payload[15]
    return {
        "seq": int.from_bytes(payload[0:2], "big"),
        "boot_id": hex(int.from_bytes(payload[2:6], "big")),
        "send_time_ms": int.from_bytes(payload[6:10], "big"),
        "lorawan_retry_count": payload[10],
        "temperature": int.from_bytes(payload[11:13], "big", signed=True) / 10.0,
        "humidity": int.from_bytes(payload[13:15], "big") / 10.0,
        "joined": bool(flags & 0x01),
        "application_retry": bool(flags & 0x08),
        "flags": hex(flags),
    }


def decode_message_payload(payload: bytes) -> tuple[dict[str, Any] | None, str]:
    import base64 as _b64
    text = payload.decode("utf-8", errors="replace")
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None, text
    if not isinstance(data, dict):
        return None, json.dumps(data, ensure_ascii=False, indent=2)
    display_data = dict(data)
    encoded = data.get("data")
    if isinstance(encoded, str):
        try:
            decoded = _b64.b64decode(encoded, validate=True)
            parsed = _parse_lora_payload(decoded)
            if parsed is not None:
                display_data["parsed_payload"] = parsed
        except (ValueError, _b64.binascii.Error):
            pass
    return data, json.dumps(display_data, ensure_ascii=False, indent=2)


def extract_gateway(data: dict[str, Any] | None) -> str:
    if not data:
        return "-"
    for key in ("gatewayId", "gateway_id", "gateway", "host"):
        value = data.get(key)
        if value not in (None, ""):
            return str(value)
    rx_info = data.get("rxInfo")
    if isinstance(rx_info, list):
        for item in rx_info:
            if not isinstance(item, dict):
                continue
            for key in ("gatewayId", "gateway_id"):
                value = item.get(key)
                if value not in (None, ""):
                    return str(value)
    return "-"


# ---------------------------------------------------------------------------
# MQTT helpers
# ---------------------------------------------------------------------------

def create_mqtt_client(client_id: str):
    if mqtt is None:
        raise RuntimeError("缺少 paho-mqtt，请先执行: pip install paho-mqtt")
    if hasattr(mqtt, "CallbackAPIVersion"):
        return mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION1, client_id=client_id)
    return mqtt.Client(client_id=client_id)


# ---------------------------------------------------------------------------
# PublicBrokerMonitor
# ---------------------------------------------------------------------------

class PublicBrokerMonitor:
    """Independent window for the default public MQTT broker."""

    POLL_INTERVAL_MS = 100

    def __init__(self, parent: tk.Tk, on_close: Callable[[], None]) -> None:
        self.on_close_callback = on_close
        self.window = tk.Toplevel(parent)
        self.window.title("公共 MQTT Broker 消息监视器")
        self.window.geometry("1000x650")
        self.window.minsize(720, 480)
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.client = None
        self.closing = False
        self.after_id = None
        self.message_count = 0
        self.payloads: dict[str, str] = {}

        self.host_var = tk.StringVar(value=PUBLIC_BROKER_HOST)
        self.port_var = tk.StringVar(value=str(PUBLIC_BROKER_PORT))
        self.status_var = tk.StringVar(value="未连接")
        self.count_var = tk.StringVar(value="消息 0")

        self._build_ui()
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.after_id = self.window.after(self.POLL_INTERVAL_MS, self._drain_events)
        self.connect()

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.window, padding=12)
        outer.pack(fill=tk.BOTH, expand=True)

        connection = ttk.Frame(outer)
        connection.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(connection, text="Broker").pack(side=tk.LEFT)
        self.host_entry = ttk.Entry(connection, textvariable=self.host_var, width=24)
        self.host_entry.pack(side=tk.LEFT, padx=(6, 12))
        ttk.Label(connection, text="端口").pack(side=tk.LEFT)
        self.port_entry = ttk.Entry(connection, textvariable=self.port_var, width=7)
        self.port_entry.pack(side=tk.LEFT, padx=(6, 12))
        self.connect_button = ttk.Button(connection, text="连接", command=self.toggle_connection, width=9)
        self.connect_button.pack(side=tk.LEFT)
        ttk.Button(connection, text="清空", command=self.clear, width=9).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Label(connection, textvariable=self.count_var).pack(side=tk.RIGHT)

        ttk.Label(outer, textvariable=self.status_var).pack(fill=tk.X, anchor=tk.W, pady=(0, 4))
        ttk.Label(outer, text=f"订阅 {len(PUBLIC_BROKER_TOPICS)} 个统一上行 Topic").pack(
            fill=tk.X, anchor=tk.W, pady=(0, 8)
        )

        table_frame = ttk.Frame(outer)
        table_frame.pack(fill=tk.BOTH, expand=True)
        columns = ("time", "topic", "qos", "retained", "payload")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings")
        headings = {"time": "接收时间", "topic": "Topic", "qos": "QoS", "retained": "Retain", "payload": "消息数据"}
        widths = {"time": 190, "topic": 300, "qos": 55, "retained": 65, "payload": 360}
        for col in columns:
            self.tree.heading(col, text=headings[col])
            self.tree.column(col, width=widths[col], minwidth=50,
                             anchor=tk.CENTER if col in ("qos", "retained") else tk.W,
                             stretch=col in ("topic", "payload"))
        scrollbar = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.bind("<<TreeviewSelect>>", self._show_selected)

        ttk.Label(outer, text="完整消息").pack(fill=tk.X, pady=(10, 4))
        detail_frame = ttk.Frame(outer)
        detail_frame.pack(fill=tk.BOTH)
        self.detail = tk.Text(detail_frame, height=10, wrap=tk.NONE, font=("Consolas", 10), state=tk.DISABLED)
        scroll_y = ttk.Scrollbar(detail_frame, orient=tk.VERTICAL, command=self.detail.yview)
        scroll_x = ttk.Scrollbar(detail_frame, orient=tk.HORIZONTAL, command=self.detail.xview)
        self.detail.configure(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set)
        self.detail.grid(row=0, column=0, sticky="nsew")
        scroll_y.grid(row=0, column=1, sticky="ns")
        scroll_x.grid(row=1, column=0, sticky="ew")
        detail_frame.columnconfigure(0, weight=1)
        detail_frame.rowconfigure(0, weight=1)

    def toggle_connection(self) -> None:
        if self.client is None:
            self.connect()
        else:
            self.disconnect()

    def connect(self) -> None:
        host = self.host_var.get().strip()
        try:
            port = int(self.port_var.get())
            if not host or not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            messagebox.showerror("参数错误", "请输入有效的 Broker 和端口（1-65535）。", parent=self.window)
            return
        try:
            client = create_mqtt_client(f"public-monitor-{id(self):x}")
            client.user_data_set({"host": host, "port": port})
            client.on_connect = self._on_connect
            client.on_disconnect = self._on_disconnect
            client.on_message = self._on_message
            client.reconnect_delay_set(min_delay=1, max_delay=30)
            client.connect_async(host, port, keepalive=60)
            client.loop_start()
        except Exception as exc:
            messagebox.showerror("连接失败", str(exc), parent=self.window)
            return
        self.client = client
        self.status_var.set(f"正在连接 tcp://{host}:{port} ...")
        self.connect_button.configure(text="断开")
        self.host_entry.configure(state=tk.DISABLED)
        self.port_entry.configure(state=tk.DISABLED)

    def disconnect(self) -> None:
        client, self.client = self.client, None
        if client is not None:
            try:
                client.disconnect()
            finally:
                client.loop_stop()
        self.status_var.set("已断开")
        self.connect_button.configure(text="连接")
        self.host_entry.configure(state=tk.NORMAL)
        self.port_entry.configure(state=tk.NORMAL)

    def _on_connect(self, client, userdata, flags, rc, properties=None) -> None:
        if int(rc) == 0:
            client.subscribe([(t, 1) for t in PUBLIC_BROKER_TOPICS])
            self.events.put(("status", f"已连接 tcp://{userdata['host']}:{userdata['port']}"))
        else:
            self.events.put(("status", f"连接失败，返回码 {rc}"))

    def _on_disconnect(self, client, userdata, rc, properties=None) -> None:
        if not self.closing and self.client is client:
            self.events.put(("status", f"连接已断开（{rc}），正在重连..."))

    def _on_message(self, client, userdata, msg) -> None:
        raw = msg.payload.decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw)
            payload = json.dumps(parsed, ensure_ascii=False, indent=2)
        except json.JSONDecodeError:
            payload = raw
        self.events.put(("message", PublicBrokerMessage(
            received_at=datetime.now().astimezone().isoformat(sep=" ", timespec="milliseconds"),
            topic=msg.topic, qos=msg.qos, retained=bool(msg.retain), payload=payload,
        )))

    def _drain_events(self) -> None:
        try:
            while True:
                event, value = self.events.get_nowait()
                if event == "status":
                    self.status_var.set(value)
                elif event == "message":
                    self._add_message(value)
        except queue.Empty:
            pass
        if not self.closing:
            self.after_id = self.window.after(self.POLL_INTERVAL_MS, self._drain_events)

    def _add_message(self, message: PublicBrokerMessage) -> None:
        preview = " ".join(message.payload.split())
        if len(preview) > 220:
            preview = preview[:217] + "..."
        item = self.tree.insert("", 0, values=(
            message.received_at, message.topic, message.qos,
            "是" if message.retained else "否", preview,
        ))
        self.payloads[item] = message.payload
        self.message_count += 1
        self.count_var.set(f"消息 {self.message_count}")
        children = self.tree.get_children()
        if len(children) > 500:
            expired = children[500:]
            self.tree.delete(*expired)
            for child in expired:
                self.payloads.pop(child, None)

    def _show_selected(self, event=None) -> None:
        selection = self.tree.selection()
        if selection:
            MqttMonitorApp._set_detail_text(self.detail, self.payloads.get(selection[0], ""))

    def clear(self) -> None:
        children = self.tree.get_children()
        if children:
            self.tree.delete(*children)
        self.payloads.clear()
        self.message_count = 0
        self.count_var.set("消息 0")
        MqttMonitorApp._set_detail_text(self.detail, "")

    def focus(self) -> None:
        self.window.deiconify()
        self.window.lift()
        self.window.focus_force()

    def close(self) -> None:
        if self.closing:
            return
        self.closing = True
        self.disconnect()
        if self.after_id is not None:
            try:
                self.window.after_cancel(self.after_id)
            except tk.TclError:
                pass
        self.window.destroy()
        self.on_close_callback()


# ---------------------------------------------------------------------------
# MqttMonitorApp
# ---------------------------------------------------------------------------

class MqttMonitorApp:
    """MQTT monitor GUI — pure display + downlink command sender.

    All bridge logic (MQTT↔VSOA, registry, dedup, retry) is in bridge/main.py.
    """

    POLL_INTERVAL_MS = 100

    def __init__(
        self,
        root: tk.Tk,
        host: str,
        port: int,
        topics: tuple[str, ...],
        max_messages: int,
        server_url: str,
        vsoa_bind_host: str,
        vsoa_bind_port: int,
        vsoa_auto_start: bool,
        vsoa_advertised_url: str,
        bridge_config: Any,
        mqtt_username: str = "",
        mqtt_password: str = "",
        mqtt_client_id: str = "mqtt-display",
    ) -> None:
        self.root = root
        self.topics = topics
        self.max_messages = max_messages
        self.mqtt_username = mqtt_username
        self.mqtt_password = mqtt_password
        self.mqtt_client_id = mqtt_client_id
        self.vsoa_advertised_url = vsoa_advertised_url
        self.server_url = server_url
        self.rpc_server_url = f"vsoa://127.0.0.1:{bridge_config.vsoa.server.port}"
        self.max_command_timeout_ms = bridge_config.downlink.command.max_timeout_ms

        # state
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.client = None
        self.public_broker_monitor: PublicBrokerMonitor | None = None
        self.connected = False
        self.closing = False
        self.message_count = 0
        self.vsoa_count = 0
        self.payloads: dict[str, str] = {}
        self.vsoa_payloads: dict[str, str] = {}

        # tk vars
        self.host_var = tk.StringVar(value=host)
        self.port_var = tk.StringVar(value=str(port))
        self.status_var = tk.StringVar(value="未连接")
        self.count_var = tk.StringVar(value="消息 0")
        self.vsoa_count_var = tk.StringVar(value="VSOA 事件 0")
        self.bridge_status_var = tk.StringVar(value="bridge: 3001 RPC | 3000 Pub/Sub")

        # local business VSOA server (thin shell, no command handling)
        self.local_vsoa_server = LocalVsoaServer(
            server_url=server_url, bind_host=vsoa_bind_host, bind_port=vsoa_bind_port,
            auto_start=vsoa_auto_start,
            status_callback=lambda s: self.events.put(("server_status", s)),
        )

        # VSOA event listener — subscribes to bridge's uplink publications
        self.vsoa_listener = VsoaEventListener(
            server_url=self.rpc_server_url,
            event_callback=lambda url, data: self.events.put(("vsoa_event", (url, data))),
            status_callback=lambda s: self.events.put(("vsoa_status", s)),
        )

        self._configure_window()
        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(self.POLL_INTERVAL_MS, self._drain_events)
        self.local_vsoa_server.start_if_needed()
        self.connect()
        self.vsoa_listener.start()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _configure_window(self) -> None:
        self.root.title("MQTT 实时消息监视器")
        self.root.geometry("1180x700")
        self.root.minsize(820, 500)
        style = ttk.Style(self.root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Treeview", rowheight=28)
        style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 9, "bold"))

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill=tk.BOTH, expand=True)

        # --- connection bar ---
        bar = ttk.Frame(outer)
        bar.pack(fill=tk.X, pady=(0, 10))
        ttk.Label(bar, text="Host").pack(side=tk.LEFT)
        self.host_entry = ttk.Entry(bar, textvariable=self.host_var, width=20)
        self.host_entry.pack(side=tk.LEFT, padx=(6, 14))
        ttk.Label(bar, text="端口").pack(side=tk.LEFT)
        self.port_entry = ttk.Entry(bar, textvariable=self.port_var, width=7)
        self.port_entry.pack(side=tk.LEFT, padx=(6, 14))
        self.connect_button = ttk.Button(bar, text="连接", command=self.toggle_connection, width=9)
        self.connect_button.pack(side=tk.LEFT)
        ttk.Button(bar, text="清空", command=self.clear_messages, width=9).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Label(bar, textvariable=self.vsoa_count_var).pack(side=tk.RIGHT)
        ttk.Label(bar, text=" | ").pack(side=tk.RIGHT)
        ttk.Label(bar, textvariable=self.count_var).pack(side=tk.RIGHT)
        self.status_label = ttk.Label(bar, textvariable=self.status_var)
        self.status_label.pack(side=tk.RIGHT, padx=(0, 18))

        # --- actions ---
        actions = ttk.Frame(outer)
        actions.pack(fill=tk.X, pady=(0, 8))
        ttk.Button(actions, text="发布 /ctrl/cmd (Pub/Sub)",
                   command=self._open_pubsub_dialog, width=22).pack(side=tk.LEFT)
        ttk.Button(actions, text="RPC 发送",
                   command=self._open_rpc_dialog, width=12).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(actions, text="公共 Broker 监视器",
                   command=self._open_public_broker_monitor, width=18).pack(side=tk.LEFT, padx=(8, 0))

        # --- bridge status ---
        ttk.Label(outer, textvariable=self.bridge_status_var).pack(fill=tk.X, anchor=tk.W, pady=(0, 8))

        # --- notebook: MQTT messages + VSOA events ---
        self.notebook = ttk.Notebook(outer)
        self.notebook.pack(fill=tk.BOTH, expand=True)

        # -- Tab 1: MQTT messages --
        mqtt_tab = ttk.Frame(self.notebook)
        self.notebook.add(mqtt_tab, text="MQTT 消息")
        mqtt_table = ttk.Frame(mqtt_tab)
        mqtt_table.pack(fill=tk.BOTH, expand=True)
        columns = ("time", "host", "gateway", "topic", "qos", "payload")
        self.tree = ttk.Treeview(mqtt_table, columns=columns, show="headings")
        headings = {
            "time": "接收时间", "host": "Broker", "gateway": "网关",
            "topic": "Topic", "qos": "QoS", "payload": "消息数据",
        }
        widths = {"time": 170, "host": 140, "gateway": 175, "topic": 265, "qos": 48, "payload": 360}
        for col in columns:
            self.tree.heading(col, text=headings[col])
            self.tree.column(col, width=widths[col], minwidth=45,
                             anchor=tk.CENTER if col == "qos" else tk.W,
                             stretch=col in ("topic", "payload"))
        scrollbar1 = ttk.Scrollbar(mqtt_table, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar1.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar1.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.bind("<<TreeviewSelect>>", self._show_mqtt_detail)
        # MQTT detail
        ttk.Label(mqtt_tab, text="MQTT 消息详情").pack(fill=tk.X, pady=(4, 2))
        self.mqtt_detail = tk.Text(mqtt_tab, height=8, wrap=tk.NONE, font=("Consolas", 10), state=tk.DISABLED)
        mqtt_tab.after(0, lambda: mqtt_tab.rowconfigure(0, weight=1))

        # -- Tab 2: VSOA bridge events --
        vsoa_tab = ttk.Frame(self.notebook)
        self.notebook.add(vsoa_tab, text="VSOA 桥接事件")
        vsoa_table = ttk.Frame(vsoa_tab)
        vsoa_table.pack(fill=tk.BOTH, expand=True)
        vsoa_columns = ("time", "url", "summary")
        self.vsoa_tree = ttk.Treeview(vsoa_table, columns=vsoa_columns, show="headings")
        self.vsoa_tree.heading("time", text="时间")
        self.vsoa_tree.heading("url", text="VSOA URL")
        self.vsoa_tree.heading("summary", text="摘要")
        self.vsoa_tree.column("time", width=170, minwidth=100, stretch=False)
        self.vsoa_tree.column("url", width=170, minwidth=100, stretch=False)
        self.vsoa_tree.column("summary", width=600, minwidth=200, stretch=True)
        scrollbar2 = ttk.Scrollbar(vsoa_table, orient=tk.VERTICAL, command=self.vsoa_tree.yview)
        self.vsoa_tree.configure(yscrollcommand=scrollbar2.set)
        self.vsoa_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar2.pack(side=tk.RIGHT, fill=tk.Y)
        self.vsoa_tree.bind("<<TreeviewSelect>>", self._show_vsoa_detail)
        # VSOA detail
        ttk.Label(vsoa_tab, text="VSOA 事件详情").pack(fill=tk.X, pady=(4, 2))
        self.vsoa_detail = tk.Text(vsoa_tab, height=8, wrap=tk.NONE, font=("Consolas", 10), state=tk.DISABLED)
        vsoa_tab.after(0, lambda: vsoa_tab.rowconfigure(0, weight=1))

    # ------------------------------------------------------------------
    # MQTT
    # ------------------------------------------------------------------

    def toggle_connection(self) -> None:
        if self.client is None:
            self.connect()
        else:
            self.disconnect()

    def connect(self) -> None:
        host = self.host_var.get().strip()
        try:
            port = int(self.port_var.get())
            if not host or not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            messagebox.showerror("参数错误", "请输入有效的 Host 和端口（1-65535）。")
            return
        try:
            client = create_mqtt_client(f"{self.mqtt_client_id}-{id(self):x}")
            if self.mqtt_username:
                client.username_pw_set(self.mqtt_username, self.mqtt_password)
            client.user_data_set({"host": host})
            client.on_connect = self._on_connect
            client.on_disconnect = self._on_disconnect
            client.on_message = self._on_message
            client.reconnect_delay_set(min_delay=1, max_delay=30)
            client.connect_async(host, port, keepalive=60)
            client.loop_start()
        except Exception as exc:
            messagebox.showerror("MQTT 连接失败", str(exc))
            return
        self.client = client
        self.status_var.set(f"正在连接 {host}:{port} ...")
        self.connect_button.configure(text="断开")
        self.host_entry.configure(state=tk.DISABLED)
        self.port_entry.configure(state=tk.DISABLED)

    def disconnect(self) -> None:
        client, self.client = self.client, None
        self.connected = False
        if client is not None:
            try:
                client.disconnect()
            finally:
                client.loop_stop()
        self.status_var.set("已断开")
        self.connect_button.configure(text="连接")
        self.host_entry.configure(state=tk.NORMAL)
        self.port_entry.configure(state=tk.NORMAL)

    def _on_connect(self, client, userdata, flags, rc, properties=None) -> None:
        if int(rc) == 0:
            for topic in self.topics:
                client.subscribe(topic, qos=1)
            self.events.put(("status", f"已连接 {userdata['host']}"))
        else:
            self.events.put(("status", f"连接失败，返回码 {rc}"))

    def _on_disconnect(self, client, userdata, rc, properties=None) -> None:
        if not self.closing and self.client is client:
            self.events.put(("status", f"连接已断开（{rc}），正在重连..."))

    def _on_message(self, client, userdata, msg) -> None:
        data, payload = decode_message_payload(msg.payload)
        received = ReceivedMessage(
            received_at=datetime.now().astimezone().isoformat(sep=" ", timespec="milliseconds"),
            host=userdata["host"], gateway=extract_gateway(data),
            topic=msg.topic, qos=msg.qos, payload=payload,
        )
        self.events.put(("message", received))

    # ------------------------------------------------------------------
    # event loop
    # ------------------------------------------------------------------

    def _drain_events(self) -> None:
        try:
            while True:
                event, value = self.events.get_nowait()
                if event == "message":
                    self._add_message(value)
                elif event == "status":
                    self.status_var.set(value)
                elif event == "server_status":
                    self.bridge_status_var.set(f"VSOA Server: {value}")
                elif event == "vsoa_event":
                    self._add_vsoa_event(*value)
                elif event == "vsoa_status":
                    self.bridge_status_var.set(f"VSOA: {value}")
                elif event == "pubsub_result":
                    success, cmd_id = value
                    if success:
                        self.bridge_status_var.set(f"/ctrl/cmd 已发布 → bridge 处理中")
                        messagebox.showinfo("已发布",
                            f"/ctrl/cmd 已发送到 {self.vsoa_advertised_url}\nbridge 将处理并发布到 MQTT。")
                    else:
                        self.bridge_status_var.set("/ctrl/cmd 发布失败")
                        messagebox.showerror("发布失败", f"无法连接到 {self.vsoa_advertised_url}")
        except queue.Empty:
            pass
        if not self.closing:
            self.root.after(self.POLL_INTERVAL_MS, self._drain_events)

    def _add_message(self, message: ReceivedMessage) -> None:
        preview = " ".join(message.payload.split())
        if len(preview) > 180:
            preview = preview[:177] + "..."
        item = self.tree.insert("", 0, values=(
            message.received_at, message.host, message.gateway,
            message.topic, message.qos, preview,
        ))
        self.payloads[item] = message.payload
        self.message_count += 1
        self.count_var.set(f"消息 {self.message_count}")
        children = self.tree.get_children()
        if len(children) > self.max_messages:
            expired = children[self.max_messages:]
            self.tree.delete(*expired)
            for child in expired:
                self.payloads.pop(child, None)

    def _show_mqtt_detail(self, event=None) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        self._set_detail_text(self.mqtt_detail, self.payloads.get(selection[0], ""))

    def _show_vsoa_detail(self, event=None) -> None:
        selection = self.vsoa_tree.selection()
        if not selection:
            return
        self._set_detail_text(self.vsoa_detail, self.vsoa_payloads.get(selection[0], ""))

    def _add_vsoa_event(self, url: str, data: dict[str, Any]) -> None:
        received_at = datetime.now().astimezone().isoformat(sep=" ", timespec="milliseconds")
        summary = json.dumps(data, ensure_ascii=False)
        if len(summary) > 200:
            summary = summary[:197] + "..."
        item = self.vsoa_tree.insert("", 0, values=(received_at, url, summary))
        self.vsoa_payloads[item] = json.dumps(data, ensure_ascii=False, indent=2)
        self.vsoa_count += 1
        self.vsoa_count_var.set(f"VSOA 事件 {self.vsoa_count}")
        # keep last 500
        children = self.vsoa_tree.get_children()
        if len(children) > 500:
            expired = children[500:]
            self.vsoa_tree.delete(*expired)
            for child in expired:
                self.vsoa_payloads.pop(child, None)

    @staticmethod
    def _set_detail_text(widget: tk.Text, value: str) -> None:
        widget.configure(state=tk.NORMAL)
        widget.delete("1.0", tk.END)
        widget.insert("1.0", value)
        widget.configure(state=tk.DISABLED)

    def clear_messages(self) -> None:
        children = self.tree.get_children()
        if children:
            self.tree.delete(*children)
        self.payloads.clear()
        self.message_count = 0
        self.count_var.set("消息 0")
        self._set_detail_text(self.mqtt_detail, "")
        # also clear VSOA events
        vsoa_children = self.vsoa_tree.get_children()
        if vsoa_children:
            self.vsoa_tree.delete(*vsoa_children)
        self.vsoa_payloads.clear()
        self.vsoa_count = 0
        self.vsoa_count_var.set("VSOA 事件 0")
        self._set_detail_text(self.vsoa_detail, "")

    # ------------------------------------------------------------------
    # Pub/Sub downlink — send datagram to business VSOA server
    # ------------------------------------------------------------------

    def _open_pubsub_dialog(self) -> None:
        """Send /ctrl/cmd via VSOA Pub/Sub.  Bridge subscribes & handles MQTT."""
        command = {
            "command_id": f"gui-{int(time.time() * 1000)}",
            "device_type": "lora",
            "device_id": "",
            "action": "set",
            "params": {},
        }

        dialog = tk.Toplevel(self.root)
        dialog.title("Pub/Sub 下行 → /ctrl/cmd → bridge → MQTT")
        dialog.geometry("620x620")
        dialog.minsize(480, 340)
        dialog.transient(self.root)
        dialog.grab_set()

        body = ttk.Frame(dialog, padding=12)
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(body, text=f"目标: {self.server_url}  →  bridge 订阅 /ctrl/cmd  →  MQTT").pack(
            fill=tk.X, pady=(0, 6))
        ttk.Label(body, text="命令 JSON").pack(fill=tk.X, pady=(0, 6))
        editor = tk.Text(body, wrap=tk.NONE, font=("Consolas", 10))
        editor.pack(fill=tk.BOTH, expand=True)
        editor.insert("1.0", json.dumps(command, ensure_ascii=False, indent=2))

        buttons = ttk.Frame(body)
        buttons.pack(fill=tk.X, pady=(10, 0))
        ttk.Button(buttons, text="取消", command=dialog.destroy).pack(side=tk.RIGHT)

        def publish() -> None:
            try:
                data = json.loads(editor.get("1.0", tk.END))
            except json.JSONDecodeError as exc:
                messagebox.showerror("JSON 格式错误", str(exc), parent=dialog)
                return
            if not isinstance(data, dict):
                messagebox.showerror("消息格式错误", "必须为 JSON 对象。", parent=dialog)
                return

            from src.downlink.command import validate
            valid, code = validate(data, self.max_command_timeout_ms, check_timeout=False)
            if not valid:
                messagebox.showerror("校验失败", f"错误码：{code}", parent=dialog)
                return

            dialog.destroy()

            def send() -> None:
                ok = self.local_vsoa_server.publish_control_command(data, target_url=self.server_url)
                self.events.put(("pubsub_result", (ok, str(data.get("command_id", "")))))

            threading.Thread(target=send, daemon=True).start()

        ttk.Button(buttons, text="发布", command=publish).pack(side=tk.RIGHT, padx=(0, 8))

    # ------------------------------------------------------------------
    # RPC downlink — client.fetch() to bridge:3001
    # ------------------------------------------------------------------

    def _open_rpc_dialog(self) -> None:
        """Call bridge RPC /bridge/send_command — synchronous ACK."""
        command = {
            "command_id": f"rpc-gui-{int(time.time() * 1000)}",
            "device_type": "lora",
            "device_id": "",
            "action": "set",
            "params": {},
        }

        dialog = tk.Toplevel(self.root)
        dialog.title("RPC /bridge/send_command（同步回执）")
        dialog.geometry("620x700")
        dialog.minsize(480, 380)
        dialog.transient(self.root)
        dialog.grab_set()

        body = ttk.Frame(dialog, padding=12)
        body.pack(fill=tk.BOTH, expand=True)

        info = ttk.Frame(body)
        info.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(info, text=f"RPC 目标: {self.rpc_server_url}  ", font=("Consolas", 9)).pack(side=tk.LEFT)
        ttk.Label(info, text="超时: 5s  阻塞等待 bridge 回执", font=("Consolas", 9)).pack(side=tk.LEFT, padx=(12, 0))

        ttk.Label(body, text="命令 JSON").pack(fill=tk.X, pady=(0, 6))
        editor = tk.Text(body, wrap=tk.NONE, font=("Consolas", 10))
        editor.pack(fill=tk.BOTH, expand=True)
        editor.insert("1.0", json.dumps(command, ensure_ascii=False, indent=2))

        result_frame = ttk.Frame(body)
        result_var = tk.StringVar(value="")

        def call_rpc() -> None:
            try:
                data = json.loads(editor.get("1.0", tk.END))
            except json.JSONDecodeError as exc:
                messagebox.showerror("JSON 格式错误", str(exc), parent=dialog)
                return
            if not isinstance(data, dict):
                messagebox.showerror("消息格式错误", "必须为 JSON 对象。", parent=dialog)
                return

            from src.downlink.command import validate
            valid, code = validate(data, self.max_command_timeout_ms, check_timeout=False)
            if not valid:
                messagebox.showerror("校验失败", f"错误码：{code}", parent=dialog)
                return

            editor.configure(state=tk.DISABLED)
            for child in buttons.winfo_children():
                child.configure(state=tk.DISABLED)
            result_var.set("正在调用 RPC...")

            def do_rpc() -> None:
                try:
                    rpc_client = vsoa.Client()
                    try:
                        st = rpc_client.connect(self.rpc_server_url, timeout=3.0)
                        if st != vsoa.Client.CONNECT_OK:
                            self.events.put(("rpc_result", (False, f"连接失败: {st}", data.get("command_id", ""))))
                            return
                        h, p, s = rpc_client.fetch(
                            "/bridge/send_command", payload=vsoa.Payload(param=data), timeout=5.0,
                        )
                        if s == vsoa.Client.CONNECT_OK:
                            result = dict(p.param) if p and p.param else {}
                            msg = json.dumps(result, ensure_ascii=False, indent=2)
                            self.events.put(("rpc_result", (True, msg, data.get("command_id", ""))))
                        else:
                            self.events.put(("rpc_result", (False, f"RPC 状态: {s}", data.get("command_id", ""))))
                    finally:
                        rpc_client.close()
                except Exception as exc:
                    self.events.put(("rpc_result", (False, str(exc), data.get("command_id", ""))))

            threading.Thread(target=do_rpc, daemon=True).start()

        buttons = ttk.Frame(body)
        buttons.pack(fill=tk.X, pady=(10, 0))
        ttk.Button(buttons, text="取消", command=dialog.destroy).pack(side=tk.RIGHT)
        ttk.Button(buttons, text="发送 (fetch)", command=call_rpc).pack(side=tk.RIGHT, padx=(0, 8))

        result_frame.pack(fill=tk.X, pady=(10, 0))
        ttk.Label(result_frame, text="回执:").pack(fill=tk.X, anchor=tk.W)
        result_text = tk.Text(result_frame, height=8, wrap=tk.NONE, font=("Consolas", 10), state=tk.DISABLED)
        scroll_y = ttk.Scrollbar(result_frame, orient=tk.VERTICAL, command=result_text.yview)
        result_text.configure(yscrollcommand=scroll_y.set)
        result_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll_y.pack(side=tk.RIGHT, fill=tk.Y)

        def drain_rpc() -> None:
            try:
                while True:
                    event, value = self.events.get_nowait()
                    if event == "rpc_result":
                        success, msg, cmd_id = value
                        result_text.configure(state=tk.NORMAL)
                        result_text.delete("1.0", tk.END)
                        result_text.insert("1.0", msg)
                        result_text.configure(state=tk.DISABLED)
                        editor.configure(state=tk.NORMAL)
                        for child in buttons.winfo_children():
                            child.configure(state=tk.NORMAL)
                        result_var.set("RPC 完成" if success else "RPC 失败")
                        self.bridge_status_var.set(
                            f"RPC 成功: {cmd_id}" if success else f"RPC 失败: {msg[:80]}"
                        )
                    else:
                        self.events.put((event, value))
            except queue.Empty:
                pass
            if dialog.winfo_exists():
                dialog.after(100, drain_rpc)

        result_var.set("等待发送...")
        dialog.after(100, drain_rpc)

    # ------------------------------------------------------------------
    # public broker
    # ------------------------------------------------------------------

    def _open_public_broker_monitor(self) -> None:
        if self.public_broker_monitor is not None:
            self.public_broker_monitor.focus()
            return
        self.public_broker_monitor = PublicBrokerMonitor(self.root, on_close=self._on_public_broker_closed)

    def _on_public_broker_closed(self) -> None:
        self.public_broker_monitor = None

    # ------------------------------------------------------------------
    # shutdown
    # ------------------------------------------------------------------

    def close(self) -> None:
        self.closing = True
        if self.public_broker_monitor is not None:
            self.public_broker_monitor.close()
        self.vsoa_listener.stop()
        self.disconnect()
        self.root.destroy()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MQTT 实时消息监视器（纯演示客户端）")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="桥接配置文件")
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"MQTT Broker Host（默认 {DEFAULT_HOST}）")
    parser.add_argument("--port", type=int, help="覆盖 MQTT Broker 端口")
    parser.add_argument("--server-url", help="覆盖 VSOA server_url")
    parser.add_argument("--vsoa-bind-host", help="覆盖自动 VSOA Server 监听地址")
    parser.add_argument("--vsoa-bind-port", type=int, help="覆盖自动 VSOA Server 监听端口")
    parser.add_argument("--vsoa-advertised-url", help="覆盖 VSOA 对外地址")
    parser.add_argument("--topic", action="append", dest="topics", help="额外订阅 Topic")
    parser.add_argument("--max-messages", type=int, default=500, help="表格最大消息数")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.max_messages < 1:
        print("--max-messages 必须大于 0", file=sys.stderr)
        return 2
    if mqtt is None:
        print("缺少 paho-mqtt，请先执行: pip install paho-mqtt", file=sys.stderr)
        return 1
    if vsoa is None:
        print("缺少 vsoa", file=sys.stderr)
        return 1

    try:
        from src.config import load_config
        config = load_config(args.config)
    except Exception as exc:
        print(f"读取配置文件失败: {exc}", file=sys.stderr)
        return 1

    host = args.host
    port = args.port or config.mqtt.port or DEFAULT_PORT
    server_url = args.server_url or config.vsoa.pubsub_client.server_url
    if not server_url.startswith("vsoa://"):
        print("VSOA server_url 必须以 vsoa:// 开头", file=sys.stderr)
        return 2
    vsoa_bind_host = args.vsoa_bind_host or config.vsoa.business_server.bind_host
    vsoa_bind_port = args.vsoa_bind_port or config.vsoa.business_server.port
    vsoa_advertised_url = args.vsoa_advertised_url or config.vsoa.business_server.advertised_url
    if not vsoa_bind_host or not 1 <= vsoa_bind_port <= 65535:
        print("VSOA Server 监听地址或端口无效", file=sys.stderr)
        return 2
    if not vsoa_advertised_url.startswith("vsoa://"):
        print("VSOA 对外地址必须以 vsoa:// 开头", file=sys.stderr)
        return 2

    configured_topics = tuple(config.mqtt.uplink_topics)
    topics = tuple(args.topics or dict.fromkeys((*configured_topics, *DEFAULT_TOPICS)))

    root = tk.Tk()
    MqttMonitorApp(
        root, host=host, port=port, topics=topics, max_messages=args.max_messages,
        server_url=server_url,
        vsoa_bind_host=vsoa_bind_host, vsoa_bind_port=vsoa_bind_port,
        vsoa_auto_start=config.vsoa.business_server.auto_start,
        vsoa_advertised_url=vsoa_advertised_url,
        bridge_config=config,
        mqtt_username=config.mqtt.username, mqtt_password=config.mqtt.password,
        mqtt_client_id=f"{config.mqtt.client_id}-receiver",
    )
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
