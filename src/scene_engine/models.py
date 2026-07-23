"""Data models and validation for smart scene rules."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


VALID_OPERATORS = {"gt", "gte", "lt", "lte", "eq", "neq"}
VALID_LOGIC = {"and", "or"}
VALID_TRIGGER_MODES = {"level", "edge"}
VALID_DEVICE_TYPES = {"lora", "zigbee", "wifi", "generic"}
VALID_ACTIONS = {"set", "reset", "capture"}


class SceneValidationError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class SceneCondition:
    sensor: str
    operator: str
    value: float | bool
    trigger_mode: str = "level"
    device_id: str = ""
    hold_seconds: int = 0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SceneCondition":
        sensor = str(data.get("sensor", "")).strip()
        trigger_mode = str(data.get("trigger_mode") or ("edge" if sensor == "pir" else "level"))
        condition = cls(
            sensor, str(data.get("operator", "")), data.get("value"), trigger_mode,
            str(data.get("device_id", "")).strip(), int(data.get("hold_seconds", 0)),
        )
        if not condition.sensor:
            raise SceneValidationError("条件缺少 sensor")
        if condition.operator not in VALID_OPERATORS:
            raise SceneValidationError(f"不支持的比较运算符: {condition.operator}")
        if condition.trigger_mode not in VALID_TRIGGER_MODES:
            raise SceneValidationError(f"不支持的触发模式: {condition.trigger_mode}")
        if condition.hold_seconds < 0 or condition.hold_seconds > 86400:
            raise SceneValidationError("持续满足时间必须在 0-86400 秒之间")
        if isinstance(condition.value, str):
            text = condition.value.strip().lower()
            if text in {"true", "false"}:
                condition.value = text == "true"
            else:
                try:
                    condition.value = float(text)
                except ValueError as exc:
                    raise SceneValidationError("条件阈值必须是数字或布尔值") from exc
        if not isinstance(condition.value, (int, float, bool)):
            raise SceneValidationError("条件阈值必须是数字或布尔值")
        return condition


@dataclass
class SceneAction:
    device_type: str
    device_id: str
    action: str
    params: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SceneAction":
        action = cls(
            str(data.get("device_type", "")), str(data.get("device_id", "")).strip(),
            str(data.get("action", "")), dict(data.get("params") or {}),
        )
        if action.device_type not in VALID_DEVICE_TYPES:
            raise SceneValidationError("动作 device_type 必须是 lora、zigbee、wifi 或 generic")
        if not action.device_id:
            raise SceneValidationError("动作缺少 device_id")
        if action.action not in VALID_ACTIONS:
            raise SceneValidationError("动作 action 必须是 set、reset 或 capture")
        if not action.params:
            raise SceneValidationError("动作 params 不能为空")
        return action


@dataclass
class SceneRule:
    scene_id: str
    name: str
    description: str
    condition_logic: str
    conditions: list[SceneCondition]
    actions: list[SceneAction]
    enabled: bool = True
    duration_seconds: int = 0
    cooldown_seconds: int = 60
    schedule_start: str | None = None
    schedule_end: str | None = None
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    last_triggered_at: str | None = None

    @classmethod
    def from_dict(
        cls, data: dict[str, Any], *, default_cooldown: int = 60,
        max_conditions: int = 10, max_actions: int = 10,
    ) -> "SceneRule":
        conditions = [SceneCondition.from_dict(item) for item in data.get("conditions") or []]
        actions = [SceneAction.from_dict(item) for item in data.get("actions") or []]
        if not conditions or len(conditions) > max_conditions:
            raise SceneValidationError(f"条件数量必须为 1-{max_conditions}")
        if not actions or len(actions) > max_actions:
            raise SceneValidationError(f"动作数量必须为 1-{max_actions}")
        logic = str(data.get("condition_logic", "and")).lower()
        if logic not in VALID_LOGIC:
            raise SceneValidationError("condition_logic 必须是 and 或 or")
        duration = int(data.get("duration_seconds", 0))
        cooldown = int(data.get("cooldown_seconds", duration or default_cooldown))
        if duration < 0 or cooldown < 0:
            raise SceneValidationError("持续时间和冷却期不能为负数")
        now = utc_now()
        return cls(
            scene_id=str(data.get("scene_id") or f"scene-{uuid4().hex[:12]}"),
            name=str(data.get("name", "")).strip(),
            description=str(data.get("description", "")).strip(),
            condition_logic=logic, conditions=conditions, actions=actions,
            enabled=bool(data.get("enabled", True)), duration_seconds=duration,
            cooldown_seconds=cooldown, schedule_start=data.get("schedule_start") or None,
            schedule_end=data.get("schedule_end") or None,
            created_at=str(data.get("created_at") or now), updated_at=now,
            last_triggered_at=data.get("last_triggered_at") or None,
        ).validated()

    def validated(self) -> "SceneRule":
        if not self.scene_id or not self.name:
            raise SceneValidationError("scene_id 和 name 不能为空")
        return self

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
