"""
MQTT 全量监视脚本 — 终端 1

同时订阅上行和下行 MQTT topic，帮助你观察所有在 Broker 上流动的消息。

用法:
    python tools/mqtt_monitor.py [--broker broker.emqx.io] [--port 1883]

topic 覆盖:
    - bridge/downlink/#         下行控制消息 (bridge → 设备)
    - bridge/uplink/#           上行数据消息 (设备 → bridge, 规范格式)
    - lora/+/up                 LoRaWAN 网关兼容格式
    - zigbee/+/report           Zigbee2MQTT 兼容格式
"""

import argparse
import json
import sys
import time

try:
    import paho.mqtt.client as mqtt
except ImportError:
    print("[ERROR] paho-mqtt 未安装，请执行: pip install paho-mqtt")
    sys.exit(1)

# ---------------------------------------------------------------------------
# 颜色标记
# ---------------------------------------------------------------------------
C = {
    "reset":   "\033[0m",
    "bold":    "\033[1m",
    "green":   "\033[92m",    # 上行
    "cyan":    "\033[96m",    # 下行
    "yellow":  "\033[93m",    # 系统
    "red":     "\033[91m",    # 错误
    "dim":     "\033[2m",
}

# topic 分类
DOWNLINK_PREFIXES = ("bridge/downlink/",)
UPLINK_BRIDGE_PREFIX = "bridge/uplink/"
LORA_UPLINK = "lora/"
ZIGBEE_UPLINK = "zigbee/"


def classify(topic: str) -> tuple[str, str]:
    """返回 (方向标签, 颜色)。"""
    if topic.startswith("bridge/downlink/"):
        return ("DOWNLINK", C["cyan"])
    if topic.startswith(UPLINK_BRIDGE_PREFIX):
        return ("UPLINK", C["green"])
    if topic.startswith(LORA_UPLINK):
        return ("UPLINK(LoRa)", C["green"])
    if topic.startswith(ZIGBEE_UPLINK):
        return ("UPLINK(Zigbee)", C["green"])
    return ("OTHER", C["yellow"])


def try_json(s: str) -> str:
    """尝试格式化 JSON，失败则原样返回。"""
    try:
        obj = json.loads(s)
        return json.dumps(obj, indent=2, ensure_ascii=False)
    except (json.JSONDecodeError, TypeError):
        return s


# ---------------------------------------------------------------------------
# paho 回调
# ---------------------------------------------------------------------------
def on_connect(client, userdata, flags, reason_code, properties=None):
    topics = [
        "bridge/downlink/#",
        "bridge/uplink/#",
        "lora/+/up",
        "zigbee/+/report",
    ]
    for t in topics:
        client.subscribe(t)
    print(f"{C['bold']}{C['green']}[OK]{C['reset']} 已连接 Broker (rc={reason_code})")
    print(f"    订阅 {len(topics)} 个 topic 模式:")
    for t in topics:
        print(f"      · {t}")
    print(f"{C['dim']}━━━━━━ 等待消息... (Ctrl+C 退出) ━━━━━━{C['reset']}\n")


def on_message(client, userdata, msg):
    label, color = classify(msg.topic)
    payload_str = try_json(msg.payload.decode("utf-8", errors="replace"))
    ts = time.strftime("%H:%M:%S")

    print(f"{C['dim']}[{ts}]{C['reset']} {color}{C['bold']}[{label}]{C['reset']} "
          f"{C['yellow']}{msg.topic}{C['reset']}")
    print(f"  └─ {payload_str}")
    print()


def on_disconnect(client, userdata, reason_code, properties=None):
    print(f"{C['red']}[断连]{C['reset']} rc={reason_code}，paho 将自动重连...")


# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="MQTT 全量监视")
    parser.add_argument("--broker", default="broker.emqx.io", help="MQTT Broker 地址")
    parser.add_argument("--port", type=int, default=1883, help="MQTT 端口")
    parser.add_argument("--client-id", default="bridge-monitor", help="Client ID")
    args = parser.parse_args()

    print(f"{C['bold']}MQTT 全量监视{C['reset']}")
    print(f"  Broker : {args.broker}:{args.port}")
    print(f"  Client : {args.client_id}")
    print()

    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=args.client_id,
    )
    client.on_connect = on_connect
    client.on_message = on_message
    client.on_disconnect = on_disconnect

    try:
        client.connect(args.broker, args.port, 60)
    except Exception as e:
        print(f"{C['red']}[FATAL] 无法连接 Broker: {e}{C['reset']}")
        sys.exit(1)

    try:
        client.loop_forever()
    except KeyboardInterrupt:
        print(f"\n{C['yellow']}[EXIT] 监视结束{C['reset']}")
        client.disconnect()


if __name__ == "__main__":
    main()
