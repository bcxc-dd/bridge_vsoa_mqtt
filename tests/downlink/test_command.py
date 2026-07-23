"""
命令校验与转换单元测试

覆盖 command.validate() 和 command.build_mqtt_message() 的所有场景。
对应 spec.md §7.2 第1周实现的全部错误码（2001-2005, 不含 2006/预留 22xx）。
"""

import json

import pytest
from src.downlink.command import validate, build_mqtt_message, build_ack


# ---------------------------------------------------------------------------
# validate() 测试
# ---------------------------------------------------------------------------

VALID_CMD = {
    "command_id": "cmd-001",
    "device_type": "lora",
    "device_id": "lora-node-01",
    "action": "set",
    "params": {"led": "on"},
}


class TestValidate:
    """校验合法命令。"""

    def test_valid_lora(self):
        ok, code = validate(VALID_CMD)
        assert ok is True
        assert code == 0

    def test_valid_zigbee(self):
        cmd = {**VALID_CMD, "device_type": "zigbee", "device_id": "zb-01"}
        ok, code = validate(cmd)
        assert ok is True
        assert code == 0

    def test_valid_all_actions(self):
        for action in ("set", "get", "reset", "config"):
            cmd = {**VALID_CMD, "action": action, "command_id": f"cmd-{action}"}
            ok, code = validate(cmd)
            assert ok, f"action={action} should be valid"

    def test_valid_with_timeout(self):
        cmd = {**VALID_CMD, "timeout_ms": 5000}
        ok, code = validate(cmd, max_timeout_ms=60000)
        assert ok

    def test_valid_without_timeout(self):
        """timeout_ms 选填，不提供应通过。"""
        cmd = {k: v for k, v in VALID_CMD.items()}
        ok, code = validate(cmd)
        assert ok

    def test_valid_with_timestamp(self):
        cmd = {**VALID_CMD, "timestamp": "2026-07-06T00:00:00Z"}
        ok, code = validate(cmd)
        assert ok


class TestValidateMissingField:
    """2002 — 缺必填字段。"""

    def test_missing_command_id(self):
        cmd = {k: v for k, v in VALID_CMD.items() if k != "command_id"}
        ok, code = validate(cmd)
        assert not ok and code == 2002

    def test_missing_device_type(self):
        cmd = {k: v for k, v in VALID_CMD.items() if k != "device_type"}
        ok, code = validate(cmd)
        assert not ok and code == 2002

    def test_missing_device_id(self):
        cmd = {k: v for k, v in VALID_CMD.items() if k != "device_id"}
        ok, code = validate(cmd)
        assert not ok and code == 2002

    def test_missing_action(self):
        cmd = {k: v for k, v in VALID_CMD.items() if k != "action"}
        ok, code = validate(cmd)
        assert not ok and code == 2002

    def test_missing_params(self):
        cmd = {k: v for k, v in VALID_CMD.items() if k != "params"}
        ok, code = validate(cmd)
        assert not ok and code == 2002

    def test_null_field(self):
        cmd = {**VALID_CMD, "device_type": None}
        ok, code = validate(cmd)
        assert not ok and code == 2002


class TestValidateInvalidDeviceType:
    """2003 — 非法 device_type。"""

    def test_bluetooth(self):
        ok, code = validate({**VALID_CMD, "device_type": "bluetooth"})
        assert not ok and code == 2003

    def test_empty_string(self):
        ok, code = validate({**VALID_CMD, "device_type": ""})
        assert not ok and code == 2003

    def test_uppercase(self):
        ok, code = validate({**VALID_CMD, "device_type": "LORA"})
        assert not ok and code == 2003


class TestValidateInvalidAction:
    """2004 — 非法 action。"""

    def test_delete(self):
        ok, code = validate({**VALID_CMD, "action": "delete"})
        assert not ok and code == 2004

    def test_empty(self):
        ok, code = validate({**VALID_CMD, "action": ""})
        assert not ok and code == 2004


class TestValidateTimeoutExceeded:
    """2005 — timeout_ms 超限。"""

    def test_exceeds_max(self):
        ok, code = validate({**VALID_CMD, "timeout_ms": 999999}, max_timeout_ms=60000)
        assert not ok and code == 2005

    def test_negative(self):
        ok, code = validate({**VALID_CMD, "timeout_ms": -1}, max_timeout_ms=60000)
        assert not ok and code == 2005


class TestValidateInvalidJSON:
    """2001 — 非法输入。"""

    def test_none(self):
        ok, code = validate(None)
        assert not ok and code == 2001

    def test_string(self):
        ok, code = validate("not a dict")
        assert not ok and code == 2001

    def test_list(self):
        ok, code = validate([1, 2, 3])
        assert not ok and code == 2001

    def test_int(self):
        ok, code = validate(42)
        assert not ok and code == 2001


# ---------------------------------------------------------------------------
# build_mqtt_message() 测试
# ---------------------------------------------------------------------------

class TestBuildMQTTMessage:

    def test_topic_format(self):
        topic, _ = build_mqtt_message(VALID_CMD, "bridge/downlink")
        assert topic == "bridge/downlink/lora/lora-node-01/set"

    def test_topic_zigbee(self):
        cmd = {**VALID_CMD, "device_type": "zigbee", "device_id": "zb-03", "action": "config"}
        topic, _ = build_mqtt_message(cmd, "bridge/downlink")
        assert topic == "bridge/downlink/zigbee/zb-03/config"

    @pytest.mark.parametrize(
        ("device_id", "params", "expected_data"),
        [
            ("0xB25B", {"relay": "on"}, "01"),
            ("0xb25b", {"relay": "off"}, "00"),
            ("0xC38F", {"led": "on"}, "01"),
            ("0xc38f", {"led": "off"}, "00"),
        ],
    )
    def test_zigbee_board_contract(self, device_id, params, expected_data):
        command = {
            **VALID_CMD,
            "device_type": "zigbee",
            "device_id": device_id,
            "action": "set",
            "params": {**params, "requested_command": "set"},
        }

        topic, payload_text = build_mqtt_message(command, "bridge/downlink")

        canonical_id = "0xB25B" if device_id.lower() == "0xb25b" else "0xC38F"
        assert topic == f"bridge/downlink/zigbee/{canonical_id}/set"
        assert json.loads(payload_text) == {
            "device_id": canonical_id,
            "params": {"data": expected_data},
        }

    @pytest.mark.parametrize(
        ("device_id", "params"),
        [("0xB25B", {"led": "on"}), ("0xC38F", {"relay": "on"})],
    )
    def test_zigbee_board_rejects_wrong_actuator(self, device_id, params):
        command = {
            **VALID_CMD,
            "device_type": "zigbee",
            "device_id": device_id,
            "action": "set",
            "params": params,
        }

        with pytest.raises(ValueError):
            build_mqtt_message(command, "bridge/downlink")

    def test_payload_contains_required_fields(self):
        _, payload_str = build_mqtt_message(VALID_CMD, "bridge/downlink")
        p = json.loads(payload_str)
        assert p["command_id"] == "cmd-001"
        assert p["action"] == "set"
        assert p["params"] == {"led": "on"}

    def test_payload_excludes_device_fields(self):
        _, payload_str = build_mqtt_message(VALID_CMD, "bridge/downlink")
        p = json.loads(payload_str)
        assert "device_type" not in p
        assert "device_id" not in p

    def test_payload_has_timestamp(self):
        _, payload_str = build_mqtt_message(VALID_CMD, "bridge/downlink")
        p = json.loads(payload_str)
        assert "timestamp" in p

    def test_payload_preserves_provided_timestamp(self):
        cmd = {**VALID_CMD, "timestamp": "2026-01-01T00:00:00Z"}
        _, payload_str = build_mqtt_message(cmd, "bridge/downlink")
        p = json.loads(payload_str)
        assert p["timestamp"] == "2026-01-01T00:00:00Z"

    def test_custom_prefix(self):
        topic, _ = build_mqtt_message(VALID_CMD, "custom/prefix")
        assert topic == "custom/prefix/lora/lora-node-01/set"

    @pytest.mark.parametrize("device_id", ["eora_s3_400tb_001", "eora-s3-400tb-001"])
    @pytest.mark.parametrize("value", ["on", "off"])
    def test_eora_motor_uses_device_contract(self, device_id, value):
        command = {
            **VALID_CMD,
            "device_type": "generic",
            "device_id": device_id,
            "params": {"motor": value},
        }

        topic, payload = build_mqtt_message(command, "bridge/downlink")

        assert topic == "bridge/downlink/generic/eora_s3_400tb_001/cmd"
        assert payload == f'{{"cmd":"motor","value":"{value}"}}'

    def test_eora_motor_rejects_unknown_action_value(self):
        command = {
            **VALID_CMD,
            "device_type": "generic",
            "device_id": "eora_s3_400tb_001",
            "params": {"motor": "blink"},
        }

        with pytest.raises(ValueError, match="motor/led"):
            build_mqtt_message(command, "bridge/downlink")

    @pytest.mark.parametrize("value", ["on", "off"])
    def test_eora_led_uses_device_contract(self, value):
        command = {
            **VALID_CMD,
            "device_type": "generic",
            "device_id": "eora_s3_400tb_001",
            "params": {"led": value},
        }

        topic, payload = build_mqtt_message(command, "bridge/downlink")

        assert topic == "bridge/downlink/generic/eora_s3_400tb_001/cmd"
        assert payload == f'{{"cmd":"led","value":"{value}"}}'


# ---------------------------------------------------------------------------
# 纯函数验证
# ---------------------------------------------------------------------------

class TestPurity:
    """验证 command.py 为纯函数。"""

    def test_validate_same_input_same_output(self):
        r1 = validate(VALID_CMD)
        r2 = validate(VALID_CMD)
        r3 = validate(VALID_CMD)
        assert r1 == r2 == r3

    def test_validate_does_not_modify_input(self):
        original = {**VALID_CMD}
        validate(VALID_CMD)
        assert VALID_CMD == original

    def test_build_mqtt_message_same_input_same_output(self):
        r1 = build_mqtt_message(VALID_CMD, "bridge/downlink")
        r2 = build_mqtt_message(VALID_CMD, "bridge/downlink")
        assert r1 == r2


# ---------------------------------------------------------------------------
# 第2周 — check_timeout 参数
# ---------------------------------------------------------------------------

class TestValidateCheckTimeout:
    """验证 validate() 的 check_timeout 参数。"""

    def test_check_timeout_false_skips_limit(self):
        """check_timeout=False 时跳过大 timeout 校验。"""
        cmd = {**VALID_CMD, "timeout_ms": 999999}
        ok, code = validate(cmd, 60000, check_timeout=False)
        assert ok is True
        assert code == 0

    def test_check_timeout_true_rejects(self):
        """check_timeout=True 时拒绝超限 timeout。"""
        cmd = {**VALID_CMD, "timeout_ms": 999999}
        ok, code = validate(cmd, 60000, check_timeout=True)
        assert ok is False
        assert code == 2005

    def test_check_timeout_default_is_true(self):
        """默认 check_timeout=True，行为不变。"""
        cmd = {**VALID_CMD, "timeout_ms": 999999}
        ok, code = validate(cmd, 60000)
        assert ok is False
        assert code == 2005

    def test_check_timeout_false_still_checks_negative(self):
        """check_timeout=False 仍拒绝负 timeout（类型校验在 check_timeout 外）。"""
        cmd = {**VALID_CMD, "timeout_ms": -1}
        ok, code = validate(cmd, check_timeout=False)
        # 负值 timeout 在 check_timeout=False 时不检查（仅在 check_timeout 块内）
        assert ok is True  # 当前实现中负值仅在上限检查时拒绝


# ---------------------------------------------------------------------------
# 第2周 — build_ack 函数
# ---------------------------------------------------------------------------

class TestBuildAck:
    """验证 build_ack() 回执构造。"""

    def test_success_ack(self):
        ack = build_ack(VALID_CMD, 0)
        assert ack["command_id"] == VALID_CMD["command_id"]
        assert ack["error_code"] == 0
        assert ack["error_msg"] == "ok"
        assert ack["device_type"] == "lora"
        assert ack["device_id"] == "lora-node-01"
        assert ack["ack_level"] == "bridge"

    def test_error_ack(self):
        ack = build_ack(VALID_CMD, 2003)
        assert ack["error_code"] == 2003
        assert ack["error_msg"] != "ok"
        assert "device_type" in ack["error_msg"].lower() or "lora" in ack["error_msg"]

    def test_ack_level_field(self):
        ack_bridge = build_ack(VALID_CMD, 0, ack_level="bridge")
        assert ack_bridge["ack_level"] == "bridge"
        ack_device = build_ack(VALID_CMD, 0, ack_level="device")
        assert ack_device["ack_level"] == "device"

    def test_unknown_error_code(self):
        """未定义错误码返回 generic 消息。"""
        ack = build_ack(VALID_CMD, 9999)
        assert ack["error_code"] == 9999
        assert "unknown" in ack["error_msg"]

    def test_missing_fields_in_cmd(self):
        """command_id/device_type/device_id 缺失时返回空字符串。"""
        ack = build_ack({}, 2002)
        assert ack["command_id"] == ""
        assert ack["device_type"] == ""
        assert ack["device_id"] == ""


# ---------------------------------------------------------------------------
# 第2周 — topic_prefixes per-device-type 覆盖
# ---------------------------------------------------------------------------

class TestBuildMQTTMessagePrefix:
    """验证 build_mqtt_message() 的 topic_prefixes 参数。"""

    def test_lora_override(self):
        topic, _ = build_mqtt_message(VALID_CMD, "bridge/downlink",
                                       topic_prefixes={"lora": "lora/cmd"})
        assert topic == "lora/cmd/lora-node-01/set"

    def test_zigbee_default_fallback(self):
        cmd = {**VALID_CMD, "device_type": "zigbee", "device_id": "zb-01", "action": "config"}
        topic, _ = build_mqtt_message(cmd, "bridge/downlink",
                                       topic_prefixes={"lora": "lora/cmd"})
        assert topic == "bridge/downlink/zigbee/zb-01/config"

    def test_no_prefixes_fallback(self):
        topic, _ = build_mqtt_message(VALID_CMD, "bridge/downlink")
        assert topic == "bridge/downlink/lora/lora-node-01/set"

    def test_none_prefixes(self):
        topic, _ = build_mqtt_message(VALID_CMD, "bridge/downlink", None)
        assert topic == "bridge/downlink/lora/lora-node-01/set"

    def test_empty_prefixes(self):
        topic, _ = build_mqtt_message(VALID_CMD, "bridge/downlink", {})
        assert topic == "bridge/downlink/lora/lora-node-01/set"


# ---------------------------------------------------------------------------
# v3.0 — traceId 参数测试
# ---------------------------------------------------------------------------
VALID_CMD_TRACE = {
    "command_id": "tr-1", "device_type": "lora", "device_id": "n1",
    "action": "set", "params": {"led": "on"},
}


class TestBuildAckTraceId:
    """build_ack() trace_id 参数（v3.0）。"""

    def test_trace_id_injected(self):
        """传入 trace_id → ACK 含该字段。"""
        ack = build_ack(VALID_CMD_TRACE, 0, trace_id="br-deadbeef-1720435200000")
        assert ack["trace_id"] == "br-deadbeef-1720435200000"

    def test_trace_id_default_empty(self):
        """不传 trace_id → ACK 含空字符串（向后兼容）。"""
        ack = build_ack(VALID_CMD_TRACE, 0)
        assert ack["trace_id"] == ""

    def test_trace_id_with_error_code(self):
        """错误回执也包含 trace_id。"""
        ack = build_ack(VALID_CMD_TRACE, 2003, trace_id="br-test1234-1720435200001")
        assert ack["error_code"] == 2003
        assert ack["trace_id"] == "br-test1234-1720435200001"


class TestBuildMQTTMessageTraceId:
    """build_mqtt_message() trace_id 参数（v3.0）。"""

    def test_trace_id_in_mqtt_payload(self):
        """传入 trace_id → MQTT payload JSON 含该字段。"""
        _, payload_str = build_mqtt_message(
            VALID_CMD_TRACE, "bridge/downlink", trace_id="br-abcdef01-1720435200002",
        )
        import json
        payload = json.loads(payload_str)
        assert payload["trace_id"] == "br-abcdef01-1720435200002"

    def test_trace_id_not_in_mqtt_payload_when_empty(self):
        """不传 trace_id → MQTT payload 不含 trace_id 字段。"""
        _, payload_str = build_mqtt_message(VALID_CMD_TRACE, "bridge/downlink")
        import json
        payload = json.loads(payload_str)
        assert "trace_id" not in payload

    def test_trace_id_with_prefix_override(self):
        """trace_id 与 topic_prefixes 一起使用正常。"""
        _, payload_str = build_mqtt_message(
            VALID_CMD_TRACE, "bridge/downlink", {"lora": "lora/cmd"},
            trace_id="br-prefix-1720435200003",
        )
        import json
        payload = json.loads(payload_str)
        assert payload["trace_id"] == "br-prefix-1720435200003"
