import paho.mqtt.client as mqtt
import sys

BROKER = "192.168.137.118"
PORT = 1883

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print(f"✓ 连接成功: {BROKER}:{PORT}")
        client.subscribe("#")
        print("✓ 已订阅所有 topic (#)")
    else:
        print(f"✗ 连接失败，错误码: {rc}")

def on_message(client, userdata, msg):
    print(f"[{msg.topic}] {msg.payload.decode()}")

client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
client.on_connect = on_connect
client.on_message = on_message

try:
    print(f"正在连接 {BROKER}:{PORT}...")
    client.connect(BROKER, PORT, 60)
    client.loop_forever()
except Exception as e:
    print(f"✗ 连接异常: {e}")
    sys.exit(1)
