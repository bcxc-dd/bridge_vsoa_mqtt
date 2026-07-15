"""幂等去重单元测试。

覆盖: 首次标记、重复拒绝、TTL 过期恢复、容量溢出淘汰、
      并发安全、竞速测试、clear、边界条件。
"""

import os
import threading
import time

import pytest

from src.downlink.dedup import DedupCache


class TestDedupBasic:
    """基本行为。"""

    def test_first_check_returns_true(self):
        """首次 check_and_mark 返回 True。"""
        cache = DedupCache(ttl_seconds=300)
        assert cache.check_and_mark("cmd-001") is True
        assert cache.size == 1

    def test_duplicate_returns_false(self):
        """相同 ID 再次调用返回 False。"""
        cache = DedupCache(ttl_seconds=300)
        cache.check_and_mark("cmd-001")
        assert cache.check_and_mark("cmd-001") is False
        # 条目不应增加
        assert cache.size == 1

    def test_different_ids_independent(self):
        """不同 ID 各自独立去重。"""
        cache = DedupCache(ttl_seconds=300)
        assert cache.check_and_mark("cmd-001") is True
        assert cache.check_and_mark("cmd-002") is True
        assert cache.check_and_mark("cmd-003") is True
        assert cache.size == 3

        # 各自的重复检查
        assert cache.check_and_mark("cmd-001") is False
        assert cache.check_and_mark("cmd-002") is False


class TestDedupTTL:
    """TTL 过期行为。"""

    def test_expired_id_treated_as_new(self):
        """超过 TTL 后相同 ID 视为新命令。"""
        cache = DedupCache(ttl_seconds=0.2)  # 200ms TTL
        assert cache.check_and_mark("cmd-001") is True
        assert cache.check_and_mark("cmd-001") is False  # 立即重试 → False

        # 等待 TTL 过期
        time.sleep(0.25)
        assert cache.check_and_mark("cmd-001") is True  # 过期后视为新命令

    def test_not_expired_still_blocked(self):
        """TTL 未过期时仍被拦截。"""
        cache = DedupCache(ttl_seconds=5)
        cache.check_and_mark("cmd-001")
        assert cache.check_and_mark("cmd-001") is False


class TestDedupCapacity:
    """容量控制。"""

    def test_evict_oldest_when_exceeded(self):
        """超出 max_size 时淘汰最老条目。"""
        cache = DedupCache(ttl_seconds=300, max_size=3)

        # 插入 3 条
        cache.check_and_mark("cmd-001")
        time.sleep(0.01)
        cache.check_and_mark("cmd-002")
        time.sleep(0.01)
        cache.check_and_mark("cmd-003")
        assert cache.size == 3

        # 插入第 4 条 → 淘汰最老的 cmd-001
        cache.check_and_mark("cmd-004")
        assert cache.size == 3

        # cmd-001 被淘汰，可以重新插入
        assert cache.check_and_mark("cmd-001") is True

    def test_exact_capacity_no_eviction(self):
        """刚好等于 max_size 时不淘汰。"""
        cache = DedupCache(ttl_seconds=300, max_size=2)
        cache.check_and_mark("cmd-001")
        cache.check_and_mark("cmd-002")
        assert cache.size == 2
        # 不插入新条目，不应淘汰
        assert cache.check_and_mark("cmd-001") is False
        assert cache.size == 2


class TestDedupConcurrency:
    """并发安全。"""

    def test_concurrent_different_ids(self):
        """2 线程同时不同 ID 各自独立去重，无竞态。"""
        cache = DedupCache(ttl_seconds=300)
        results_a: list[bool] = []
        results_b: list[bool] = []

        def worker_a():
            for i in range(0, 100, 2):
                results_a.append(cache.check_and_mark(f"cmd-{i:04d}"))

        def worker_b():
            for i in range(1, 100, 2):
                results_b.append(cache.check_and_mark(f"cmd-{i:04d}"))

        t_a = threading.Thread(target=worker_a)
        t_b = threading.Thread(target=worker_b)
        t_a.start()
        t_b.start()
        t_a.join()
        t_b.join()

        # 所有不同 ID 的首次调用都应返回 True
        assert all(results_a), f"thread A had False results: {results_a}"
        assert all(results_b), f"thread B had False results: {results_b}"
        assert cache.size == 100

    def test_race_same_id_exactly_one_true(self):
        """竞速测试：相同 command_id 两线程同时竞速 → 恰好一个 True。"""
        cache = DedupCache(ttl_seconds=300)
        winner = [0]  # 记录 True 的次数
        loser = [0]   # 记录 False 的次数

        # 使用 barrier 确保两线程同时开始
        barrier = threading.Barrier(2, timeout=2)

        def racer():
            barrier.wait()
            if cache.check_and_mark("race-cmd"):
                winner[0] += 1
            else:
                loser[0] += 1

        threads = [threading.Thread(target=racer) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 恰好一个获胜，一个失败
        assert winner[0] == 1, f"Expected 1 winner, got {winner[0]}"
        assert loser[0] == 1, f"Expected 1 loser, got {loser[0]}"


class TestDedupClear:
    """clear 行为。"""

    def test_clear_removes_all(self):
        """clear 后所有条目被移除。"""
        cache = DedupCache(ttl_seconds=300)
        cache.check_and_mark("cmd-001")
        cache.check_and_mark("cmd-002")
        assert cache.size == 2

        cache.clear()
        assert cache.size == 0
        # 清除后可以重新插入
        assert cache.check_and_mark("cmd-001") is True


class TestDedupEdge:
    """边界条件。"""

    def test_invalid_ttl_raises(self):
        """ttl_seconds <= 0 时抛 ValueError。"""
        with pytest.raises(ValueError, match="ttl_seconds 必须 > 0"):
            DedupCache(ttl_seconds=0)

        with pytest.raises(ValueError, match="ttl_seconds 必须 > 0"):
            DedupCache(ttl_seconds=-1)

    def test_invalid_max_size_raises(self):
        """max_size <= 0 时抛 ValueError。"""
        with pytest.raises(ValueError, match="max_size 必须 > 0"):
            DedupCache(max_size=0)

    def test_contains_operator(self):
        """__contains__ 支持 'id' in cache 语法。"""
        cache = DedupCache(ttl_seconds=300)
        cache.check_and_mark("cmd-001")
        assert "cmd-001" in cache
        assert "cmd-002" not in cache

    def test_repr(self):
        """__repr__ 包含关键参数。"""
        cache = DedupCache(ttl_seconds=120, max_size=500)
        r = repr(cache)
        assert "120" in r
        assert "500" in r

    def test_default_values(self):
        """默认 TTL=300s, max_size=10000。"""
        cache = DedupCache()
        assert cache.check_and_mark("test") is True
        assert cache.check_and_mark("test") is False
