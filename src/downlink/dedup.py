"""
幂等去重模块 — 基于 command_id 的内存去重缓存

DedupCache 类用于防止相同 command_id 在 TTL 窗口内被重复执行。
线程安全（RPC 线程和 Pub/Sub 主线程可能并发调用）。

对应 spec.md §4.4。

注意：DedupCache 是单一职责组件，不知道 enabled 配置。
是否启用来重由 main.py 决定（见任务 26）。
"""

from __future__ import annotations

import threading
import time


class DedupCache:
    """基于 command_id 的幂等去重缓存。

    使用内存 dict + 惰性淘汰（检查时清理过期条目）。
    线程安全（内部使用 threading.Lock）。
    """

    def __init__(self, ttl_seconds: int = 300, max_size: int = 10000) -> None:
        """初始化去重缓存。

        Args:
            ttl_seconds: 命令 ID 的有效期（秒），超时后自动清理。默认 300s（5 分钟）。
            max_size: 最大容量。超出时淘汰最老条目。默认 10000。
        """
        if ttl_seconds <= 0:
            raise ValueError(f"ttl_seconds 必须 > 0，当前值: {ttl_seconds}")
        if max_size <= 0:
            raise ValueError(f"max_size 必须 > 0，当前值: {max_size}")

        self._ttl = ttl_seconds
        self._max_size = max_size
        self._cache: dict[str, float] = {}   # command_id → 插入时间戳
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # 核心接口
    # ------------------------------------------------------------------

    def check_and_mark(self, command_id: str) -> bool:
        """检查 command_id 是否已存在；若不存在则标记并返回 True。

        此方法是原子操作（检查 + 标记在同一个锁内完成），保证竞速场景下
        恰好一个调用方返回 True。

        Args:
            command_id: 命令唯一标识。

        Returns:
            True  — 新命令（未见过或已过期），已标记
            False — 重复命令（在 TTL 窗口内）
        """
        now = time.monotonic()
        with self._lock:
            # 惰性淘汰：清理过期条目（全量扫描，仅在必要时进行）
            self._evict_expired_locked(now)

            if command_id in self._cache:
                return False

            # 容量控制：超出 max_size 时淘汰最老的条目
            if len(self._cache) >= self._max_size:
                self._evict_oldest_locked()

            self._cache[command_id] = now
            return True

    def clear(self) -> None:
        """清空所有缓存条目。"""
        with self._lock:
            self._cache.clear()

    # ------------------------------------------------------------------
    # 查询接口（调试用）
    # ------------------------------------------------------------------

    @property
    def size(self) -> int:
        """当前缓存条目数（含可能过期的条目，惰性淘汰前）。"""
        with self._lock:
            return len(self._cache)

    def __contains__(self, command_id: str) -> bool:
        """支持 'id' in cache 语法（仅检查存在性，不淘汰）。"""
        with self._lock:
            return command_id in self._cache

    def __repr__(self) -> str:
        return f"DedupCache(ttl={self._ttl}s, max={self._max_size}, size={self.size})"

    # ------------------------------------------------------------------
    # 内部方法（调用时必须持有 self._lock）
    # ------------------------------------------------------------------

    def _evict_expired_locked(self, now: float) -> None:
        """清理所有过期条目（调用时必须持有锁）。"""
        threshold = now - self._ttl
        expired = [cid for cid, ts in self._cache.items() if ts < threshold]
        for cid in expired:
            del self._cache[cid]

    def _evict_oldest_locked(self) -> None:
        """淘汰最老的 1 条条目（调用时必须持有锁且 cache 非空）。"""
        if not self._cache:
            return
        oldest_id = min(self._cache, key=lambda k: self._cache[k])
        del self._cache[oldest_id]
