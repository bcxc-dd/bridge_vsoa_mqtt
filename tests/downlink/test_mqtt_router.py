from src.mqtt_handler import MQTTPublisherRouter


class FakeHandler:
    def __init__(self, connected=True):
        self.is_connected = connected
        self.messages = []

    def publish(self, topic, payload, qos=None):
        self.messages.append((topic, payload, qos))
        return self.is_connected


def test_project_downlink_uses_project_broker():
    default = FakeHandler()
    zigbee = FakeHandler()
    router = MQTTPublisherRouter(default)
    router.add_route("zigbee", zigbee)

    assert router.publish(
        "bridge/downlink/zigbee/0xC38F/set",
        '{"device_id":"0xC38F","params":{"data":"01"}}',
        qos=1,
    )

    assert default.messages == []
    assert zigbee.messages[0][0] == "bridge/downlink/zigbee/0xC38F/set"


def test_non_project_topic_uses_default_broker():
    default = FakeHandler()
    zigbee = FakeHandler()
    router = MQTTPublisherRouter(default)
    router.add_route("zigbee", zigbee)

    assert router.publish("application/app/device/node/command/down", "{}")

    assert default.messages == [("application/app/device/node/command/down", "{}", None)]
    assert zigbee.messages == []


def test_router_reports_any_live_connection():
    default = FakeHandler(connected=False)
    zigbee = FakeHandler(connected=True)
    router = MQTTPublisherRouter(default)
    router.add_route("zigbee", zigbee)

    assert router.is_connected
