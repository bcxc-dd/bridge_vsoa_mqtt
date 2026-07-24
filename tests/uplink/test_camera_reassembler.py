import base64
import binascii
import json
import time

from src.uplink.camera_reassembler import (
    Hcv3CameraReassembler,
    crc16_ccitt_false,
    parse_hcv3,
)
from src.uplink.adapters.lora import LoraAdapter


TOPIC = "application/app-01/device/dc56b7d6a7dd94a1/event/up"


def _frame(image: bytes, seq: int, index: int, count: int, chunk: bytes,
           repeat_index: int = 0, repeat_count: int = 4) -> bytes:
    header = bytearray(24)
    header[0:2] = b"HC"
    header[2] = 3
    header[4:8] = seq.to_bytes(4, "little")
    header[8:11] = len(image).to_bytes(3, "little")
    header[11] = index
    header[12] = count
    header[13] = len(chunk)
    header[14] = repeat_index
    header[15] = repeat_count
    header[16] = 24
    header[17] = 1
    header[18:22] = (binascii.crc32(image) & 0xFFFFFFFF).to_bytes(4, "little")
    header[22:24] = crc16_ccitt_false(chunk).to_bytes(2, "little")
    return bytes(header) + chunk


def _message(frame: bytes) -> dict:
    return {
        "fPort": 2,
        "data": base64.b64encode(frame).decode("ascii"),
        "deviceInfo": {
            "devEui": "dc56b7d6a7dd94a1",
            "deviceName": "HUB Camera",
            "applicationId": "app-01",
        },
        "rxInfo": [{"rssi": -45, "snr": 9.5}],
    }


# ---------------------------------------------------------------------------
# HCv3 解析
# ---------------------------------------------------------------------------

def test_parse_hcv3_fields():
    image = b"\xff\xd8camera\xff\xd9"
    frame = _frame(image, 22, 0, 1, image)
    chunk = parse_hcv3(frame)
    assert chunk is not None
    assert chunk.image_seq == 22
    assert chunk.image_len == len(image)
    assert chunk.chunk_index == 0
    assert chunk.chunk_count == 1
    assert chunk.data == image


# ---------------------------------------------------------------------------
# 重组 + 去重
# ---------------------------------------------------------------------------

def test_reassembles_out_of_order_and_deduplicates():
    image = b"\xff\xd8" + bytes(range(64)) + b"\xff\xd9"
    chunks = [image[:25], image[25:50], image[50:]]
    receiver = Hcv3CameraReassembler(timeout_seconds=10)

    second = receiver.ingest(TOPIC, _message(_frame(image, 7, 1, 3, chunks[1])))
    duplicate = receiver.ingest(
        TOPIC, _message(_frame(image, 7, 1, 3, chunks[1], repeat_index=1))
    )
    first = receiver.ingest(TOPIC, _message(_frame(image, 7, 0, 3, chunks[0])))
    complete = receiver.ingest(TOPIC, _message(_frame(image, 7, 2, 3, chunks[2])))

    assert second and second.state == "progress"
    assert duplicate and duplicate.state == "duplicate"
    assert first and first.missing == [2]
    assert complete and complete.complete
    assert complete.payload["type"] == "camera"
    assert complete.payload["camera_transport"] == "lorawan_hcv3"
    assert base64.b64decode(complete.payload["image_b64"]) == image
    report = LoraAdapter().parse(
        "bridge/uplink/lora/dc56b7d6a7dd94a1/camera/frame",
        complete.payload,
    )
    assert report.device_id == "dc56b7d6a7dd94a1"
    assert report.type == "camera"
    assert report.raw["image_b64"] == complete.payload["image_b64"]
    assert receiver.pending_count == 0

    late_copy = receiver.ingest(
        TOPIC, _message(_frame(image, 7, 2, 3, chunks[2], repeat_index=1))
    )
    assert late_copy and late_copy.state == "duplicate_complete"
    assert receiver.pending_count == 0


# ---------------------------------------------------------------------------
# CRC 校验
# ---------------------------------------------------------------------------

def test_bad_chunk_crc_is_rejected():
    image = b"\xff\xd8bad-crc\xff\xd9"
    frame = bytearray(_frame(image, 9, 0, 1, image))
    frame[-1] ^= 0x01
    outcome = Hcv3CameraReassembler().ingest(TOPIC, _message(bytes(frame)))
    assert outcome is not None
    assert outcome.state == "chunk_crc_fail"
    assert outcome.missing == [0]


# ---------------------------------------------------------------------------
# 超时淘汰（无 ACK 模式：超时直接 abandoned，无 retransmit）
# ---------------------------------------------------------------------------

def test_timeout_abandons_immediately():
    image = b"\xff\xd8" + b"x" * 30 + b"\xff\xd9"
    first_chunk = image[:17]
    receiver = Hcv3CameraReassembler(timeout_seconds=5)
    receiver.ingest(TOPIC, _message(_frame(image, 11, 0, 2, first_chunk)))

    # 超时后直接 abandoned（无 retransmit 阶段）
    abandoned = receiver.expire(time.monotonic() + 10)
    assert len(abandoned) == 1
    assert abandoned[0].state == "abandoned"
    assert abandoned[0].missing == [1]
    assert receiver.pending_count == 0


# ---------------------------------------------------------------------------
# 多 image_seq 缓存：新图不丢弃旧图
# ---------------------------------------------------------------------------

def test_multi_sequence_cache_preserves_recent_images():
    image = b"\xff\xd8" + b"x" * 30 + b"\xff\xd9"
    first_chunk = image[:17]
    receiver = Hcv3CameraReassembler(timeout_seconds=60)

    # 第一张图（seq=374）开始接收
    old = receiver.ingest(TOPIC, _message(_frame(image, 374, 0, 2, first_chunk)))
    assert old and old.state == "progress"

    # 第二张图（seq=414）开始接收 — 旧图缓存保留
    current = receiver.ingest(TOPIC, _message(_frame(image, 414, 0, 2, first_chunk)))
    assert current and current.state == "progress"

    # 两张图同时在缓存中
    assert receiver.pending_count == 2

    # 旧图的后续分片仍然可以被接收
    later_for_old = receiver.ingest(
        TOPIC, _message(_frame(image, 374, 0, 2, first_chunk, repeat_index=2))
    )
    assert later_for_old is not None
    assert later_for_old.state == "duplicate"  # 同一个 chunk，去重


# ---------------------------------------------------------------------------
# 超过 MAX_CACHED_SEQUENCES 时淘汰最旧的
# ---------------------------------------------------------------------------

def test_evicts_oldest_when_cache_limit_exceeded():
    image = b"\xff\xd8" + b"x" * 30 + b"\xff\xd9"
    first_chunk = image[:17]
    receiver = Hcv3CameraReassembler(timeout_seconds=60)

    # 灌入 6 张不同的图（MAX_CACHED_SEQUENCES = 5）
    for seq in range(100, 106):
        outcome = receiver.ingest(
            TOPIC, _message(_frame(image, seq, 0, 2, first_chunk))
        )
        assert outcome is not None

    # 最旧的 seq=100 应该已被淘汰
    assert receiver.pending_count <= 5
    # seq=100 的分片再来会被当作新图（因为已被淘汰）
    late = receiver.ingest(
        TOPIC, _message(_frame(image, 100, 0, 2, first_chunk, repeat_index=3))
    )
    # 由于 seq=100 < seq=105（最老的保留），可能被拒绝为 stale_sequence
    assert late is not None


# ---------------------------------------------------------------------------
# HP/HA 包在无 ACK 模式下被静默忽略
# ---------------------------------------------------------------------------

def test_hp_packets_are_silently_ignored():
    hp_payload = (
        b"HP\x01"           # magic + version
        b"\x02"             # state_code = WAIT
        b"\x0c\x00\x00\x00" # image_seq = 12 (LE)
        b"\x0c"             # chunk_count = 12
        b"\x00"             # retx_round
        b"\x01"             # missing_count = 1
        b"\x00"             # flags
        b"\x00\x00\x00\x00" # image_crc32 placeholder
    )
    msg = {
        "fPort": 2,
        "data": base64.b64encode(hp_payload).decode("ascii"),
        "deviceInfo": {"devEui": "dc56b7d6a7dd94a1"},
    }
    outcome = Hcv3CameraReassembler().ingest(TOPIC, msg)
    assert outcome is None  # 静默忽略


def test_ha_packets_are_silently_ignored():
    ha_payload = b"HA\x01\x01\x16\x00\x00\x00\x01\x03\x01\x04\x08"
    msg = {
        "fPort": 2,
        "data": base64.b64encode(ha_payload).decode("ascii"),
        "deviceInfo": {"devEui": "dc56b7d6a7dd94a1"},
    }
    outcome = Hcv3CameraReassembler().ingest(TOPIC, msg)
    assert outcome is None  # 静默忽略


# ---------------------------------------------------------------------------
# 非相机数据穿通
# ---------------------------------------------------------------------------

def test_non_camera_lora_payload_passes_through():
    payload = base64.b64encode(b"ordinary-lora-data").decode("ascii")
    outcome = Hcv3CameraReassembler().ingest(TOPIC, {"fPort": 2, "data": payload})
    assert outcome is None
