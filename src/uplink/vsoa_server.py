"""
VSOA RPC Server for the uplink bridge.

Exposes 7 query URLs and publishes /device/update and /bridge/event
notifications whenever a device is upserted.

.. note::

    The vsoa-python library (v1.0.4) does not pass the full URL to
    ``@server.command()`` handlers — only ``(cli, req, payload)``.
    We monkey-patch ``Client._pinput`` to store *url* on the client
    object so that the ``/device/`` prefix handler can extract the
    device-id from the request URL.
"""

from __future__ import annotations

import logging
import threading
import time
import traceback
from typing import Any

import vsoa
import vsoa.server as _vsrv

from src.device_registry import DeviceRegistry
from src.config import BridgeConfig

# ---------------------------------------------------------------------------
# Monkey-patch: store URL on Client so handlers can read it
# ---------------------------------------------------------------------------
_original_pinput = _vsrv.Client._pinput


def _patched_pinput(self, header, url, param, data):
    self._vsoa_request_url = url.url if hasattr(url, "url") else str(url)
    return _original_pinput(self, header, url, param, data)


_vsrv.Client._pinput = _patched_pinput

logger = logging.getLogger(__name__)


class UplinkVsoaServer:
    """VSOA Server that provides device query RPCs and publish notifications.

    Registered URLs (all GET):
      /bridge/health
      /adapter/list
      /uplink/schema
      /device/list
      /device/all/data
      /device/          — prefix match for /device/{id}/data, /device/{id}/status

    Publish URLs:
      /device/update    — fired on every device upsert
      /bridge/event     — fired on every uplink message processed
    """

    def __init__(
        self,
        config: BridgeConfig,
        registry: DeviceRegistry,
        adapters: list | None = None,
        server: vsoa.Server | None = None,
    ) -> None:
        self._config = config
        self._registry = registry
        self._adapters = adapters or []
        self._external_server = server
        self._server: vsoa.Server | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._start_time_ms = int(time.time() * 1000)

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """启动 VSOA Server（注册所有上行 RPC 端点）。

        如果构造时传入了外部 server（server= 参数），则直接在其上注册 handler，
        不创建线程、不调用 run()——server 生命周期由调用方管理。

        否则按独立模式创建自己的 server 并在后台线程运行（向后兼容）。
        """
        if self._external_server is not None:
            svr = self._external_server
        else:
            svr = vsoa.Server({
                "name": self._config.bridge.name,
                "version": self._config.bridge.version,
            })
        self._server = svr
        self._start_time_ms = int(time.time() * 1000)

        _self = self  # closure ref

        # -- exact-match handlers --
        @svr.command("/bridge/health")
        def _health(cli, req, payload):
            _self._handle_health(cli, req)

        @svr.command("/adapter/list")
        def _adapter_list(cli, req, payload):
            _self._handle_adapter_list(cli, req)

        @svr.command("/uplink/schema")
        def _schema(cli, req, payload):
            _self._handle_schema(cli, req)

        @svr.command("/device/list")
        def _dev_list(cli, req, payload):
            _self._handle_device_list(cli, req)

        @svr.command("/device/all/data")
        def _dev_all_data(cli, req, payload):
            _self._handle_device_all_data(cli, req)

        # -- catch-all for /device/{id}/data and /device/{id}/status --
        # vsoa-python 1.0.4 wildcard matching requires '/' right after prefix,
        # so /device/ can't match /device/{id}/data.  Use "/" as catch-all
        # (the library has a built-in fallback: if "/" is in __wccmds it
        # returns it for any URL not matched by an exact handler).
        @svr.command("/")
        def _catch_all(cli, req, payload):
            url_path = getattr(cli, "_vsoa_request_url", "")
            if url_path.startswith("/device/") and url_path not in (
                "/device/list", "/device/all/data",
            ):
                _self._handle_device_entry(cli, req, payload)
            else:
                cli.reply(req.seqno, vsoa.Payload(
                    param={"error": "not found: " + url_path},
                ))

        # 如果是自己的 server，启动后台线程运行；否则 server 由外部管理
        if self._external_server is None:
            def _run():
                self._running = True
                logger.info(
                    "[VSOA] Server starting on %s:%d",
                    self._config.vsoa.server.bind_host, self._config.vsoa.server.port,
                )
                try:
                    svr.run(self._config.vsoa.server.bind_host, self._config.vsoa.server.port)
                except Exception:
                    logger.error("[VSOA] Server exception:\n%s", traceback.format_exc())
                finally:
                    self._running = False
                    logger.info("[VSOA] Server stopped")

            self._thread = threading.Thread(target=_run, daemon=True, name="vsoa-uplink")
            self._thread.start()

            # wait briefly for the server to come up
            waited = 0
            while not self._running and waited < 50:
                time.sleep(0.1)
                waited += 1
        else:
            self._running = True
            logger.info("[VSOA] Uplink endpoints registered on shared server")

    def stop(self) -> None:
        self._running = False
        if self._external_server is None:
            logger.info("[VSOA] stop requested")
        else:
            logger.info("[VSOA] uplink stop requested (server lifecycle external)")

    @property
    def is_running(self) -> bool:
        return self._running

    # ------------------------------------------------------------------
    # publish
    # ------------------------------------------------------------------

    def publish(self, url: str, data: dict[str, Any]) -> None:
        if not self._server or not self._running:
            return
        try:
            self._server.publish(
                vsoa.URL(url),
                vsoa.Payload(param=data),
            )
        except Exception:
            logger.debug("[VSOA] publish %s failed (non-fatal)", url)

    # ------------------------------------------------------------------
    # handlers
    # ------------------------------------------------------------------

    def _handle_health(self, cli, req) -> None:
        uptime_sec = (int(time.time() * 1000) - self._start_time_ms) // 1000
        reply = {
            "service":          self._config.bridge.name,
            "status":           "running",
            "uptime":           uptime_sec,
            "devices":          self._registry.count,
            "version":          self._config.bridge.version,
            "vsoa_rpc_port":    self._config.vsoa.server.port,
            "tcp_inject_port":  9090,
        }
        self._reply(cli, req, reply)

    def _handle_adapter_list(self, cli, req) -> None:
        adapters = []
        for ad in self._adapters:
            adapters.append({
                "name":   ad.name,
                "source": ad.source,
            })
        self._reply(cli, req, {
            "count": len(adapters),
            "adapters": adapters,
        })

    def _handle_schema(self, cli, req) -> None:
        self._reply(cli, req, {
            "schema":   "uplink_report.v2",
            "required": ["device_id", "timestamp"],
            "optional": [
                "type", "status", "temperature", "humidity", "pressure",
                "unit", "battery", "signal", "source", "adapter",
            ],
        })

    def _handle_device_list(self, cli, req) -> None:
        devices = self._registry.list_all()
        items = []
        for d in devices:
            items.append({
                "device_id":    d.device_id,
                "type":         d.type,
                "status":       d.status,
                "source":       d.source,
                "adapter":      d.adapter,
                "last_update":  d.timestamp,
                "report_count": d.report_count,
            })
        self._reply(cli, req, {"count": len(items), "devices": items})

    def _handle_device_all_data(self, cli, req) -> None:
        devices = self._registry.list_all()
        self._reply(cli, req, {
            "count": len(devices),
            "data":  [d.to_json() for d in devices],
        })

    def _handle_device_entry(self, cli, req, payload) -> None:
        """Handle /device/{device_id}/data and /device/{device_id}/status."""
        # The URL is stored on cli by our monkey-patch above.
        url_path = getattr(cli, "_vsoa_request_url", "")

        if not url_path:
            self._reply_error(cli, req, "missing URL in request")
            return

        device_id, field = _parse_device_url(url_path)
        if not device_id or field not in ("data", "status"):
            self._reply_error(
                cli, req,
                "expected /device/{device_id}/data or /device/{device_id}/status",
            )
            return

        dev = self._registry.lookup(device_id)
        if dev is None:
            self._reply_error(cli, req, "Device not found")
            return

        self._reply(cli, req, dev.to_json())

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _reply(self, cli, req, data: dict[str, Any]) -> None:
        try:
            cli.reply(req.seqno, vsoa.Payload(param=data))
        except Exception:
            logger.exception("[VSOA] reply failed")

    def _reply_error(self, cli, req, message: str) -> None:
        self._reply(cli, req, {"error": message})


def _parse_device_url(path: str) -> tuple[str, str]:
    """Parse ``/device/{device_id}/{field}`` into (id, field).

    Returns ``("", "")`` on malformed input.
    """
    prefix = "/device/"
    if not path.startswith(prefix):
        return "", ""
    rest = path[len(prefix):]  # e.g. "lora_env_01/data"
    parts = rest.split("/", 1)
    if len(parts) != 2:
        return "", ""
    return parts[0], parts[1]
