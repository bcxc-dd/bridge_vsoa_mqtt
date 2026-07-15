"""
最小测试：验证 VSOA Server.publish() 的线程安全性。

测试场景:
  A. 从 RPC handler 内调用 publish (同线程 — 事件循环线程)
  B. 从另一个线程调用 publish (跨线程 — 模拟 MQTT/PubSub 回调线程)

如果 A 通 B 不通 → 确认跨线程问题，需要用 RPC 代理方案修复
如果 A 和 B 都通 → 问题在 bridge 代码别处
如果 A 和 B 都不通 → VSOA publish 根本不支持客户端间投递

用法: python tools/_test_publish.py
"""

import sys
import threading
import time

import vsoa

SERVER_PORT = 3099
RESULTS: dict[str, bool] = {}


def main():
    print("=" * 60)
    print("  VSOA Server.publish() 线程安全性测试")
    print("=" * 60)
    print()

    # ---- 启动 VSOA Server ----
    server = vsoa.Server({"name": "publish-test"})

    # 注册一个 RPC handler，它内部调用 server.publish (在事件循环线程)
    @server.command("/test_publish_from_handler")
    def _publish_in_handler(cli, req, payload):
        """从 RPC handler (事件循环线程) 调用 publish。"""
        print("  [TEST A] RPC handler 收到请求，正在 publish...")
        server.publish(
            vsoa.URL("/test_topic"),
            vsoa.Payload(param={"source": "rpc_handler", "msg": "hello_from_event_loop"}),
        )
        cli.reply(req.seqno, vsoa.Payload(param={"ok": True}))
        print("  [TEST A] publish 调用完成")

    # 启动 server 线程
    server_ready = threading.Event()

    def run_server():
        server_ready.set()
        server.run("127.0.0.1", SERVER_PORT)

    server_thread = threading.Thread(target=run_server, daemon=True, name="vsoa-server")
    server_thread.start()
    server_ready.wait()
    time.sleep(0.5)
    print(f"  [OK] VSOA Server 监听 127.0.0.1:{SERVER_PORT}\n")

    # ---- 启动订阅 Client ----
    received: list[dict] = []

    def on_message(cli, url, payload, quick):
        try:
            if payload and hasattr(payload, "param"):
                data = dict(payload.param)
                received.append(data)
                url_str = url.url if hasattr(url, "url") else str(url)
                print(f"  [SUBSCRIBER] 收到消息! url={url_str} data={data}")
        except Exception:
            pass

    sub_client = vsoa.Client()
    sub_client.onmessage = on_message
    ret = sub_client.connect(f"vsoa://127.0.0.1:{SERVER_PORT}")
    assert ret == 0, f"订阅客户端连接失败: {ret}"
    sub_client.subscribe("/test_topic")

    sub_thread = threading.Thread(target=sub_client.run, daemon=True, name="vsoa-sub")
    sub_thread.start()
    time.sleep(0.5)
    print("  [OK] 订阅 Client 就绪，监听 /test_topic\n")

    # ---- 启动 RPC Client (用于触发 handler 内 publish) ----
    rpc_client = vsoa.Client()
    ret = rpc_client.connect(f"vsoa://127.0.0.1:{SERVER_PORT}")
    assert ret == 0, f"RPC 客户端连接失败: {ret}"

    rpc_thread = threading.Thread(target=rpc_client.run, daemon=True, name="vsoa-rpc")
    rpc_thread.start()
    time.sleep(0.5)
    print("  [OK] RPC Client 就绪\n")

    # ==================================================================
    # TEST A: 从 RPC handler 内 publish (事件循环线程)
    # ==================================================================
    print("─── TEST A: RPC handler 内调用 publish (事件循环线程) ───")
    received.clear()
    h, p, s = rpc_client.fetch(
        "/test_publish_from_handler",
        payload=vsoa.Payload(param={"cmd": "test_a"}),
        timeout=3.0,
    )
    time.sleep(1.0)  # 等 subscriber 收到消息
    RESULTS["A: handler内publish → subscriber收到"] = len(received) > 0
    print(f"  结果: {'✓ 通过' if RESULTS['A: handler内publish → subscriber收到'] else '✗ 失败'}"
          f" (收到 {len(received)} 条)\n")

    # ==================================================================
    # TEST B: 从另一个线程直接 publish (跨线程)
    # ==================================================================
    print("─── TEST B: 另一个线程直接调用 publish (跨线程) ───")
    received.clear()

    def publish_from_other_thread():
        print("  [TEST B] 其他线程正在调用 server.publish...")
        server.publish(
            vsoa.URL("/test_topic"),
            vsoa.Payload(param={"source": "other_thread", "msg": "hello_from_other_thread"}),
        )
        print("  [TEST B] publish 调用完成")

    bg_thread = threading.Thread(target=publish_from_other_thread, daemon=True)
    bg_thread.start()
    bg_thread.join()
    time.sleep(1.0)  # 等 subscriber 收到
    RESULTS["B: 跨线程publish → subscriber收到"] = len(received) > 0
    print(f"  结果: {'✓ 通过' if RESULTS['B: 跨线程publish → subscriber收到'] else '✗ 失败'}"
          f" (收到 {len(received)} 条)\n")

    # ==================================================================
    # 总结
    # ==================================================================
    print("=" * 60)
    print("  测试结论:")
    for desc, ok in RESULTS.items():
        icon = "✓" if ok else "✗"
        print(f"    {icon}  {desc}")
    print()

    if RESULTS.get("A: handler内publish → subscriber收到"):
        if RESULTS.get("B: 跨线程publish → subscriber收到"):
            print("  → publish 线程安全，问题在 bridge 代码别处")
        else:
            print("  → 确认跨线程问题！修复方案: RPC 代理 publish")
            print("     (通过自连 RPC Client fetch 触发 handler 内 publish)")
    else:
        print("  → VSOA Python 库可能不支持 Server→Client pub/sub")
        print("     需要改用其他 ACK 机制 (如共享队列/RPC轮询)")
    print("=" * 60)

    # 清理
    sub_client.close()
    rpc_client.close()
    print("\n[DONE]")


if __name__ == "__main__":
    main()
