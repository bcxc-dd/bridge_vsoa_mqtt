"""
Unit tests for the device registry.
"""

from __future__ import annotations

import threading
import time

import pytest

from src.uplink.adapters.base import UplinkReport


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_report(device_id: str, **kwargs) -> UplinkReport:
    r = UplinkReport()
    r.device_id = device_id
    r.timestamp = int(time.time() * 1000)
    for k, v in kwargs.items():
        setattr(r, k, v)
    return r


# ========================================================================
# Basic CRUD
# ========================================================================

class TestUpsert:
    def test_create_new_device(self, device_registry):
        r = _make_report("dev1", raw={"temperature": 25.0}, source="lora", adapter="lora_adapter",
                         type="temperature")
        dev, created = device_registry.upsert(r)
        assert created is True
        assert dev.device_id == "dev1"
        assert dev.raw["temperature"] == 25.0
        assert dev.report_count == 1
        assert device_registry.count == 1

    def test_update_existing_device(self, device_registry):
        r1 = _make_report("dev1", raw={"temperature": 25.0})
        device_registry.upsert(r1)

        r2 = _make_report("dev1", raw={"temperature": 26.0})
        dev, created = device_registry.upsert(r2)
        assert created is False
        assert dev.raw["temperature"] == 26.0
        assert dev.report_count == 2
        assert device_registry.count == 1  # still 1

    def test_device_limit(self, device_registry):
        # Create a small registry (max 2)
        from src.device_registry import DeviceRegistry
        small = DeviceRegistry(max_devices=2)
        r1 = _make_report("a", raw={"temperature": 1.0})
        r2 = _make_report("b", raw={"temperature": 2.0})
        r3 = _make_report("c", raw={"temperature": 3.0})
        d1, created = small.upsert(r1)
        assert created and d1 is not None
        d2, created = small.upsert(r2)
        assert created and d2 is not None
        d3, created = small.upsert(r3)
        assert d3 is None and not created
        assert small.count == 2


class TestLookup:
    def test_existing(self, device_registry):
        device_registry.upsert(_make_report("dev1"))
        assert device_registry.lookup("dev1") is not None

    def test_non_existent(self, device_registry):
        assert device_registry.lookup("ghost") is None


class TestListAll:
    def test_empty(self, device_registry):
        assert device_registry.list_all() == []

    def test_with_devices(self, device_registry):
        device_registry.upsert(_make_report("a"))
        device_registry.upsert(_make_report("b"))
        assert len(device_registry.list_all()) == 2


class TestToJson:
    def test_includes_all_keys(self, device_registry):
        r = _make_report("dev1", raw={"temperature": 25.0, "humidity": 60.0}, source="lora",
                         adapter="lora_adapter", type="multi", status="online",
                         unit="celsius")
        r.topic = "bridge/uplink/lora/dev1/data"
        r.timestamp = 1783329001000
        dev, _ = device_registry.upsert(r)
        j = dev.to_json()
        assert j["device_id"] == "dev1"
        assert j["raw"]["temperature"] == 25.0
        assert j["raw"]["humidity"] == 60.0
        assert j["source"] == "lora"
        assert j["adapter"] == "lora_adapter"
        assert j["report_count"] == 1
        assert j["last_topic"] == "bridge/uplink/lora/dev1/data"
        assert "name" in j
        assert "registered_at" in j


# ========================================================================
# Thread safety
# ========================================================================

class TestThreadSafety:
    def test_concurrent_upserts(self):
        from src.device_registry import DeviceRegistry
        reg = DeviceRegistry(max_devices=200)

        errors = []

        def hammer():
            for i in range(50):
                r = _make_report(f"dev_{i % 20}", raw={"temperature": float(i)})
                d, created = reg.upsert(r)
                if d is None:
                    errors.append("upsert failed")

        threads = [threading.Thread(target=hammer) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert reg.count <= 20  # at most 20 unique devices
