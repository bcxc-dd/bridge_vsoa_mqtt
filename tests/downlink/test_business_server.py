import socket
import threading
import time

import vsoa

from src.downlink.business_server import BusinessVsoaServer
from src.downlink.pubsub_handler import PubSubHandler


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_local_business_server_relays_downlink_to_mqtt_publisher():
    port = free_port()
    url = "vsoa://127.0.0.1:{}".format(port)
    server = BusinessVsoaServer(url, "127.0.0.1", port, auto_start=True)
    published = []
    handler = PubSubHandler(
        server_url=url,
        subscribe_urls=["/ctrl/cmd"],
        mqtt_publisher=lambda topic, payload, qos=1: published.append((topic, payload)) or True,
        reconnect_max_retries=2,
        reconnect_interval_ms=20,
    )
    sender = vsoa.Client()
    try:
        assert server.start_if_needed()
        assert handler.connect()
        thread = threading.Thread(target=handler.run_forever, daemon=True)
        thread.start()
        assert sender.connect(url, timeout=1.0) == vsoa.Client.CONNECT_OK
        assert sender.datagram("/ctrl/cmd", payload=vsoa.Payload(param={
            "command_id": "business-server-test",
            "device_type": "generic",
            "device_id": "eora_s3_400tb_001",
            "action": "set",
            "params": {"led": "on"},
        }), quick=False)
        deadline = time.monotonic() + 2.0
        while not published and time.monotonic() < deadline:
            time.sleep(0.02)
        assert published == [(
            "bridge/downlink/generic/eora_s3_400tb_001/cmd",
            '{"cmd":"led","value":"on"}',
        )]
    finally:
        sender.close()
        handler.stop()
        server.stop()
