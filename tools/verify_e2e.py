"""
端到端验证脚本 v1.1 — 终端 3 (验证主控)

适配合并版 bridge-merged 架构，原 bridge verify.py 升级版。
核心变化: ACK 订阅改走 bridge VSOA Server (3001)，不再使用独立 ACK Server (3009)。

v1.1 修复:
  - /device/list 返回格式: {"count": N, "devices": [...]}
  - /adapter/list 返回格式: {"count": N, "adapters": [...]}
  - /bridge/health 字段名: service/devices/uptime
  - 错误格式兼容: {"error": "..."} (上行 vsoa_server 旧格式, 待更新为数字码)
  - 新增 /device/update 订阅 — 诊断 VSOA publish 是否可达

用法 (按顺序启动):
  终端1: python tools/mqtt_monitor.py              (MQTT 全量监视)
  终端2: cd bridge-merged && python src/main.py    (bridge 主程序)
  终端4: python tools/sim_device.py                (设备模拟器)
  终端3: python tools/verify_e2e.py                (本脚本 — 最后启动)
"""

import json
import sys
import time
import threading
from typing import Any

import vsoa

# ---------------------------------------------------------------------------
# 端口 (合并版架构)
# ---------------------------------------------------------------------------
BUSINESS_PORT = 3000    # 业务层 VSOA Server (Pub/Sub 命令发布方)
BRIDGE_PORT = 3001      # bridge VSOA Server (RPC + ACK + 查询 + 通知)

# ---------------------------------------------------------------------------
# 颜色
# ---------------------------------------------------------------------------
C = {
    "reset":  "\033[0m",
    "bold":   "\033[1m",
    "green":  "\033[92m",
    "red":    "\033[91m",
    "yellow": "\033[93m",
    "cyan":   "\033[96m",
    "magenta":"\033[95m",
    "dim":    "\033[2m",
}


def _ok(msg: str) -> None:
    print(f"  {C['green']}[OK]{C['reset']} {msg}")


def _fail(msg: str) -> None:
    print(f"  {C['red']}[FAIL]{C['reset']} {msg}")


def _warn(msg: str) -> None:
    print(f"  {C['yellow']}[WARN]{C['reset']} {msg}")


def _info(msg: str) -> None:
    print(f"  {C['cyan']}[INFO]{C['reset']} {msg}")


def _rpc_fetch(client, url: str, timeout: float = 3.0) -> dict | None:
    """封装 fetch 调用，返回 payload.param dict 或 None。"""
    try:
        h, p, s = client.fetch(url, payload=vsoa.Payload(param={}), timeout=timeout)
        if s == vsoa.Client.CONNECT_OK and p and hasattr(p, "param"):
            return dict(p.param) if p.param else {}
    except Exception:
        pass
    return None


def _safe_get(d: dict | None, *keys: str, default: Any = "?") -> Any:
    """从 dict 中安全取第一个存在的 key。"""
    if d is None:
        return default
    for k in keys:
        if k in d:
            return d[k]
    return default


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print(f"{C['bold']}{'=' * 60}{C['reset']}")
    print(f"{C['bold']}  bridge-merged 端到端验证 v1.1{C['reset']}")
    print(f"{C['bold']}  合并版架构 (ACK 走 3001, 无 3009){C['reset']}")
    print(f"{C['bold']}{'=' * 60}{C['reset']}")
    print()
    print("端口:")
    print("  3000 — 业务层 VSOA Server (本脚本启动，/ctrl/cmd 发布方)")
    print("  3001 — bridge VSOA Server (RPC + ACK + 设备查询 + 通知)")
    print("  1883 — MQTT Broker (公网 broker.emqx.io)")
    print()
    print(f"{C['yellow']}前提: 终端1 MQTT Monitor | 终端2 bridge | 终端4 sim_device{C['reset']}")
    print()

    # ==================================================================
    # Step 1: 启动业务层 VSOA Server (Pub/Sub 命令发布方)
    # ==================================================================
    print(f"{C['bold']}[1/6] 启动业务层 VSOA Server{C['reset']} (port {BUSINESS_PORT})...")
    biz_server = vsoa.Server({"name": "verify-business"})
    threading.Thread(
        target=lambda: biz_server.run("127.0.0.1", BUSINESS_PORT),
        daemon=True,
    ).start()
    waited = 0
    while not biz_server._running and waited < 50:
        time.sleep(0.1); waited += 1
    if not biz_server._running:
        _fail("业务层 VSOA Server 启动失败"); sys.exit(1)
    _ok(f"业务层 VSOA Server 就绪 — vsoa://127.0.0.1:{BUSINESS_PORT}")

    # ==================================================================
    # Step 2: 连接 bridge VSOA Server (3001) — RPC Client
    # ==================================================================
    print()
    print(f"{C['bold']}[2/6] 连接 bridge VSOA Server{C['reset']} (port {BRIDGE_PORT})...")
    print("  (RPC Client — /bridge/send_command + 查询端点)")

    rpc_client = vsoa.Client()
    for attempt in range(1, 30):
        ret = rpc_client.connect(f"vsoa://127.0.0.1:{BRIDGE_PORT}")
        if ret == 0:
            break
        if attempt == 1:
            print(f"  {C['dim']}等待 bridge 启动 (port {BRIDGE_PORT})...{C['reset']}")
        time.sleep(1.5)
    else:
        _fail("无法连接 bridge VSOA Server (port 3001)，请确认终端2 bridge 已启动")
        sys.exit(1)

    threading.Thread(target=rpc_client.run, daemon=True).start()
    time.sleep(0.5)
    _ok("RPC Client 就绪")

    # ==================================================================
    # Step 3: 连接 bridge VSOA Server (3001) — 多用途订阅 Client
    #         合并版核心变化: 所有 Pub/Sub 都走 3001，无 3009
    #         同时订阅 /ctrl/ack + /device/update + /bridge/event
    #         以诊断 VSOA publish 是否工作
    # ==================================================================
    print()
    print(f"{C['bold']}[3/6] 准备 Pub/Sub 订阅 Client{C['reset']} (bridge VSOA Server port {BRIDGE_PORT})...")
    print(f"  {C['cyan']}[NEW] 合并版: ACK + 通知 统一走 3001 publish{C['reset']}")
    print(f"  {C['cyan']}       订阅: /ctrl/ack + /device/update + /bridge/event{C['reset']}")

    sub_client = vsoa.Client()
    received_acks: list[dict] = []
    received_updates: list[dict] = []
    received_events: list[dict] = []

    def on_message(cli, url, payload, quick):
        try:
            if payload and hasattr(payload, "param") and payload.param:
                data = dict(payload.param)
                url_str = url.url if hasattr(url, "url") else str(url)

                if "ctrl/ack" in url_str:
                    received_acks.append(data)
                    code = data.get("error_code", -1)
                    icon = f"{C['green']}✓{C['reset']}" if code == 0 else f"{C['red']}✗{C['reset']}"
                    print(f"  {icon} [/ctrl/ack] cmd_id={data.get('command_id','?')} "
                          f"code={code} msg={data.get('error_msg','?')}")
                elif "device/update" in url_str:
                    received_updates.append(data)
                    print(f"  {C['magenta']}[EVENT]{C['reset']} [/device/update] "
                          f"device={data.get('device_id','?')} type={data.get('type','?')}")
                elif "bridge/event" in url_str:
                    received_events.append(data)
                    print(f"  {C['magenta']}[EVENT]{C['reset']} [/bridge/event] "
                          f"event={data.get('event','?')} device={data.get('device_id','?')}")
        except Exception:
            pass

    sub_client.onmessage = on_message
    for attempt in range(1, 30):
        ret = sub_client.connect(f"vsoa://127.0.0.1:{BRIDGE_PORT}")
        if ret == 0:
            break
        if attempt == 1:
            print(f"  {C['dim']}等待 bridge VSOA Server (port {BRIDGE_PORT})...{C['reset']}")
        time.sleep(1.5)
    else:
        _fail("无法连接 bridge VSOA Server (Pub/Sub 订阅)")
        sys.exit(1)

    sub_client.subscribe("/ctrl/ack")
    sub_client.subscribe("/device/update")
    sub_client.subscribe("/bridge/event")
    threading.Thread(target=sub_client.run, daemon=True).start()
    time.sleep(0.5)
    _ok("Pub/Sub 订阅就绪 — /ctrl/ack + /device/update + /bridge/event")

    # ==================================================================
    # Step 4: 健康检查 + 查询端点
    # ==================================================================
    print()
    print(f"{C['bold']}[4/6] 健康检查 + 查询端点{C['reset']}")

    # 健康检查 — 字段名: service, status, uptime, devices, version
    hc = _rpc_fetch(rpc_client, "/bridge/health")
    if hc:
        svc = _safe_get(hc, "service")
        st = _safe_get(hc, "status")
        dev_count = _safe_get(hc, "devices")
        ver = _safe_get(hc, "version")
        uptime = _safe_get(hc, "uptime")
        _ok(f"bridge 健康: service={svc} status={st} devices={dev_count} "
            f"uptime={uptime}s version={ver}")
    else:
        _fail("健康检查失败")

    # 适配器列表 — 返回 {"count": N, "adapters": [...]}
    al = _rpc_fetch(rpc_client, "/adapter/list")
    if al:
        adapters = al.get("adapters", [])
        if isinstance(adapters, list) and adapters:
            names = [a.get("name", "?") if isinstance(a, dict) else str(a) for a in adapters]
            _ok(f"适配器: {', '.join(names)} ({al.get('count', len(adapters))} 个)")
        else:
            _warn("适配器列表为空或格式异常")
    else:
        _fail("适配器列表查询失败")

    # 当前设备列表 — 返回 {"count": N, "devices": [...]}
    dl = _rpc_fetch(rpc_client, "/device/list")
    current_dev_ids: list[str] = []
    if dl:
        dev_list = dl.get("devices", [])
        cnt = dl.get("count", len(dev_list))
        current_dev_ids = [
            d.get("device_id", "") if isinstance(d, dict) else str(d)
            for d in (dev_list if isinstance(dev_list, list) else [])
        ]
        if current_dev_ids:
            _ok(f"已注册 {cnt} 个设备:")
            for d in (dev_list if isinstance(dev_list, list) else []):
                did = d.get("device_id", "?") if isinstance(d, dict) else d
                dtype = d.get("type", "?") if isinstance(d, dict) else "?"
                src = d.get("source", "?") if isinstance(d, dict) else "?"
                print(f"      · {did:24s} type={dtype:12s} source={src}")
        else:
            _warn("当前注册表为空 — 需要先做上行上报注册设备")
    else:
        _warn("设备列表查询失败")

    print()
    print(f"  {C['yellow']}>>> 现在去终端4 (sim_device) 输入 'auto' 注册预设设备 <<<{C['reset']}")
    print(f"  {C['yellow']}    (或手动 'lora lora-node-01' + 'zigbee zb-sensor-01'){C['reset']}")
    input(f"  {C['bold']}确认设备已注册后，按 Enter 继续...{C['reset']}")

    # ==================================================================
    # Step 5: 重新查询设备注册表（注册后）
    # ==================================================================
    print()
    print(f"{C['bold']}[5/6] 设备注册后 — 重新查询注册表{C['reset']}")

    # 重新查设备列表
    dl2 = _rpc_fetch(rpc_client, "/device/list")
    post_dev_ids: list[str] = []
    if dl2:
        dev_list2 = dl2.get("devices", [])
        cnt2 = dl2.get("count", len(dev_list2))
        post_dev_ids = [
            d.get("device_id", "") if isinstance(d, dict) else str(d)
            for d in (dev_list2 if isinstance(dev_list2, list) else [])
        ]
        _ok(f"注册后设备数: {cnt2}")
        for did in post_dev_ids:
            print(f"      · {did}")

    # 查询 lora-node-01 详细数据
    lora_data = _rpc_fetch(rpc_client, "/device/lora-node-01/data")
    if lora_data:
        # 处理两种错误格式: {"error": "..."} 或 {"error_code": N, ...}
        if "error" in lora_data and "device_id" not in lora_data:
            _fail(f"查询 lora-node-01 失败: {lora_data.get('error')}")
        else:
            _ok(f"lora-node-01 数据: temp={lora_data.get('raw', {}).get('temperature')} "
                f"hum={lora_data.get('raw', {}).get('humidity')} "
                f"type={lora_data.get('type','?')} "
                f"source={lora_data.get('source','?')} "
                f"report_count={lora_data.get('report_count','?')}")

    # 查询 zb-sensor-01 详细数据
    zb_data = _rpc_fetch(rpc_client, "/device/zb-sensor-01/data")
    if zb_data:
        if "error" in zb_data and "device_id" not in zb_data:
            _fail(f"查询 zb-sensor-01 失败: {zb_data.get('error')}")
        else:
            _ok(f"zb-sensor-01 数据: temp={zb_data.get('raw', {}).get('temperature')} "
                f"hum={zb_data.get('raw', {}).get('humidity')} "
                f"type={zb_data.get('type','?')} "
                f"source={zb_data.get('source','?')}")

    # 查询不存在设备
    nodata = _rpc_fetch(rpc_client, "/device/nonexistent-999/data")
    if nodata:
        err_str = nodata.get("error", "")
        err_code = nodata.get("error_code")
        # 当前上行 vsoa_server 使用旧格式 {"error": "Device not found"}
        # 符合预期行为 — 记录状态
        if err_str == "Device not found":
            _ok(f"不存在设备 → {{\"error\": \"Device not found\"}} "
                f"{C['dim']}(上行侧待更新为数字错误码 1001){C['reset']}")
        elif err_code is not None:
            _ok(f"不存在设备 → error_code={err_code} (数字码格式)")
        else:
            _warn(f"不存在设备返回: {json.dumps(nodata, ensure_ascii=False)}")

    # 检查 /device/update 和 /bridge/event 是否收到 (VSOA publish 诊断)
    upd_count = len(received_updates)
    evt_count = len(received_events)
    if upd_count > 0:
        _ok(f"VSOA publish 可达: /device/update 收到 {upd_count} 条 ✓")
    else:
        _warn(f"/device/update 收到 0 条 — VSOA publish 可能未送达 "
              f"(server.publish 跨线程调用问题?)")

    if evt_count > 0:
        _ok(f"/bridge/event 收到 {evt_count} 条")
    else:
        _warn(f"/bridge/event 收到 0 条")

    # ==================================================================
    # Step 6: 运行测试套件
    # ==================================================================
    print()
    print(f"{C['bold']}[6/6] 运行测试套件{C['reset']}")
    print(f"{C['dim']}{'─' * 56}{C['reset']}")

    test_results: list[tuple[str, bool, str]] = []

    def check(desc: str, condition: bool, detail: str = "") -> None:
        test_results.append((desc, condition, detail))
        icon = f"{C['green']}✓{C['reset']}" if condition else f"{C['red']}✗{C['reset']}"
        d = f" — {detail}" if detail else ""
        print(f"  {icon} {desc}{d}")

    # ------------------------------------------------------------------
    # 阶段1: 上行设备注册
    # ------------------------------------------------------------------
    print()
    print(f"{C['bold']}─── 阶段1: 上行 — 设备注册表 ───{C['reset']}")

    check("设备注册表非空", len(post_dev_ids) > 0, f"当前 {len(post_dev_ids)} 个设备")
    check("lora-node-01 已注册", "lora-node-01" in post_dev_ids)
    check("zb-sensor-01 已注册", "zb-sensor-01" in post_dev_ids)
    check("单设备数据查询成功 (lora-node-01)",
          lora_data is not None and "device_id" in (lora_data or {}),
          f"source={_safe_get(lora_data, 'source')} type={_safe_get(lora_data, 'type')}")
    check("单设备数据查询成功 (zb-sensor-01)",
          zb_data is not None and "device_id" in (zb_data or {}),
          f"source={_safe_get(zb_data, 'source')} type={_safe_get(zb_data, 'type')}")

    # ------------------------------------------------------------------
    # 阶段2: 下行 RPC 通道
    # ------------------------------------------------------------------
    print()
    print(f"{C['bold']}─── 阶段2: 下行 RPC 通道 (同步 ACK) ───{C['reset']}")

    # 2a: 正常命令 — lora
    cmd_lora = {
        "command_id": f"rpc-lora-{int(time.time())}",
        "device_type": "lora",
        "device_id": "lora-node-01",
        "action": "set",
        "params": {"led": "on"},
    }
    h, p, s = rpc_client.fetch("/bridge/send_command",
                                payload=vsoa.Payload(param=cmd_lora), timeout=5.0)
    if s == vsoa.Client.CONNECT_OK:
        r = p.param
        ok_code0 = r.get("error_code") == 0
        has_trace = r.get("trace_id", "").startswith("br-")
        check("RPC LoRa set → code=0", ok_code0,
              f"code={r.get('error_code')} trace={r.get('trace_id','?')[:30]}...")
        check("  ACK 含 trace_id (br-格式)", has_trace, r.get("trace_id", "?"))
        check("  ack_level='bridge'", r.get("ack_level") == "bridge",
              f"ack_level={r.get('ack_level','?')}")

    # 2b: 正常命令 — zigbee
    cmd_zb = {
        "command_id": f"rpc-zb-{int(time.time())}",
        "device_type": "zigbee",
        "device_id": "zb-sensor-01",
        "action": "config",
        "params": {"interval": 60},
    }
    h, p, s = rpc_client.fetch("/bridge/send_command",
                                payload=vsoa.Payload(param=cmd_zb), timeout=5.0)
    if s == vsoa.Client.CONNECT_OK:
        r = p.param
        check("RPC Zigbee config → code=0", r.get("error_code") == 0,
              f"code={r.get('error_code')} trace={r.get('trace_id','?')[:30]}...")

    # ------------------------------------------------------------------
    # 阶段3: 注册表共用 + 边界情况 (RPC)
    # ------------------------------------------------------------------
    print()
    print(f"{C['bold']}─── 阶段3: 注册表共用 + 边界情况 ───{C['reset']}")

    # 未注册设备 → 2203
    cmd_noreg = {
        "command_id": f"rpc-noreg-{int(time.time())}",
        "device_type": "lora",
        "device_id": "device-not-in-registry",
        "action": "set",
        "params": {"led": "on"},
    }
    h, p, s = rpc_client.fetch("/bridge/send_command",
                                payload=vsoa.Payload(param=cmd_noreg), timeout=5.0)
    if s == vsoa.Client.CONNECT_OK:
        r = p.param
        check("未注册设备 → code=2203 (ERR_DEVICE_NOT_FOUND)",
              r.get("error_code") == 2203,
              f"code={r.get('error_code')} msg={r.get('error_msg','?')}")

    # 非法 device_type → 2003
    cmd_bad = {
        "command_id": f"rpc-bad-{int(time.time())}",
        "device_type": "bluetooth",
        "device_id": "lora-node-01",
        "action": "set",
        "params": {},
    }
    h, p, s = rpc_client.fetch("/bridge/send_command",
                                payload=vsoa.Payload(param=cmd_bad), timeout=5.0)
    if s == vsoa.Client.CONNECT_OK:
        r = p.param
        check("非法 device_type → code=2003", r.get("error_code") == 2003,
              f"code={r.get('error_code')}")

    # 缺少必填字段 → 2002
    cmd_missing = {
        "command_id": f"rpc-missing-{int(time.time())}",
        "device_type": "lora",
        # 缺少 device_id
        "action": "set",
        "params": {},
    }
    h, p, s = rpc_client.fetch("/bridge/send_command",
                                payload=vsoa.Payload(param=cmd_missing), timeout=5.0)
    if s == vsoa.Client.CONNECT_OK:
        r = p.param
        check("缺少必填字段 → code=2002", r.get("error_code") == 2002,
              f"code={r.get('error_code')} msg={r.get('error_msg','?')}")

    # 幂等去重
    dup_id = f"rpc-dup-{int(time.time())}"
    cmd_dup = {
        "command_id": dup_id,
        "device_type": "lora",
        "device_id": "lora-node-01",
        "action": "get",
        "params": {},
    }
    h1, p1, s1 = rpc_client.fetch("/bridge/send_command",
                                   payload=vsoa.Payload(param=cmd_dup), timeout=5.0)
    c1 = p1.param.get("error_code") if (s1 == vsoa.Client.CONNECT_OK and p1.param) else -1
    h2, p2, s2 = rpc_client.fetch("/bridge/send_command",
                                   payload=vsoa.Payload(param=cmd_dup), timeout=5.0)
    c2 = p2.param.get("error_code") if (s2 == vsoa.Client.CONNECT_OK and p2.param) else -1
    _info(f"去重测试: 第1次→{c1}, 第2次→{c2}")
    check("幂等去重: 第1次 code=0", c1 == 0, f"code={c1}")
    check("幂等去重: 第2次 code=2006", c2 == 2006, f"code={c2}")

    # traceId 格式
    cmd_tr = {
        "command_id": f"rpc-trace-{int(time.time())}",
        "device_type": "lora",
        "device_id": "lora-node-01",
        "action": "get",
        "params": {},
    }
    h, p, s = rpc_client.fetch("/bridge/send_command",
                                payload=vsoa.Payload(param=cmd_tr), timeout=5.0)
    if s == vsoa.Client.CONNECT_OK and p.param:
        trace_id = p.param.get("trace_id", "")
        parts = trace_id.split("-")
        valid_fmt = (trace_id.startswith("br-") and len(parts) >= 3
                     and len(parts[1]) == 8 and parts[2].isdigit())
        check("traceId 格式: br-{8位hex}-{毫秒时间戳}", valid_fmt, trace_id)

    # ------------------------------------------------------------------
    # 阶段4: 下行 Pub/Sub 通道
    # ------------------------------------------------------------------
    print()
    print(f"{C['bold']}─── 阶段4: 下行 Pub/Sub 通道 (异步 ACK) ───{C['reset']}")

    ack_before = len(received_acks)

    # 4a: 正常 Pub/Sub 命令
    cmd_pub = {
        "command_id": f"pub-ok-{int(time.time())}",
        "device_type": "lora",
        "device_id": "lora-node-01",
        "action": "set",
        "params": {"led": "off"},
    }
    biz_server.publish("/ctrl/cmd", vsoa.Payload(param=cmd_pub))
    _info(f"publish /ctrl/cmd → cmd_id={cmd_pub['command_id']} (等待 3s...)")
    time.sleep(3.0)

    pub_new = received_acks[ack_before:]
    if pub_new:
        last = pub_new[0]
        check("Pub/Sub LoRa set → ACK code=0", last.get("error_code") == 0,
              f"cmd_id={last.get('command_id','?')} code={last.get('error_code')}")
        check("  Pub/Sub ACK 含 trace_id", last.get("trace_id", "").startswith("br-"),
              last.get("trace_id", "?"))
    else:
        check("Pub/Sub LoRa set → 收到 ACK", False,
              f"未收到 /ctrl/ack (已等 3s, 共收到 {len(received_acks)} 条)")

    # 4b: Pub/Sub 非法命令
    ack_before2 = len(received_acks)
    cmd_pub_bad = {
        "command_id": f"pub-bad-{int(time.time())}",
        "device_type": "bluetooth",
        "device_id": "dev1",
        "action": "set",
        "params": {},
    }
    biz_server.publish("/ctrl/cmd", vsoa.Payload(param=cmd_pub_bad))
    _info(f"publish /ctrl/cmd (bad device_type=bluetooth, 等待 3s...)")
    time.sleep(3.0)

    bad_new = received_acks[ack_before2:]
    if bad_new:
        last = bad_new[0]
        check("Pub/Sub bad cmd → ACK code=2003", last.get("error_code") == 2003,
              f"code={last.get('error_code')}")
    else:
        check("Pub/Sub bad cmd → 收到 ACK", False,
              f"未收到 /ctrl/ack ({len(received_acks)} 条总计)")

    # ------------------------------------------------------------------
    # 阶段5: VSOA publish 诊断
    # ------------------------------------------------------------------
    print()
    print(f"{C['bold']}─── 阶段5: VSOA publish 诊断 ───{C['reset']}")

    # 桥内部 _vsoa_publish 从不同线程调用:
    #   - 上行处理 (MQTT 线程) → /device/update + /bridge/event
    #   - PubSub 处理 (VSOA Client 线程) → /ctrl/ack
    # 如果 /device/update 能收到但 /ctrl/ack 收不到 → PubSub 链路问题
    # 如果都收不到 → server.publish() 跨线程调用问题

    print(f"  /device/update 收到: {len(received_updates)} 条")
    print(f"  /bridge/event  收到: {len(received_events)} 条")
    print(f"  /ctrl/ack      收到: {len(received_acks)} 条")

    if len(received_updates) == 0 and len(received_acks) == 0:
        _warn("所有 VSOA publish 都未送达 — 可能是 server.publish() 跨线程调用问题")
        _warn("这需要修改 bridge 的 _vsoa_publish 实现 (用队列或回调注入到 VSOA 事件循环)")
    elif len(received_updates) > 0 and len(received_acks) == 0:
        _warn("/device/update 可达但 /ctrl/ack 不可达 — PubSub handler 链路问题")
        _warn("请检查终端2 bridge 日志是否有 [PubSub] 相关输出")
    elif len(received_acks) > 0:
        _ok("VSOA publish 工作正常！")

    # ------------------------------------------------------------------
    # 结果汇总
    # ------------------------------------------------------------------
    print()
    print(f"{C['bold']}{'=' * 60}{C['reset']}")
    print(f"{C['bold']}  验证结果汇总{C['reset']}")
    print(f"{C['bold']}{'=' * 60}{C['reset']}")
    print()

    passed = sum(1 for _, ok, _ in test_results if ok)
    failed = sum(1 for _, ok, _ in test_results if not ok)

    for desc, ok, detail in test_results:
        icon = f"{C['green']}✓{C['reset']}" if ok else f"{C['red']}✗{C['reset']}"
        d = f" [{C['dim']}{detail}{C['reset']}]" if detail else ""
        print(f"  {icon} {desc}{d}")

    print()
    print(f"  通过: {C['green']}{passed}{C['reset']}  /  "
          f"失败: {C['red']}{failed}{C['reset']}  /  "
          f"总计: {passed + failed}")
    print(f"  Pub/Sub ACK 累计: {C['cyan']}{len(received_acks)}{C['reset']} 条  |  "
          f"/device/update: {C['magenta']}{len(received_updates)}{C['reset']} 条  |  "
          f"/bridge/event: {C['magenta']}{len(received_events)}{C['reset']} 条")

    # 交叉验证
    print()
    print(f"{C['dim']}━━━━━━ 交叉验证 (其他终端) ━━━━━━{C['reset']}")
    print(f"  终端1 (mqtt_monitor) — 应看到:")
    print(f"    · 上行: bridge/uplink/lora/lora-node-01/data")
    print(f"    · 上行: bridge/uplink/zigbee/zb-sensor-01/data")
    print(f"    · 下行: bridge/downlink/lora/lora-node-01/set")
    print(f"    · 下行: bridge/downlink/zigbee/zb-sensor-01/config")
    print(f"  终端2 (bridge) — 关键日志:")
    print(f"    · [UPLINK] registered ... device=lora-node-01")
    print(f"    · [UPLINK] registered ... device=zb-sensor-01")
    print(f"    · [RPC] ... -> MQTT OK")
    print(f"    · [PubSub] ... -> MQTT OK (如果 PubSub 链路正常)")
    print()

    if failed > 0:
        print(f"{C['red']}有 {failed} 项失败，请检查终端2 bridge 日志排查。{C['reset']}")
        # 不exit 1，因为有些失败可能是已知问题（VSOA publish 跨线程等）
    else:
        print(f"{C['green']}全部 {passed} 项通过！桥接组件工作正常。{C['reset']}")


if __name__ == "__main__":
    main()
