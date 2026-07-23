"""VSOA RPC management endpoints for SceneEngine."""

from __future__ import annotations

import logging
from typing import Any

import vsoa

from src.error_codes import (
    ERR_SCENE_ALREADY_EXISTS, ERR_SCENE_INVALID_RULE, ERR_SCENE_NOT_FOUND,
    ERR_SCENE_PERSIST_FAILED, SUCCESS,
)

from .models import SceneValidationError

logger = logging.getLogger("bridge.scene.rpc")


class SceneRpcServer:
    ENDPOINTS = ("list", "get", "add", "update", "delete", "enable", "disable", "sensors", "reload")

    def __init__(self, engine: Any, server: vsoa.Server) -> None:
        self.engine = engine
        self.server = server
        self.running = False

    def start(self) -> None:
        for operation in self.ENDPOINTS:
            self._register(operation)
        self.running = True
        logger.info("[SCENE] registered %d VSOA RPC endpoints", len(self.ENDPOINTS))

    def stop(self) -> None:
        self.running = False

    def _register(self, operation: str) -> None:
        @self.server.command(f"/scene/{operation}")
        def handler(cli: Any, req: Any, payload: Any) -> None:
            data = {}
            try:
                if payload and getattr(payload, "param", None):
                    data = dict(payload.param)
                result = self._dispatch(operation, data)
                self._reply(cli, req, SUCCESS, "ok", result)
            except KeyError as exc:
                code = ERR_SCENE_ALREADY_EXISTS if str(exc).strip("'") == "already_exists" else ERR_SCENE_NOT_FOUND
                self._reply(cli, req, code.code, code.message, None)
            except SceneValidationError as exc:
                self._reply(cli, req, ERR_SCENE_INVALID_RULE.code, str(exc), None)
            except (OSError, ValueError) as exc:
                self._reply(cli, req, ERR_SCENE_PERSIST_FAILED.code, str(exc), None)
            except Exception as exc:
                logger.exception("[SCENE] RPC %s failed", operation)
                self._reply(cli, req, ERR_SCENE_INVALID_RULE.code, str(exc), None)

    def _dispatch(self, operation: str, data: dict[str, Any]) -> Any:
        scene_id = str(data.get("scene_id", ""))
        if operation == "list": return {"scenes": self.engine.list_rules()}
        if operation == "get": return self.engine.get_rule(scene_id) or (_raise("not_found"))
        if operation == "add": return self.engine.add_rule(data)
        if operation == "update": return self.engine.update_rule(scene_id, data)
        if operation == "delete": self.engine.remove_rule(scene_id); return {"scene_id": scene_id}
        if operation == "enable": return self.engine.set_enabled(scene_id, True)
        if operation == "disable": return self.engine.set_enabled(scene_id, False)
        if operation == "sensors": return {"sensors": self.engine.sensors()}
        if operation == "reload": self.engine.reload(); return {"count": len(self.engine.list_rules())}
        raise SceneValidationError("未知场景操作")

    @staticmethod
    def _reply(cli: Any, req: Any, code: int, message: str, data: Any) -> None:
        cli.reply(req.seqno, vsoa.Payload(param={"error_code": code, "error_msg": message, "data": data}))


def _raise(message: str) -> Any:
    raise KeyError(message)
