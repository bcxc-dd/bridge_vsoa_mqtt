"""
MQTT 设备模拟器 — 终端 4

模拟 LoRa / Zigbee 设备通过 MQTT 上报数据，用于手动验证上行链路。
支持的设备类型和 payload 格式与真实设备一致。

用法:
    python tools/sim_device.py [--broker broker.emqx.io] [--port 1883]

交互命令:
    lora <id>     — 注册并上报一个 LoRa 设备 (温度+湿度)
    zigbee <id>   — 注册并上报一个 Zigbee 设备 (温度+湿度+电量)
    lora2 <id>    — LoRaWAN 网关兼容格式 (lora/+/up)
    zb2 <id>      — Zigbee2MQTT 兼容格式 (zigbee/+/report)
    list          — 列出已模拟的设备
    help          — 显示帮助
    quit          — 退出
"""

import argparse
import json
import random
import sys
import time

try:
    import paho.mqtt.client as mqtt
except ImportError:
    print("[ERROR] paho-mqtt 未安装，请执行: pip install paho-mqtt")
    sys.exit(1)

# ---------------------------------------------------------------------------
# 颜色
# ---------------------------------------------------------------------------
C = {
    "reset":  "\033[0m",
    "bold":   "\033[1m",
    "green":  "\033[92m",
    "cyan":   "\033[96m",
    "yellow": "\033[93m",
    "dim":    "\033[2m",
}

# ---------------------------------------------------------------------------
# 设备模板
# ---------------------------------------------------------------------------

def _now_ms() -> int:
    return int(time.time() * 1000)


def _r(base: float, delta: float) -> float:
    """随机抖动"""
    return round(base + random.uniform(-delta, delta), 1)


class SimDevice:
    """一个模拟设备的状态。"""

    def __init__(self, device_id: str, dev_type: str):
        self.device_id = device_id
        self.dev_type = dev_type  # "lora" | "zigbee"
        self.report_count = 0
        # 模拟传感器基线
        self.temp_base = random.uniform(22.0, 28.0)
        self.hum_base = random.uniform(45.0, 65.0)
        self.batt = random.randint(75, 100)

    def next_payload(self) -> dict:
        """生成下一次上报的 payload。"""
        self.report_count += 1
        now = _now_ms()
        temp = _r(self.temp_base, 0.8)
        hum = _r(self.hum_base, 2.0)
        self.batt = max(0, min(100, self.batt - random.randint(0, 1)))

        if self.dev_type == "lora":
            return {
                "device_id": self.device_id,
                "devEUI": f"70B3D5E57E{self.device_id[-6:]:0>6s}"[:16],
                "deviceName": f"LoRa-{self.device_id}",
                "temperature": temp,
                "humidity": hum,
                "battery": self.batt,
                "signal": random.randint(-80, -40),
                "snr": _r(7.0, 3.0),
                "timestamp": now,
                "rxInfo": [{
                    "rssi": random.randint(-80, -40),
                    "loRaSNR": _r(7.0, 3.0),
                }],
            }
        else:  # zigbee
            return {
                "device_id": self.device_id,
                "friendly_name": f"ZB-{self.device_id}",
                "ieeeAddr": f"00:15:8D:00:02:{random.randint(10,99):02d}:"
                            f"{random.randint(10,99):02d}:{random.randint(10,99):02d}",
                "temperature": temp,
                "humidity": hum,
                "battery": self.batt,
                "linkquality": random.randint(30, 100),
                "last_seen": now,
                "timestamp": now,
            }


# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="MQTT 设备模拟器")
    parser.add_argument("--broker", default="broker.emqx.io", help="MQTT Broker 地址")
    parser.add_argument("--port", type=int, default=1883, help="MQTT 端口")
    parser.add_argument("--client-id", default="bridge-sim-device", help="Client ID")
    parser.add_argument("--qos", type=int, default=1, help="MQTT QoS")
    args = parser.parse_args()

    print(f"{C['bold']}MQTT 设备模拟器{C['reset']}")
    print(f"  Broker : {args.broker}:{args.port}")
    print(f"  QoS    : {args.qos}")
    print()

    # 连接 MQTT
    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=args.client_id,
    )
    client.on_connect = lambda c, u, f, rc, p: print(
        f"{C['green']}[OK]{C['reset']} 已连接 Broker (rc={rc})"
    )
    client.on_disconnect = lambda c, u, f, rc, p: print(
        f"{C['yellow']}[断连]{C['reset']} rc={rc}"
    )
    client.on_publish = lambda c, u, mid, rc, p: None  # 静默

    try:
        client.connect(args.broker, args.port, 60)
    except Exception as e:
        print(f"[FATAL] 无法连接 Broker: {e}")
        sys.exit(1)

    client.loop_start()

    devices: dict[str, SimDevice] = {}

    def _publish(topic: str, payload: dict) -> None:
        """发布 MQTT 消息并打印确认。"""
        payload_str = json.dumps(payload, ensure_ascii=False)
        info = client.publish(topic, payload_str, qos=args.qos)
        # 简化输出
        preview = json.dumps(payload, ensure_ascii=False)
        if len(preview) > 120:
            preview = preview[:117] + "..."
        label = "UPLINK(LoRa)" if "lora" in topic else "UPLINK(Zigbee)"
        color = C["cyan"] if "lora" in topic else C["green"]
        print(f"{C['dim']}[{time.strftime('%H:%M:%S')}]{C['reset']} "
              f"{color}{C['bold']}[{label}]{C['reset']} "
              f"{C['yellow']}{topic}{C['reset']}")
        print(f"  └─ {preview}")

    # ------------------------------------------------------------------
    # 命令处理
    # ------------------------------------------------------------------
    def do_lora(device_id: str) -> None:
        """LoRa 设备上报 (bridge/uplink 规范格式)。"""
        if device_id not in devices:
            devices[device_id] = SimDevice(device_id, "lora")
            print(f"  {C['green']}[NEW]{C['reset']} 创建 LoRa 设备: {device_id}")
        dev = devices[device_id]
        payload = dev.next_payload()
        _publish(f"bridge/uplink/lora/{device_id}/data", payload)

    def do_lora2(device_id: str) -> None:
        """LoRaWAN 网关兼容格式 (lora/+/up)。"""
        if device_id not in devices:
            devices[device_id] = SimDevice(device_id, "lora")
            print(f"  {C['green']}[NEW]{C['reset']} 创建 LoRa 设备: {device_id}")
        dev = devices[device_id]
        payload = dev.next_payload()
        # 用 devEUI 做 topic 的 device_id 部分
        dev_eui = payload.get("devEUI", device_id)
        _publish(f"lora/{dev_eui}/up", payload)

    def do_zigbee(device_id: str) -> None:
        """Zigbee 设备上报 (bridge/uplink 规范格式)。"""
        if device_id not in devices:
            devices[device_id] = SimDevice(device_id, "zigbee")
            print(f"  {C['green']}[NEW]{C['reset']} 创建 Zigbee 设备: {device_id}")
        dev = devices[device_id]
        payload = dev.next_payload()
        _publish(f"bridge/uplink/zigbee/{device_id}/data", payload)

    def do_zb2(device_id: str) -> None:
        """Zigbee2MQTT 兼容格式 (zigbee/+/report)。"""
        if device_id not in devices:
            devices[device_id] = SimDevice(device_id, "zigbee")
            print(f"  {C['green']}[NEW]{C['reset']} 创建 Zigbee 设备: {device_id}")
        dev = devices[device_id]
        payload = dev.next_payload()
        ieee = payload.get("ieeeAddr", device_id).replace(":", "_")
        _publish(f"zigbee/{ieee}/report", payload)

    # ------------------------------------------------------------------
    # 交互循环
    # ------------------------------------------------------------------
    print(f"{C['dim']}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{C['reset']}")
    print(f"  命令:")
    print(f"    {C['cyan']}lora <id>{C['reset']}     — 上报 LoRa 设备 (bridge/uplink/lora/+/data)")
    print(f"    {C['cyan']}lora2 <id>{C['reset']}    — 上报 LoRaWAN 格式 (lora/+/up)")
    print(f"    {C['green']}zigbee <id>{C['reset']}   — 上报 Zigbee 设备 (bridge/uplink/zigbee/+/data)")
    print(f"    {C['green']}zb2 <id>{C['reset']}      — 上报 Zigbee2MQTT 格式 (zigbee/+/report)")
    print(f"    {C['yellow']}list{C['reset']}         — 列出已模拟设备")
    print(f"    {C['yellow']}auto{C['reset']}         — 自动注册预设设备 (lora-node-01 + zb-sensor-01)")
    print(f"    {C['yellow']}help{C['reset']}         — 帮助")
    print(f"    {C['yellow']}quit{C['reset']}         — 退出")
    print(f"{C['dim']}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{C['reset']}")
    print()

    def cmd_auto() -> None:
        """一键注册验证所需的预设设备。"""
        print(f"{C['yellow']}[AUTO] 注册预设设备...{C['reset']}")
        do_lora("lora-node-01")
        time.sleep(0.3)
        do_zigbee("zb-sensor-01")
        time.sleep(0.3)
        print(f"{C['green']}[AUTO] 完成！lora-node-01 + zb-sensor-01 已注册{C['reset']}")

    def cmd_list() -> None:
        if not devices:
            print(f"  (空 — 还没有模拟设备)")
            return
        print(f"  {len(devices)} 个模拟设备:")
        for did, dev in devices.items():
            print(f"    {dev.dev_type:7s} | {did:20s} | "
                  f"temp={dev.temp_base:.1f}°C hum={dev.hum_base:.0f}% "
                  f"rpt={dev.report_count}")

    handlers = {
        "lora":   lambda a: do_lora(a[0]) if a else print("用法: lora <id>"),
        "lora2":  lambda a: do_lora2(a[0]) if a else print("用法: lora2 <id>"),
        "zigbee": lambda a: do_zigbee(a[0]) if a else print("用法: zigbee <id>"),
        "zb2":    lambda a: do_zb2(a[0]) if a else print("用法: zb2 <id>"),
        "auto":   lambda a: cmd_auto(),
        "list":   lambda a: cmd_list(),
        "help":   lambda a: print("lora / lora2 / zigbee / zb2 / list / auto / quit"),
        "quit":   lambda a: None,
    }

    while True:
        try:
            line = input(f"{C['bold']}sim>{C['reset']} ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not line:
            continue

        parts = line.split()
        cmd = parts[0].lower()
        args_list = parts[1:]

        if cmd == "quit":
            break

        handler = handlers.get(cmd)
        if handler:
            handler(args_list)
        else:
            print(f"  未知命令: {cmd}，输入 help 查看帮助")

    # 退出
    print(f"\n{C['yellow']}[EXIT] 设备模拟器关闭{C['reset']}")
    print(f"  共模拟 {len(devices)} 个设备，总上报 {sum(d.report_count for d in devices.values())} 次")
    client.loop_stop()
    client.disconnect()


if __name__ == "__main__":
    main()
