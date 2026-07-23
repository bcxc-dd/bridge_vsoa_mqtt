"""Thread-safe, threshold-oriented smart scene engine."""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from src.downlink.command import build_mqtt_message
from src.trace_id import generate_trace_id

from .models import SceneRule, SceneValidationError, utc_now
from .persistence import SceneStore
from .sensors import SENSOR_DEFINITIONS, extract_sensor, list_sensors

logger = logging.getLogger("bridge.scene")


class SceneEngine:
    def __init__(
        self, rules_path: str, mqtt_publisher: Callable[[str, str, int], bool] | None,
        mqtt_topic_prefix: str, mqtt_topic_prefixes: dict | None, registry: Any,
        *, default_cooldown_seconds: int = 60, max_rules: int = 100,
        max_conditions_per_rule: int = 10, max_actions_per_rule: int = 10,
        event_publisher: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self.store = SceneStore(Path(rules_path))
        self.publisher = mqtt_publisher
        self.topic_prefix = mqtt_topic_prefix
        self.topic_prefixes = mqtt_topic_prefixes or {}
        self.registry = registry
        self.default_cooldown = default_cooldown_seconds
        self.max_rules = max_rules
        self.max_conditions = max_conditions_per_rule
        self.max_actions = max_actions_per_rule
        self.event_publisher = event_publisher
        self._rules: dict[str, SceneRule] = {}
        self._lock = threading.RLock()
        self._edge_state: dict[tuple[str, str, str], bool] = {}
        self._level_since: dict[tuple[str, str, str], float] = {}
        self._last_trigger_monotonic: dict[str, float] = {}
        self._timers: set[threading.Timer] = set()
        self.running = False

    @property
    def limits(self) -> dict[str, int]:
        return {
            "default_cooldown": self.default_cooldown,
            "max_conditions": self.max_conditions,
            "max_actions": self.max_actions,
        }

    def start(self) -> None:
        self.reload()
        self.running = True
        logger.info("[SCENE] engine started: %d rules", len(self._rules))

    def stop(self) -> None:
        with self._lock:
            self.running = False
            for timer in list(self._timers):
                timer.cancel()
            self._timers.clear()

    def reload(self) -> None:
        rules = self.store.load(
            default_cooldown=self.default_cooldown,
            max_conditions=self.max_conditions, max_actions=self.max_actions,
        )
        if len(rules) > self.max_rules:
            raise SceneValidationError(f"场景数量超过上限 {self.max_rules}")
        with self._lock:
            self._rules = {rule.scene_id: rule for rule in rules}

    def list_rules(self) -> list[dict[str, Any]]:
        with self._lock:
            return [rule.to_dict() for rule in self._rules.values()]

    def get_rule(self, scene_id: str) -> dict[str, Any] | None:
        with self._lock:
            rule = self._rules.get(scene_id)
            return rule.to_dict() if rule else None

    def add_rule(self, data: dict[str, Any]) -> dict[str, Any]:
        rule = SceneRule.from_dict(data, default_cooldown=self.default_cooldown, max_conditions=self.max_conditions, max_actions=self.max_actions)
        self._validate_sensors(rule)
        with self._lock:
            if rule.scene_id in self._rules:
                raise KeyError("already_exists")
            if len(self._rules) >= self.max_rules:
                raise SceneValidationError(f"场景数量超过上限 {self.max_rules}")
            self._rules[rule.scene_id] = rule
            self._save_locked()
        return rule.to_dict()

    def update_rule(self, scene_id: str, data: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            previous = self._rules.get(scene_id)
            if previous is None:
                raise KeyError("not_found")
        merged = {**previous.to_dict(), **data, "scene_id": scene_id, "created_at": previous.created_at}
        rule = SceneRule.from_dict(merged, default_cooldown=self.default_cooldown, max_conditions=self.max_conditions, max_actions=self.max_actions)
        self._validate_sensors(rule)
        with self._lock:
            self._rules[scene_id] = rule
            self._save_locked()
        return rule.to_dict()

    def remove_rule(self, scene_id: str) -> None:
        with self._lock:
            if scene_id not in self._rules:
                raise KeyError("not_found")
            del self._rules[scene_id]
            self._save_locked()

    def set_enabled(self, scene_id: str, enabled: bool) -> dict[str, Any]:
        with self._lock:
            rule = self._rules.get(scene_id)
            if rule is None:
                raise KeyError("not_found")
            rule.enabled = enabled
            rule.updated_at = utc_now()
            self._save_locked()
            return rule.to_dict()

    def sensors(self) -> list[dict[str, Any]]:
        return list_sensors()

    def on_uplink(self, device_info: Any) -> list[dict[str, Any]]:
        if not self.running:
            return []
        triggered = []
        with self._lock:
            for rule in list(self._rules.values()):
                if not rule.enabled or not self._schedule_active(rule):
                    continue
                matches, snapshot = self._matches(rule, device_info)
                if not matches or self._cooling_down(rule):
                    continue
                actions_sent = [result for action in rule.actions if (result := self._send_action(rule, action, stop=False))]
                if not actions_sent:
                    continue
                rule.last_triggered_at = utc_now()
                rule.updated_at = rule.last_triggered_at
                self._last_trigger_monotonic[rule.scene_id] = time.monotonic()
                event = {
                    "event": "scene_triggered", "scene_id": rule.scene_id, "scene_name": rule.name,
                    "device_id": device_info.device_id, "triggered_at": rule.last_triggered_at,
                    "conditions_snapshot": snapshot, "actions_sent": actions_sent,
                    "trace_id": actions_sent[0]["trace_id"],
                }
                triggered.append(event)
                if self.event_publisher:
                    self.event_publisher("/scene/trigger", event)
                if rule.duration_seconds > 0:
                    self._schedule_stop(rule)
                logger.info("[SCENE] triggered scene=%s source_device=%s actions=%d", rule.scene_id, device_info.device_id, len(actions_sent))
            if triggered:
                self._save_locked()
        return triggered

    def _matches(self, rule: SceneRule, device_info: Any) -> tuple[bool, dict[str, Any]]:
        results = []
        snapshot = {}
        for condition in rule.conditions:
            if condition.device_id and condition.device_id != device_info.device_id:
                results.append(False)
                snapshot[f"{condition.device_id}:{condition.sensor}"] = None
                continue
            actual = extract_sensor(device_info, condition.sensor)
            snapshot[f"{condition.device_id or device_info.device_id}:{condition.sensor}"] = actual
            current = actual is not None and self._compare(actual, condition.operator, condition.value)
            key = (rule.scene_id, device_info.device_id, condition.sensor)
            if condition.trigger_mode == "level" and condition.hold_seconds > 0:
                if current:
                    started = self._level_since.setdefault(key, time.monotonic())
                    current = time.monotonic() - started >= condition.hold_seconds
                else:
                    self._level_since.pop(key, None)
            if condition.trigger_mode == "edge":
                previous = self._edge_state.get(key, False)
                self._edge_state[key] = current
                current = current and not previous
            results.append(current)
        return ((all(results) if rule.condition_logic == "and" else any(results)), snapshot)

    @staticmethod
    def _compare(actual: Any, operator: str, threshold: Any) -> bool:
        operations = {
            "gt": lambda: actual > threshold, "gte": lambda: actual >= threshold,
            "lt": lambda: actual < threshold, "lte": lambda: actual <= threshold,
            "eq": lambda: actual == threshold, "neq": lambda: actual != threshold,
        }
        try:
            return bool(operations[operator]())
        except (KeyError, TypeError):
            return False

    def _cooling_down(self, rule: SceneRule) -> bool:
        last = self._last_trigger_monotonic.get(rule.scene_id)
        return last is not None and time.monotonic() - last < rule.cooldown_seconds

    def _send_action(self, rule: SceneRule, action: Any, *, stop: bool) -> dict[str, Any] | None:
        if action.action == "capture":
            return {
                "device_id": action.device_id, "device_type": action.device_type,
                "action": action.action, "topic": "platform://camera/save",
                "params": dict(action.params), "command_id": f"scene-{uuid4().hex}",
                "trace_id": f"scene-{uuid4().hex}", "stop": False,
            }
        if self.publisher is None:
            logger.warning("[SCENE] no MQTT publisher; action skipped")
            return None
        target = self.registry.lookup(action.device_id) if self.registry else None
        if target is None:
            logger.warning("[SCENE] target device not registered: %s", action.device_id)
            return None
        params = self._stop_params(action.params) if stop else dict(action.params)
        command_id = f"scene-{uuid4().hex}"
        trace_id = generate_trace_id()
        command = {
            "command_id": command_id, "device_type": action.device_type,
            "device_id": action.device_id, "action": action.action,
            "params": params, "timestamp": utc_now(),
        }
        topic, payload = build_mqtt_message(command, self.topic_prefix, self.topic_prefixes, trace_id=trace_id)
        if not self.publisher(topic, payload, 1):
            logger.error("[SCENE] publish failed scene=%s topic=%s", rule.scene_id, topic)
            return None
        return {"device_id": action.device_id, "device_type": action.device_type, "action": action.action, "topic": topic, "params": params, "command_id": command_id, "trace_id": trace_id, "stop": stop}

    def _schedule_stop(self, rule: SceneRule) -> None:
        def callback() -> None:
            try:
                with self._lock:
                    current = self._rules.get(rule.scene_id)
                    if current:
                        results = [self._send_action(current, action, stop=True) for action in current.actions]
                        if self.event_publisher:
                            self.event_publisher("/scene/trigger", {"event": "scene_stopped", "scene_id": current.scene_id, "scene_name": current.name, "triggered_at": utc_now(), "actions_sent": [item for item in results if item]})
            finally:
                with self._lock:
                    self._timers.discard(timer)
        timer = threading.Timer(rule.duration_seconds, callback)
        timer.daemon = True
        self._timers.add(timer)
        timer.start()

    @staticmethod
    def _stop_params(params: dict[str, Any]) -> dict[str, Any]:
        if params.get("motor") == "rotate":
            return {"motor": "off"}
        stop = {}
        mapping = {"on": "off", "blink": "off", "beep": "off", "start": "stop", True: False}
        for key, value in params.items():
            stop[key] = mapping.get(value, value)
        return stop

    @staticmethod
    def _parse_schedule(value: str | None, now: datetime) -> tuple[str, Any] | None:
        if not value:
            return None
        text = value.strip()
        try:
            hour, minute = [int(part) for part in text.split(":", 1)]
            return "time", hour * 60 + minute
        except (ValueError, TypeError):
            pass
        try:
            return "datetime", datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            logger.warning("[SCENE] unsupported schedule ignored: %s", value)
            return None

    def _schedule_active(self, rule: SceneRule) -> bool:
        now = datetime.now().astimezone()
        start = self._parse_schedule(rule.schedule_start, now)
        end = self._parse_schedule(rule.schedule_end, now)
        if start and start[0] == "datetime" and now < start[1]:
            return False
        if end and end[0] == "datetime" and now > end[1]:
            return False
        if start and end and start[0] == end[0] == "time":
            current = now.hour * 60 + now.minute
            return start[1] <= current <= end[1] if start[1] <= end[1] else current >= start[1] or current <= end[1]
        return True

    def _validate_sensors(self, rule: SceneRule) -> None:
        unknown = [condition.sensor for condition in rule.conditions if condition.sensor not in SENSOR_DEFINITIONS]
        if unknown:
            raise SceneValidationError(f"未知传感指标: {', '.join(unknown)}")

    def _save_locked(self) -> None:
        self.store.save(list(self._rules.values()))
