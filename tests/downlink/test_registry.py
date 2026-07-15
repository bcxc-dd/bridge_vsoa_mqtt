"""设备注册表单元测试。

覆盖: 正常加载、lookup 命中/未命中、list_by_type 过滤、
      文件不存在抛异常、空设备列表、__contains__ 支持。
"""

import os
import tempfile

import pytest

from src.device_registry import DeviceInfo, DeviceRegistry


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------
def _write_yaml(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


# ---------------------------------------------------------------------------
# 测试类
# ---------------------------------------------------------------------------
class TestDeviceRegistryBasic:
    """正常加载与基本查询。"""

    def test_load_and_lookup_existing(self):
        """lookup 命中返回 DeviceInfo。"""
        yaml_content = """
devices:
  lora-node-01:
    type: lora
    description: "LoRa 测试节点 01"
  zb-sensor-01:
    type: zigbee
    description: "Zigbee 传感器 01"
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, encoding="utf-8"
        ) as f:
            f.write(yaml_content)
            tmp_path = f.name

        try:
            registry = DeviceRegistry(seed_file=tmp_path)
            info = registry.lookup("lora-node-01")
            assert info is not None
            assert info.device_id == "lora-node-01"
            assert info.type == "lora"
            assert info.description == "LoRa 测试节点 01"
        finally:
            os.unlink(tmp_path)

    def test_lookup_missing(self):
        """lookup 未命中返回 None。"""
        yaml_content = """
devices:
  lora-node-01:
    type: lora
    description: "LoRa"
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, encoding="utf-8"
        ) as f:
            f.write(yaml_content)
            tmp_path = f.name

        try:
            registry = DeviceRegistry(seed_file=tmp_path)
            assert registry.lookup("nonexistent") is None
        finally:
            os.unlink(tmp_path)

    def test_device_count(self):
        """device_count 返回正确数量。"""
        yaml_content = """
devices:
  n1:
    type: lora
    description: "d1"
  n2:
    type: lora
    description: "d2"
  n3:
    type: zigbee
    description: "d3"
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, encoding="utf-8"
        ) as f:
            f.write(yaml_content)
            tmp_path = f.name

        try:
            registry = DeviceRegistry(seed_file=tmp_path)
            assert registry.device_count == 3
        finally:
            os.unlink(tmp_path)


class TestDeviceRegistryFilter:
    """list_by_type 过滤。"""

    def test_list_by_type_lora(self):
        """list_by_type("lora") 只返回 lora 设备。"""
        yaml_content = """
devices:
  n1:
    type: lora
    description: "LoRa 1"
  n2:
    type: zigbee
    description: "Zigbee"
  n3:
    type: lora
    description: "LoRa 2"
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, encoding="utf-8"
        ) as f:
            f.write(yaml_content)
            tmp_path = f.name

        try:
            registry = DeviceRegistry(seed_file=tmp_path)
            lora_devs = registry.list_by_type("lora")
            assert len(lora_devs) == 2
            assert all(d.type == "lora" for d in lora_devs)
        finally:
            os.unlink(tmp_path)

    def test_list_by_type_empty(self):
        """list_by_type 无匹配时返回空列表。"""
        yaml_content = """
devices:
  n1:
    type: lora
    description: "LoRa"
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, encoding="utf-8"
        ) as f:
            f.write(yaml_content)
            tmp_path = f.name

        try:
            registry = DeviceRegistry(seed_file=tmp_path)
            assert registry.list_by_type("zigbee") == []
        finally:
            os.unlink(tmp_path)


class TestDeviceRegistryErrors:
    """异常路径。"""

    def test_file_not_found(self):
        """文件不存在时抛 FileNotFoundError。"""
        with pytest.raises(FileNotFoundError, match="设备注册表文件不存在"):
            DeviceRegistry(seed_file="__nonexistent_file__.yaml")

    def test_missing_devices_key(self):
        """缺少 devices 键时抛 ValueError。"""
        yaml_content = "other_key: 123"
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, encoding="utf-8"
        ) as f:
            f.write(yaml_content)
            tmp_path = f.name

        try:
            with pytest.raises(ValueError, match="顶层 'devices' 映射"):
                DeviceRegistry(seed_file=tmp_path)
        finally:
            os.unlink(tmp_path)

    def test_devices_not_dict(self):
        """devices 不是映射时抛 ValueError。"""
        yaml_content = "devices: [1, 2, 3]"
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, encoding="utf-8"
        ) as f:
            f.write(yaml_content)
            tmp_path = f.name

        try:
            with pytest.raises(ValueError, match="顶层 'devices' 映射"):
                DeviceRegistry(seed_file=tmp_path)
        finally:
            os.unlink(tmp_path)


class TestDeviceRegistryEdge:
    """边界条件。"""

    def test_empty_devices(self):
        """空设备列表正常加载，count=0。"""
        yaml_content = "devices: {}"
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, encoding="utf-8"
        ) as f:
            f.write(yaml_content)
            tmp_path = f.name

        try:
            registry = DeviceRegistry(seed_file=tmp_path)
            assert registry.device_count == 0
            assert registry.lookup("anything") is None
        finally:
            os.unlink(tmp_path)

    def test_contains_operator(self):
        """__contains__ 支持 'id' in registry 语法。"""
        yaml_content = """
devices:
  n1:
    type: lora
    description: "Test"
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, encoding="utf-8"
        ) as f:
            f.write(yaml_content)
            tmp_path = f.name

        try:
            registry = DeviceRegistry(seed_file=tmp_path)
            assert "n1" in registry
            assert "nonexistent" not in registry
        finally:
            os.unlink(tmp_path)

    def test_reserved_fields_are_none_by_default(self):
        """预留字段（dev_eui, mqtt_topic_template）缺省为 None。"""
        yaml_content = """
devices:
  n1:
    type: lora
    description: "Test"
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, encoding="utf-8"
        ) as f:
            f.write(yaml_content)
            tmp_path = f.name

        try:
            registry = DeviceRegistry(seed_file=tmp_path)
            info = registry.lookup("n1")
            assert info is not None
            assert info.dev_eui is None
            assert info.mqtt_topic_template is None
        finally:
            os.unlink(tmp_path)

    def test_reserved_fields_preserved(self):
        """预留字段有值时保留。"""
        yaml_content = """
devices:
  n1:
    type: lora
    description: "Test"
    dev_eui: "70B3D5ED00000001"
    mqtt_topic_template: "app/1/device/{dev_eui}/cmd"
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, encoding="utf-8"
        ) as f:
            f.write(yaml_content)
            tmp_path = f.name

        try:
            registry = DeviceRegistry(seed_file=tmp_path)
            info = registry.lookup("n1")
            assert info is not None
            assert info.dev_eui == "70B3D5ED00000001"
            assert info.mqtt_topic_template == "app/1/device/{dev_eui}/cmd"
        finally:
            os.unlink(tmp_path)

    def test_repr(self):
        """__repr__ 包含设备数量。"""
        yaml_content = """
devices:
  n1:
    type: lora
    description: "d1"
  n2:
    type: zigbee
    description: "d2"
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, encoding="utf-8"
        ) as f:
            f.write(yaml_content)
            tmp_path = f.name

        try:
            registry = DeviceRegistry(seed_file=tmp_path)
            r = repr(registry)
            assert "2 devices" in r
        finally:
            os.unlink(tmp_path)
