"""
VSOA 下行命令监控 — 在任意机器上运行，实时捕获 bridge 的下行 ACK 和设备注册事件

用法:
  python vsoa_monitor.py [--host 192.168.3.100]
"""

import sys
import time
import threading
import argparse

import vsoa


def main():
    parser = argparse.ArgumentParser(description="VSOA 下行命令监控")
    parser.add_argument("--host", default="127.0.0.1", help="bridge 主机 IP")
    args = parser.parse_args()

    BRIDGE_PORT = 3001
    host = args.host

    print(f"连接 bridge VSOA Server: vsoa://{host}:{BRIDGE_PORT}")
    print(f"订阅: /ctrl/ack (下行 ACK)  +  /device/update (设备注册)")
    print("等待事件...\n")

    client = vsoa.Client()

    def on_message(cli, url, payload, quick):
        try:
            if payload and hasattr(payload, "param") and payload.param:
                data = dict(payload.param)
                url_str = url.url if hasattr(url, "url") else str(url)

                ts = time.strftime("%H:%M:%S")
                if "ctrl/ack" in url_str:
                    code = data.get("error_code", -1)
                    icon = "✓" if code == 0 else "✗"
                    print(f"[{ts}] {icon} 下行ACK | cmd_id={data.get('command_id','?')} "
                          f"device={data.get('device_type','?')}/{data.get('device_id','?')} "
                          f"code={code} {data.get('error_msg','?')} "
                          f"trace={data.get('trace_id','?')[:30]}...")
                elif "device/update" in url_str:
                    print(f"[{ts}] ↑ 设备注册 | device={data.get('device_id','?')} "
                          f"type={data.get('type','?')} source={data.get('source','?')}")
        except Exception:
            pass

    client.onmessage = on_message

    ret = client.connect(f"vsoa://{host}:{BRIDGE_PORT}")
    if ret != 0:
        print(f"连接失败！请确认 bridge 已启动且 {host}:{BRIDGE_PORT} 可达")
        sys.exit(1)

    client.subscribe("/ctrl/ack")
    client.subscribe("/device/update")
    threading.Thread(target=client.run, daemon=True).start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n退出")


if __name__ == "__main__":
    main()
