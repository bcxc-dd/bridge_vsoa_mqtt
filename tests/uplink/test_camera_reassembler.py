import base64
import binascii
import json
import time

from src.uplink.camera_reassembler import (
    Hcv3CameraReassembler,
    build_chirpstack_downlink,
    build_ha_payload,
    crc16_ccitt_false,
    parse_hcv3,
    parse_hpv1,
)
from src.uplink.adapters.lora import LoraAdapter


TOPIC = "application/app-01/device/dc56b7d6a7dd94a1/event/up"


def _frame(image: bytes, seq: int, index: int, count: int, chunk: bytes,
           repeat_index: int = 0, repeat_count: int = 2) -> bytes:
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


def _hp(seq: int, state: int = 2, chunk_count: int = 2, missing_count: int = 0) -> bytes:
    header = bytearray(16)
    header[0:2] = b"HP"
    header[2] = 1
    header[3] = state
    header[4:8] = seq.to_bytes(4, "little")
    header[8] = chunk_count
    header[10] = missing_count
    return bytes(header)


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


def test_parse_hpv1_status_poll_fields():
    poll = parse_hpv1(_hp(607, state=2, chunk_count=12, missing_count=1))
    assert poll is not None
    assert poll.image_seq == 607
    assert poll.state_code == 2
    assert poll.chunk_count == 12
    assert poll.missing_count == 1


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
    assert complete.control_status == 0
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


def test_bad_chunk_crc_is_rejected():
    image = b"\xff\xd8bad-crc\xff\xd9"
    frame = bytearray(_frame(image, 9, 0, 1, image))
    frame[-1] ^= 0x01
    outcome = Hcv3CameraReassembler().ingest(TOPIC, _message(bytes(frame)))
    assert outcome is not None
    assert outcome.state == "chunk_crc_fail"
    assert outcome.missing == [0]


def test_timeout_reports_missing_chunks_then_abandons():
    image = b"\xff\xd8" + b"x" * 30 + b"\xff\xd9"
    first_chunk = image[:17]
    receiver = Hcv3CameraReassembler(
        timeout_seconds=5, max_retransmit_requests=1
    )
    receiver.ingest(TOPIC, _message(_frame(image, 11, 0, 2, first_chunk)))

    timed_out = receiver.expire(time.monotonic() + 13)
    assert len(timed_out) == 1
    assert timed_out[0].state == "timeout"
    assert timed_out[0].missing == [1]
    assert timed_out[0].control_status == 1

    abandoned = receiver.expire(time.monotonic() + 26)
    assert len(abandoned) == 1
    assert abandoned[0].state == "abandoned"
    assert receiver.pending_count == 0


def test_hp_poll_triggers_dynamic_retransmit_request():
    image = b"\xff\xd8" + b"x" * 30 + b"\xff\xd9"
    first_chunk = image[:17]
    receiver = Hcv3CameraReassembler(max_retransmit_requests=1)
    receiver.ingest(TOPIC, _message(_frame(image, 12, 0, 2, first_chunk)))

    early = receiver.ingest(TOPIC, _message(_hp(12, missing_count=1)))
    assert early is not None
    assert early.state == "hp_status"
    assert early.control_status is None

    with receiver._lock:
        state = receiver._images[("dc56b7d6a7dd94a1", 12)]
        state.first_seen_at -= 13
    timed_out = receiver.ingest(TOPIC, _message(_hp(12, missing_count=1)))
    assert timed_out is not None
    assert timed_out.state == "timeout"
    assert timed_out.control_status == 1
    assert timed_out.missing == [1]


def test_hp_poll_does_not_ack_every_time_after_complete():
    image = b"\xff\xd8" + b"x" * 30 + b"\xff\xd9"
    chunks = [image[:17], image[17:]]
    receiver = Hcv3CameraReassembler()
    complete = receiver.ingest(TOPIC, _message(_frame(image, 13, 0, 2, chunks[0])))
    assert complete and complete.control_status is None
    complete = receiver.ingest(TOPIC, _message(_frame(image, 13, 1, 2, chunks[1])))
    assert complete and complete.control_status == 0
    with receiver._lock:
        ack_at = receiver._completed[("dc56b7d6a7dd94a1", 13)].last_ack_at

    first_hp = receiver.ingest(TOPIC, _message(_hp(13)))
    assert first_hp is not None
    assert first_hp.state == "hp_status"
    assert first_hp.control_status is None
    with receiver._lock:
        assert receiver._completed[("dc56b7d6a7dd94a1", 13)].last_ack_at == ack_at

    with receiver._lock:
        completed = receiver._completed[("dc56b7d6a7dd94a1", 13)]
        completed.last_ack_at -= 8
    delayed_hp = receiver.ingest(TOPIC, _message(_hp(13)))
    assert delayed_hp is not None
    assert delayed_hp.state == "hp_ack_poll"
    assert delayed_hp.control_status == 0

    with receiver._lock:
        completed = receiver._completed[("dc56b7d6a7dd94a1", 13)]
        completed.last_ack_at -= 8
    later_hp = receiver.ingest(TOPIC, _message(_hp(13)))
    assert later_hp is not None
    assert later_hp.state == "hp_status"
    assert later_hp.control_status is None


def test_new_sequence_discards_old_session_and_suppresses_old_retx():
    image = b"\xff\xd8" + b"x" * 30 + b"\xff\xd9"
    first_chunk = image[:17]
    receiver = Hcv3CameraReassembler(max_retransmit_requests=2)

    old = receiver.ingest(TOPIC, _message(_frame(image, 374, 0, 2, first_chunk)))
    assert old and old.state == "progress"
    current = receiver.ingest(TOPIC, _message(_frame(image, 414, 0, 2, first_chunk)))
    assert current and current.state == "progress"
    assert receiver.pending_count == 1
    assert not receiver.is_latest("dc56b7d6a7dd94a1", 374)
    assert receiver.is_latest("dc56b7d6a7dd94a1", 414)

    stale_hp = receiver.ingest(TOPIC, _message(_hp(374, missing_count=1)))
    assert stale_hp is not None
    assert stale_hp.state == "stale_sequence"
    assert stale_hp.control_status is None

    timed_out = receiver.expire(time.monotonic() + 13)
    assert len(timed_out) == 1
    assert timed_out[0].image_seq == 414
    assert timed_out[0].control_status == 1


def test_ha_retransmit_and_chirpstack_downlink():
    binary = build_ha_payload(22, 1, [1, 4, 8])
    assert binary == b"HA\x01\x01\x16\x00\x00\x00\x01\x03\x01\x04\x08"
    topic, encoded = build_chirpstack_downlink(
        "app-01", "dc56b7d6a7dd94a1", binary, fport=3
    )
    body = json.loads(encoded)
    assert topic == "application/app-01/device/dc56b7d6a7dd94a1/command/down"
    assert body["fPort"] == 3
    assert body["confirmed"] is False
    assert base64.b64decode(body["data"]) == binary


def test_non_camera_lora_payload_passes_through():
    payload = base64.b64encode(b"ordinary-lora-data").decode("ascii")
    outcome = Hcv3CameraReassembler().ingest(TOPIC, {"fPort": 2, "data": payload})
    assert outcome is None
