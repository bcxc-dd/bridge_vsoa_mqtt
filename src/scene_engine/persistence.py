"""Atomic YAML persistence for scene rules."""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from .models import SceneRule


class SceneStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self, **limits) -> list[SceneRule]:
        if not self.path.exists():
            return []
        with self.path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        scenes = data.get("scenes", [])
        if not isinstance(scenes, list):
            raise ValueError("scenes.yaml 顶层 scenes 必须是列表")
        return [SceneRule.from_dict(item, **limits) for item in scenes]

    def save(self, rules: list[SceneRule]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        with temp.open("w", encoding="utf-8", newline="\n") as handle:
            yaml.safe_dump(
                {"scenes": [rule.to_dict() for rule in rules]}, handle,
                allow_unicode=True, sort_keys=False,
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, self.path)
