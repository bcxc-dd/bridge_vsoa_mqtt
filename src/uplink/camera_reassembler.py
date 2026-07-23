"""HCv3 LoRaWAN camera chunk parsing and JPEG reassembly."""

from __future__ import annotations

import base64
import binascii
import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional


HC_MAGIC = b"HC"
HC_VERSION = 3
HC_MIN_HEADER_LEN = 24
HC_CODEC_JPEG = 1
HP_MAGIC = b"HP"
HP_VERSION = 1
HP_HEADER_LEN = 16
HA_MAGIC = b"HA"
HA_VERSION = 1
ACK_RESEND_INTERVAL_SECONDS = 7.0


class CameraChunkError(ValueError):
    """Raised when an HCv3 packet is structurally invalid."""


@dataclass(frozen=True)
class CameraChunk:
    flags: int
    image_seq: int
    image_len: int
    chunk_index: int
    chunk_count: int
    chunk_len: int
    repeat_index: int
    repeat_count: int
    header_len: int
    codec: int
    image_crc32: int
    chunk_crc16: int
    data: bytes


@dataclass(frozen=True)
class CameraPoll:
    state_code: int
    image_seq: int
    chunk_count: int
    retx_round: int
    missing_count: int
    flags: int
    image_crc32: int


@dataclass
class CameraOutcome:
    state: str
    device_id: str
    app_id: str
    image_seq: int
    chunk_index: int = -1
    chunk_count: int = 0
    received_count: int = 0
    repeat_index: int = 0
    repeat_count: int = 1
    hp_state: str = ""
    expected_timeout_ms: int = 0
    missing: list[int] = field(default_factory=list)
    error: str = ""
    payload: Optional[dict[str, Any]] = None
    control_status: Optional[int] = None

    @property
    def complete(self) -> bool:
        return self.state == "complete" and self.payload is not None

    def to_event(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "event": "camera_reassembly",
            "device_id": self.device_id,
            "source": "lora",
            "adapter": "lora_camera_hcv3",
            "state": self.state,
            "image_seq": self.image_seq,
            "chunk_index": self.chunk_index,
            "chunk_number": self.chunk_index + 1 if self.chunk_index >= 0 else 0,
            "chunk_count": self.chunk_count,
            "received_count": self.received_count,
            "repeat_index": self.repeat_index,
            "repeat_count": self.repeat_count,
            "expected_timeout_ms": self.expected_timeout_ms,
            "missing": list(self.missing),
            "timestamp": int(time.time() * 1000),
        }
        if self.hp_state:
            result["hp_state"] = self.hp_state
        if self.error:
            result["error"] = self.error
        return result


@dataclass
class _ImageState:
    device_id: str
    app_id: str
    device_name: str
    image_seq: int
    image_len: int
    chunk_count: int
    image_crc32: int
    repeat_count: int
    rx_info: Any
    first_seen_at: float
    last_seen_at: float
    chunks: dict[int, bytes] = field(default_factory=dict)
    retransmit_requests: int = 0


@dataclass
class _CompletedState:
    completed_at: float
    last_ack_at: float
    app_id: str
    device_name: str
    chunk_count: int
    repeat_count: int
    ack_resend_sent: bool = False


def crc16_ccitt_false(data: bytes) -> int:
    crc = 0xFFFF
    for value in data:
        crc ^= value << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def parse_hcv3(payload: bytes, max_image_bytes: int = 65535) -> Optional[CameraChunk]:
    """Parse one HCv3 frame; return ``None`` for non-camera payloads."""
    if len(payload) < 2 or payload[:2] != HC_MAGIC:
        return None
    if len(payload) < HC_MIN_HEADER_LEN:
        raise CameraChunkError("HCv3 frame shorter than 24-byte header")
    if payload[2] != HC_VERSION:
        raise CameraChunkError(f"unsupported HC version: {payload[2]}")

    header_len = payload[16]
    image_len = int.from_bytes(payload[8:11], "little")
    chunk_index = payload[11]
    chunk_count = payload[12]
    chunk_len = payload[13]
    codec = payload[17]

    if header_len < HC_MIN_HEADER_LEN or header_len > len(payload):
        raise CameraChunkError(f"invalid HC header_len: {header_len}")
    if codec != HC_CODEC_JPEG:
        raise CameraChunkError(f"unsupported camera codec: {codec}")
    if image_len <= 0 or image_len > max_image_bytes:
        raise CameraChunkError(f"invalid image_len: {image_len}")
    if chunk_count <= 0 or chunk_index >= chunk_count:
        raise CameraChunkError(f"invalid chunk position: {chunk_index}/{chunk_count}")

    chunk_data = payload[header_len:]
    if chunk_len != len(chunk_data):
        raise CameraChunkError(
            f"chunk_len mismatch: header={chunk_len}, actual={len(chunk_data)}"
        )

    return CameraChunk(
        flags=payload[3],
        image_seq=int.from_bytes(payload[4:8], "little"),
        image_len=image_len,
        chunk_index=chunk_index,
        chunk_count=chunk_count,
        chunk_len=chunk_len,
        repeat_index=payload[14],
        repeat_count=max(1, payload[15]),
        header_len=header_len,
        codec=codec,
        image_crc32=int.from_bytes(payload[18:22], "little"),
        chunk_crc16=int.from_bytes(payload[22:24], "little"),
        data=chunk_data,
    )


def parse_hpv1(payload: bytes) -> Optional[CameraPoll]:
    """Parse one HPv1 status/ACK-poll frame; return ``None`` for other payloads."""
    if len(payload) < 2 or payload[:2] != HP_MAGIC:
        return None
    if len(payload) < HP_HEADER_LEN:
        raise CameraChunkError("HPv1 frame shorter than 16-byte header")
    if payload[2] != HP_VERSION:
        raise CameraChunkError(f"unsupported HP version: {payload[2]}")
    return CameraPoll(
        state_code=payload[3],
        image_seq=int.from_bytes(payload[4:8], "little"),
        chunk_count=payload[8],
        retx_round=payload[9],
        missing_count=payload[10],
        flags=payload[11],
        image_crc32=int.from_bytes(payload[12:16], "little"),
    )


def build_ha_payload(image_seq: int, status: int, missing: Optional[list[int]] = None) -> bytes:
    """Build an HA v1 ACK_OK or RETX_REQUEST binary payload."""
    missing = list(missing or [])
    if status not in (0, 1, 2):
        raise ValueError("HA status must be 0, 1 or 2")
    if len(missing) > 255 or any(index < 0 or index > 255 for index in missing):
        raise ValueError("HA missing chunk indexes must fit in uint8")
    command = 0 if status == 0 else 1
    return (
        HA_MAGIC
        + bytes((HA_VERSION, command))
        + int(image_seq).to_bytes(4, "little")
        + bytes((status, len(missing)))
        + bytes(missing)
    )


def build_chirpstack_downlink(
    app_id: str,
    dev_eui: str,
    binary_payload: bytes,
    fport: int = 3,
    confirmed: bool = False,
) -> tuple[str, str]:
    topic = f"application/{app_id}/device/{dev_eui}/command/down"
    body = {
        "devEui": dev_eui,
        "confirmed": confirmed,
        "fPort": fport,
        "data": base64.b64encode(binary_payload).decode("ascii"),
    }
    return topic, json.dumps(body, separators=(",", ":"))


class Hcv3CameraReassembler:
    """Thread-safe cache for out-of-order and repeated HCv3 chunks."""

    def __init__(
        self,
        timeout_seconds: float = 45,
        max_image_bytes: int = 8192,
        max_retransmit_requests: int = 3,
        uplink_fport: int = 2,
    ) -> None:
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self.max_image_bytes = max(1024, int(max_image_bytes))
        self.max_retransmit_requests = max(1, int(max_retransmit_requests))
        self.uplink_fport = int(uplink_fport)
        self._images: dict[tuple[str, int], _ImageState] = {}
        self._completed: dict[tuple[str, int], _CompletedState] = {}
        self._latest_sequence: dict[str, int] = {}
        self._lock = threading.Lock()

    def ingest(self, topic: str, message: dict[str, Any]) -> Optional[CameraOutcome]:
        encoded = message.get("data")
        if not isinstance(encoded, str) or not encoded:
            return None
        fport = message.get("fPort", message.get("f_port"))
        if fport is not None:
            try:
                if int(fport) != self.uplink_fport:
                    return None
            except (TypeError, ValueError):
                return None
        try:
            binary = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            return None

        try:
            poll = parse_hpv1(binary)
        except CameraChunkError as exc:
            if not binary.startswith(HP_MAGIC):
                return None
            device_id, app_id, _ = _message_identity(topic, message)
            return CameraOutcome(
                state="invalid", device_id=device_id or "unknown", app_id=app_id,
                image_seq=0, error=str(exc),
            )
        if poll is not None:
            return self._ingest_poll(topic, message, poll)

        try:
            chunk = parse_hcv3(binary, self.max_image_bytes)
        except CameraChunkError as exc:
            if not binary.startswith(HC_MAGIC):
                return None
            device_id, app_id, _ = _message_identity(topic, message)
            return CameraOutcome(
                state="invalid", device_id=device_id, app_id=app_id,
                image_seq=0, error=str(exc),
            )
        if chunk is None:
            return None

        device_id, app_id, device_name = _message_identity(topic, message)
        if not device_id:
            return CameraOutcome(
                state="invalid", device_id="unknown", app_id=app_id,
                image_seq=chunk.image_seq, chunk_index=chunk.chunk_index,
                chunk_count=chunk.chunk_count, error="missing LoRaWAN DevEUI",
            )

        if crc16_ccitt_false(chunk.data) != chunk.chunk_crc16:
            return CameraOutcome(
                state="chunk_crc_fail", device_id=device_id, app_id=app_id,
                image_seq=chunk.image_seq, chunk_index=chunk.chunk_index,
                chunk_count=chunk.chunk_count, repeat_index=chunk.repeat_index,
                repeat_count=chunk.repeat_count, missing=[chunk.chunk_index],
                error="chunk CRC16 mismatch",
            )

        now = time.monotonic()
        key = (device_id, chunk.image_seq)
        with self._lock:
            if not self._activate_sequence_locked(device_id, chunk.image_seq):
                return CameraOutcome(
                    state="stale_sequence", device_id=device_id, app_id=app_id,
                    image_seq=chunk.image_seq, chunk_index=chunk.chunk_index,
                    chunk_count=chunk.chunk_count, repeat_index=chunk.repeat_index,
                    repeat_count=chunk.repeat_count,
                )
            completed_at = self._completed.get(key)
            if completed_at is not None and now - completed_at.completed_at < self.timeout_seconds * 2:
                return CameraOutcome(
                    state="duplicate_complete", device_id=device_id, app_id=app_id,
                    image_seq=chunk.image_seq, chunk_index=chunk.chunk_index,
                    chunk_count=chunk.chunk_count, received_count=chunk.chunk_count,
                    repeat_index=chunk.repeat_index, repeat_count=chunk.repeat_count,
                )
            state = self._images.get(key)
            if state is None:
                state = _ImageState(
                    device_id=device_id, app_id=app_id, device_name=device_name,
                    image_seq=chunk.image_seq, image_len=chunk.image_len,
                    chunk_count=chunk.chunk_count, image_crc32=chunk.image_crc32,
                    repeat_count=chunk.repeat_count, rx_info=message.get("rxInfo"),
                    first_seen_at=now, last_seen_at=now,
                )
                self._images[key] = state
            elif not _metadata_matches(state, chunk):
                return CameraOutcome(
                    state="invalid", device_id=device_id, app_id=app_id,
                    image_seq=chunk.image_seq, chunk_index=chunk.chunk_index,
                    chunk_count=chunk.chunk_count, received_count=len(state.chunks),
                    error="HCv3 metadata changed within one image_seq",
                )

            state.last_seen_at = now
            duplicate = chunk.chunk_index in state.chunks
            if not duplicate:
                state.chunks[chunk.chunk_index] = chunk.data
            received_count = len(state.chunks)
            if received_count < state.chunk_count:
                return CameraOutcome(
                    state="duplicate" if duplicate else "progress",
                    device_id=device_id, app_id=app_id, image_seq=chunk.image_seq,
                    chunk_index=chunk.chunk_index, chunk_count=state.chunk_count,
                    received_count=received_count, repeat_index=chunk.repeat_index,
                    repeat_count=chunk.repeat_count,
                    expected_timeout_ms=_expected_timeout_ms(state),
                    missing=_missing_chunks(state),
                )

            image = b"".join(state.chunks[index] for index in range(state.chunk_count))
            image = image[:state.image_len]
            del self._images[key]

        actual_crc32 = binascii.crc32(image) & 0xFFFFFFFF
        if actual_crc32 != chunk.image_crc32:
            return CameraOutcome(
                state="image_crc_fail", device_id=device_id, app_id=app_id,
                image_seq=chunk.image_seq, chunk_index=chunk.chunk_index,
                chunk_count=chunk.chunk_count, received_count=chunk.chunk_count,
                error="image CRC32 mismatch", control_status=2,
            )
        if not image.startswith(b"\xff\xd8") or not image.endswith(b"\xff\xd9"):
            return CameraOutcome(
                state="jpeg_invalid", device_id=device_id, app_id=app_id,
                image_seq=chunk.image_seq, chunk_index=chunk.chunk_index,
                chunk_count=chunk.chunk_count, received_count=chunk.chunk_count,
                error="JPEG SOI/EOI marker mismatch", control_status=2,
            )

        with self._lock:
            self._completed[key] = _CompletedState(
                completed_at=now, last_ack_at=now, app_id=app_id,
                device_name=device_name, chunk_count=chunk.chunk_count,
                repeat_count=chunk.repeat_count,
            )

        image_payload = {
            "device_id": device_id,
            "deviceName": device_name or device_id,
            "devEUI": device_id,
            "type": "camera",
            "status": "online",
            "source": "lora",
            "camera_transport": "lorawan_hcv3",
            "format": "jpeg",
            "image_mime": "image/jpeg",
            "image_b64": base64.b64encode(image).decode("ascii"),
            "bytes": len(image),
            "image_seq": chunk.image_seq,
            "chunk_count": chunk.chunk_count,
            "repeat_count": chunk.repeat_count,
            "image_crc32": f"{chunk.image_crc32:08x}",
            "fPort": self.uplink_fport,
            "timestamp": int(time.time() * 1000),
        }
        if message.get("rxInfo") is not None:
            image_payload["rxInfo"] = message["rxInfo"]
        if isinstance(message.get("deviceInfo"), dict):
            image_payload["deviceInfo"] = dict(message["deviceInfo"])
        return CameraOutcome(
            state="complete", device_id=device_id, app_id=app_id,
            image_seq=chunk.image_seq, chunk_index=chunk.chunk_index,
            chunk_count=chunk.chunk_count, received_count=chunk.chunk_count,
            repeat_index=chunk.repeat_index, repeat_count=chunk.repeat_count,
            expected_timeout_ms=int((chunk.chunk_count * chunk.repeat_count + 8) * 1000),
            payload=image_payload, control_status=0,
        )

    def _ingest_poll(
        self,
        topic: str,
        message: dict[str, Any],
        poll: CameraPoll,
    ) -> CameraOutcome:
        device_id, app_id, device_name = _message_identity(topic, message)
        device_id = device_id or "unknown"
        now = time.monotonic()
        key = (device_id, poll.image_seq)
        hp_state = _hp_state_name(poll.state_code)
        with self._lock:
            if not self._activate_sequence_locked(device_id, poll.image_seq):
                return CameraOutcome(
                    state="stale_sequence", device_id=device_id, app_id=app_id,
                    image_seq=poll.image_seq, chunk_count=poll.chunk_count,
                    hp_state=hp_state,
                )
            completed = self._completed.get(key)
            if completed is not None:
                should_resend_ack = (
                    not completed.ack_resend_sent
                    and now - completed.last_ack_at >= ACK_RESEND_INTERVAL_SECONDS
                )
                if should_resend_ack:
                    completed.last_ack_at = now
                    completed.ack_resend_sent = True
                return CameraOutcome(
                    state="hp_ack_poll" if should_resend_ack else "hp_status",
                    device_id=device_id, app_id=app_id or completed.app_id,
                    image_seq=poll.image_seq, chunk_count=completed.chunk_count,
                    received_count=completed.chunk_count, repeat_count=completed.repeat_count,
                    hp_state=hp_state,
                    control_status=0 if should_resend_ack else None,
                )

            state = self._images.get(key)
            if state is None:
                return CameraOutcome(
                    state="hp_status", device_id=device_id, app_id=app_id,
                    image_seq=poll.image_seq, chunk_count=poll.chunk_count,
                    hp_state=hp_state,
                )

            missing = _missing_chunks(state)
            expected_ms = _expected_timeout_ms(state)
            timed_out = (now - state.first_seen_at) * 1000 >= expected_ms
            if timed_out and missing:
                state.retransmit_requests += 1
                abandoned = state.retransmit_requests > self.max_retransmit_requests
                if abandoned:
                    del self._images[key]
                return CameraOutcome(
                    state="abandoned" if abandoned else "timeout",
                    device_id=device_id, app_id=app_id or state.app_id,
                    image_seq=poll.image_seq, chunk_count=state.chunk_count,
                    received_count=len(state.chunks), repeat_count=state.repeat_count,
                    hp_state=hp_state, expected_timeout_ms=expected_ms,
                    missing=missing,
                    error="image reassembly timed out",
                    control_status=None if abandoned else 1,
                )

            return CameraOutcome(
                state="hp_status", device_id=device_id, app_id=app_id or state.app_id,
                image_seq=poll.image_seq, chunk_count=state.chunk_count,
                received_count=len(state.chunks), repeat_count=state.repeat_count,
                hp_state=hp_state, expected_timeout_ms=expected_ms,
                missing=missing,
            )

    def expire(self, now: Optional[float] = None) -> list[CameraOutcome]:
        """Return timeout outcomes and retain caches while retries remain."""
        current = time.monotonic() if now is None else now
        outcomes: list[CameraOutcome] = []
        with self._lock:
            completed_ttl = self.timeout_seconds * 2
            for key, completed in list(self._completed.items()):
                if current - completed.completed_at >= completed_ttl:
                    del self._completed[key]
            for key, state in list(self._images.items()):
                if self._latest_sequence.get(state.device_id) != state.image_seq:
                    del self._images[key]
                    continue
                expected_ms = _expected_timeout_ms(state)
                if (current - state.first_seen_at) * 1000 < expected_ms:
                    continue
                missing = _missing_chunks(state)
                state.retransmit_requests += 1
                abandoned = state.retransmit_requests > self.max_retransmit_requests
                outcomes.append(CameraOutcome(
                    state="abandoned" if abandoned else "timeout",
                    device_id=state.device_id, app_id=state.app_id,
                    image_seq=state.image_seq, chunk_count=state.chunk_count,
                    received_count=len(state.chunks), missing=missing,
                    repeat_count=state.repeat_count,
                    expected_timeout_ms=expected_ms,
                    error="image reassembly timed out",
                    control_status=None if abandoned else 1,
                ))
                if abandoned:
                    del self._images[key]
                else:
                    state.first_seen_at = current
                    state.last_seen_at = current
        return outcomes

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._images)

    def is_latest(self, device_id: str, image_seq: int) -> bool:
        with self._lock:
            return self._latest_sequence.get(device_id) == image_seq

    def _activate_sequence_locked(self, device_id: str, image_seq: int) -> bool:
        latest = self._latest_sequence.get(device_id)
        if latest is not None:
            if image_seq == latest:
                return True
            if not _sequence_is_newer(image_seq, latest):
                return False

        self._latest_sequence[device_id] = image_seq
        for key in list(self._images):
            if key[0] == device_id and key[1] != image_seq:
                del self._images[key]
        for key in list(self._completed):
            if key[0] == device_id and key[1] != image_seq:
                del self._completed[key]
        return True


def _message_identity(topic: str, message: dict[str, Any]) -> tuple[str, str, str]:
    device_info = message.get("deviceInfo")
    if not isinstance(device_info, dict):
        device_info = {}
    parts = topic.rstrip("/").split("/")
    topic_app = parts[1] if len(parts) >= 4 and parts[0] == "application" else ""
    topic_device = parts[3] if len(parts) >= 4 and parts[0] == "application" and parts[2] == "device" else ""
    device_id = str(
        device_info.get("devEui")
        or message.get("devEUI")
        or message.get("dev_eui")
        or topic_device
        or ""
    )
    app_id = str(device_info.get("applicationId") or topic_app or "")
    device_name = str(device_info.get("deviceName") or message.get("deviceName") or device_id)
    return device_id, app_id, device_name


def _metadata_matches(state: _ImageState, chunk: CameraChunk) -> bool:
    return (
        state.image_len == chunk.image_len
        and state.chunk_count == chunk.chunk_count
        and state.image_crc32 == chunk.image_crc32
        and state.repeat_count == chunk.repeat_count
    )


def _sequence_is_newer(candidate: int, current: int) -> bool:
    """Compare uint32 image sequences while allowing wraparound."""
    delta = (candidate - current) & 0xFFFFFFFF
    return 0 < delta < 0x80000000


def _missing_chunks(state: _ImageState) -> list[int]:
    return [index for index in range(state.chunk_count) if index not in state.chunks]


def _expected_timeout_ms(state: _ImageState) -> int:
    return int(max(1, state.chunk_count) * max(1, state.repeat_count) * 1000 + 8000)


def _hp_state_name(state_code: int) -> str:
    return {
        0: "IDLE",
        1: "IMG",
        2: "WAIT",
        3: "RETX",
    }.get(state_code, f"UNKNOWN_{state_code}")
