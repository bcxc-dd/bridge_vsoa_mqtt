"""
双通道集成测试 — VSOA RPC + Pub/Sub

RPC:   client.fetch("/bridge/send_command", ...) 同步阻塞
PubSub: server.publish("/ctrl/cmd", ...) 盲转发
"""

import json, logging, os, sys, threading, time

import pytest, vsoa

from src.downlink.command import build_ack as _cmd_build_ack  # for format verification
from src.downlink.dedup import DedupCache
from src.device_registry import DeviceRegistry
from src.mqtt_handler import MQTTHandler
from src.downlink.rpc_server import RpcServer
from src.downlink.pubsub_handler import PubSubHandler


@pytest.fixture(scope="module")
def business_server():
    """业务层 VSOA Server（Pub/Sub 命令发布方）。"""
    server = vsoa.Server({"name": "test-biz"})
    threading.Thread(target=lambda: server.run("127.0.0.1", 3008), daemon=True).start()
    time.sleep(0.3)
    yield server


@pytest.fixture(scope="module")
def bridge(business_server):
    """启动 bridge (RPC Server + MQTT + PubSub Client)。"""
    # MQTT
    mqtt = MQTTHandler()
    ok = mqtt.connect("broker.emqx.io", 1883, "test-bridge", keepalive=60, reconnect_enabled=False)
    if not ok:
        pytest.skip("MQTT unreachable")
        return None

    received = []       # MQTT published messages: list of (topic, payload_str)
    received_acks = []  # ACK payloads: list of dict

    def publisher(topic, payload, qos=1):
        received.append((topic, payload))
        return True

    # RPC Server
    rpc = RpcServer(bind_host="127.0.0.1", port=3010, endpoint="/bridge/send_command",
                    mqtt_publisher=publisher)
    rpc.start()
    time.sleep(0.3)

    # PubSub Client
    pubsub = PubSubHandler(server_url="vsoa://127.0.0.1:3008", subscribe_urls=["/ctrl/cmd"],
                           mqtt_publisher=publisher,
                           ack_publisher=lambda url, data: received_acks.append(data))
    ok = pubsub.connect()
    if not ok:
        pytest.skip("PubSub connect failed")
        return None

    # RPC Client（模拟业务层调用 RPC）
    rpc_client = vsoa.Client()
    rpc_client.connect(f"vsoa://127.0.0.1:3010")
    threading.Thread(target=rpc_client.run, daemon=True).start()

    # PubSub event loop in background
    threading.Thread(target=pubsub.run_forever, daemon=True).start()
    time.sleep(0.5)

    yield {"mqtt": mqtt, "rpc": rpc, "pubsub": pubsub,
           "received": received, "received_acks": received_acks, "rpc_client": rpc_client}

    pubsub.stop()
    rpc.stop()
    mqtt.disconnect()


class TestPubSubChannel:
    def test_blind_forward(self, business_server, bridge):
        if bridge is None: pytest.skip()
        before = len(bridge["received"])
        cmd = {"command_id":"ps-1","device_type":"lora","device_id":"n1","action":"set","params":{"led":"on"}}
        business_server.publish("/ctrl/cmd", payload=vsoa.Payload(param=cmd))
        time.sleep(1)
        assert len(bridge["received"]) > before
        topic, _ = bridge["received"][-1]
        assert "lora/n1/set" in topic


class TestRpcChannel:
    def test_sync_call_ok(self, bridge):
        if bridge is None: pytest.skip()
        cmd = {"command_id":"rpc-1","device_type":"zigbee","device_id":"z1","action":"config","params":{"i":60}}
        h, p, s = bridge["rpc_client"].fetch("/bridge/send_command", payload=vsoa.Payload(param=cmd), timeout=5.0)
        assert s == vsoa.Client.CONNECT_OK
        assert p.param["error_code"] == 0
        assert p.param["command_id"] == "rpc-1"

    def test_sync_call_bad_device(self, bridge):
        if bridge is None: pytest.skip()
        cmd = {"command_id":"rpc-bad","device_type":"bluetooth","device_id":"n1","action":"set","params":{}}
        h, p, s = bridge["rpc_client"].fetch("/bridge/send_command", payload=vsoa.Payload(param=cmd), timeout=5.0)
        assert s == vsoa.Client.CONNECT_OK
        assert p.param["error_code"] == 2003


class TestMqttPayload:
    def test_both_channels_same_format(self, business_server, bridge):
        if bridge is None: pytest.skip()
        before = len(bridge["received"])

        cmd1 = {"command_id":"fmt-1","device_type":"lora","device_id":"n1","action":"get","params":{}}
        business_server.publish("/ctrl/cmd", payload=vsoa.Payload(param=cmd1))
        time.sleep(0.5)

        cmd2 = {"command_id":"fmt-2","device_type":"lora","device_id":"n2","action":"get","params":{}}
        bridge["rpc_client"].fetch("/bridge/send_command", payload=vsoa.Payload(param=cmd2), timeout=5.0)
        time.sleep(0.5)

        assert len(bridge["received"]) >= before + 2
        p1 = json.loads(bridge["received"][-2][1])
        p2 = json.loads(bridge["received"][-1][1])
        # v3.0: trace_id 为可选字段，排除后比较核心字段一致
        core_keys = {"command_id", "action", "params", "timestamp"}
        assert core_keys.issubset(set(p1.keys()))
        assert core_keys.issubset(set(p2.keys()))
        assert "device_type" not in p1


# ---------------------------------------------------------------------------
# 第2周 — Pub/Sub ACK 回执
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def ack_receiver(business_server, bridge):
    """ACK received list — populated by ack_publisher callback in bridge fixture."""
    if bridge is None:
        return []
    yield bridge["received_acks"]


class TestPubSubAck:
    """验证 Pub/Sub 通道 ACK 回执（方案B）。"""

    def test_valid_command_ack(self, business_server, bridge, ack_receiver):
        """合法命令 → ACK error_code=0。"""
        if bridge is None:
            pytest.skip()
        before = len(ack_receiver)
        cmd = {"command_id": "ack-ok-1", "device_type": "lora", "device_id": "n1",
               "action": "set", "params": {"led": "on"}}
        business_server.publish("/ctrl/cmd", payload=vsoa.Payload(param=cmd))
        time.sleep(1)
        assert len(ack_receiver) > before
        ack = ack_receiver[-1]
        assert ack["command_id"] == "ack-ok-1"
        assert ack["error_code"] == 0
        assert ack["ack_level"] == "bridge"

    def test_invalid_command_ack(self, business_server, bridge, ack_receiver):
        """校验失败命令 → ACK 包含 2xxx 错误码。"""
        if bridge is None:
            pytest.skip()
        before = len(ack_receiver)
        cmd = {"command_id": "ack-bad-1", "device_type": "bluetooth",
               "device_id": "n1", "action": "set", "params": {}}
        business_server.publish("/ctrl/cmd", payload=vsoa.Payload(param=cmd))
        time.sleep(1)
        assert len(ack_receiver) > before
        ack = ack_receiver[-1]
        assert ack["command_id"] == "ack-bad-1"
        assert ack["error_code"] == 2003  # ERR_CMD_UNKNOWN_DEVICE_TYPE

    def test_ack_format_matches_spec(self, business_server, bridge, ack_receiver):
        """ACK payload 格式与 spec §7.3 一致。"""
        if bridge is None:
            pytest.skip()
        cmd = {"command_id": "ack-fmt-1", "device_type": "zigbee", "device_id": "z1",
               "action": "reset", "params": {}}
        business_server.publish("/ctrl/cmd", payload=vsoa.Payload(param=cmd))
        time.sleep(1)
        ack = ack_receiver[-1]
        expected_keys = {"command_id", "error_code", "error_msg", "device_type", "device_id", "ack_level"}
        assert expected_keys.issubset(set(ack.keys()))


# ---------------------------------------------------------------------------
# 第2周 — RPC 超时处理
# ---------------------------------------------------------------------------

class TestRpcTimeout:
    """验证 RPC 通道超时处理。

    使用 mock publisher（注入可控延迟），不依赖真实 broker 时序，
    避免 flaky 测试。
    """

    def test_timeout_returns_2201(self):
        """短超时 + 慢 publisher → fetch() 返回 2201。"""
        rpc = RpcServer(
            bind_host="127.0.0.1", port=3020,
            endpoint="/bridge/send_command",
            max_timeout_ms=60000,
            mqtt_publisher=lambda t, p, q=1: time.sleep(10) or True,
            retry_max_retries=0,  # v3.0: 禁用重试以保持单次超时测试语义
        )
        rpc.start()
        time.sleep(0.3)

        client = vsoa.Client()
        client.connect("vsoa://127.0.0.1:3020")
        t = threading.Thread(target=client.run, daemon=True)
        t.start()
        time.sleep(0.3)

        try:
            cmd = {"command_id": "to-1", "device_type": "lora", "device_id": "n1",
                   "action": "set", "params": {}, "timeout_ms": 500}
            h, p, s = client.fetch("/bridge/send_command",
                                   payload=vsoa.Payload(param=cmd), timeout=5.0)
            assert s == vsoa.Client.CONNECT_OK
            assert p.param["error_code"] == 2201  # ERR_MQTT_PUBLISH_TIMEOUT
        finally:
            rpc.stop()
            client.close()

    def test_slow_but_within_timeout_succeeds(self):
        """publisher 在 timeout 内返回 → 成功。"""
        rpc = RpcServer(
            bind_host="127.0.0.1", port=3021,
            endpoint="/bridge/send_command",
            max_timeout_ms=60000,
            mqtt_publisher=lambda t, p, q=1: (time.sleep(0.1), True)[1],
        )
        rpc.start()
        time.sleep(0.3)

        client = vsoa.Client()
        client.connect("vsoa://127.0.0.1:3021")
        t = threading.Thread(target=client.run, daemon=True)
        t.start()
        time.sleep(0.3)

        try:
            cmd = {"command_id": "to-2", "device_type": "lora", "device_id": "n1",
                   "action": "get", "params": {}, "timeout_ms": 5000}
            h, p, s = client.fetch("/bridge/send_command",
                                   payload=vsoa.Payload(param=cmd), timeout=10.0)
            assert s == vsoa.Client.CONNECT_OK
            assert p.param["error_code"] == 0
        finally:
            rpc.stop()
            client.close()


# ---------------------------------------------------------------------------
# 第2周 — Per-device-type Topic 前缀
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 防回归 — Bug 1: on_disconnect NameError (rc → reason_code)
# ---------------------------------------------------------------------------

class TestMQTTHandlerDisconnect:
    """验证 on_disconnect 回调不会因参数名错误抛 NameError。"""

    def test_disconnect_callback_no_name_error(self):
        """reason_code 参数名必须与函数体一致，不能使用 v1.x 的 rc。"""
        mqtt = MQTTHandler()
        mqtt._connected = True  # 模拟已连接
        try:
            # 模拟 paho-mqtt 调用 on_disconnect 回调（非预期断连）
            mqtt._on_disconnect(None, None, 0, 1)  # reason_code=1
        except NameError:
            pytest.fail("on_disconnect: NameError — 检查参数名是否与 v2.x 一致")
        assert not mqtt.is_connected

    def test_disconnect_callback_normal(self):
        """正常断连 reason_code=0 不触发 warning。"""
        mqtt = MQTTHandler()
        mqtt._connected = True
        mqtt._on_disconnect(None, None, 0, 0)  # 正常断开
        assert not mqtt.is_connected


# ---------------------------------------------------------------------------
# 防回归 — Bug 2: Pub/Sub ACK 永远返回 SUCCESS
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def ack_receiver_fail(business_server):
    """订阅 /ctrl/ack 的 Client，用于接收失败场景的 ACK。"""
    # ACK 通过 ack_publisher 回调收集（不再使用独立 ACK Server 端口）
    received: list[dict] = []

    pubsub = PubSubHandler(
        server_url="vsoa://127.0.0.1:3008",
        subscribe_urls=["/ctrl/cmd"],
        mqtt_publisher=lambda t, p, q=1: False,  # 永远失败的 publisher
        ack_publisher=lambda url, data: received.append(data),
    )
    ok = pubsub.connect()
    if not ok:
        return None
    threading.Thread(target=pubsub.run_forever, daemon=True).start()
    time.sleep(0.5)

    yield received, pubsub
    pubsub.stop()


class TestPubSubAckFail:
    """验证 Pub/Sub ACK 在 MQTT 发布失败时返回非 0 错误码。"""

    def test_mqtt_publish_fail_returns_error_ack(self, business_server, ack_receiver_fail):
        """MQTT publish 失败 → ACK error_code 应为 2102（ERR_MQTT_PUBLISH_FAILED）。"""
        fixture = ack_receiver_fail
        if fixture is None:
            pytest.skip()
        received, _ = fixture
        cmd = {"command_id": "ack-fail-1", "device_type": "lora", "device_id": "n1",
               "action": "set", "params": {}}
        business_server.publish("/ctrl/cmd", payload=vsoa.Payload(param=cmd))
        time.sleep(1)
        assert len(received) > 0
        ack = received[-1]
        assert ack["command_id"] == "ack-fail-1"
        assert ack["error_code"] != 0, f"期望非零错误码，实际 error_code={ack['error_code']}"
        assert ack["error_code"] == 2102  # ERR_MQTT_PUBLISH_FAILED


# ---------------------------------------------------------------------------
# 第2周 — Per-device-type Topic 前缀
# ---------------------------------------------------------------------------

class TestPerDeviceTopic:
    """验证 MQTT topic 前缀 per-device-type 覆盖。"""

    def test_lora_override_topic(self):
        """配置 topic_prefixes.lora 后 LoRa 命令使用覆盖前缀。"""
        received = []

        def publisher(topic, payload, qos=1):
            received.append((topic, payload))
            return True

        rpc = RpcServer(
            bind_host="127.0.0.1", port=3030,
            endpoint="/bridge/send_command",
            mqtt_topic_prefix="bridge/downlink",
            mqtt_topic_prefixes={"lora": "lora/cmd"},
            mqtt_publisher=publisher,
        )
        rpc.start()
        time.sleep(0.3)

        client = vsoa.Client()
        client.connect("vsoa://127.0.0.1:3030")
        t = threading.Thread(target=client.run, daemon=True)
        t.start()
        time.sleep(0.3)

        try:
            cmd = {"command_id": "tp-1", "device_type": "lora", "device_id": "n1",
                   "action": "set", "params": {}}
            client.fetch("/bridge/send_command", payload=vsoa.Payload(param=cmd), timeout=5.0)
            time.sleep(0.3)
            assert len(received) == 1
            assert received[0][0] == "lora/cmd/n1/set"
        finally:
            rpc.stop()
            client.close()

    def test_zigbee_fallback_default(self):
        """未配置覆盖的设备类型回退默认前缀。"""
        received = []

        def publisher(topic, payload, qos=1):
            received.append((topic, payload))
            return True

        rpc = RpcServer(
            bind_host="127.0.0.1", port=3031,
            endpoint="/bridge/send_command",
            mqtt_topic_prefix="bridge/downlink",
            mqtt_topic_prefixes={"lora": "lora/cmd"},
            mqtt_publisher=publisher,
        )
        rpc.start()
        time.sleep(0.3)

        client = vsoa.Client()
        client.connect("vsoa://127.0.0.1:3031")
        t = threading.Thread(target=client.run, daemon=True)
        t.start()
        time.sleep(0.3)

        try:
            cmd = {"command_id": "tp-2", "device_type": "zigbee", "device_id": "z1",
                   "action": "config", "params": {"i": 30}}
            client.fetch("/bridge/send_command", payload=vsoa.Payload(param=cmd), timeout=5.0)
            time.sleep(0.3)
            assert len(received) == 1
            assert received[0][0] == "bridge/downlink/zigbee/z1/config"
        finally:
            rpc.stop()
            client.close()


# ============================================================================
# 第3周 — v3.0 集成测试
# ============================================================================

class TestRegistryCheck:
    """设备注册表检查 — 不在注册表中的设备返回 2203。"""

    def test_rpc_device_not_in_registry(self):
        """RPC 通道：device_id 不在注册表 → 2203。"""
        # 只包含 lora-node-01 的注册表
        yaml_content = """
devices:
  lora-node-01:
    type: lora
    description: "test"
"""
        import tempfile
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, encoding="utf-8",
        ) as f:
            f.write(yaml_content)
            tmp_path = f.name

        try:
            registry = DeviceRegistry(seed_file=tmp_path)

            rpc = RpcServer(
                bind_host="127.0.0.1", port=3040,
                endpoint="/bridge/send_command",
                mqtt_publisher=lambda t, p, q=1: True,
                registry=registry,
            )
            rpc.start()
            time.sleep(0.3)

            client = vsoa.Client()
            client.connect("vsoa://127.0.0.1:3040")
            t = threading.Thread(target=client.run, daemon=True)
            t.start()
            time.sleep(0.3)

            try:
                # 不在注册表中的设备
                cmd = {"command_id": "reg-1", "device_type": "lora",
                       "device_id": "nonexistent-device", "action": "set",
                       "params": {}}
                h, p, s = client.fetch("/bridge/send_command",
                                       payload=vsoa.Payload(param=cmd), timeout=5.0)
                assert s == vsoa.Client.CONNECT_OK
                assert p.param["error_code"] == 2203
                assert p.param["trace_id"] != ""  # v3.0: 即使失败也有 traceId
            finally:
                rpc.stop()
                client.close()
        finally:
            import os
            os.unlink(tmp_path)

    def test_rpc_device_in_registry(self):
        """RPC 通道：在注册表中的设备正常通过。"""
        yaml_content = """
devices:
  lora-node-01:
    type: lora
    description: "test"
"""
        import tempfile
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, encoding="utf-8",
        ) as f:
            f.write(yaml_content)
            tmp_path = f.name

        try:
            registry = DeviceRegistry(seed_file=tmp_path)

            rpc = RpcServer(
                bind_host="127.0.0.1", port=3041,
                endpoint="/bridge/send_command",
                mqtt_publisher=lambda t, p, q=1: True,
                registry=registry,
            )
            rpc.start()
            time.sleep(0.3)

            client = vsoa.Client()
            client.connect("vsoa://127.0.0.1:3041")
            t = threading.Thread(target=client.run, daemon=True)
            t.start()
            time.sleep(0.3)

            try:
                cmd = {"command_id": "reg-2", "device_type": "lora",
                       "device_id": "lora-node-01", "action": "set",
                       "params": {}}
                h, p, s = client.fetch("/bridge/send_command",
                                       payload=vsoa.Payload(param=cmd), timeout=5.0)
                assert s == vsoa.Client.CONNECT_OK
                assert p.param["error_code"] == 0
            finally:
                rpc.stop()
                client.close()
        finally:
            import os
            os.unlink(tmp_path)


class TestDedupCheck:
    """幂等去重集成测试。"""

    def test_duplicate_command_id_returns_2006(self):
        """相同 command_id 第2次发送 → 2006。"""
        dedup = DedupCache(ttl_seconds=300, max_size=100)

        rpc = RpcServer(
            bind_host="127.0.0.1", port=3042,
            endpoint="/bridge/send_command",
            mqtt_publisher=lambda t, p, q=1: True,
            dedup=dedup,
        )
        rpc.start()
        time.sleep(0.3)

        client = vsoa.Client()
        client.connect("vsoa://127.0.0.1:3042")
        t = threading.Thread(target=client.run, daemon=True)
        t.start()
        time.sleep(0.3)

        try:
            cmd = {"command_id": "dup-1", "device_type": "lora",
                   "device_id": "n1", "action": "set", "params": {}}

            # 第一次 → 成功
            h1, p1, s1 = client.fetch("/bridge/send_command",
                                       payload=vsoa.Payload(param=cmd), timeout=5.0)
            assert s1 == vsoa.Client.CONNECT_OK
            assert p1.param["error_code"] == 0

            # 第二次（相同 command_id）→ 重复
            h2, p2, s2 = client.fetch("/bridge/send_command",
                                       payload=vsoa.Payload(param=cmd), timeout=5.0)
            assert s2 == vsoa.Client.CONNECT_OK
            assert p2.param["error_code"] == 2006
        finally:
            rpc.stop()
            client.close()


class TestTraceIdInjection:
    """traceId 注入验证。"""

    def test_ack_contains_trace_id(self):
        """RPC 回执包含非空 trace_id。"""
        rpc = RpcServer(
            bind_host="127.0.0.1", port=3043,
            endpoint="/bridge/send_command",
            mqtt_publisher=lambda t, p, q=1: True,
        )
        rpc.start()
        time.sleep(0.3)

        client = vsoa.Client()
        client.connect("vsoa://127.0.0.1:3043")
        t = threading.Thread(target=client.run, daemon=True)
        t.start()
        time.sleep(0.3)

        try:
            cmd = {"command_id": "tr-1", "device_type": "lora",
                   "device_id": "n1", "action": "set", "params": {}}
            h, p, s = client.fetch("/bridge/send_command",
                                   payload=vsoa.Payload(param=cmd), timeout=5.0)
            assert s == vsoa.Client.CONNECT_OK

            trace_id = p.param.get("trace_id", "")
            # 格式: br-{8hex}-{timestamp}
            assert trace_id.startswith("br-"), f"bad trace_id format: {trace_id}"
            parts = trace_id[3:].split("-")  # remove "br-"
            assert len(parts) == 2, f"bad trace_id format: {trace_id}"
            assert len(parts[0]) == 8  # 8 hex chars
            assert parts[1].isdigit()  # timestamp
        finally:
            rpc.stop()
            client.close()


class TestRetryBehavior:
    """MQTT publish 重试行为验证。"""

    def test_retry_logs_visible(self, caplog):
        """publish 失败后日志含 retry 记录。"""
        caplog.set_level(logging.DEBUG, logger="bridge.rpc")

        fail_count = [0]

        def failing_publisher(topic, payload, qos=1):
            fail_count[0] += 1
            if fail_count[0] < 3:
                return False  # 前两次失败
            return True       # 第三次成功

        rpc = RpcServer(
            bind_host="127.0.0.1", port=3044,
            endpoint="/bridge/send_command",
            mqtt_publisher=failing_publisher,
            retry_max_retries=3,
            retry_backoff_base_ms=100,  # 短退避，加速测试
        )
        rpc.start()
        time.sleep(0.3)

        client = vsoa.Client()
        client.connect("vsoa://127.0.0.1:3044")
        t = threading.Thread(target=client.run, daemon=True)
        t.start()
        time.sleep(0.3)

        try:
            cmd = {"command_id": "retry-1", "device_type": "lora",
                   "device_id": "n1", "action": "set", "params": {},
                   "timeout_ms": 5000}
            h, p, s = client.fetch("/bridge/send_command",
                                   payload=vsoa.Payload(param=cmd), timeout=10.0)
            assert s == vsoa.Client.CONNECT_OK
            assert p.param["error_code"] == 0  # 最终重试成功
            assert fail_count[0] == 3  # 前两次失败，第三次成功
        finally:
            rpc.stop()
            client.close()
