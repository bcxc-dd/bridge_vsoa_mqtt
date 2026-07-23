"""Sensor definitions and robust value extraction."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class SensorDef:
    sensor_id: str
    label: str
    unit: str
    value_type: Callable[[Any], Any]
    aliases: tuple[str, ...]

    def public(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("value_type", None)
        data["data_type"] = "bool" if self.value_type is bool else "float"
        return data


SENSOR_DEFINITIONS: dict[str, SensorDef] = {
    "temperature": SensorDef("temperature", "温度", "°C", float, ("temperature", "temp")),
    "humidity": SensorDef("humidity", "空气湿度", "%", float, ("humidity", "hum")),
    "soil_moisture": SensorDef("soil_moisture", "土壤湿度", "%", float, ("soil_moisture", "soil_humidity", "soil")),
    "precipitation": SensorDef("precipitation", "降水", "mm", float, ("precipitation", "rain", "rainfall")),
    "illuminance": SensorDef("illuminance", "光照", "lux", float, ("illuminance", "light", "lux", "brightness")),
    "smoke": SensorDef("smoke", "烟雾", "", bool, ("smoke", "smoke_alarm", "gas")),
    "pir": SensorDef("pir", "人体红外", "", bool, ("pir", "motion", "presence", "occupancy", "infrared")),
    "voltage": SensorDef("voltage", "电压", "V", float, ("voltage", "volt", "battery_voltage", "supply_voltage")),
}


def _to_bool(value: Any) -> bool:
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "on", "yes", "1", "alarm", "detected", "occupied"}:
            return True
        if text in {"false", "off", "no", "0", "normal", "clear", "unoccupied"}:
            return False
    return bool(value)


def extract_sensor(device_info: Any, sensor_id: str) -> float | bool | None:
    definition = SENSOR_DEFINITIONS.get(sensor_id)
    if definition is None:
        return None
    raw = getattr(device_info, "raw", {}) or {}
    sources = [raw]
    for key in ("data", "telemetry", "decoded", "object"):
        child = raw.get(key) if isinstance(raw, dict) else None
        if isinstance(child, dict):
            sources.append(child)
    for source in sources:
        for alias in definition.aliases:
            if alias not in source:
                continue
            value = source[alias]
            try:
                return _to_bool(value) if definition.value_type is bool else float(value)
            except (TypeError, ValueError):
                return None
    direct = getattr(device_info, sensor_id, None)
    if direct is not None:
        try:
            return _to_bool(direct) if definition.value_type is bool else float(direct)
        except (TypeError, ValueError):
            return None
    return None


def list_sensors() -> list[dict[str, Any]]:
    return [item.public() for item in SENSOR_DEFINITIONS.values()]
