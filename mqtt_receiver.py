"""MQTT real-time message monitor.

Run:
    python "mqtt_receiver .py"

The window displays the broker host, local receive time, gateway ID, topic,
QoS and payload for every received MQTT message.
"""

from __future__ import annotations

import argparse
import base64
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
    "s3/eora-s3-400tb-001/data",
)
PUBLIC_BROKER_HOST = "broker.emqx.io"
PUBLIC_BROKER_PORT = 1883
VSOA_DEVICE_ID = "XAX523"
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
DEFAULT_CONFIG = Path(__file__).resolve().parent / "bridge_vsoa_mqtt" / "config.yaml"
BRIDGE_ROOT = DEFAULT_CONFIG.parent

if str(BRIDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(BRIDGE_ROOT))


@dataclass(frozen=True)
class ReceivedMessage:
    token: int
    received_at: str
    host: str
    gateway: str
    topic: str
    qos: int
    payload: str
    vsoa_payload: str
    vsoa_messages: tuple[tuple[str, dict[str, Any]], ...]
    vsoa_status: str


@dataclass(frozen=True)
class VsoaTask:
    token: int
    messages: tuple[tuple[str, dict[str, Any]], ...]
    target_url: str | None = None


@dataclass(frozen=True)
class PublicBrokerMessage:
    received_at: str
    topic: str
    qos: int
    retained: bool
    payload: str


class LocalVsoaServer:
    """Start a local business VSOA server when the configured one is absent."""

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
            raise RuntimeError("缺少 vsoa，请使用 bridge_vsoa_mqtt 的虚拟环境运行")
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

        server = vsoa.Server({"name": "MQTT Receiver Business Server"})
        self.server = server

        # Client datagrams are republished so normal VSOA subscribers can see them.
        def on_data(cli, url, payload, quick) -> None:
            try:
                server.publish(url, payload)
            except Exception as exc:
                self.status_callback(f"VSOA 数据转发失败：{exc}")

        server.ondata = on_data

        @server.command("/ctrl/cmd")
        def receive_command(cli, req, payload) -> None:
            """Allow an external Client.call() to publish a control command."""
            try:
                param = dict(payload.param) if isinstance(payload.param, dict) else {}
                param["device_id"] = VSOA_DEVICE_ID
                cli.reply(req.seqno, vsoa.Payload(param={"ok": True}))
                server.publish("/ctrl/cmd", vsoa.Payload(param=param))
            except Exception as exc:
                self.status_callback(f"VSOA 控制命令发布失败：{exc}")

        def run_server() -> None:
            try:
                server.run(self.bind_host, self.bind_port)
            except Exception as exc:
                self.status_callback(f"本地 VSOA Server 启动失败：{exc}")

        self.thread = threading.Thread(
            target=run_server,
            name="local-vsoa-business-server",
            daemon=True,
        )
        self.thread.start()

        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if self._can_connect(timeout=0.2):
                self.status_callback(
                    f"本地 VSOA Server 已监听 {self.bind_host}:{self.bind_port}"
                )
                return True
            time.sleep(0.1)

        self.status_callback("本地 VSOA Server 启动超时")
        return False

    def publish_control_command(
        self,
        data: dict[str, Any],
        target_url: str | None = None,
    ) -> bool:
        """Publish one valid control command for /ctrl/cmd subscribers."""
        command = dict(data)
        command["device_id"] = VSOA_DEVICE_ID
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


def parse_lora_payload(payload: bytes) -> dict[str, Any] | None:
    """Decode the 16-byte device payload used by the original receiver."""
    if len(payload) != 16:
        return None

    flags = payload[15]
    return {
        "seq": int.from_bytes(payload[0:2], byteorder="big"),
        "boot_id": hex(int.from_bytes(payload[2:6], byteorder="big")),
        "send_time_ms": int.from_bytes(payload[6:10], byteorder="big"),
        "lorawan_retry_count": payload[10],
        "temperature": int.from_bytes(
            payload[11:13], byteorder="big", signed=True
        ) / 10.0,
        "humidity": int.from_bytes(payload[13:15], byteorder="big") / 10.0,
        "joined": bool(flags & 0x01),
        "application_retry": bool(flags & 0x08),
        "flags": hex(flags),
    }


def decode_message_payload(payload: bytes) -> tuple[dict[str, Any] | None, str]:
    """Return parsed JSON and a readable payload representation."""
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
            decoded = base64.b64decode(encoded, validate=True)
            parsed = parse_lora_payload(decoded)
            if parsed is not None:
                display_data["parsed_payload"] = parsed
        except (ValueError, base64.binascii.Error):
            pass

    return data, json.dumps(display_data, ensure_ascii=False, indent=2)


def extract_gateway(data: dict[str, Any] | None) -> str:
    """Find a gateway identifier in common LoRaWAN/WiFi message shapes."""
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


def enrich_payload_for_adapter(data: dict[str, Any]) -> dict[str, Any]:
    """Expose nested/proprietary LoRa fields to the existing project adapter."""
    enriched = dict(data)

    encoded = data.get("data")
    if isinstance(encoded, str):
        try:
            decoded = base64.b64decode(encoded, validate=True)
            parsed = parse_lora_payload(decoded)
            if parsed:
                for key, value in parsed.items():
                    enriched.setdefault(key, value)
        except (ValueError, base64.binascii.Error):
            pass

    device_info = data.get("deviceInfo")
    if isinstance(device_info, dict):
        if device_info.get("deviceName"):
            enriched.setdefault("deviceName", device_info["deviceName"])
        for key in ("devEui", "devEUI", "dev_eui"):
            if device_info.get(key):
                enriched.setdefault("devEUI", device_info[key])
                break

    return enriched


def convert_to_vsoa_messages(
    topic: str,
    data: dict[str, Any],
    registry: Any,
) -> tuple[tuple[str, dict[str, Any]], ...]:
    """Run the project's adapter pipeline and build its two uplink messages."""
    from src.uplink.adapters import select_adapter

    payload = enrich_payload_for_adapter(data)
    adapter = select_adapter(topic, payload)
    report = adapter.parse(topic, payload)
    device, created = registry.upsert(report)
    if device is None:
        raise RuntimeError("设备注册表已满，无法转换新设备")

    event = {
        "event": "data_received",
        "device_id": report.device_id,
        "source": report.source,
        "adapter": report.adapter,
        "gateway_id": extract_gateway(data),
        "timestamp": int(time.time() * 1000),
        "registry_action": "registered" if created else "updated",
    }
    device_update = device.to_json()
    device_update["device_id"] = VSOA_DEVICE_ID
    event["device_id"] = VSOA_DEVICE_ID
    return (
        ("/device/update", device_update),
        ("/bridge/event", event),
    )


def format_vsoa_messages(
    messages: tuple[tuple[str, dict[str, Any]], ...],
) -> str:
    """Format converted VSOA URLs and payloads for the monitor."""
    return json.dumps(
        [{"url": url, "payload": payload} for url, payload in messages],
        ensure_ascii=False,
        indent=2,
    )


class VsoaForwarder:
    """Background, reconnecting VSOA client for MQTT uplink delivery."""

    def __init__(
        self,
        server_url: str,
        reconnect_interval_ms: int,
        result_callback: Callable[[int, bool, str], None],
    ) -> None:
        if vsoa is None:
            raise RuntimeError("缺少 vsoa，请使用 bridge_vsoa_mqtt 的虚拟环境运行")
        self.server_url = server_url
        self.reconnect_interval = max(reconnect_interval_ms, 100) / 1000.0
        self.result_callback = result_callback
        self.tasks: queue.Queue[VsoaTask] = queue.Queue(maxsize=1000)
        self.stop_event = threading.Event()
        self.client = None
        self.connected_url = ""
        self.thread = threading.Thread(
            target=self._run,
            name="mqtt-to-vsoa-forwarder",
            daemon=True,
        )
        self.thread.start()

    def enqueue(self, task: VsoaTask) -> bool:
        try:
            self.tasks.put_nowait(task)
            return True
        except queue.Full:
            return False

    def _connect(self, server_url: str) -> bool:
        self._close_client()
        client = vsoa.Client()
        try:
            result = client.connect(server_url, timeout=2.0)
        except Exception:
            client.close()
            return False
        if result != vsoa.Client.CONNECT_OK:
            client.close()
            return False
        self.client = client
        self.connected_url = server_url
        return True

    def _send(self, task: VsoaTask) -> bool:
        if self.client is None or not self.client.connected:
            return False
        try:
            for url, param in task.messages:
                payload = vsoa.Payload(param=param)
                if not self.client.datagram(url, payload=payload, quick=False):
                    return False
            return True
        except Exception:
            return False

    def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                task = self.tasks.get(timeout=0.2)
            except queue.Empty:
                continue

            retry_reported = False
            target_url = task.target_url or self.server_url
            while not self.stop_event.is_set():
                if (
                    self.client is None
                    or not self.client.connected
                    or self.connected_url != target_url
                ):
                    if not self._connect(target_url):
                        if not retry_reported:
                            self.result_callback(task.token, False, "等待 VSOA 重连")
                            retry_reported = True
                        self.stop_event.wait(self.reconnect_interval)
                        continue
                if self._send(task):
                    self.result_callback(task.token, True, "Server 发布成功")
                    break
                self._close_client()
                if not retry_reported:
                    self.result_callback(task.token, False, "发送失败，正在重试")
                    retry_reported = True

            self.tasks.task_done()
        self._close_client()

    def _close_client(self) -> None:
        client, self.client = self.client, None
        self.connected_url = ""
        if client is not None:
            try:
                client.close()
            except Exception:
                pass

    def close(self) -> None:
        self.stop_event.set()
        self._close_client()
        self.thread.join(timeout=3.0)


class VsoaCommandClientRunner:
    """Start the project's VSOA PubSub client without blocking the GUI."""

    def __init__(
        self,
        server_url: str,
        subscribe_urls: list[str],
        max_timeout_ms: int,
        mqtt_topic_prefix: str,
        mqtt_topic_prefixes: dict[str, str],
        mqtt_publisher: Callable[[str, str], bool],
        ack_publish_url: str,
        ack_publisher: Callable[[str, dict[str, Any]], None],
        registry: Any,
        dedup: Any,
        reconnect_interval_ms: int,
        reconnect_max_retries: int,
        reconnect_backoff_multiplier: float,
        status_callback: Callable[[str], None],
    ) -> None:
        self.server_url = server_url
        self.subscribe_urls = subscribe_urls
        self.max_timeout_ms = max_timeout_ms
        self.mqtt_topic_prefix = mqtt_topic_prefix
        self.mqtt_topic_prefixes = mqtt_topic_prefixes
        self.mqtt_publisher = mqtt_publisher
        self.ack_publish_url = ack_publish_url
        self.ack_publisher = ack_publisher
        self.registry = registry
        self.dedup = dedup
        self.reconnect_interval_ms = max(reconnect_interval_ms, 100)
        self.reconnect_max_retries = max(reconnect_max_retries, 1)
        self.reconnect_backoff_multiplier = reconnect_backoff_multiplier
        self.status_callback = status_callback
        self.stop_event = threading.Event()
        self.handler = None
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        if self.thread is not None and self.thread.is_alive():
            return
        self.thread = threading.Thread(
            target=self._run,
            name="vsoa-command-client",
            daemon=True,
        )
        self.thread.start()

    def _run(self) -> None:
        from src.downlink.pubsub_handler import PubSubHandler

        while not self.stop_event.is_set():
            self.status_callback(f"正在连接 {self.server_url}")
            handler = PubSubHandler(
                server_url=self.server_url,
                subscribe_urls=self.subscribe_urls,
                max_timeout_ms=self.max_timeout_ms,
                mqtt_topic_prefix=self.mqtt_topic_prefix,
                mqtt_topic_prefixes=self.mqtt_topic_prefixes,
                mqtt_publisher=self.mqtt_publisher,
                ack_publish_url=self.ack_publish_url,
                ack_publisher=self.ack_publisher,
                registry=self.registry,
                dedup=self.dedup,
                reconnect_interval_ms=self.reconnect_interval_ms,
                reconnect_max_retries=self.reconnect_max_retries,
                reconnect_backoff_multiplier=self.reconnect_backoff_multiplier,
            )
            self.handler = handler
            if handler.connect():
                subscriptions = ", ".join(self.subscribe_urls)
                self.status_callback(f"已连接，订阅 {subscriptions}")
                handler.run_forever()
            if self.stop_event.is_set():
                break
            self.status_callback("连接失败，等待重试")
            self.stop_event.wait(self.reconnect_interval_ms / 1000.0)

        self.status_callback("已停止")

    def close(self) -> None:
        self.stop_event.set()
        handler, self.handler = self.handler, None
        if handler is not None:
            handler.stop()
        if self.thread is not None:
            self.thread.join(timeout=3.0)


def create_mqtt_client(client_id: str):
    """Create a client compatible with paho-mqtt 1.x and 2.x."""
    if mqtt is None:
        raise RuntimeError("缺少 paho-mqtt，请先执行: pip install paho-mqtt")

    if hasattr(mqtt, "CallbackAPIVersion"):
        return mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION1,
            client_id=client_id,
        )
    return mqtt.Client(client_id=client_id)


class PublicBrokerMonitor:
    """Independent window for the default public MQTT broker."""

    POLL_INTERVAL_MS = 100

    def __init__(
        self,
        parent: tk.Tk,
        on_close: Callable[[], None],
    ) -> None:
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
        self.connect_button = ttk.Button(
            connection,
            text="连接",
            command=self.toggle_connection,
            width=9,
        )
        self.connect_button.pack(side=tk.LEFT)
        ttk.Button(connection, text="清空", command=self.clear, width=9).pack(
            side=tk.LEFT, padx=(8, 0)
        )
        ttk.Label(connection, textvariable=self.count_var).pack(side=tk.RIGHT)

        ttk.Label(outer, textvariable=self.status_var).pack(
            fill=tk.X, anchor=tk.W, pady=(0, 4)
        )
        ttk.Label(
            outer,
            text=f"订阅 {len(PUBLIC_BROKER_TOPICS)} 个统一上行 Topic",
        ).pack(fill=tk.X, anchor=tk.W, pady=(0, 8))

        table_frame = ttk.Frame(outer)
        table_frame.pack(fill=tk.BOTH, expand=True)
        columns = ("time", "topic", "qos", "retained", "payload")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings")
        headings = {
            "time": "接收时间",
            "topic": "Topic",
            "qos": "QoS",
            "retained": "Retain",
            "payload": "消息数据",
        }
        widths = {
            "time": 190,
            "topic": 300,
            "qos": 55,
            "retained": 65,
            "payload": 360,
        }
        for column in columns:
            self.tree.heading(column, text=headings[column])
            self.tree.column(
                column,
                width=widths[column],
                minwidth=50,
                anchor=tk.CENTER if column in ("qos", "retained") else tk.W,
                stretch=column in ("topic", "payload"),
            )
        scrollbar = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.bind("<<TreeviewSelect>>", self._show_selected)

        ttk.Label(outer, text="完整消息").pack(fill=tk.X, pady=(10, 4))
        detail_frame = ttk.Frame(outer)
        detail_frame.pack(fill=tk.BOTH)
        self.detail = tk.Text(
            detail_frame,
            height=10,
            wrap=tk.NONE,
            font=("Consolas", 10),
            state=tk.DISABLED,
        )
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
            messagebox.showerror(
                "参数错误",
                "请输入有效的 Broker 和端口（1-65535）。",
                parent=self.window,
            )
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
            client.subscribe([(topic, 1) for topic in PUBLIC_BROKER_TOPICS])
            self.events.put(
                ("status", f"已连接 tcp://{userdata['host']}:{userdata['port']}")
            )
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
        self.events.put(
            (
                "message",
                PublicBrokerMessage(
                    received_at=datetime.now().astimezone().isoformat(
                        sep=" ", timespec="milliseconds"
                    ),
                    topic=msg.topic,
                    qos=msg.qos,
                    retained=bool(msg.retain),
                    payload=payload,
                ),
            )
        )

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
        item = self.tree.insert(
            "",
            0,
            values=(
                message.received_at,
                message.topic,
                message.qos,
                "是" if message.retained else "否",
                preview,
            ),
        )
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
            MqttMonitorApp._set_detail_text(
                self.detail,
                self.payloads.get(selection[0], ""),
            )

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


class MqttMonitorApp:
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
        reconnect_interval_ms: int,
        registry_max_devices: int,
        bridge_config: Any,
        mqtt_username: str = "",
        mqtt_password: str = "",
        mqtt_client_id: str = "mqtt-display",
    ) -> None:
        from src.device_registry import DeviceRegistry

        self.root = root
        self.topics = topics
        self.max_messages = max_messages
        self.mqtt_username = mqtt_username
        self.mqtt_password = mqtt_password
        self.mqtt_client_id = mqtt_client_id
        self.mqtt_qos = bridge_config.mqtt.qos
        self.vsoa_advertised_url = vsoa_advertised_url
        self.max_command_timeout_ms = bridge_config.downlink.command.max_timeout_ms
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.client = None
        self.public_broker_monitor: PublicBrokerMonitor | None = None
        self.connected = False
        self.closing = False
        self.message_count = 0
        self.next_token = 1
        self.vsoa_sent_count = 0
        self.payloads: dict[str, str] = {}
        self.vsoa_payloads: dict[str, str] = {}
        self.vsoa_messages_by_item: dict[
            str, tuple[tuple[str, dict[str, Any]], ...]
        ] = {}
        self.items_by_token: dict[int, str] = {}
        self.resend_tokens: set[int] = set()
        self.registry = DeviceRegistry(max_devices=registry_max_devices)

        dedup = None
        if bridge_config.downlink.command.dedup.enabled:
            from src.downlink.dedup import DedupCache

            dedup = DedupCache(
                ttl_seconds=bridge_config.downlink.command.dedup.ttl_seconds,
                max_size=bridge_config.downlink.command.dedup.max_size,
            )

        self.vsoa_forwarder = VsoaForwarder(
            server_url=vsoa_advertised_url,
            reconnect_interval_ms=reconnect_interval_ms,
            result_callback=self._handle_vsoa_forward_result,
        )

        self.host_var = tk.StringVar(value=host)
        self.port_var = tk.StringVar(value=str(port))
        self.status_var = tk.StringVar(value="未连接")
        self.count_var = tk.StringVar(value="消息 0")
        self.vsoa_target_var = tk.StringVar(
            value=(
                f"VSOA 本机连接：{server_url}    "
                f"对外地址：{vsoa_advertised_url}"
            )
        )
        self.vsoa_status_var = tk.StringVar(value="等待 MQTT 消息")
        self.vsoa_server_status_var = tk.StringVar(value="VSOA Server：检测中")
        self.command_client_status_var = tk.StringVar(value="VSOA 控制客户端：未启动")

        self.local_vsoa_server = LocalVsoaServer(
            server_url=server_url,
            bind_host=vsoa_bind_host,
            bind_port=vsoa_bind_port,
            auto_start=vsoa_auto_start,
            status_callback=lambda status: self.events.put(("server_status", status)),
        )

        self.vsoa_command_client = VsoaCommandClientRunner(
            server_url=server_url,
            subscribe_urls=list(bridge_config.vsoa.pubsub_client.subscribe_urls),
            max_timeout_ms=bridge_config.downlink.command.max_timeout_ms,
            mqtt_topic_prefix=bridge_config.mqtt.downlink_topic_prefix,
            mqtt_topic_prefixes=bridge_config.mqtt.downlink_topic_prefixes,
            mqtt_publisher=self._publish_mqtt_command,
            ack_publish_url=bridge_config.vsoa.pubsub_client.ack_publish_url,
            ack_publisher=self._enqueue_vsoa_ack,
            registry=self.registry,
            dedup=dedup,
            reconnect_interval_ms=bridge_config.vsoa.reconnect.interval_ms,
            reconnect_max_retries=bridge_config.vsoa.reconnect.max_retries,
            reconnect_backoff_multiplier=bridge_config.vsoa.reconnect.backoff_multiplier,
            status_callback=lambda status: self.events.put(("command_status", status)),
        )

        self._configure_window()
        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(self.POLL_INTERVAL_MS, self._drain_events)
        self.local_vsoa_server.start_if_needed()
        self.connect()
        self.vsoa_command_client.start()

    def _configure_window(self) -> None:
        self.root.title("MQTT 实时消息监视器")
        self.root.geometry("1180x720")
        self.root.minsize(820, 520)

        style = ttk.Style(self.root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Treeview", rowheight=28)
        style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 9, "bold"))

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill=tk.BOTH, expand=True)

        connection = ttk.Frame(outer)
        connection.pack(fill=tk.X, pady=(0, 10))

        ttk.Label(connection, text="Host").pack(side=tk.LEFT)
        self.host_entry = ttk.Entry(connection, textvariable=self.host_var, width=20)
        self.host_entry.pack(side=tk.LEFT, padx=(6, 14))
        ttk.Label(connection, text="端口").pack(side=tk.LEFT)
        self.port_entry = ttk.Entry(connection, textvariable=self.port_var, width=7)
        self.port_entry.pack(side=tk.LEFT, padx=(6, 14))

        self.connect_button = ttk.Button(
            connection, text="连接", command=self.toggle_connection, width=9
        )
        self.connect_button.pack(side=tk.LEFT)
        ttk.Button(connection, text="清空", command=self.clear_messages, width=9).pack(
            side=tk.LEFT, padx=(8, 0)
        )
        ttk.Label(connection, textvariable=self.count_var).pack(side=tk.RIGHT)
        self.status_label = ttk.Label(connection, textvariable=self.status_var)
        self.status_label.pack(side=tk.RIGHT, padx=(0, 18))

        actions = ttk.Frame(outer)
        actions.pack(fill=tk.X, pady=(0, 10))
        ttk.Button(
            actions,
            text="发布 /ctrl/cmd",
            command=self._open_control_command_dialog,
            width=16,
        ).pack(side=tk.LEFT)
        ttk.Button(
            actions,
            text="公共 Broker 监视器",
            command=self._open_public_broker_monitor,
            width=18,
        ).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(
            actions,
            text="重发选中 VSOA",
            command=self._resend_selected_vsoa,
            width=16,
        ).pack(side=tk.LEFT, padx=(8, 0))

        vsoa_bar = ttk.Frame(outer)
        vsoa_bar.pack(fill=tk.X, pady=(0, 10))
        ttk.Label(vsoa_bar, textvariable=self.vsoa_target_var).pack(
            fill=tk.X, anchor=tk.W
        )
        ttk.Label(vsoa_bar, textvariable=self.vsoa_status_var).pack(
            fill=tk.X, anchor=tk.W, pady=(3, 0)
        )

        command_bar = ttk.Frame(outer)
        command_bar.pack(fill=tk.X, pady=(0, 10))
        ttk.Label(command_bar, textvariable=self.vsoa_server_status_var).pack(
            fill=tk.X, anchor=tk.W
        )
        ttk.Label(command_bar, textvariable=self.command_client_status_var).pack(
            fill=tk.X, anchor=tk.W, pady=(3, 0)
        )

        table_frame = ttk.Frame(outer)
        table_frame.pack(fill=tk.BOTH, expand=True)

        columns = ("time", "host", "gateway", "topic", "qos", "vsoa", "payload")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings")
        headings = {
            "time": "接收时间",
            "host": "Broker Host",
            "gateway": "网关",
            "topic": "Topic",
            "qos": "QoS",
            "vsoa": "VSOA",
            "payload": "消息数据",
        }
        widths = {
            "time": 165,
            "host": 135,
            "gateway": 170,
            "topic": 260,
            "qos": 48,
            "vsoa": 120,
            "payload": 260,
        }
        for column in columns:
            self.tree.heading(column, text=headings[column])
            self.tree.column(
                column,
                width=widths[column],
                minwidth=45,
                anchor=tk.CENTER if column == "qos" else tk.W,
                stretch=column in ("topic", "payload"),
            )

        scrollbar = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.bind("<<TreeviewSelect>>", self._show_selected_payload)

        ttk.Label(outer, text="消息详情").pack(fill=tk.X, pady=(10, 4))
        details = ttk.Notebook(outer, height=205)
        details.pack(fill=tk.BOTH, expand=False)
        self.detail = self._add_detail_tab(details, "MQTT 原始消息")
        self.vsoa_detail = self._add_detail_tab(details, "VSOA 转换结果")

    def _add_detail_tab(self, notebook: ttk.Notebook, title: str) -> tk.Text:
        frame = ttk.Frame(notebook)
        notebook.add(frame, text=title)
        text_widget = tk.Text(
            frame,
            height=10,
            wrap=tk.NONE,
            font=("Consolas", 10),
            state=tk.DISABLED,
        )
        scroll_y = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=text_widget.yview)
        scroll_x = ttk.Scrollbar(frame, orient=tk.HORIZONTAL, command=text_widget.xview)
        text_widget.configure(
            yscrollcommand=scroll_y.set,
            xscrollcommand=scroll_x.set,
        )
        text_widget.grid(row=0, column=0, sticky="nsew")
        scroll_y.grid(row=0, column=1, sticky="ns")
        scroll_x.grid(row=1, column=0, sticky="ew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        return text_widget

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

    def _open_public_broker_monitor(self) -> None:
        if self.public_broker_monitor is not None:
            self.public_broker_monitor.focus()
            return
        self.public_broker_monitor = PublicBrokerMonitor(
            self.root,
            on_close=self._public_broker_monitor_closed,
        )

    def _public_broker_monitor_closed(self) -> None:
        self.public_broker_monitor = None

    def _resend_selected_vsoa(self) -> None:
        selection = self.tree.selection()
        if not selection:
            messagebox.showwarning("未选择消息", "请先在表格中选择一条消息。")
            return
        item = selection[0]
        messages = self.vsoa_messages_by_item.get(item, ())
        if not messages:
            messagebox.showwarning(
                "无法重发",
                "该消息没有可重发的 VSOA 转换结果。",
            )
            return

        token = self.next_token
        self.next_token += 1
        self.items_by_token[token] = item
        self.resend_tokens.add(token)
        self.tree.set(item, "vsoa", "正在重发")
        task = VsoaTask(
            token=token,
            messages=messages,
            target_url=self.vsoa_advertised_url,
        )
        if not self.vsoa_forwarder.enqueue(task):
            self.resend_tokens.discard(token)
            self.items_by_token.pop(token, None)
            self.tree.set(item, "vsoa", "重发失败：队列已满")
            messagebox.showerror("VSOA 重发失败", "VSOA 发送队列已满。")

    def _open_control_command_dialog(self) -> None:
        device_type = "lora"
        devices = self.registry.list_all()
        if devices:
            if devices[0].source in ("lora", "zigbee"):
                device_type = devices[0].source

        command = {
            "command_id": f"gui-{int(time.time() * 1000)}",
            "device_type": device_type,
            "device_id": VSOA_DEVICE_ID,
            "action": "set",
            "params": {},
        }

        dialog = tk.Toplevel(self.root)
        dialog.title("发布 VSOA /ctrl/cmd")
        dialog.geometry("620x430")
        dialog.minsize(480, 320)
        dialog.transient(self.root)
        dialog.grab_set()

        body = ttk.Frame(dialog, padding=12)
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(body, text="/ctrl/cmd JSON").pack(fill=tk.X, pady=(0, 6))
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
                messagebox.showerror("消息格式错误", "控制消息必须是 JSON 对象。", parent=dialog)
                return

            data["device_id"] = VSOA_DEVICE_ID

            from src.downlink.command import validate

            valid, error_code = validate(
                data,
                self.max_command_timeout_ms,
                check_timeout=False,
            )
            if not valid:
                messagebox.showerror(
                    "控制消息校验失败",
                    f"错误码：{error_code}",
                    parent=dialog,
                )
                return

            dialog.destroy()

            def send() -> None:
                success = self.local_vsoa_server.publish_control_command(
                    data,
                    target_url=self.vsoa_advertised_url,
                )
                self.events.put(
                    (
                        "manual_command_result",
                        (success, str(data.get("command_id", ""))),
                    )
                )

            threading.Thread(target=send, daemon=True).start()

        ttk.Button(buttons, text="发布", command=publish).pack(
            side=tk.RIGHT, padx=(0, 8)
        )

    def _publish_mqtt_command(self, topic: str, payload: str) -> bool:
        """Publish a VSOA downlink command through the monitor's MQTT client."""
        client = self.client
        if client is None:
            return False
        try:
            result = client.publish(topic, payload, qos=self.mqtt_qos)
            if result.rc != mqtt.MQTT_ERR_SUCCESS:
                return False
            result.wait_for_publish(timeout=5.0)
            return result.is_published()
        except Exception:
            return False

    def _enqueue_vsoa_ack(self, url: str, data: dict[str, Any]) -> None:
        """Return command ACK data to the configured VSOA server."""
        forwarded_data = dict(data)
        forwarded_data["device_id"] = VSOA_DEVICE_ID
        if not self.vsoa_forwarder.enqueue(
            VsoaTask(
                token=0,
                messages=((url, forwarded_data),),
                target_url=self.vsoa_advertised_url,
            )
        ):
            self.events.put(("command_status", "ACK 队列已满"))

    def _handle_vsoa_forward_result(
        self, token: int, success: bool, status: str
    ) -> None:
        # token=0 is an internal command ACK, not a displayed MQTT uplink row.
        if token > 0:
            self.events.put(("vsoa_result", (token, success, status)))

    def _on_connect(self, client, userdata, flags, rc, properties=None) -> None:
        if int(rc) == 0:
            for topic in self.topics:
                client.subscribe(topic, qos=1)
            self.events.put(("status", (True, f"已连接 {userdata['host']}")))
        else:
            self.events.put(("status", (False, f"连接失败，返回码 {rc}")))

    def _on_disconnect(self, client, userdata, rc, properties=None) -> None:
        if not self.closing and self.client is client:
            self.events.put(("status", (False, f"连接已断开（{rc}），正在重连...")))

    def _on_message(self, client, userdata, msg) -> None:
        data, payload = decode_message_payload(msg.payload)
        token = self.next_token
        self.next_token += 1
        task = None
        vsoa_status = "待转换"
        vsoa_payload = ""
        vsoa_messages: tuple[tuple[str, dict[str, Any]], ...] = ()
        if data is None:
            vsoa_status = "跳过：非 JSON"
        else:
            try:
                vsoa_messages = convert_to_vsoa_messages(msg.topic, data, self.registry)
                task = VsoaTask(
                    token=token,
                    messages=vsoa_messages,
                    target_url=self.vsoa_advertised_url,
                )
                vsoa_payload = format_vsoa_messages(vsoa_messages)
                vsoa_status = "已入队"
            except Exception as exc:
                vsoa_status = f"转换失败：{exc}"
                vsoa_payload = json.dumps(
                    {"error": str(exc)}, ensure_ascii=False, indent=2
                )

        received = ReceivedMessage(
            token=token,
            received_at=datetime.now().astimezone().isoformat(
                sep=" ", timespec="milliseconds"
            ),
            host=userdata["host"],
            gateway=extract_gateway(data),
            topic=msg.topic,
            qos=msg.qos,
            payload=payload,
            vsoa_payload=vsoa_payload,
            vsoa_messages=vsoa_messages,
            vsoa_status=vsoa_status,
        )
        self.events.put(("message", received))
        if task is not None and not self.vsoa_forwarder.enqueue(task):
            self.events.put(("vsoa_result", (token, False, "队列已满")))

    def _drain_events(self) -> None:
        try:
            while True:
                event, value = self.events.get_nowait()
                if event == "message":
                    self._add_message(value)
                elif event == "status":
                    self.connected, status = value
                    self.status_var.set(status)
                elif event == "vsoa_result":
                    self._update_vsoa_status(*value)
                elif event == "command_status":
                    self.command_client_status_var.set(f"VSOA 控制客户端：{value}")
                elif event == "server_status":
                    self.vsoa_server_status_var.set(f"VSOA Server：{value}")
                elif event == "manual_command_result":
                    success, command_id = value
                    if success:
                        self.command_client_status_var.set(
                            f"/ctrl/cmd 发布成功：{command_id}"
                        )
                        messagebox.showinfo(
                            "VSOA 发布成功",
                            f"/ctrl/cmd 已由 Server 发布到\n{self.vsoa_advertised_url}\n\n"
                            "此状态不包含远端客户端处理确认。",
                        )
                    else:
                        self.command_client_status_var.set("/ctrl/cmd 发布失败")
                        messagebox.showerror(
                            "VSOA 发布失败",
                            f"无法向 {self.vsoa_advertised_url} 发布 /ctrl/cmd。",
                        )
        except queue.Empty:
            pass

        if not self.closing:
            self.root.after(self.POLL_INTERVAL_MS, self._drain_events)

    def _add_message(self, message: ReceivedMessage) -> None:
        preview = " ".join(message.payload.split())
        if len(preview) > 180:
            preview = preview[:177] + "..."

        item = self.tree.insert(
            "",
            0,
            values=(
                message.received_at,
                message.host,
                message.gateway,
                message.topic,
                message.qos,
                message.vsoa_status,
                preview,
            ),
        )
        self.payloads[item] = message.payload
        self.vsoa_payloads[item] = message.vsoa_payload
        self.vsoa_messages_by_item[item] = message.vsoa_messages
        self.items_by_token[message.token] = item

        self.message_count += 1
        self.count_var.set(f"消息 {self.message_count}")
        children = self.tree.get_children()
        if len(children) > self.max_messages:
            expired = children[self.max_messages :]
            self.tree.delete(*expired)
            for item in expired:
                self.payloads.pop(item, None)
                self.vsoa_payloads.pop(item, None)
                self.vsoa_messages_by_item.pop(item, None)
            expired_set = set(expired)
            self.items_by_token = {
                token: item
                for token, item in self.items_by_token.items()
                if item not in expired_set
            }

    def _update_vsoa_status(self, token: int, success: bool, status: str) -> None:
        item = self.items_by_token.get(token)
        if token in self.resend_tokens:
            if item is not None and self.tree.exists(item):
                self.tree.set(
                    item,
                    "vsoa",
                    "重发成功" if success else f"重发：{status}",
                )
            if success:
                self.resend_tokens.discard(token)
                self.items_by_token.pop(token, None)
                self.vsoa_status_var.set(
                    f"选中消息重发成功 → {self.vsoa_advertised_url}"
                )
                messagebox.showinfo(
                    "VSOA 重发成功",
                    f"选中的 VSOA 消息已重新发送到\n"
                    f"{self.vsoa_advertised_url}\n\n"
                    "此状态表示 Server 已接受消息，不包含远端处理确认。",
                )
            else:
                self.vsoa_status_var.set(
                    f"选中消息重发等待/失败：{status} → "
                    f"{self.vsoa_advertised_url}"
                )
            return

        if item is not None and self.tree.exists(item):
            self.tree.set(item, "vsoa", status)
        if success:
            self.vsoa_sent_count += 1
            self.vsoa_status_var.set(
                f"发布成功 {self.vsoa_sent_count} 条 → {self.vsoa_advertised_url}"
            )
        else:
            self.vsoa_status_var.set(
                f"发布失败/等待：{status} → {self.vsoa_advertised_url}"
            )

    def _show_selected_payload(self, event=None) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        item = selection[0]
        self._set_detail_text(self.detail, self.payloads.get(item, ""))
        self._set_detail_text(self.vsoa_detail, self.vsoa_payloads.get(item, ""))

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
        self.vsoa_payloads.clear()
        self.vsoa_messages_by_item.clear()
        self.items_by_token.clear()
        self.resend_tokens.clear()
        self.message_count = 0
        self.count_var.set("消息 0")
        self._set_detail_text(self.detail, "")
        self._set_detail_text(self.vsoa_detail, "")

    def close(self) -> None:
        self.closing = True
        if self.public_broker_monitor is not None:
            self.public_broker_monitor.close()
        self.vsoa_command_client.close()
        self.disconnect()
        self.vsoa_forwarder.close()
        self.root.destroy()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MQTT 实时消息监视器")
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG),
        help="桥接配置文件（默认 bridge_vsoa_mqtt/config.yaml）",
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=f"MQTT Broker Host（默认 {DEFAULT_HOST}）",
    )
    parser.add_argument("--port", type=int, help="覆盖配置文件中的 MQTT Broker 端口")
    parser.add_argument(
        "--server-url",
        help="覆盖配置文件中的 VSOA server_url",
    )
    parser.add_argument(
        "--vsoa-bind-host",
        help="覆盖自动 VSOA Server 的监听地址",
    )
    parser.add_argument(
        "--vsoa-bind-port",
        type=int,
        help="覆盖自动 VSOA Server 的监听端口",
    )
    parser.add_argument(
        "--vsoa-advertised-url",
        help="覆盖界面显示的 VSOA 对外访问地址",
    )
    parser.add_argument(
        "--topic",
        action="append",
        dest="topics",
        help="订阅 Topic，可重复指定；不指定时使用脚本内默认 Topic",
    )
    parser.add_argument(
        "--max-messages",
        type=int,
        default=500,
        help="表格中最多保留的消息数（默认 500）",
    )
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
        print(
            "缺少 vsoa，请使用 bridge_vsoa_mqtt 的虚拟环境运行",
            file=sys.stderr,
        )
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
    vsoa_advertised_url = (
        args.vsoa_advertised_url or config.vsoa.business_server.advertised_url
    )
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
        root,
        host=host,
        port=port,
        topics=topics,
        max_messages=args.max_messages,
        server_url=server_url,
        vsoa_bind_host=vsoa_bind_host,
        vsoa_bind_port=vsoa_bind_port,
        vsoa_auto_start=config.vsoa.business_server.auto_start,
        vsoa_advertised_url=vsoa_advertised_url,
        reconnect_interval_ms=config.vsoa.reconnect.interval_ms,
        registry_max_devices=config.uplink.max_devices,
        bridge_config=config,
        mqtt_username=config.mqtt.username,
        mqtt_password=config.mqtt.password,
        mqtt_client_id=f"{config.mqtt.client_id}-receiver",
    )
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
