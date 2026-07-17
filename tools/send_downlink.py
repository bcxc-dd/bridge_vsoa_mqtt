"""
VSOA 下行命令发送脚本 — 通过 bridge 发 MQTT 消息到 LoRa/Zigbee 设备

用法:
  1. 先启动 bridge:  cd bridge-merged && python src/main.py
  2. 再跑本脚本:    cd bridge-merged && python tools/send_downlink.py

支持两种通道:
  - RPC (默认):  同步阻塞，一行拿到 ACK 回执
  - Pub/Sub:     异步发布，ACK 通过 /ctrl/ack 订阅接收

环境要求:
  pip install vsoa
"""

import sys
import time
import threading
import argparse
from typing import Any

import vsoa

# ---------------------------------------------------------------------------
# 端口
# ---------------------------------------------------------------------------
BUSINESS_PORT = 3000    # 业务层 VSOA Server (Pub/Sub 命令发布)
BRIDGE_PORT = 3001      # bridge VSOA Server (RPC + ACK)

# ---------------------------------------------------------------------------
# RPC 通道: 同步发送，直接拿回执
# ---------------------------------------------------------------------------
def send_rpc(device_type: str, device_id: str, action: str,
             params: dict, command_id: str = "", timeout: float = 5.0,
             host: str = "127.0.0.1"):
    """通过 VSOA RPC 发送下行命令，同步等待 ACK 回执。"""
    if not command_id:
        command_id = f"cmd-{int(time.time())}"

    cmd = {
        "command_id": command_id,
        "device_type": device_type,
        "device_id": device_id,
        "action": action,
        "params": params,
    }

    client = vsoa.Client()
    ret = client.connect(f"vsoa://{host}:{BRIDGE_PORT}")
    if ret != 0:
        print(f"[FAIL] 无法连接 bridge RPC Server (port {BRIDGE_PORT})")
        print(f"       请确认 bridge 已启动: cd bridge-merged && python src/main.py")
        return None

    # client.run() 必须在后台线程运行事件循环
    threading.Thread(target=client.run, daemon=True).start()
    time.sleep(0.3)

    print(f"[RPC] 发送命令: {command_id}")
    print(f"      device_type={device_type}, device_id={device_id}, action={action}")
    print(f"      params={params}")

    h, p, s = client.fetch("/bridge/send_command",
                           payload=vsoa.Payload(param=cmd), timeout=timeout)

    if s == vsoa.Client.CONNECT_OK and p and hasattr(p, "param"):
        result = dict(p.param)
        return result
    else:
        print(f"[FAIL] fetch 失败, status={s}")
        return None


# ---------------------------------------------------------------------------
# Pub/Sub 通道: 异步发布，ACK 通过 /ctrl/ack 订阅接收
# ---------------------------------------------------------------------------
def send_pubsub(device_type: str, device_id: str, action: str,
                params: dict, command_id: str = "",
                host: str = "127.0.0.1"):
    """通过 VSOA Pub/Sub 发送下行命令，ACK 异步返回。"""
    if not command_id:
        command_id = f"pub-{int(time.time())}"

    cmd = {
        "command_id": command_id,
        "device_type": device_type,
        "device_id": device_id,
        "action": action,
        "params": params,
    }

    # 1. 启动业务层 VSOA Server (port 3000) — 命令发布方
    server = vsoa.Server({"name": "send-downlink-pubsub"})
    threading.Thread(target=lambda: server.run("127.0.0.1", BUSINESS_PORT),
                     daemon=True).start()
    waited = 0
    while not server._running and waited < 50:
        time.sleep(0.1); waited += 1
    if not server._running:
        print("[FAIL] 业务层 VSOA Server 启动失败")
        return None

    # 2. 连接 bridge 订阅 ACK
    ack_client = vsoa.Client()
    received_acks: list[dict] = []

    def on_ack(cli, url, payload, quick):
        try:
            if payload and hasattr(payload, "param") and payload.param:
                ack = dict(payload.param)
                received_acks.append(ack)
                icon = "✓" if ack["error_code"] == 0 else "✗"
                print(f"  [{icon}] ACK: cmd_id={ack['command_id']} "
                      f"code={ack['error_code']} msg={ack['error_msg']}")
        except Exception:
            pass

    ack_client.onmessage = on_ack
    for attempt in range(1, 20):
        ret = ack_client.connect(f"vsoa://{host}:{BRIDGE_PORT}")
        if ret == 0:
            break
        if attempt == 1:
            print(f"      等待 bridge ({host}:{BRIDGE_PORT})...")
        time.sleep(1.5)
    else:
        print("[FAIL] 无法连接 bridge ACK")
        return None

    ack_client.subscribe("/ctrl/ack")
    threading.Thread(target=ack_client.run, daemon=True).start()
    time.sleep(0.3)

    # 3. 发布命令
    print(f"[PubSub] 发布命令: {command_id}")
    print(f"         device_type={device_type}, device_id={device_id}, action={action}")
    print(f"         params={params}")
    server.publish("/ctrl/cmd", payload=vsoa.Payload(param=cmd))

    # 4. 等待 ACK
    time.sleep(2.0)
    return received_acks


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="VSOA 下行命令发送工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # RPC: 同步 (推荐)
  python tools/send_downlink.py rpc lora lora-node-01 set '{"led":"on"}'

  # RPC: Zigbee
  python tools/send_downlink.py rpc zigbee zb-sensor-01 config '{"interval":60}'

  # Pub/Sub: 异步 ACK
  python tools/send_downlink.py pubsub lora lora-node-01 get '{}'

  # 交互模式
  python tools/send_downlink.py
        """,
    )
    sub = parser.add_subparsers(dest="mode", help="通道选择")

    # RPC 子命令
    rpc = sub.add_parser("rpc", help="RPC 同步通道 (推荐)")
    rpc.add_argument("device_type", choices=["lora", "zigbee"])
    rpc.add_argument("device_id")
    rpc.add_argument("action", choices=["set", "get", "reset", "config"])
    rpc.add_argument("params", help='JSON 字符串, 如: \'{"led":"on"}\'')
    rpc.add_argument("--cid", default="", help="自定义 command_id")
    rpc.add_argument("--host", default="127.0.0.1", help="bridge 主机 IP (默认 127.0.0.1)")

    # PubSub 子命令
    pub = sub.add_parser("pubsub", help="Pub/Sub 异步通道")
    pub.add_argument("device_type", choices=["lora", "zigbee"])
    pub.add_argument("device_id")
    pub.add_argument("action", choices=["set", "get", "reset", "config"])
    pub.add_argument("params", help='JSON 字符串, 如: \'{"led":"on"}\'')
    pub.add_argument("--cid", default="", help="自定义 command_id")
    pub.add_argument("--host", default="127.0.0.1", help="bridge 主机 IP (默认 127.0.0.1)")

    args = parser.parse_args()

    # 未指定子命令 → 交互模式
    if not args.mode:
        return interactive_mode()

    import json as _json
    # 支持两种 params 格式:
    #   1. JSON:  '{"led":"on"}'  (bash / --% 前缀)
    #   2. KV:    led=on 或 led=on,interval=60  (PowerShell 友好)
    params_str: str = args.params
    try:
        params = _json.loads(params_str)
    except (_json.JSONDecodeError, ValueError):
        # 尝试 KV 解析
        params = _parse_kv(params_str)
        if params is None:
            print(f"[FAIL] params 格式错误: {args.params}")
            print(f"       支持 JSON:  --% rpc lora n1 set '{{\"led\":\"on\"}}'")
            print(f"       支持 KV:    rpc lora n1 set led=on")
            print(f"       支持 KV:    rpc lora n1 set led=on,interval=60")
            sys.exit(1)
        print(f"[INFO] KV 解析: {params}")

    if args.mode == "rpc":
        result = send_rpc(args.device_type, args.device_id, args.action,
                          params, command_id=args.cid, host=args.host)
        if result:
            print()
            print("=" * 50)
            print(f"  ACK 回执:")
            print(f"  command_id: {result.get('command_id')}")
            print(f"  error_code: {result.get('error_code')} ({result.get('error_msg')})")
            print(f"  device:     {result.get('device_type')}/{result.get('device_id')}")
            print(f"  ack_level:  {result.get('ack_level')}")
            print(f"  trace_id:   {result.get('trace_id', '')[:40]}...")
            print("=" * 50)
    else:
        send_pubsub(args.device_type, args.device_id, args.action,
                    params, command_id=args.cid, host=args.host)


def _parse_kv(raw: str) -> dict | None:
    """解析 key=value 格式的参数字符串 (PowerShell 友好).

    支持:
      led=on
      led=on,interval=60,val=123
    """
    if not raw or "=" not in raw:
        return None
    result: dict[str, Any] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair:
            continue
        if "=" not in pair:
            return None
        k, v = pair.split("=", 1)
        k = k.strip()
        v = v.strip()
        # 自动类型转换: 数字、true/false
        if v.isdigit():
            v = int(v)
        elif v.lower() == "true":
            v = True
        elif v.lower() == "false":
            v = False
        result[k] = v
    return result if result else None


def interactive_mode():
    """交互模式：一步步引导用户发送命令。"""
    import json as _json

    print("=" * 50)
    print("  VSOA 下行命令发送 — 交互模式")
    print("=" * 50)
    print()

    # Bridge 主机
    print(f"Bridge 主机 IP [127.0.0.1]:")
    host = input("> ").strip() or "127.0.0.1"

    print()
    # 选择通道
    print("选择通道:")
    print("  [1] RPC     — 同步, 一行拿到 ACK (推荐)")
    print("  [2] Pub/Sub — 异步, /ctrl/ack 订阅")
    ch = input("> ").strip()

    # 设备类型
    print()
    print("设备类型: [lora] / zigbee")
    dt = input("> ").strip() or "lora"

    # 设备 ID
    print()
    print("设备 ID (如 lora-node-01):")
    did = input("> ").strip()
    if not did:
        print("设备 ID 不能为空")
        return

    # 动作
    print()
    print("动作: [set] / get / reset / config")
    act = input("> ").strip() or "set"

    # 参数
    print()
    print("参数 (JSON, 如 {\"led\":\"on\"}):")
    params_str = input("> ").strip() or '{}'
    try:
        params = _json.loads(params_str)
    except _json.JSONDecodeError:
        print(f"[FAIL] 非法 JSON: {params_str}")
        return

    print()
    print(f"即将发送: {dt}/{did} action={act} params={params}")
    ok = input("确认? [Y/n] ").strip().lower()
    if ok and ok != "y":
        print("已取消")
        return

    print()

    if ch == "2":
        send_pubsub(dt, did, act, params, host=host)
    else:
        result = send_rpc(dt, did, act, params, host=host)
        if result:
            code = result.get("error_code", -1)
            ok_str = "✓ 成功" if code == 0 else f"✗ 失败 (code={code})"
            print(f"\n  结果: {ok_str}")
            print(f"  {result.get('error_msg')}")
            print(f"  trace_id: {result.get('trace_id', '?')}")


if __name__ == "__main__":
    main()
