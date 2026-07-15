"""bridge 下行消息专用 MQTT 订阅验证脚本。"""
import paho.mqtt.client as mqtt

def on_connect(client, userdata, flags, reason_code, properties=None):
    print(f"已连接 Broker (rc={reason_code})")
    client.subscribe("bridge/downlink/#")
    print("已订阅 bridge/downlink/# (仅下行控制消息)")

def on_message(client, userdata, msg):
    print(f"📥 收到: topic={msg.topic}")
    print(f"        payload={msg.payload.decode('utf-8')}")
    print()

client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
client.on_connect = on_connect
client.on_message = on_message
client.connect("broker.emqx.io", 1883, 60)
print("bridge MQTT 订阅端启动，等待消息... (Ctrl+C 退出)")
client.loop_forever()
