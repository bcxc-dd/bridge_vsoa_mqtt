"""
统一设备注册表 — 上行自动填充 + 下行查询校验。

合并自:
  - bridge-uplink/src/device_registry.py  (上行 DeviceData + upsert)
  - bridge/src/downlink/device_registry.py (下行 DeviceInfo + YAML 加载)

合并后: DeviceInfo 同时包含上行传感器字段和下行预留字段。
        DeviceRegistry 同时支持 upsert() 和 lookup()。
        线程安全（threading.Lock）。
        可选 seed_file 用于 YAML 预装设备（向后兼容下行测试）。
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import yaml

try:
    from .uplink.adapters.base import UplinkReport
except ImportError:
    from src.uplink.adapters.base import UplinkReport  # type: ignore[assignment]

logger = logging.getLogger("bridge.registry")


# ---------------------------------------------------------------------------
# DeviceInfo
# ---------------------------------------------------------------------------

@dataclass
class DeviceInfo:
    """设备注册表中的设备条目。

    上行 upsert() 时填充传感器字段（temperature/humidity/signal 等），
    下行 lookup() 时返回完整信息（含 device_id/type 用于校验）。
    """

    # --- 核心标识 ---
    device_id: str = ""
    type: str = ""                          # "lora" | "zigbee" | "generic" | "multi"
    status: str = "online"
    description: str = ""                   # 人类可读描述（seed_file 加载时填充）

    # --- 上行填充 ---
    name: str = ""
    source: str = ""                        # "lora" | "zigbee" | "generic" | "mqtt"
    adapter: str = ""                       # 使用的适配器名称
    unit: str = ""
    last_topic: str = ""

    registered_at: int = 0                  # 首次注册时间（Unix epoch ms）
    timestamp: int = 0                      # 最后上报时间（Unix epoch ms）
    report_count: int = 0

    # --- 载荷透传 ---
    # 所有传感器测量值通过此字段传递，bridge 不建模具体传感器类型
    raw: dict[str, Any] = field(default_factory=dict)

    # --- 下行预留 ---
    mqtt_topic_template: str | None = None  # 设备级 topic 模板（预留）
    dev_eui: str | None = None              # LoRaWAN DevEUI（预留）
    app_id: str | None = None               # ChirpStack application ID（从上行 topic 自动获取）

    def to_json(self) -> dict[str, Any]:
        """序列化为 VSOA 查询响应格式。"""
        d: dict[str, Any] = {
            "device_id":     self.device_id,
            "name":          self.name or self.device_id,
            "type":          self.type,
            "status":        self.status,
            "source":        self.source,
            "adapter":       self.adapter,
            "timestamp":     self.timestamp,
            "registered_at": self.registered_at,
            "report_count":  self.report_count,
            "last_topic":    self.last_topic,
        }
        if self.unit:
            d["unit"] = self.unit
        d["raw"] = self.raw
        return d

    def __repr__(self) -> str:
        return f"DeviceInfo({self.device_id!r}, type={self.type!r})"


# ---------------------------------------------------------------------------
# DeviceRegistry
# ---------------------------------------------------------------------------

class DeviceRegistry:
    """线程安全的统一设备注册表。

    Usage:
        # 空注册表（上行自动填充）
        registry = DeviceRegistry(max_devices=64)

        # 从 YAML 预装设备（可选，向后兼容）
        registry = DeviceRegistry(seed_file="devices.yaml")

        # 上行: 自动注册/更新
        dev, created = registry.upsert(report)

        # 下行: 查询校验
        info = registry.lookup("lora-node-01")   # DeviceInfo | None

        # 查询
        all_devices = registry.list_all()
        lora_devices = registry.list_by_type("lora")
    """

    def __init__(
        self,
        max_devices: int = 64,
        seed_file: str | None = None,
    ) -> None:
        """初始化设备注册表。

        Args:
            max_devices: 最大设备容量（默认 64）。
            seed_file: 可选的 YAML 种子文件路径，用于预装设备。
                       格式: devices.yaml（顶层 'devices' 映射）。
        """
        self._max = max_devices
        self._lock = threading.Lock()
        self._devices: dict[str, DeviceInfo] = {}

        # 可选: 从 YAML 种子文件预装设备
        if seed_file is not None:
            self._load_seed(seed_file)

    # ------------------------------------------------------------------
    # 上行接口
    # ------------------------------------------------------------------

    def upsert(self, report: UplinkReport) -> tuple[DeviceInfo | None, bool]:
        """插入或更新设备（来自上行 MQTT 消息）。

        Returns:
            ``(device, created)`` — *created* 为 True 时是新设备。
            注册表满时返回 ``(None, False)``。
        """
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

        with self._lock:
            existing = self._devices.get(report.device_id)

            if existing is not None:
                self._merge(report, existing, now_ms)
                existing.report_count += 1
                return existing, False

            # 新设备
            if len(self._devices) >= self._max:
                logger.error(
                    "[REGISTRY] Device limit reached (%d), dropping device_id=%s",
                    self._max, report.device_id,
                )
                return None, False

            dev = DeviceInfo()
            dev.device_id = report.device_id
            dev.registered_at = now_ms
            self._merge(report, dev, now_ms)
            dev.report_count = 1
            self._devices[dev.device_id] = dev

            logger.info(
                "[REGISTRY] registered device='%s' type=%s source=%s",
                dev.device_id, dev.type, dev.source,
            )
            return dev, True

    # ------------------------------------------------------------------
    # 查询接口（上行 + 下行共用）
    # ------------------------------------------------------------------

    def lookup(self, device_id: str) -> DeviceInfo | None:
        """按 device_id 查询设备信息。O(1)。"""
        with self._lock:
            return self._devices.get(device_id)

    def list_all(self) -> list[DeviceInfo]:
        """列出所有已注册设备。"""
        with self._lock:
            return list(self._devices.values())

    def list_by_type(self, device_type: str) -> list[DeviceInfo]:
        """按设备类型过滤。

        Args:
            device_type: "lora" | "zigbee" | "generic" 等。
        """
        with self._lock:
            return [d for d in self._devices.values() if d.type == device_type]

    @property
    def count(self) -> int:
        """已注册设备总数。"""
        with self._lock:
            return len(self._devices)

    # 别名（兼容下行旧接口）
    @property
    def device_count(self) -> int:
        return self.count

    def __contains__(self, device_id: str) -> bool:
        with self._lock:
            return device_id in self._devices

    def __repr__(self) -> str:
        return f"DeviceRegistry({self.count} devices)"

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    @staticmethod
    def _merge(report: UplinkReport, dev: DeviceInfo, now_ms: int) -> None:
        """将 UplinkReport 字段合并到 DeviceInfo。"""
        dev.source = report.source or dev.source
        dev.adapter = report.adapter or dev.adapter
        dev.type = report.type or dev.type
        dev.status = report.status or dev.status
        dev.last_topic = report.topic or dev.last_topic
        dev.timestamp = report.timestamp if report.timestamp > 0 else now_ms

        if report.name:
            dev.name = report.name
        if report.unit:
            dev.unit = report.unit

        # 载荷透传：增量合并，保留历史字段
        if report.raw:
            dev.raw.update(report.raw)

        # ChirpStack 扩展字段（方案 B）
        if getattr(report, "dev_eui", ""):
            dev.dev_eui = report.dev_eui
        if getattr(report, "app_id", ""):
            dev.app_id = report.app_id

    def _load_seed(self, source_path: str) -> None:
        """从 YAML 种子文件预装设备（向后兼容下行 YAML 加载）。"""
        if not os.path.isfile(source_path):
            raise FileNotFoundError(f"设备注册表文件不存在: {source_path}")

        with open(source_path, "r", encoding="utf-8") as f:
            raw: dict[str, Any] = yaml.safe_load(f) or {}

        devices_raw = raw.get("devices")
        if not isinstance(devices_raw, dict):
            raise ValueError(
                f"设备注册表格式错误: 缺少顶层 'devices' 映射 (source={source_path})"
            )

        for device_id, entry in devices_raw.items():
            if not isinstance(entry, dict):
                raise ValueError(
                    f"设备 '{device_id}' 的条目不是合法的字典 (source={source_path})"
                )
            dev = DeviceInfo(
                device_id=device_id,
                type=str(entry.get("type", "")),
                description=str(entry.get("description", "")),
                mqtt_topic_template=entry.get("mqtt_topic_template"),
                dev_eui=entry.get("dev_eui"),
            )
            self._devices[device_id] = dev

        logger.info("[REGISTRY] Seeded %d devices from %s", len(self._devices), source_path)
