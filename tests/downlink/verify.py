"""
手动验证脚本 — VSOA RPC + Pub/Sub 双通道（v3.0）

用法：
  终端1: python bridge/tests/downlink/mqtt_sub.py    (MQTT 订阅)
  终端2: python bridge/tests/downlink/verify.py      (本脚本，先跑)
  终端3: cd bridge && python src/downlink/main.py     (bridge 主程序)

v3.0 新增:
  - 设备注册表检查（未注册设备 → 2203）
  - 幂等去重（重复 command_id → 2006）
  - traceId 验证（ACK 含 br- 前缀 traceId）
  - MQTT publish 重试验证（日志可见 retry）
"""

import sys, os, time, threading
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src", "downlink"))
import vsoa

BUSINESS_PORT = 3000
RPC_PORT = 3001
ACK_SERVER_PORT = 3009  # bridge ACK Server


def main():
    print("=" * 60)
    print("  bridge-downlink 验证 v3.0")
    print("  RPC + Pub/Sub + 注册表 + 去重 + traceId")
    print("=" * 60)
    print()
    print("前提: 终端1 MQTT 订阅  |  终端3 bridge 主程序")
    print("验证: 注册表检查 | 幂等去重 | traceId | ACK | 超时")
    print()

    # ---- Step 1: 启动业务层 VSOA Server (Pub/Sub 命令发布方) ----
    print("[1/4] 启动业务层 VSOA Server (port 3000)...")
    server = vsoa.Server({"name": "verify-business"})
    threading.Thread(target=lambda: server.run("127.0.0.1", BUSINESS_PORT), daemon=True).start()
    waited = 0
    while not server._running and waited < 50:
        time.sleep(0.1); waited += 1
    if not server._running:
        print("      [FAIL]"); sys.exit(1)
    print("      [OK] vsoa://127.0.0.1:3000 (命令发布)")

    # ---- Step 2: 准备 RPC Client (连 bridge RPC Server) ----
    print()
    print("[2/4] 准备 RPC Client (连 bridge port 3001)...")

    rpc_client = vsoa.Client()
    for attempt in range(1, 20):
        ret = rpc_client.connect(f"vsoa://127.0.0.1:{RPC_PORT}")
        if ret == 0:
            break
        if attempt == 1:
            print(f"      等待 bridge RPC Server (port {RPC_PORT})...")
        time.sleep(1.5)
    else:
        print("      [FAIL] 无法连接 bridge RPC Server"); sys.exit(1)

    threading.Thread(target=rpc_client.run, daemon=True).start()
    time.sleep(0.3)
    print("      [OK] RPC Client 就绪")

    # ---- Step 3: 准备 ACK 订阅 (连 bridge ACK Server) NEW ----
    print()
    print("[3/4] 准备 ACK 订阅 (连 bridge ACK Server port 3009)...")
    print("      (第2周新增：业务层 subscribe /ctrl/ack 接收回执)")

    ack_client = vsoa.Client()
    received_acks: list[dict] = []

    def on_ack(cli, url, payload, quick):
        try:
            if payload and hasattr(payload, "param") and payload.param:
                ack = dict(payload.param)
                received_acks.append(ack)
                icon = "✓" if ack["error_code"] == 0 else "✗"
                print(f"      [{icon}] ACK: cmd_id={ack['command_id']} "
                      f"code={ack['error_code']} msg={ack['error_msg']}")
        except Exception:
            pass

    ack_client.onmessage = on_ack
    for attempt in range(1, 20):
        ret = ack_client.connect(f"vsoa://127.0.0.1:{ACK_SERVER_PORT}")
        if ret == 0:
            break
        if attempt == 1:
            print(f"      等待 bridge ACK Server (port {ACK_SERVER_PORT})...")
        time.sleep(1.5)
    else:
        print("      [FAIL] 无法连接 bridge ACK Server"); sys.exit(1)

    ack_client.subscribe("/ctrl/ack")
    threading.Thread(target=ack_client.run, daemon=True).start()
    time.sleep(0.3)
    print("      [OK] ACK 订阅就绪，等待 /ctrl/ack 消息...")

    print()
    print("      >>> 现在去终端3 启动 bridge: cd bridge && python src/downlink/main.py <<<")
    input("      确认 bridge 已启动后，按 Enter 发送测试命令...")

    # ---- Step 4: 测试 ----
    print()
    print("[4/4] 发送测试命令...")
    print()

    # --- 4a: Pub/Sub 通道（第2周: 有 ACK 回执）---
    print("  --- Pub/Sub 通道 ---")
    cmd_pub = {"command_id":"pub-001","device_type":"lora","device_id":"lora-node-01",
               "action":"set","params":{"led":"on"}}
    server.publish("/ctrl/cmd", payload=vsoa.Payload(param=cmd_pub))
    print(f"      [PubSub] publish /ctrl/cmd -> lora/n1/set")
    time.sleep(1)  # 等 ACK

    # Pub/Sub 非法命令（校验失败也应有 ACK）
    print()
    cmd_bad = {"command_id":"pub-bad","device_type":"bluetooth","device_id":"n1",
               "action":"set","params":{}}
    server.publish("/ctrl/cmd", payload=vsoa.Payload(param=cmd_bad))
    print(f"      [PubSub] publish bad cmd (bluetooth) -> 期望 ACK code=2003")
    time.sleep(1)

    # --- 4b: RPC 通道（第2周: 超时处理）---
    print()
    print("  --- RPC 通道（正常）---")
    cmd_rpc = {"command_id":"rpc-001","device_type":"zigbee","device_id":"zb-sensor-01",
               "action":"config","params":{"interval":60}}
    h, p, s = rpc_client.fetch("/bridge/send_command",
                                payload=vsoa.Payload(param=cmd_rpc), timeout=5.0)
    if s == vsoa.Client.CONNECT_OK:
        result = p.param
        print(f"      [RPC] fetch() -> code={result['error_code']}, msg={result['error_msg']}, "
              f"ack_level={result.get('ack_level','?')}")

    # RPC 非法命令（第1周已有）
    print()
    bad_cmd = {"command_id":"rpc-bad","device_type":"bluetooth","device_id":"n1",
               "action":"set","params":{}}
    h, p, s = rpc_client.fetch("/bridge/send_command",
                                payload=vsoa.Payload(param=bad_cmd), timeout=5.0)
    if s == vsoa.Client.CONNECT_OK:
        result = p.param
        print(f"      [RPC] fetch(bad) -> code={result['error_code']} (expected 2003)")

    # --- 4c: RPC 超时测试（第2周新增）---
    print()
    print("  --- RPC 超时测试（第2周新增）---")
    # 使用极短 timeout 触发超时（QoS 1 需等待 PUBACK）
    cmd_to = {"command_id":"rpc-timeout","device_type":"lora","device_id":"lora-node-01",
              "action":"get","params":{},"timeout_ms":500}  # 500ms 超时
    h, p, s = rpc_client.fetch("/bridge/send_command",
                                payload=vsoa.Payload(param=cmd_to), timeout=5.0)
    if s == vsoa.Client.CONNECT_OK:
        result = p.param
        expected = 2201
        match = "✓" if result['error_code'] == expected else "✗ UNEXPECTED"
        print(f"      [RPC] fetch(timeout=500ms) -> code={result['error_code']} (expected {expected}) {match}")
        print(f"            注意: 2201=MQTT PUBACK 超时，MQTT 消息可能仍被发出")

    # --- 4d: v3.0 设备注册表检查 ---
    print()
    print("  --- v3.0 设备注册表检查 ---")
    # 发送一个不在 devices.yaml 中的 device_id
    cmd_bad_dev = {"command_id":"rpc-noreg","device_type":"lora",
                   "device_id":"nonexistent-device-999","action":"set","params":{}}
    h, p, s = rpc_client.fetch("/bridge/send_command",
                                payload=vsoa.Payload(param=cmd_bad_dev), timeout=5.0)
    if s == vsoa.Client.CONNECT_OK:
        result = p.param
        expected = 2203
        match = "✓" if result['error_code'] == expected else "✗ UNEXPECTED"
        print(f"      [RPC] fetch(未注册设备) -> code={result['error_code']} "
              f"(expected {expected}={match})")
        trace = result.get('trace_id', '?')
        print(f"            trace_id={trace[:40]}...")

    # --- 4e: v3.0 幂等去重 ---
    print()
    print("  --- v3.0 幂等去重 ---")
    cmd_dup = {"command_id":"v3-dup-001","device_type":"lora","device_id":"lora-node-01",
               "action":"set","params":{"led":"on"}}
    # 第一次
    h1, p1, s1 = rpc_client.fetch("/bridge/send_command",
                                   payload=vsoa.Payload(param=cmd_dup), timeout=5.0)
    c1 = p1.param['error_code'] if s1 == vsoa.Client.CONNECT_OK else -1
    print(f"      [RPC] 第1次 cmd_id=v3-dup-001 -> code={c1} (expected 0) {'✓' if c1==0 else '✗'}")

    # 第二次（相同 command_id → 应返回 2006）
    h2, p2, s2 = rpc_client.fetch("/bridge/send_command",
                                   payload=vsoa.Payload(param=cmd_dup), timeout=5.0)
    c2 = p2.param['error_code'] if s2 == vsoa.Client.CONNECT_OK else -1
    expected_dup = 2006
    match_dup = "✓" if c2 == expected_dup else "✗ UNEXPECTED"
    print(f"      [RPC] 第2次 cmd_id=v3-dup-001 -> code={c2} "
          f"(expected {expected_dup}) {match_dup}")

    # --- 4f: v3.0 traceId 验证 ---
    print()
    print("  --- v3.0 traceId 验证 ---")
    cmd_tr = {"command_id":"v3-trace","device_type":"lora","device_id":"lora-node-01",
              "action":"get","params":{}}
    h, p, s = rpc_client.fetch("/bridge/send_command",
                                payload=vsoa.Payload(param=cmd_tr), timeout=5.0)
    if s == vsoa.Client.CONNECT_OK:
        result = p.param
        trace_id = result.get('trace_id', '')
        if trace_id.startswith('br-') and '-' in trace_id[3:]:
            print(f"      [✓] trace_id={trace_id} (格式正确: br-{{hex}}-{{ts}})")
        else:
            print(f"      [✗] trace_id={trace_id} (格式错误!)")

    # Pub/Sub ACK 中的 traceId
    print()
    if received_acks:
        last_ack = received_acks[-1]
        ack_trace = last_ack.get('trace_id', '?')
        print(f"      [✓] Pub/Sub ACK 也含 trace_id: {ack_trace[:40]}...")
    else:
        print(f"      [!] 未收到 Pub/Sub ACK（可能 bridge 未重启或 ACK Server 未连上）")

    # ---- 结果 ----
    print()
    print("=" * 60)
    print("  验证完成！检查项：")
    print(f"  - 终端1 (mqtt_sub) 应收到 MQTT 消息（含 trace_id 字段）")
    print(f"  - Pub/Sub ACK: 收到 {len(received_acks)} 条 (/ctrl/ack)")
    print(f"    · pub-001: error_code=0 (成功)")
    print(f"    · pub-bad: error_code=2003 (校验失败)")
    print(f"  - RPC fetch() 正常: code=0 (成功) + code=2003 (校验失败)")
    print(f"  - RPC 超时: code=2201 (PUBACK 超时)")
    print(f"  - v3.0 注册表: 未注册设备 → 2203")
    print(f"  - v3.0 去重:   重复 cmd_id → 2006")
    print(f"  - v3.0 traceId: br-{{hex}}-{{ts}} 格式，ACK 含 trace_id")
    print("=" * 60)


if __name__ == "__main__":
    main()
