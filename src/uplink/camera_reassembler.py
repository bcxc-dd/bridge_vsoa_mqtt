"""HCv3 LoRaWAN camera chunk parsing and JPEG reassembly.

No-ACK multi-repeat mode: the HUB sends every image chunk multiple times
across several repeat rounds.  The reassembler keeps up to
*MAX_CACHED_SEQUENCES* concurrent ``image_seq`` caches per device and
expires them purely by timeout — no HA / ACK / RETX downlink is ever sent.
"""

from __future__ import annotations

import base64
import binascii
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional


HC_MAGIC = b"HC"
HC_VERSION = 3
HC_MIN_HEADER_LEN = 24
HC_CODEC_JPEG = 1

# Maximum number of concurrent image_seq caches kept per device.
MAX_CACHED_SEQUENCES = 5


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


@dataclass
class CameraOutcome:
    """Result produced after ingesting one HCv3 chunk (or a timeout expiry)."""

    state: str
    device_id: str
    app_id: str
    image_seq: int
    chunk_index: int = -1
    chunk_count: int = 0
    received_count: int = 0
    repeat_index: int = 0
    repeat_count: int = 1
    expected_timeout_ms: int = 0
    missing: list[int] = field(default_factory=list)
    error: str = ""
    payload: Optional[dict[str, Any]] = None

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


@dataclass
class _CompletedState:
    completed_at: float
    app_id: str
    device_name: str
    chunk_count: int
    repeat_count: int


# ---------------------------------------------------------------------------
# CRC16-CCITT-FALSE (same polynomial used by the HUB)
# ---------------------------------------------------------------------------

def crc16_ccitt_false(data: bytes) -> int:
    crc = 0xFFFF
    for value in data:
        crc ^= value << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


# ---------------------------------------------------------------------------
# HCv3 parser
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Reassembler
# ---------------------------------------------------------------------------

class Hcv3CameraReassembler:
    """Thread-safe cache for out-of-order and repeated HCv3 chunks.

    No-ACK multi-repeat mode.  Keeps up to *MAX_CACHED_SEQUENCES*
    concurrent ``image_seq`` caches per device and expires them purely
    by timeout — no HA / ACK / RETX downlink is ever sent.
    """

    def __init__(
        self,
        timeout_seconds: float = 60,
        max_image_bytes: int = 8192,
        uplink_fport: int = 2,
    ) -> None:
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self.max_image_bytes = max(1024, int(max_image_bytes))
        self.uplink_fport = int(uplink_fport)
        self._images: dict[tuple[str, int], _ImageState] = {}
        self._completed: dict[tuple[str, int], _CompletedState] = {}
        self._active_sequences: dict[str, list[int]] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def ingest(self, topic: str, message: dict[str, Any]) -> Optional[CameraOutcome]:
        """Ingest one uplink message.  Return an outcome or ``None``."""
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

        # HP / HA packets are silently ignored in no-ACK mode.
        if len(binary) >= 2 and binary[:2] in {b"HP", b"HA"}:
            return None

        try:
            chunk = parse_hcv3(binary, self.max_image_bytes)
        except CameraChunkError as exc:
            if not binary.startswith(HC_MAGIC):
                return None
            device_id, app_id, _ = _message_identity(topic, message)
            return CameraOutcome(
                state="invalid", device_id=device_id or "unknown", app_id=app_id,
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

        # ---- chunk CRC16 ----
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
            # ---- register / evict sequences ----
            if not self._activate_sequence_locked(device_id, chunk.image_seq):
                return CameraOutcome(
                    state="stale_sequence", device_id=device_id, app_id=app_id,
                    image_seq=chunk.image_seq, chunk_index=chunk.chunk_index,
                    chunk_count=chunk.chunk_count, repeat_index=chunk.repeat_index,
                    repeat_count=chunk.repeat_count,
                )

            # ---- already completed? ----
            completed = self._completed.get(key)
            if completed is not None:
                return CameraOutcome(
                    state="duplicate_complete", device_id=device_id, app_id=app_id,
                    image_seq=chunk.image_seq, chunk_index=chunk.chunk_index,
                    chunk_count=chunk.chunk_count, received_count=chunk.chunk_count,
                    repeat_index=chunk.repeat_index, repeat_count=chunk.repeat_count,
                )

            # ---- fetch or create image state ----
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

            # ---- dedup store ----
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

            # ---- all chunks received → assemble ----
            image = b"".join(state.chunks[index] for index in range(state.chunk_count))
            image = image[:state.image_len]
            del self._images[key]

        # ---- image-level CRC32 ----
        actual_crc32 = binascii.crc32(image) & 0xFFFFFFFF
        if actual_crc32 != chunk.image_crc32:
            return CameraOutcome(
                state="image_crc_fail", device_id=device_id, app_id=app_id,
                image_seq=chunk.image_seq, chunk_index=chunk.chunk_index,
                chunk_count=chunk.chunk_count, received_count=chunk.chunk_count,
                error="image CRC32 mismatch",
            )

        # ---- JPEG markers ----
        if not image.startswith(b"\xff\xd8") or not image.endswith(b"\xff\xd9"):
            return CameraOutcome(
                state="jpeg_invalid", device_id=device_id, app_id=app_id,
                image_seq=chunk.image_seq, chunk_index=chunk.chunk_index,
                chunk_count=chunk.chunk_count, received_count=chunk.chunk_count,
                error="JPEG SOI/EOI marker mismatch",
            )

        # ---- mark completed ----
        with self._lock:
            self._completed[key] = _CompletedState(
                completed_at=now, app_id=app_id,
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
            payload=image_payload,
        )

    def expire(self, now: Optional[float] = None) -> list[CameraOutcome]:
        """Expire caches that have exceeded *timeout_seconds*.

        Returns outcomes for timed-out sequences (for logging only —
        no downlink is sent in no-ACK mode).
        """
        current = time.monotonic() if now is None else now
        outcomes: list[CameraOutcome] = []
        with self._lock:
            # Purge stale completed entries.
            completed_ttl = self.timeout_seconds * 2
            for key, completed in list(self._completed.items()):
                if current - completed.completed_at >= completed_ttl:
                    del self._completed[key]

            # Purge timed-out in-progress images.
            for key, state in list(self._images.items()):
                if current - state.first_seen_at < self.timeout_seconds:
                    continue
                missing = _missing_chunks(state)
                outcomes.append(CameraOutcome(
                    state="abandoned",
                    device_id=state.device_id, app_id=state.app_id,
                    image_seq=state.image_seq, chunk_count=state.chunk_count,
                    received_count=len(state.chunks), missing=missing,
                    repeat_count=state.repeat_count,
                    expected_timeout_ms=_expected_timeout_ms(state),
                    error="image reassembly timed out",
                ))
                del self._images[key]
                self._remove_sequence_locked(state.device_id, state.image_seq)

        return outcomes

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._images)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _activate_sequence_locked(self, device_id: str, image_seq: int) -> bool:
        """Register *image_seq* for *device_id*.

        Keeps at most ``MAX_CACHED_SEQUENCES`` concurrent sequences per
        device.  Evicts the oldest when the limit is exceeded.  Returns
        ``False`` when *image_seq* is stale (wraparound-aware).
        """
        active = self._active_sequences.setdefault(device_id, [])
        if image_seq in active:
            return True
        # Reject sequences that are older than the newest known one
        # (uint32 wraparound-aware comparison).
        if active and not _sequence_is_newer(image_seq, active[-1]):
            return False
        active.append(image_seq)
        # Evict oldest when over the per-device limit.
        while len(active) > MAX_CACHED_SEQUENCES:
            evicted = active.pop(0)
            evicted_key = (device_id, evicted)
            self._images.pop(evicted_key, None)
            self._completed.pop(evicted_key, None)
        return True

    def _remove_sequence_locked(self, device_id: str, image_seq: int) -> None:
        active = self._active_sequences.get(device_id, [])
        if image_seq in active:
            active.remove(image_seq)
        if not active:
            self._active_sequences.pop(device_id, None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
    """Compare uint32 image sequences allowing wraparound."""
    delta = (candidate - current) & 0xFFFFFFFF
    return 0 < delta < 0x80000000


def _missing_chunks(state: _ImageState) -> list[int]:
    return [index for index in range(state.chunk_count) if index not in state.chunks]


def _expected_timeout_ms(state: _ImageState) -> int:
    return int(max(1, state.chunk_count) * max(1, state.repeat_count) * 1000 + 8000)
