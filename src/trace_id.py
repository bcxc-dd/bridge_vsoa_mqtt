"""
traceId 生成器 — 全链路追踪标识。

格式: br-{8位hex随机}-{毫秒时间戳}
示例: br-a3f8c2d1-1720435200000
"""

import secrets
import time


def generate_trace_id() -> str:
    """生成全局唯一的链路追踪 ID。

    格式: br-{8位hex随机}-{毫秒时间戳}
    示例: br-a3f8c2d1-1720435200000

    - ``br`` — bridge 前缀
    - ``8位hex随机`` — secrets.token_hex(4)，碰撞概率极低
    - ``毫秒时间戳`` — int(time.time() * 1000)
    """
    rand_hex = secrets.token_hex(4)          # 8 hex chars
    ts_ms = int(time.time() * 1000)
    return f"br-{rand_hex}-{ts_ms}"
