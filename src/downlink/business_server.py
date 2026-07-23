"""Local VSOA business server used by the bidirectional bridge."""

from __future__ import annotations

import logging
import threading
import time
from urllib.parse import urlparse

import vsoa


logger = logging.getLogger("bridge.business_server")


class BusinessVsoaServer:
    """Provide /ctrl/cmd Pub/Sub locally when no external service exists."""

    LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}

    def __init__(self, server_url: str, bind_host: str, bind_port: int,
                 auto_start: bool = True) -> None:
        parsed = urlparse(server_url)
        self.server_url = server_url
        self.host = parsed.hostname or ""
        self.bind_host = bind_host
        self.bind_port = bind_port
        self.auto_start = auto_start
        self.server = None
        self.thread = None
        self.owns_server = False

    def _can_connect(self, timeout: float = 0.5) -> bool:
        client = vsoa.Client()
        try:
            return client.connect(self.server_url, timeout=timeout) == vsoa.Client.CONNECT_OK
        except Exception:
            return False
        finally:
            client.close()

    def start_if_needed(self) -> bool:
        if self._can_connect():
            logger.info("[BUSINESS] reusing existing VSOA Server: %s", self.server_url)
            return True
        if not self.auto_start:
            logger.error("[BUSINESS] auto-start disabled and server unavailable: %s", self.server_url)
            return False
        if self.host not in self.LOOPBACK_HOSTS:
            logger.error("[BUSINESS] remote VSOA Server unavailable: %s", self.server_url)
            return False
        if not self.bind_host or not 1 <= self.bind_port <= 65535:
            logger.error("[BUSINESS] invalid bind address: %s:%s", self.bind_host, self.bind_port)
            return False

        server = vsoa.Server({"name": "MQTT-VSOA Bridge Business Server"})
        self.server = server
        self.owns_server = True

        def on_data(cli, url, payload, quick) -> None:
            try:
                server.publish(url, payload)
            except Exception:
                logger.exception("[BUSINESS] failed to relay datagram: %s", url)

        server.ondata = on_data

        @server.command("/ctrl/cmd")
        def receive_command(cli, req, payload) -> None:
            try:
                param = dict(payload.param) if isinstance(payload.param, dict) else {}
                cli.reply(req.seqno, vsoa.Payload(param={"ok": True}))
                server.publish("/ctrl/cmd", vsoa.Payload(param=param))
            except Exception:
                logger.exception("[BUSINESS] failed to relay /ctrl/cmd")

        def run_server() -> None:
            try:
                server.run(self.bind_host, self.bind_port)
            except Exception:
                if self.owns_server:
                    logger.exception("[BUSINESS] VSOA Server stopped unexpectedly")

        self.thread = threading.Thread(
            target=run_server,
            name="bridge-vsoa-business-server",
            daemon=True,
        )
        self.thread.start()

        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if self._can_connect(timeout=0.2):
                logger.info("[BUSINESS] local VSOA Server listening: %s:%d",
                            self.bind_host, self.bind_port)
                return True
            time.sleep(0.1)

        logger.error("[BUSINESS] local VSOA Server start timeout")
        self.stop()
        return False

    def stop(self) -> None:
        if self.server is not None and self.owns_server:
            self.owns_server = False
            try:
                self.server.close()
            except Exception:
                logger.exception("[BUSINESS] VSOA Server close failed")
        self.server = None
        self.thread = None
