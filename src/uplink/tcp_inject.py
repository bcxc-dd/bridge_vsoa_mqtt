"""
TCP JSON Lines listener for offline uplink testing.

Listens on port 9090 and accepts JSON Lines commands that simulate
MQTT messages, so the full adapter pipeline can be exercised without
a real MQTT broker.
"""

from __future__ import annotations

import json
import logging
import socketserver
import threading
from typing import Any, Callable, Dict

logger = logging.getLogger(__name__)

# Callback: (topic: str, payload: dict[str, Any]) -> None
InjectCallback = Callable[[str, Dict[str, Any]], None]

MAX_JSON_LEN = 8192
MAX_TOPIC_LEN = 192


class _InjectHandler(socketserver.StreamRequestHandler):
    """Per-connection handler — reads JSON Lines, one command per line."""

    def handle(self) -> None:
        callback: InjectCallback | None = getattr(self.server, "_callback", None)
        if callback is None:
            return

        logger.info("[TCP:9090] client connected from %s", self.client_address)

        buf = b""
        while True:
            try:
                chunk = self.rfile.read(4096)
            except Exception:
                break
            if not chunk:
                break

            buf += chunk
            while b"\n" in buf:
                line_bytes, buf = buf.split(b"\n", 1)
                line = line_bytes.strip()
                if not line:
                    continue

                if len(line) > MAX_JSON_LEN:
                    logger.error("[TCP:9090] input line too long, dropped")
                    continue

                try:
                    line_str = line.decode("utf-8")
                except UnicodeDecodeError:
                    logger.warning("[TCP:9090] non-UTF8 input, dropped")
                    continue

                _process_line(line_str, callback)

        logger.info("[TCP:9090] client disconnected")


def _process_line(line: str, callback: InjectCallback) -> None:
    """Parse one JSON Lines command and delegate to *callback*."""
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        logger.warning("[TCP:9090] invalid JSON: %s", line[:120])
        return

    if not isinstance(obj, dict):
        logger.warning("[TCP:9090] command is not a JSON object")
        return

    cmd = obj.get("cmd", "")
    if not cmd:
        logger.warning("[TCP:9090] missing cmd: %s", line[:120])
        return

    if cmd == "ping":
        logger.info("[TCP:9090] ping")
        return

    if cmd in ("mqtt_message", "mqtt_publish"):
        topic = obj.get("topic", "")
        payload = obj.get("payload")
        if not topic:
            logger.warning("[TCP:9090] mqtt_message missing topic")
            return
        if payload is None:
            logger.warning("[TCP:9090] mqtt_message missing payload")
            return
        # payload can be a str (JSON string) or a dict
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                logger.warning("[TCP:9090] mqtt_message payload is not valid JSON")
                return
        if not isinstance(payload, dict):
            logger.warning("[TCP:9090] mqtt_message payload is not a JSON object")
            return

        callback(topic, payload)
        return

    if cmd == "update_device":
        device_id = obj.get("device_id", "")
        if not device_id:
            logger.warning("[TCP:9090] update_device missing device_id")
            return
        topic = f"bridge/uplink/bridge/{device_id}/data"
        callback(topic, obj)
        return

    if cmd == "update_status":
        device_id = obj.get("device_id", "")
        if not device_id:
            logger.warning("[TCP:9090] update_status missing device_id")
            return
        topic = f"bridge/uplink/bridge/{device_id}/status"
        callback(topic, obj)
        return

    logger.warning("[TCP:9090] unsupported cmd=%s", cmd)


class TcpInjectServer:
    """TCP JSON Lines listener for offline testing.

    Usage::

        server = TcpInjectServer(callback, port=9090)
        server.start()
        # ... inject data via netcat / telnet / test script ...
        server.stop()
    """

    def __init__(
        self,
        callback: InjectCallback,
        host: str = "0.0.0.0",
        port: int = 9090,
    ) -> None:
        self._callback = callback
        self._host = host
        self._port = port
        self._server: socketserver.ThreadingTCPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._server = socketserver.ThreadingTCPServer(
            (self._host, self._port),
            _InjectHandler,
            bind_and_activate=True,
        )
        self._server._callback = self._callback  # type: ignore[attr-defined]
        self._server.allow_reuse_address = True

        self._thread = threading.Thread(
            target=self._server.serve_forever,
            daemon=True,
            name="tcp-inject",
        )
        self._thread.start()
        logger.info("[TCP:9090] JSON Lines injection listening on %s:%d", self._host, self._port)

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
