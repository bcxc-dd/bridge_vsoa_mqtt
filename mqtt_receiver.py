import json
import base64
import paho.mqtt.client as mqtt


# ==========================
# MQTT配置
# ==========================

MQTT_HOST = "192.168.3.219"

MQTT_PORT = 1883


# 两个数据入口
MQTT_TOPICS = [
    ("application/+/device/+/event/up", 1),
    ("application/+/device/+/command/down", 1),
    ("s3/eora-s3-400tb-001/data", 1),
    ("bridge/downlink/#", 1),
    ("bridge/uplink/#", 1),
]


# ==========================
# LoRa Payload解析
# ==========================

def parse_payload(payload):

    if len(payload) != 16:
        print(
            "Payload长度异常:",
            len(payload)
        )
        return None


    seq = int.from_bytes(
        payload[0:2],
        byteorder="big"
    )


    boot_id = int.from_bytes(
        payload[2:6],
        byteorder="big"
    )


    send_time_ms = int.from_bytes(
        payload[6:10],
        byteorder="big"
    )


    retry_count = payload[10]


    temp_raw = int.from_bytes(
        payload[11:13],
        byteorder="big",
        signed=True
    )

    temperature = temp_raw / 10.0



    humi_raw = int.from_bytes(
        payload[13:15],
        byteorder="big"
    )

    humidity = humi_raw / 10.0



    flags = payload[15]


    joined = bool(
        flags & 0x01
    )


    app_retry = bool(
        flags & 0x08
    )


    return {

        "seq": seq,

        "boot_id": hex(boot_id),

        "send_time_ms":
            send_time_ms,

        "lorawan_retry_count":
            retry_count,

        "temperature":
            temperature,

        "humidity":
            humidity,

        "joined":
            joined,

        "application_retry":
            app_retry,

        "flags":
            hex(flags)

    }



# ==========================
# MQTT连接回调
# ==========================

def on_connect(client, userdata, flags, rc):

    if rc == 0:

        print(
            "MQTT连接成功"
        )


        client.subscribe(
            MQTT_TOPICS
        )


        print(
            "订阅Topic:"
        )


        for topic, qos in MQTT_TOPICS:
            print(
                " ",
                topic
            )


    else:

        print(
            "MQTT连接失败:",
            rc
        )



# ==========================
# MQTT消息处理
# ==========================

def on_message(client, userdata, msg):


    print("\n========================")

    print(
        "Topic:"
    )

    print(
        msg.topic
    )


    try:

        data = json.loads(
            msg.payload.decode()
        )


    except Exception as e:

        print(
            "JSON解析失败:",
            e
        )

        return



    # ==================================================
    # ChirpStack 下行消息 (bridge → ChirpStack → LoRa)
    # ==================================================

    if "command/down" in msg.topic:


        print(
            "\n========== ChirpStack下行 (→ LoRa设备) =========="
        )


        print(
            "Topic:",
            msg.topic
        )


        print(
            "Payload:"
        )


        print(
            json.dumps(
                data,
                indent=2,
                ensure_ascii=False
            )
        )


        # 解码 Base64 data
        b64 = data.get(
            "data",
            ""
        )

        if b64:

            import base64 as _b64

            try:

                decoded = _b64.b64decode(
                    b64
                ).decode(
                    "utf-8"
                )


                print(
                    "\ndata (Base64解码):"
                )


                print(
                    decoded
                )


            except Exception:

                print(
                    "\ndata (Base64解码失败)"
                )



        print(
            "===================================================="
        )


        return


    # ==================================================
    # bridge 上行消息 (设备 → MQTT → bridge)
    # ==================================================

    if msg.topic.startswith("bridge/uplink"):


        print(
            "\n========== 上行数据 (设备 → bridge) =========="
        )


        print(
            "Topic:",
            msg.topic
        )


        print(
            "Payload:"
        )


        print(
            json.dumps(
                data,
                indent=2,
                ensure_ascii=False
            )
        )


        parts = msg.topic.split("/")

        if len(parts) >= 5:

            print(
                "\n解析:"
            )

            print(
                "  来源:   ",
                parts[2]
            )

            print(
                "  设备ID: ",
                parts[3]
            )

            print(
                "  类型:   ",
                parts[4]
            )



        print(
            "============================================="
        )


        return


    # ==================================================
    # bridge 下行消息 (VSOA → MQTT → 设备)
    # ==================================================

    if msg.topic.startswith("bridge/downlink"):


        print(
            "\n========== 下行命令 (bridge → 设备) =========="
        )


        print(
            "Topic:",
            msg.topic
        )


        print(
            "Payload:"
        )


        print(
            json.dumps(
                data,
                indent=2,
                ensure_ascii=False
            )
        )


        # 解析 topic 段
        parts = msg.topic.split("/")

        if len(parts) >= 5:

            print(
                "\n解析:"
            )

            print(
                "  设备类型:",
                parts[2]
            )

            print(
                "  设备ID:  ",
                parts[3]
            )

            print(
                "  动作:    ",
                parts[4]
            )



        # 高亮 trace_id
        trace_id = data.get(
            "trace_id",
            ""
        )

        if trace_id:

            print(
                "\ntrace_id:",
                trace_id
            )



        print(
            "============================================="
        )


        return


    # ==================================================
    # WiFi MQTT 数据
    # ==================================================

    if msg.topic == "s3/eora-s3-400tb-001/data":


        print(
            "\n========== WiFi数据 =========="
        )


        print(
            "设备:",
            data.get("device_id")
        )


        print(
            "DevEUI:",
            data.get("dev_eui")
        )


        print(
            "序号:",
            data.get("seq")
        )


        print(
            "Boot ID:",
            data.get("boot_id")
        )


        print(
            "发送时间:",
            data.get("send_time_ms"),
            "ms"
        )


        print(
            "温度:",
            data.get("temperature"),
            "℃"
        )


        print(
            "湿度:",
            data.get("humidity"),
            "%"
        )


        print(
            "WiFi重传:",
            data.get("wifi_retry_count")
        )


        print(
            "链路:",
            data.get("link")
        )


        print(
            "============================"
        )


        return



    # ==================================================
    # LoRaWAN / ChirpStack 数据
    # ==================================================

    if msg.topic.startswith(
        "application/"
    ):


        print(
            "\n========== LoRaWAN数据 =========="
        )


        device_info = data.get(
            "deviceInfo",
            {}
        )


        print(
            "DevEUI:",
            device_info.get(
                "devEui"
            )
        )


        print(
            "设备:",
            device_info.get(
                "deviceName"
            )
        )



        print(
            "\n时间:"
        )

        print(
            data.get(
                "time"
            )
        )



        rx_info = data.get(
            "rxInfo",
            []
        )


        if rx_info:


            rx = rx_info[0]


            print(
                "\n网关:"
            )


            print(
                "Gateway:",
                rx.get(
                    "gatewayId"
                )
            )


            print(
                "RSSI:",
                rx.get(
                    "rssi"
                )
            )


            print(
                "SNR:",
                rx.get(
                    "snr"
                )
            )



        payload_b64 = data.get(
            "data"
        )


        print(
            "\nBase64:"
        )


        print(
            payload_b64
        )



        payload = base64.b64decode(
            payload_b64
        )


        print(
            "\nHEX:"
        )


        print(
            payload.hex()
        )



        result = parse_payload(
            payload
        )


        if result:


            print(
                "\n======= LoRa解析结果 ======="
            )


            print(
                "温度:",
                result["temperature"],
                "℃"
            )


            print(
                "湿度:",
                result["humidity"],
                "%"
            )


            print(
                "序号:",
                result["seq"]
            )


            print(
                "Boot ID:",
                result["boot_id"]
            )


            print(
                "LoRaWAN重传:",
                result["lorawan_retry_count"]
            )


            print(
                "已入网:",
                result["joined"]
            )


            print(
                "应用层重传:",
                result["application_retry"]
            )


            print(
                "============================"
            )



# ==========================
# 主程序
# ==========================


client = mqtt.Client(
    mqtt.CallbackAPIVersion.VERSION1
)


client.on_connect = on_connect

client.on_message = on_message



print(
    "正在连接MQTT..."
)

try:
    client.connect(
        MQTT_HOST,
        MQTT_PORT,
        60
    )
    print("连接成功，开始监听...")
    client.loop_forever()
except Exception as e:
    print("连接失败:", e)
    print("请检查:")
    print("1. MQTT Broker 是否在运行")
    print("2. 网络是否可达")
    print("3. 端口 1883 是否开放")
