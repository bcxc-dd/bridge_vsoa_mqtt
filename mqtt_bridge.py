import paho.mqtt.client as mqtt


LOCAL_BROKER = "192.168.3.219"
REMOTE_BROKER = "192.168.137.118"

PORT = 1883

TOPIC = "s3/eora-s3-400tb-001/data"


# 连接目标MQTT(T113)
remote = mqtt.Client(
    mqtt.CallbackAPIVersion.VERSION1
)

try:
    remote.connect(
        REMOTE_BROKER,
        PORT,
        60
    )

    remote.loop_start()

    print(
        "Remote MQTT connected:",
        REMOTE_BROKER
    )

except Exception as e:
    print(
        "Remote MQTT failed:",
        e
    )
    exit()


# 本地MQTT连接成功
def on_connect(client, userdata, flags, rc):

    if rc == 0:
        print(
            "Local MQTT connected:",
            LOCAL_BROKER
        )

        client.subscribe(
            TOPIC,
            qos=1
        )

        print(
            "Subscribed:",
            TOPIC
        )

    else:
        print(
            "Local MQTT failed:",
            rc
        )


# 收到S3 WiFi数据
def on_message(client, userdata, msg):

    payload = msg.payload


    print("----------------------")
    print("Received:")
    print(payload.decode())


    result = remote.publish(
        TOPIC,
        payload,
        qos=1
    )


    if result.rc == 0:
        print(
            "Forward success"
        )
    else:
        print(
            "Forward failed:",
            result.rc
        )


local = mqtt.Client(
    mqtt.CallbackAPIVersion.VERSION1
)

local.on_connect = on_connect
local.on_message = on_message


local.connect(
    LOCAL_BROKER,
    PORT,
    60
)


print(
    "MQTT Bridge running..."
)


local.loop_forever()