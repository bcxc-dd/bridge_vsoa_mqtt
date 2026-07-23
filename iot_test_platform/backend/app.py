from __future__ import annotations

import asyncio
import base64
import csv
import hashlib
import hmac
import importlib.util
import io
import json
import math
import os
import secrets
import socket
import sqlite3
import sys
import threading
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field
import yaml

try:
    import paho.mqtt.client as mqtt
except ImportError:
    mqtt = None

try:
    import vsoa
except ImportError:
    vsoa = None


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "platform.db"
DB_BACKUP_DIR = DATA_DIR / "backups"
DB_BACKUP_KEEP = 12
PROFILES_PATH = DATA_DIR / "connection_profiles.json"
VSOA_PROFILES_PATH = DATA_DIR / "vsoa_connection_profiles.json"


def resolve_bridge_root() -> Path:
    configured = os.getenv("BRIDGE_PROJECT_ROOT", "").strip()
    candidates = [Path(configured)] if configured else []
    candidates.extend([ROOT.parent, ROOT.parent / "bridge_vsoa_mqtt"])
    for candidate in candidates:
        if (candidate / "config.yaml").is_file() and (candidate / "src" / "main.py").is_file():
            return candidate.resolve()
    return candidates[0].resolve()


BRIDGE_ROOT = resolve_bridge_root()
BRIDGE_CONFIG_PATH = BRIDGE_ROOT / "config.yaml"
BRIDGE_PYTHON = BRIDGE_ROOT / ".venv" / "Scripts" / "python.exe"
AUTH_SECRET_PATH = DATA_DIR / "auth_secret.key"
TOKEN_TTL_SECONDS = 8 * 60 * 60


def is_internal_scene_event(event: dict[str, Any]) -> bool:
    return event.get("channel") == "/scene/trigger" or event.get("device_id") == "trigger"


def backup_platform_database(stage: str) -> Path | None:
    """Create a consistent SQLite snapshot without interrupting live readers."""
    if not DB_PATH.is_file() or DB_PATH.stat().st_size == 0:
        return None
    DB_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    destination = DB_BACKUP_DIR / f"platform-{stage}-{timestamp}.db"
    with sqlite3.connect(DB_PATH) as source, sqlite3.connect(destination) as target:
        source.backup(target)
    automatic = sorted(
        DB_BACKUP_DIR.glob("platform-*.db"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for stale in automatic[DB_BACKUP_KEEP:]:
        stale.unlink(missing_ok=True)
    return destination


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def auth_secret() -> bytes:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not AUTH_SECRET_PATH.exists():
        AUTH_SECRET_PATH.write_text(secrets.token_urlsafe(48), encoding="ascii")
    return AUTH_SECRET_PATH.read_text(encoding="ascii").strip().encode("ascii")


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 180_000)
    return f"pbkdf2_sha256${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        _, salt, expected = encoded.split("$", 2)
        actual = hash_password(password, _unb64(salt)).split("$", 2)[2]
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def create_token(user: dict[str, Any]) -> str:
    header = _b64(json_text({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64(json_text({
        "sub": user["username"], "role": user["role"],
        "name": user["display_name"], "exp": int(time.time()) + TOKEN_TTL_SECONDS,
    }).encode())
    signature = _b64(hmac.new(auth_secret(), f"{header}.{payload}".encode(), hashlib.sha256).digest())
    return f"{header}.{payload}.{signature}"


def decode_token(token: str) -> dict[str, Any]:
    try:
        header, payload, signature = token.split(".")
        expected = _b64(hmac.new(auth_secret(), f"{header}.{payload}".encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            raise ValueError("signature")
        data = json.loads(_unb64(payload))
        if int(data.get("exp", 0)) < time.time():
            raise ValueError("expired")
        return data
    except Exception as exc:
        raise HTTPException(status_code=401, detail="登录状态无效或已过期") from exc


def load_bridge_profile() -> dict[str, Any]:
    if not BRIDGE_CONFIG_PATH.exists():
        return {"available": False, "root": str(BRIDGE_ROOT), "error": "config.yaml not found"}
    try:
        config = yaml.safe_load(BRIDGE_CONFIG_PATH.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        return {"available": False, "root": str(BRIDGE_ROOT), "error": str(exc)}
    mqtt_config = config.get("mqtt") or {}
    vsoa_config = config.get("vsoa") or {}
    server_config = vsoa_config.get("server") or {}
    pubsub_config = vsoa_config.get("pubsub_client") or {}
    uplink_topics = list(mqtt_config.get("uplink_topics") or [])
    supported_sources = [
        source for source in ("lora", "zigbee", "wifi", "generic")
        if any(f"/{source}/" in topic for topic in uplink_topics)
    ]
    return {
        "available": True,
        "root": str(BRIDGE_ROOT),
        "name": (config.get("bridge") or {}).get("name", "MQTT-VSOA Bridge"),
        "version": (config.get("bridge") or {}).get("version", "unknown"),
        "mqtt": {
            "broker": mqtt_config.get("broker", ""),
            "port": int(mqtt_config.get("port", 1883)),
            "qos": int(mqtt_config.get("qos", 1)),
            "client_id": mqtt_config.get("client_id", ""),
            "uplink_topics": uplink_topics,
            "downlink_topic_prefix": mqtt_config.get("downlink_topic_prefix", "bridge/downlink"),
        },
        "vsoa": {
            "local_url": f"vsoa://127.0.0.1:{int(server_config.get('port', 3001))}",
            "bind_host": server_config.get("bind_host", "127.0.0.1"),
            "remote_url": pubsub_config.get("server_url", ""),
            "subscribe_urls": list(pubsub_config.get("subscribe_urls") or []),
            "ack_url": pubsub_config.get("ack_publish_url", "/ctrl/ack"),
        },
        "supported_sources": supported_sources,
        "tests": {
            "available": BRIDGE_PYTHON.exists() and (BRIDGE_ROOT / "tests").exists(),
            "python": str(BRIDGE_PYTHON),
        },
    }


BRIDGE_PROFILE = load_bridge_profile()

UPLINK_TOPICS = list(BRIDGE_PROFILE.get("mqtt", {}).get("uplink_topics") or [])
DOWNLINK_TOPIC = f"{BRIDGE_PROFILE.get('mqtt', {}).get('downlink_topic_prefix', 'bridge/downlink')}/#"
VSOA_URLS = ["/device/update", "/bridge/event", "/ctrl/ack", "/scene/trigger"]
DEFAULT_TOPICS = [*UPLINK_TOPICS, DOWNLINK_TOPIC]


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def device_id_is_simulated(device_id: str) -> bool:
    normalized = device_id.lower()
    return normalized.startswith(("perf-", "project-test-", "platform-real-check-", "bridge-e2e-check", "sim-")) or "-perf-perf-" in normalized


def analyze_project_mapping(source: dict[str, Any], output: dict[str, Any]) -> dict[str, Any]:
    """Run the bridge project's adapter and compare its canonical report to VSOA."""
    bridge_path = str(BRIDGE_ROOT)
    if bridge_path not in sys.path:
        sys.path.insert(0, bridge_path)

    try:
        from src.uplink.adapters import select_adapter
        from src.uplink.adapters.base import AdapterParseError

        adapter = select_adapter(source["channel"], source["payload"])
        report = adapter.parse(source["channel"], source["payload"])
    except Exception as exc:
        return {
            "adapter": "",
            "canonical_input": {},
            "field_mappings": [],
            "matched_fields": [],
            "missing_fields": [],
            "generated_fields": sorted(output),
            "transport_fields": sorted(source.get("payload", {})),
            "mapping_error": str(exc),
        }

    canonical = {
        key: value
        for key, value in asdict(report).items()
        if value is not None and value != "" and not (key == "timestamp" and value == 0)
    }
    target_names = {"topic": "last_topic"}
    mappings = []
    matched = []
    missing = []
    mapped_targets = set()
    for source_name, value in canonical.items():
        target_name = target_names.get(source_name, source_name)
        mapped_targets.add(target_name)
        present = target_name in output
        target_value = output.get(target_name)
        equal = present and (
            target_value == value
            or (
                isinstance(value, (int, float))
                and isinstance(target_value, (int, float))
                and abs(float(value) - float(target_value)) < 0.01
            )
        )
        status = "matched" if equal else "different" if present else "missing"
        mappings.append(
            {
                "source": f"{adapter.name}.{source_name}",
                "target": target_name,
                "source_value": value,
                "target_value": target_value,
                "status": status,
            }
        )
        if present:
            matched.append(target_name)
        else:
            missing.append(target_name)

    return {
        "adapter": adapter.name,
        "canonical_input": canonical,
        "field_mappings": mappings,
        "matched_fields": sorted(matched),
        "missing_fields": sorted(missing),
        "generated_fields": sorted(set(output) - mapped_targets),
        "transport_fields": sorted(source.get("payload", {})),
        "mapping_error": "",
    }


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.Lock()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    source TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    project TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    trace_id TEXT,
                    latency_ms REAL,
                    status TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS test_runs (
                    id TEXT PRIMARY KEY,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    experiment TEXT NOT NULL,
                    scenario TEXT NOT NULL,
                    status TEXT NOT NULL,
                    passed INTEGER NOT NULL DEFAULT 0,
                    failed INTEGER NOT NULL DEFAULT 0,
                    duration_ms REAL NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS performance_runs (
                    id TEXT PRIMARY KEY,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    status TEXT NOT NULL,
                    config TEXT NOT NULL,
                    metrics TEXT NOT NULL,
                    series TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS users (
                    username TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS device_profiles (
                    device_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    project TEXT NOT NULL,
                    device_type TEXT NOT NULL,
                    capabilities TEXT NOT NULL,
                    thresholds TEXT NOT NULL,
                    connection_source TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS device_annotations (
                    device_id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    note TEXT NOT NULL DEFAULT '',
                    updated_by TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS alerts (
                    id TEXT PRIMARY KEY,
                    device_id TEXT NOT NULL,
                    project TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    alert_type TEXT NOT NULL,
                    message TEXT NOT NULL,
                    value TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    acknowledged_at TEXT,
                    acknowledged_by TEXT
                );
                CREATE TABLE IF NOT EXISTS commands (
                    id TEXT PRIMARY KEY,
                    trace_id TEXT NOT NULL UNIQUE,
                    device_id TEXT NOT NULL,
                    project TEXT NOT NULL,
                    command TEXT NOT NULL,
                    parameters TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    status TEXT NOT NULL,
                    requested_by TEXT NOT NULL,
                    requested_at TEXT NOT NULL,
                    acknowledged_at TEXT,
                    result TEXT
                );
                CREATE TABLE IF NOT EXISTS audit_logs (
                    id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    username TEXT NOT NULL,
                    action TEXT NOT NULL,
                    resource TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    ip_address TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS scene_triggers (
                    id TEXT PRIMARY KEY,
                    scene_id TEXT NOT NULL,
                    scene_name TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    triggered_at TEXT NOT NULL,
                    conditions_snapshot TEXT NOT NULL,
                    actions_sent TEXT NOT NULL,
                    trace_id TEXT
                );
                """
            )
            run_columns = {row[1] for row in db.execute("PRAGMA table_info(test_runs)")}
            if "details" not in run_columns:
                db.execute("ALTER TABLE test_runs ADD COLUMN details TEXT NOT NULL DEFAULT '[]'")
            # Remove only synthetic records created by the platform's retired demo bridge.
            db.execute("DELETE FROM events WHERE source IN ('vsoa-demo', 'performance-simulator')")
            db.execute("DELETE FROM performance_runs WHERE config LIKE '%\"mode\":\"demo\"%'")
            if not db.execute("SELECT 1 FROM users LIMIT 1").fetchone():
                defaults = [
                    ("admin", "系统管理员", "admin123", "admin"),
                    ("tester", "测试运维员", "tester123", "tester"),
                    ("user", "环境用户", "user123", "user"),
                ]
                db.executemany(
                    "INSERT INTO users VALUES (?, ?, ?, ?, 1, ?)",
                    [(username, name, hash_password(password), role, now_iso()) for username, name, password, role in defaults],
                )

    def insert_event(self, event: dict[str, Any]) -> None:
        with self.lock, self.connect() as db:
            db.execute(
                """
                INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event["id"],
                    event["timestamp"],
                    event["source"],
                    event["direction"],
                    event["project"],
                    event["device_id"],
                    event["channel"],
                    json_text(event["payload"]),
                    event.get("trace_id"),
                    event.get("latency_ms"),
                    event["status"],
                ),
            )

    def authenticate(self, username: str, password: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM users WHERE username = ? AND active = 1", (username,)).fetchone()
        if not row or not verify_password(password, row["password_hash"]):
            return None
        return {key: row[key] for key in ("username", "display_name", "role", "active", "created_at")}

    def users(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT username, display_name, role, active, created_at FROM users ORDER BY created_at").fetchall()
        return [dict(row) for row in rows]

    def upsert_user(self, user: dict[str, Any]) -> dict[str, Any]:
        with self.lock, self.connect() as db:
            existing = db.execute("SELECT password_hash, created_at FROM users WHERE username = ?", (user["username"],)).fetchone()
            password_hash = hash_password(user["password"]) if user.get("password") else (existing["password_hash"] if existing else "")
            if not password_hash:
                raise ValueError("新用户必须设置密码")
            created_at = existing["created_at"] if existing else now_iso()
            db.execute(
                "INSERT OR REPLACE INTO users VALUES (?, ?, ?, ?, ?, ?)",
                (user["username"], user["display_name"], password_hash, user["role"], int(user.get("active", True)), created_at),
            )
        return next(item for item in self.users() if item["username"] == user["username"])

    def audit(self, username: str, action: str, resource: str, detail: Any, ip_address: str = "") -> None:
        with self.lock, self.connect() as db:
            db.execute(
                "INSERT INTO audit_logs VALUES (?, ?, ?, ?, ?, ?, ?)",
                (uuid.uuid4().hex, now_iso(), username, action, resource, json_text(detail), ip_address),
            )

    def audits(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM audit_logs ORDER BY timestamp DESC LIMIT ?", (limit,)).fetchall()
        return [{**dict(row), "detail": json.loads(row["detail"] or "{}")} for row in rows]

    def device_profiles(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM device_profiles ORDER BY updated_at DESC").fetchall()
        return [{**dict(row), "capabilities": json.loads(row["capabilities"]), "thresholds": json.loads(row["thresholds"])} for row in rows]

    def upsert_device_profile(self, profile: dict[str, Any]) -> dict[str, Any]:
        with self.lock, self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO device_profiles VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (profile["device_id"], profile["name"], profile["project"], profile["device_type"],
                 json_text(profile["capabilities"]), json_text(profile["thresholds"]), profile.get("connection_source", ""), now_iso()),
            )
        return next(item for item in self.device_profiles() if item["device_id"] == profile["device_id"])

    def threshold_rules(self, device_id: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            row = db.execute(
                "SELECT thresholds FROM device_profiles WHERE device_id = ?",
                (device_id,),
            ).fetchone()
        if not row:
            return []
        stored = json.loads(row["thresholds"] or "{}")
        return stored.get("rules", []) if isinstance(stored, dict) else []

    def save_threshold_rules(
        self,
        device: dict[str, Any],
        rules: list[dict[str, Any]],
    ) -> dict[str, Any]:
        existing = next(
            (item for item in self.device_profiles() if item["device_id"] == device["device_id"]),
            {},
        )
        return self.upsert_device_profile({
            "device_id": device["device_id"],
            "name": existing.get("name") or device.get("name") or device["device_id"],
            "project": existing.get("project") or device.get("project") or "generic",
            "device_type": existing.get("device_type") or device.get("device_type") or "environment_sensor",
            "capabilities": existing.get("capabilities") or device.get("capabilities") or [],
            "thresholds": {"rules": rules},
            "connection_source": existing.get("connection_source") or device.get("connection_source") or "",
        })

    def device_annotations(self) -> dict[str, dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM device_annotations").fetchall()
        return {row["device_id"]: dict(row) for row in rows}

    def upsert_device_annotation(
        self, device_id: str, display_name: str, note: str, username: str
    ) -> dict[str, Any]:
        with self.lock, self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO device_annotations VALUES (?, ?, ?, ?, ?)",
                (device_id, display_name.strip(), note.strip(), username, now_iso()),
            )
        return self.device_annotations()[device_id]

    def create_alert(self, event: dict[str, Any], alert_type: str, severity: str, message: str, value: Any) -> None:
        with self.lock, self.connect() as db:
            duplicate = db.execute(
                "SELECT 1 FROM alerts WHERE device_id = ? AND alert_type = ? AND status = 'active'",
                (event["device_id"], alert_type),
            ).fetchone()
            if duplicate:
                return
            db.execute(
                "INSERT INTO alerts VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, NULL, NULL)",
                (uuid.uuid4().hex, event["device_id"], event["project"], severity, alert_type, message, json_text(value), now_iso()),
            )

    def alerts(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM alerts ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [{**dict(row), "value": json.loads(row["value"]) if row["value"] else None} for row in rows]

    def acknowledge_alert(self, alert_id: str, username: str) -> bool:
        with self.lock, self.connect() as db:
            cursor = db.execute(
                "UPDATE alerts SET status = 'acknowledged', acknowledged_at = ?, acknowledged_by = ? WHERE id = ?",
                (now_iso(), username, alert_id),
            )
        return cursor.rowcount > 0

    def create_command(self, command: dict[str, Any]) -> None:
        with self.lock, self.connect() as db:
            db.execute(
                "INSERT INTO commands VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)",
                (command["id"], command["trace_id"], command["device_id"], command["project"], command["command"],
                 json_text(command["parameters"]), command["topic"], command["status"], command["requested_by"], command["requested_at"]),
            )

    def acknowledge_command(self, trace_id: str, payload: dict[str, Any]) -> None:
        success = payload.get("error_code") == 0 if "error_code" in payload else payload.get("success", payload.get("ok", True))
        status = "acknowledged" if success else "failed"
        with self.lock, self.connect() as db:
            db.execute(
                "UPDATE commands SET status = ?, acknowledged_at = ?, result = ? WHERE trace_id = ?",
                (status, now_iso(), json_text(payload), trace_id),
            )

    def commands(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM commands ORDER BY requested_at DESC LIMIT ?", (limit,)).fetchall()
        return [{**dict(row), "parameters": json.loads(row["parameters"]), "result": json.loads(row["result"]) if row["result"] else None} for row in rows]

    def insert_scene_trigger(self, payload: dict[str, Any]) -> None:
        with self.lock, self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO scene_triggers VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    uuid.uuid4().hex, str(payload.get("scene_id", "")),
                    str(payload.get("scene_name", payload.get("scene_id", ""))),
                    str(payload.get("device_id", "unknown")),
                    str(payload.get("triggered_at") or now_iso()),
                    json_text(payload.get("conditions_snapshot") or {}),
                    json_text(payload.get("actions_sent") or []),
                    payload.get("trace_id"),
                ),
            )

    def scene_triggers(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM scene_triggers ORDER BY triggered_at DESC LIMIT ?", (limit,)).fetchall()
        return [{**dict(row), "conditions_snapshot": json.loads(row["conditions_snapshot"]), "actions_sent": json.loads(row["actions_sent"])} for row in rows]

    def recent_events(self, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM events ORDER BY timestamp DESC LIMIT ? OFFSET ?", (limit, offset)
            ).fetchall()
        return [
            event
            for row in rows
            for event in [{
                **dict(row),
                "payload": json.loads(row["payload"]),
            }]
            if not is_internal_scene_event(event)
        ]

    def temperature_series(
        self, limit_per_device: int = 500, include_performance: bool = False
    ) -> list[dict[str, Any]]:
        query = """
            SELECT timestamp, project, device_id, payload
            FROM events
            WHERE source = 'vsoa' AND channel = '/device/update'
        """
        params: list[Any] = []
        if not include_performance:
            query += " AND device_id NOT LIKE ?"
            params.append("perf-%")
        query += " ORDER BY timestamp DESC"

        with self.connect() as db:
            rows = db.execute(query, params).fetchall()

        grouped: dict[str, dict[str, Any]] = {}
        for row in rows:
            try:
                payload = json.loads(row["payload"])
            except (TypeError, json.JSONDecodeError):
                continue
            temperature = payload.get("temperature")
            if isinstance(temperature, bool) or not isinstance(temperature, (int, float)):
                continue

            device_id = row["device_id"]
            if not include_performance and device_id_is_simulated(device_id):
                continue
            series = grouped.setdefault(
                device_id,
                {
                    "device_id": device_id,
                    "project": row["project"],
                    "latest_at": row["timestamp"],
                    "latest_temperature": temperature,
                    "points": [],
                },
            )
            if len(series["points"]) >= limit_per_device:
                continue
            series["points"].append(
                {
                    "timestamp": row["timestamp"],
                    "temperature": temperature,
                    "humidity": payload.get("humidity"),
                }
            )

        result = list(grouped.values())
        for series in result:
            series["points"].reverse()
            series["point_count"] = len(series["points"])
        return sorted(result, key=lambda item: item["latest_at"], reverse=True)

    def upsert_run(self, run: dict[str, Any]) -> None:
        with self.lock, self.connect() as db:
            db.execute(
                """
                INSERT OR REPLACE INTO test_runs
                (id, started_at, finished_at, experiment, scenario, status, passed, failed, duration_ms, details)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run["id"], run["started_at"], run.get("finished_at"),
                    run["experiment"], run["scenario"], run["status"],
                    run["passed"], run["failed"], run["duration_ms"], json_text(run.get("details", [])),
                ),
            )

    def recent_runs(self, limit: int = 30) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [
                {**dict(row), "details": json.loads(row["details"] or "[]")}
                for row in db.execute(
                    "SELECT * FROM test_runs ORDER BY started_at DESC LIMIT ?", (limit,)
                ).fetchall()
            ]

    def upsert_performance_run(self, run: dict[str, Any]) -> None:
        with self.lock, self.connect() as db:
            db.execute(
                """
                INSERT OR REPLACE INTO performance_runs
                (id, started_at, finished_at, status, config, metrics, series)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run["id"], run["started_at"], run.get("finished_at"),
                    run["status"], json_text(run["config"]),
                    json_text(run.get("metrics", {})), json_text(run.get("series", [])),
                ),
            )

    def recent_performance_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                """
                SELECT * FROM performance_runs
                WHERE config LIKE '%\"mode\":\"project\"%'
                ORDER BY started_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            {
                **dict(row),
                "config": json.loads(row["config"] or "{}"),
                "metrics": json.loads(row["metrics"] or "{}"),
                "series": json.loads(row["series"] or "[]"),
            }
            for row in rows
        ]

    def performance_events(self, started_at: str, device_prefix: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                """
                SELECT * FROM events
                WHERE timestamp >= ? AND device_id LIKE ?
                ORDER BY timestamp ASC
                """,
                (started_at, f"{device_prefix}%"),
            ).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]

    def metrics(self) -> dict[str, Any]:
        cutoff = time.time() - 3600
        events = self.recent_events(1000)
        last_hour = []
        for item in events:
            try:
                if datetime.fromisoformat(item["timestamp"]).timestamp() >= cutoff:
                    last_hour.append(item)
            except ValueError:
                pass
        devices = {
            f"{item['project']}:{item['device_id']}"
            for item in last_hour
            if item["device_id"]
        }
        latencies = [
            item["latency_ms"]
            for item in self.transformation_pairs(100)
            if item.get("latency_ms") is not None
        ]
        errors = sum(item["status"] == "error" for item in last_hour)
        return {
            "messages_hour": len(last_hour),
            "active_devices": len(devices),
            "avg_latency_ms": round(sum(latencies) / len(latencies), 1) if latencies else 0,
            "success_rate": round((len(last_hour) - errors) * 100 / len(last_hour), 1)
            if last_hour else 100.0,
        }

    def device_summaries(self) -> list[dict[str, Any]]:
        events = self.recent_events(1000)
        profiles = {item["device_id"]: item for item in self.device_profiles()}
        annotations = self.device_annotations()
        grouped: dict[str, dict[str, Any]] = {}
        now = time.time()
        for event in events:
            if is_internal_scene_event(event):
                continue
            device_id = event["device_id"] or "unknown"
            group_key = f"{event['project']}:{device_id}"
            item = grouped.setdefault(
                group_key,
                {
                    "device_id": device_id,
                    "project": event["project"],
                    "last_seen": event["timestamp"],
                    "messages": 0,
                    "errors": 0,
                    "channels": set(),
                    "latest_payload": event["payload"],
                    "connection_source": event["source"],
                    "available_metrics": {},
                },
            )
            item["messages"] += 1
            item["errors"] += event["status"] == "error"
            item["channels"].add(event["channel"])
            if not is_lora_camera_packet(event["payload"]):
                for metric in discover_alert_metrics(event["payload"]):
                    item["available_metrics"].setdefault(metric["field"], metric)
        result = []
        for item in grouped.values():
            try:
                age = now - datetime.fromisoformat(item["last_seen"]).timestamp()
            except ValueError:
                age = 999999
            item["online"] = age <= 120
            item["channels"] = sorted(item["channels"])
            item["available_metrics"] = list(item["available_metrics"].values())
            profile = profiles.get(item["device_id"], {})
            annotation = annotations.get(item["device_id"], {})
            payload = item["latest_payload"] if isinstance(item["latest_payload"], dict) else {}
            inferred = []
            for key, value in payload.items():
                if isinstance(value, (int, float, bool)) and key not in {"timestamp", "report_count"}:
                    inferred.append({"id": key, "type": "switch" if isinstance(value, bool) else "telemetry", "label": key})
            item.update({
                "name": annotation.get("display_name") or profile.get("name") or payload.get("name") or payload.get("deviceName") or item["device_id"],
                "note": annotation.get("note", ""),
                "device_type": profile.get("device_type") or payload.get("type") or "environment_sensor",
                "capabilities": profile.get("capabilities") or inferred,
                "thresholds": profile.get("thresholds") or {"rules": []},
                "connection_source": profile.get("connection_source") or item["connection_source"],
                "simulated": device_id_is_simulated(item["device_id"]),
            })
            result.append(item)
        known = {item["device_id"] for item in result}
        for profile in profiles.values():
            if profile["device_id"] not in known:
                annotation = annotations.get(profile["device_id"], {})
                result.append({**profile, "name": annotation.get("display_name") or profile["name"], "note": annotation.get("note", ""), "last_seen": "", "messages": 0, "errors": 0, "channels": [], "latest_payload": {}, "available_metrics": [], "online": False, "simulated": False})
        return sorted(result, key=lambda item: item["last_seen"], reverse=True)

    def environment_dashboard(self, project: str, limit_per_device: int = 500) -> dict[str, Any]:
        metric_catalog = {
            "temperature": {"label": "温度", "unit": "°C", "data_type": "number", "aliases": ("temperature", "temperature_c", "temp", "temp_c", "air_temperature", "ambient_temperature")},
            "humidity": {"label": "空气湿度", "unit": "%", "data_type": "number", "aliases": ("humidity", "humidity_percent", "air_humidity", "relative_humidity")},
            "soil_moisture": {"label": "土壤湿度", "unit": "%", "data_type": "number", "aliases": ("soil_moisture", "soilMoisture", "soil_humidity", "soilHumidity", "moisture", "soil")},
            "rainfall": {"label": "降水水平", "unit": "mm", "data_type": "number", "aliases": ("rainfall", "rainfall_mm", "rain_level", "rainLevel", "precipitation", "precipitation_level", "rain")},
            "pressure": {"label": "气压", "unit": "hPa", "data_type": "number", "aliases": ("pressure", "air_pressure", "barometric_pressure")},
            "illuminance": {"label": "光照", "unit": "lux", "data_type": "number", "aliases": ("illuminance", "illumination", "light_level", "light", "lux")},
            "co2": {"label": "二氧化碳", "unit": "ppm", "data_type": "number", "aliases": ("co2", "co2_ppm", "carbon_dioxide")},
            "pm2_5": {"label": "PM2.5", "unit": "μg/m³", "data_type": "number", "aliases": ("pm2_5", "pm25", "pm2.5")},
            "pm10": {"label": "PM10", "unit": "μg/m³", "data_type": "number", "aliases": ("pm10",)},
            "voc": {"label": "VOC", "unit": "ppb", "data_type": "number", "aliases": ("voc", "tvoc")},
            "wind_speed": {"label": "风速", "unit": "m/s", "data_type": "number", "aliases": ("wind_speed", "windspeed")},
            "noise": {"label": "环境噪声", "unit": "dB", "data_type": "number", "aliases": ("noise", "noise_level", "sound_level")},
            "uv_index": {"label": "紫外线指数", "unit": "", "data_type": "number", "aliases": ("uv_index", "uv", "ultraviolet")},
            "water_level": {"label": "水位", "unit": "cm", "data_type": "number", "aliases": ("water_level", "water_depth")},
            "voltage": {"label": "电压", "unit": "V", "data_type": "number", "aliases": ("voltage", "voltage_v", "volt", "battery_voltage", "supply_voltage")},
            "smoke": {"label": "烟雾", "unit": "%", "data_type": "number", "aliases": ("smoke", "smoke_level", "smoke_relative_percent", "smoke_alarm", "gas")},
            "presence": {"label": "人体红外", "unit": "", "data_type": "boolean", "aliases": ("presence", "human_presence", "motion_detected", "pir", "motion", "infrared")},
        }
        aliases = {field: definition["aliases"] for field, definition in metric_catalog.items()}
        image_keys = ("image_url", "image", "image_base64", "image_b64", "photo", "picture")

        def containers(payload: dict[str, Any]) -> list[dict[str, Any]]:
            values = [payload]
            seen = {id(payload)}
            for current in values:
                for child in current.values():
                    children = [child] if isinstance(child, dict) else child if isinstance(child, list) else []
                    for nested in children:
                        if isinstance(nested, dict) and id(nested) not in seen:
                            seen.add(id(nested))
                            values.append(nested)
            return values

        def find_value(payload: dict[str, Any], names: tuple[str, ...]) -> Any:
            for container in containers(payload):
                for name in names:
                    value = container.get(name)
                    if isinstance(value, (int, float, bool)):
                        return value
            return None

        def is_camera_transport(payload: dict[str, Any]) -> bool:
            if is_lora_camera_packet(payload):
                return True
            for container in containers(payload):
                event_type = str(container.get("event", "")).lower()
                payload_type = str(container.get("type", "")).lower()
                transport = str(container.get("camera_transport", "")).lower()
                if event_type == "camera_reassembly" or payload_type in {"camera", "camera_frame"}:
                    return True
                if transport == "lorawan_hcv3":
                    return True
                encoded = container.get("data")
                if not isinstance(encoded, str) or not encoded:
                    continue
                try:
                    header = base64.b64decode(encoded, validate=True)[:3]
                except (ValueError, TypeError):
                    continue
                if header[:2] in {b"HC", b"HP"}:
                    return True
            return False

        def decode_lora_payload_metrics(payload: dict[str, Any], device_id: str) -> dict[str, Any]:
            device_name = str(payload.get("deviceName") or payload.get("name") or device_id)
            dev_eui = str(payload.get("devEUI") or payload.get("devEui") or "")
            if "ebyte test device 470" not in device_name.lower() and dev_eui != "0000000000000925":
                return {}
            encoded = payload.get("data")
            if not isinstance(encoded, str) or not encoded:
                return {}
            try:
                raw = base64.b64decode(encoded, validate=True)
            except (ValueError, TypeError):
                return {}
            if len(raw) != 37 or raw[0] != 2:
                return {}
            return {
                "temperature": round(int.from_bytes(raw[21:23], "big", signed=True) / 10, 1),
                "humidity": round(int.from_bytes(raw[23:25], "big") / 10, 1),
                "soil_moisture": round(int.from_bytes(raw[25:27], "big") / 10, 1),
                "rainfall": round(int.from_bytes(raw[27:29], "big") / 10, 1),
                "signal": int.from_bytes(raw[36:37], "big", signed=True),
                "motor_running": bool(raw[1] & 0x02),
                "led_on": bool(raw[1] & 0x04),
            }

        profiles = {item["device_id"]: item for item in self.device_profiles()}
        annotations = self.device_annotations()
        with self.connect() as db:
            if project == "wifi":
                rows = db.execute(
                    """
                    SELECT * FROM (
                        SELECT * FROM events
                        WHERE project = 'wifi'
                           OR (project = 'generic' AND channel LIKE 'bridge/uplink/%/camera/%')
                        ORDER BY timestamp DESC LIMIT 20000
                    ) ORDER BY timestamp ASC
                    """
                ).fetchall()
            elif project in {"lora", "zigbee"}:
                rows = db.execute(
                    """
                    SELECT * FROM (
                        SELECT * FROM events
                        WHERE project = ? OR channel LIKE ?
                        ORDER BY timestamp DESC LIMIT 20000
                    ) ORDER BY timestamp ASC
                    """,
                    (project, f"bridge/uplink/{project}/%"),
                ).fetchall()
            else:
                rows = db.execute(
                    """
                    SELECT * FROM (
                        SELECT * FROM events WHERE project = ?
                        ORDER BY timestamp DESC LIMIT 20000
                    ) ORDER BY timestamp ASC
                    """,
                    (project,),
                ).fetchall()

        grouped: dict[str, dict[str, Any]] = {}
        for row in rows:
            event = dict(row)
            payload = json.loads(event["payload"])
            device_id = event["device_id"] or "unknown"
            if device_id == "unknown" or device_id_is_simulated(device_id):
                continue
            item = grouped.setdefault(device_id, {
                "device_id": device_id,
                "points": [], "frames": [], "rssi": [], "snr": [], "sequences": [],
                "packets": 0, "last_seen": event["timestamp"], "latest_image": "",
                "latest_frame": {}, "camera_seen": False,
            })
            item["last_seen"] = event["timestamp"]
            camera_transport = is_camera_transport(payload)
            item["camera_seen"] = item["camera_seen"] or camera_transport
            if not camera_transport:
                point = {"timestamp": event["timestamp"]}
                for field, names in aliases.items():
                    value = find_value(payload, names)
                    if value is not None:
                        point[field] = value
                if project == "lora":
                    point.update(decode_lora_payload_metrics(payload, device_id))
                if len(point) > 1:
                    item["points"].append(point)

            rx_info = payload.get("rxInfo")
            rx = rx_info[0] if isinstance(rx_info, list) and rx_info and isinstance(rx_info[0], dict) else {}
            rssi = find_value(payload, ("rssi", "rssi_dbm", "signal"))
            snr = find_value(payload, ("snr",))
            rssi = rx.get("rssi", rssi)
            snr = rx.get("snr", snr)
            if isinstance(rssi, (int, float)):
                item["rssi"].append(rssi)
            if isinstance(snr, (int, float)):
                item["snr"].append(snr)
            sequence = payload.get("fCnt", payload.get("fcnt", payload.get("sequence", payload.get("seq"))))
            if event["source"] == "mqtt":
                item["packets"] += 1
                if isinstance(sequence, int):
                    item["sequences"].append(sequence)

            for container in containers(payload):
                for key in image_keys:
                    value = container.get(key)
                    if isinstance(value, str) and value:
                        if value.startswith(("data:image/", "http://", "https://", "/api/")):
                            item["latest_image"] = value
                        elif key in ("image_base64", "image_b64"):
                            image_format = str(container.get("format", "jpeg")).lower()
                            mime = container.get("image_mime") or ("image/png" if image_format == "png" else "image/jpeg")
                            item["latest_image"] = f"data:{mime};base64,{value}"
                        if item["latest_image"]:
                            item["latest_frame"] = {
                                "format": container.get("format") or container.get("image_mime") or "unknown",
                                "width": container.get("width"),
                                "height": container.get("height"),
                                "bytes": container.get("bytes"),
                                "fps": container.get("fps"),
                                "hub_ip": container.get("hub_ip"),
                                "topic": container.get("topic") or event["channel"],
                            }
                        break

        now = time.time()
        devices = []
        links = []
        for device_id, item in grouped.items():
            profile = profiles.get(device_id, {})
            annotation = annotations.get(device_id, {})
            # A HCv3 camera payload was historically decoded by the legacy
            # sensor adapter. Never expose those binary-derived values as
            # environmental telemetry, including rows saved before detection.
            points = [] if item["camera_seen"] else item["points"][-limit_per_device:]
            latest = {}
            for point in points:
                latest.update({key: value for key, value in point.items() if key != "timestamp"})
            sequences = item["sequences"]
            missing = 0
            comparable = 0
            for previous, current in zip(sequences, sequences[1:]):
                if current > previous:
                    missing += max(0, current - previous - 1)
                    comparable += 1
            loss_rate = None
            if comparable:
                loss_rate = round(missing * 100 / max(1, len(sequences) + missing), 2)
            try:
                online = now - datetime.fromisoformat(item["last_seen"]).timestamp() <= 120
            except ValueError:
                online = False
            device_type = "camera" if item["camera_seen"] or item["latest_image"] else profile.get("device_type") or "environment_sensor"
            devices.append({
                "device_id": device_id,
                "name": annotation.get("display_name") or profile.get("name") or ("EoRa Hub WiFi Camera" if project == "wifi" and item["latest_image"] else device_id),
                "note": annotation.get("note", ""),
                "project": project,
                "device_type": device_type,
                "capabilities": profile.get("capabilities") or [],
                "online": online,
                "last_seen": item["last_seen"],
                "latest": latest,
                "points": points,
                "metrics": [
                    {"field": field, **{key: value for key, value in definition.items() if key != "aliases"}}
                    for field, definition in metric_catalog.items()
                    if any(field in point for point in points)
                ],
                "latest_image": item["latest_image"],
                "latest_frame": item["latest_frame"],
            })
            links.append({
                "device_id": device_id,
                "name": annotation.get("display_name") or profile.get("name") or device_id,
                "online": online,
                "last_seen": item["last_seen"],
                "packets": item["packets"],
                "rssi": item["rssi"][-1] if item["rssi"] else None,
                "avg_rssi": round(sum(item["rssi"]) / len(item["rssi"]), 1) if item["rssi"] else None,
                "snr": item["snr"][-1] if item["snr"] else None,
                "avg_snr": round(sum(item["snr"]) / len(item["snr"]), 1) if item["snr"] else None,
                "missing_packets": missing if comparable else None,
                "loss_rate": loss_rate,
            })
        return {
            "project": project,
            "devices": sorted(devices, key=lambda value: value["last_seen"], reverse=True),
            "links": sorted(links, key=lambda value: value["last_seen"], reverse=True),
        }

    def lora_dashboard(self, limit_per_device: int = 500) -> dict[str, Any]:
        return self.environment_dashboard("lora", limit_per_device)

    def zigbee_dashboard(self, limit_per_device: int = 500) -> dict[str, Any]:
        return self.environment_dashboard("zigbee", limit_per_device)

    def wifi_dashboard(self, limit_per_device: int = 500) -> dict[str, Any]:
        return self.environment_dashboard("wifi", limit_per_device)

    def transformation_pairs(self, limit: int = 30) -> list[dict[str, Any]]:
        events = list(reversed(self.recent_events(400)))
        pending: dict[str, list[dict[str, Any]]] = {}
        pairs = []
        for event in events:
            if event["direction"] in ("uplink", "downlink") and event["source"] != "vsoa-demo":
                key = f"{event['project']}:{event['device_id']}"
                pending.setdefault(key, []).append(event)
                continue
            if event["direction"] != "result" or event["channel"] != "/device/update":
                continue
            key = f"{event['project']}:{event['device_id']}"
            candidates = pending.get(key, [])
            if not candidates:
                fallback = [
                    items
                    for pending_key, items in pending.items()
                    if pending_key.endswith(f":{event['device_id']}") and items
                ]
                if fallback:
                    candidates = max(fallback, key=lambda items: items[-1]["timestamp"])
            result_time = datetime.fromisoformat(event["timestamp"])
            source = None
            if candidates:
                for candidate in reversed(candidates):
                    candidate_time = datetime.fromisoformat(candidate["timestamp"])
                    delta_ms = (result_time - candidate_time).total_seconds() * 1000
                    if 0 <= delta_ms <= 1000:
                        source = candidate
                        candidates.remove(candidate)
                        break
            if not source:
                continue
            source_time = datetime.fromisoformat(source["timestamp"])
            mapping = analyze_project_mapping(source, event["payload"])
            pairs.append(
                {
                    "id": event["id"],
                    "device_id": event["device_id"],
                    "project": source["project"],
                    "input": source,
                    "output": event,
                    "latency_ms": event["latency_ms"]
                    or round((result_time - source_time).total_seconds() * 1000, 1),
                    **mapping,
                }
            )
        return list(reversed(pairs[-limit:]))


db = Database(DB_PATH)


def load_profiles() -> list[dict[str, Any]]:
    if not PROFILES_PATH.exists():
        return []
    try:
        value = json.loads(PROFILES_PATH.read_text(encoding="utf-8"))
        return value if isinstance(value, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def save_profiles(profiles: list[dict[str, Any]]) -> None:
    PROFILES_PATH.write_text(
        json.dumps(profiles, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def load_vsoa_profiles() -> list[dict[str, Any]]:
    if not VSOA_PROFILES_PATH.exists():
        return []
    try:
        value = json.loads(VSOA_PROFILES_PATH.read_text(encoding="utf-8"))
        return value if isinstance(value, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def save_vsoa_profiles(profiles: list[dict[str, Any]]) -> None:
    VSOA_PROFILES_PATH.write_text(
        json.dumps(profiles, ensure_ascii=False, indent=2), encoding="utf-8"
    )


class SocketHub:
    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()

    async def connect(self, socket: WebSocket) -> None:
        await socket.accept()
        self.clients.add(socket)

    def disconnect(self, socket: WebSocket) -> None:
        self.clients.discard(socket)

    async def broadcast(self, message: dict[str, Any]) -> None:
        stale = []
        for client in self.clients:
            try:
                await client.send_json(message)
            except Exception:
                stale.append(client)
        for client in stale:
            self.disconnect(client)


hub = SocketHub()
metrics_broadcast_at = 0.0


def infer_project(channel: str, payload: dict[str, Any]) -> str:
    normalized_channel = channel.lower().replace("-", "_")
    if channel == "s3/eora-s3-400tb-001/data" or normalized_channel == "bridge/uplink/generic/eora_s3_400tb_001/data":
        return "wifi"
    if channel.startswith("application/"):
        return "lora"
    text = f"{channel} {payload.get('source', '')} {payload.get('type', '')}".lower()
    text += f" {payload.get('protocol', '')}".lower()
    if payload.get("type") == "camera_frame" or "/camera/" in channel.lower() or "wifi" in text:
        return "wifi"
    if "zigbee" in text:
        return "zigbee"
    if "lora" in text:
        return "lora"
    return "generic"


def is_lora_camera_packet(payload: dict[str, Any]) -> bool:
    """Identify HC/HP camera traffic before telemetry and alert processing."""
    candidates = [payload]
    raw = payload.get("raw")
    if isinstance(raw, dict):
        candidates.append(raw)
    for candidate in candidates:
        if str(candidate.get("camera_transport", "")).lower() == "lorawan_hcv3":
            return True
        if str(candidate.get("event", "")).lower() == "camera_reassembly":
            return True
        encoded = candidate.get("data")
        if not isinstance(encoded, str) or not encoded:
            continue
        try:
            magic = base64.b64decode(encoded, validate=True)[:2]
        except Exception:
            continue
        if magic in {b"HC", b"HP"}:
            return True
    return False


ALERT_METRICS = {
    "temperature": ("温度", "°C", ("temperature", "temperature_c", "temp", "temp_c", "air_temperature")),
    "humidity": ("空气湿度", "%", ("humidity", "humidity_percent", "air_humidity", "relative_humidity")),
    "soil_moisture": ("土壤湿度", "%", ("soil_moisture", "soilHumidity", "soil_humidity", "moisture")),
    "rainfall": ("降水水平", "mm", ("rainfall", "rainfall_mm", "rain_level", "precipitation")),
    "voltage": ("电压", "V", ("voltage", "voltage_v", "battery_voltage", "supply_voltage")),
    "battery": ("电量", "%", ("battery", "battery_percent", "battery_level")),
    "smoke": ("烟雾", "%", ("smoke", "smoke_level", "smoke_relative_percent", "smoke_alarm")),
    "presence": ("人体红外", "", ("presence", "human_presence", "motion_detected", "motion", "infrared")),
    "illuminance": ("光照", "lux", ("illuminance", "illumination", "light_level", "lux")),
    "pressure": ("气压", "hPa", ("pressure", "air_pressure", "barometric_pressure")),
    "co2": ("二氧化碳", "ppm", ("co2", "co2_ppm", "carbon_dioxide")),
    "pm2_5": ("PM2.5", "μg/m³", ("pm2_5", "pm25", "pm2.5")),
    "pm10": ("PM10", "μg/m³", ("pm10",)),
    "voc": ("VOC", "ppb", ("voc", "tvoc")),
    "noise": ("环境噪声", "dB", ("noise", "noise_level", "sound_level")),
    "water_level": ("水位", "cm", ("water_level", "water_depth")),
    "signal": ("信号强度", "dBm", ("signal", "rssi", "rssi_dbm")),
    "snr": ("信噪比", "dB", ("snr", "lora_snr")),
}

ALERT_OPERATORS = {
    "gt": ("大于", lambda actual, threshold: actual > threshold),
    "gte": ("大于等于", lambda actual, threshold: actual >= threshold),
    "lt": ("小于", lambda actual, threshold: actual < threshold),
    "lte": ("小于等于", lambda actual, threshold: actual <= threshold),
    "eq": ("等于", lambda actual, threshold: actual == threshold),
    "neq": ("不等于", lambda actual, threshold: actual != threshold),
}


def payload_metric(payload: dict[str, Any], field: str) -> Any:
    aliases = ALERT_METRICS.get(field, (field, "", (field,)))[2]
    containers = [payload]
    seen = {id(payload)}
    for container in containers:
        for name in aliases:
            value = container.get(name)
            if isinstance(value, (int, float, bool)):
                return value
        for child in container.values():
            children = [child] if isinstance(child, dict) else child if isinstance(child, list) else []
            for nested in children:
                if isinstance(nested, dict) and id(nested) not in seen:
                    seen.add(id(nested))
                    containers.append(nested)
    return None


NON_BUSINESS_METRIC_FIELDS = {
    "timestamp", "time", "report_count", "seq", "sequence", "fcnt", "fport",
    "dr", "length", "binary_length", "chunk_index", "chunk_count", "chunk_len",
    "repeat_index", "repeat_count", "image_seq", "image_len", "bytes", "flags",
    "status_code", "command_id", "cluster_id", "manufacturer_code", "endpoint",
    "send_time_ms", "lorawan_retry_count", "uplinkid", "frequency", "bandwidth",
    "spreadingfactor", "adc_raw", "voltage_mv", "adr", "confirmed", "joined",
    "application_retry", "checksum_valid", "valid", "ok", "direction",
    "address_mode", "registered_at", "created_at", "updated_at", "last_seen",
    "channel", "port", "retry_count", "message_count", "received_count",
    "missing_count", "success_count", "failure_count", "report_interval",
}
ALL_ALERT_ALIASES = {
    alias.lower()
    for _, _, aliases in ALERT_METRICS.values()
    for alias in aliases
}


def discover_alert_metrics(payload: dict[str, Any]) -> list[dict[str, Any]]:
    discovered: dict[str, dict[str, Any]] = {}
    for field, (label, unit, _) in ALERT_METRICS.items():
        value = payload_metric(payload, field)
        if value is not None:
            discovered[field] = {
                "field": field, "label": label, "unit": unit, "value": value,
                "data_type": "boolean" if isinstance(value, bool) else "number",
            }
    queue = [payload]
    seen = {id(payload)}
    while queue:
        current = queue.pop(0)
        for key, value in current.items():
            normalized = str(key).lower()
            if (
                isinstance(value, (int, float, bool))
                and normalized
                and normalized[0].isalpha()
                and normalized not in NON_BUSINESS_METRIC_FIELDS
                and normalized not in discovered
                and normalized not in ALL_ALERT_ALIASES
                and normalized.replace("_", "").isalnum()
            ):
                discovered[normalized] = {
                    "field": normalized,
                    "label": str(key).replace("_", " "),
                    "unit": "",
                    "value": value,
                    "data_type": "boolean" if isinstance(value, bool) else "number",
                }
            children = [value] if isinstance(value, dict) else value if isinstance(value, list) else []
            for child in children:
                if isinstance(child, dict) and id(child) not in seen:
                    seen.add(id(child))
                    queue.append(child)
    return list(discovered.values())


def threshold_matches(actual: Any, operator: str, threshold: Any) -> bool:
    if operator not in ALERT_OPERATORS or actual is None:
        return False
    try:
        if isinstance(actual, bool) or isinstance(threshold, bool):
            if operator not in {"eq", "neq"}:
                return False
            expected = threshold if isinstance(threshold, bool) else str(threshold).lower() == "true"
            return ALERT_OPERATORS[operator][1](bool(actual), expected)
        return ALERT_OPERATORS[operator][1](float(actual), float(threshold))
    except (TypeError, ValueError):
        return False


def infer_device(channel: str, payload: dict[str, Any]) -> str:
    normalized_channel = channel.lower().replace("-", "_")
    if channel == "s3/eora-s3-400tb-001/data" or normalized_channel == "bridge/uplink/generic/eora_s3_400tb_001/data":
        return "eora_s3_400tb_001"
    for key in ("device_id", "deviceId", "deviceName", "devEUI", "friendly_name", "ieeeAddr"):
        value = payload.get(key)
        if value:
            return str(value)
    device_info = payload.get("deviceInfo")
    if isinstance(device_info, dict):
        nested = device_info.get("deviceName") or device_info.get("devEui")
        if nested:
            return str(nested)
    parts = channel.strip("/").split("/")
    if len(parts) >= 4 and parts[0] == "bridge":
        return parts[3]
    if len(parts) >= 2:
        return parts[1]
    return "unknown"


async def record_event(
    source: str,
    direction: str,
    channel: str,
    payload: dict[str, Any],
    *,
    status: str = "ok",
    latency_ms: float | None = None,
) -> dict[str, Any]:
    global metrics_broadcast_at
    event = {
        "id": uuid.uuid4().hex,
        "timestamp": now_iso(),
        "source": source,
        "direction": direction,
        "project": infer_project(channel, payload),
        "device_id": infer_device(channel, payload),
        "channel": channel,
        "payload": payload,
        "trace_id": payload.get("trace_id") or payload.get("traceId"),
        "latency_ms": latency_ms,
        "status": status,
    }
    await asyncio.to_thread(db.insert_event, event)
    if channel == "/ctrl/ack" and event.get("trace_id"):
        await asyncio.to_thread(db.acknowledge_command, event["trace_id"], payload)
    if channel == "/scene/trigger" and payload.get("event") == "scene_triggered":
        await asyncio.to_thread(db.insert_scene_trigger, payload)
        for action in payload.get("actions_sent", []):
            if action.get("action") == "capture":
                try:
                    await asyncio.to_thread(
                        save_latest_camera_image,
                        str(action.get("device_id", "")),
                        str((action.get("params") or {}).get("save_directory", "")),
                    )
                except Exception as exc:
                    db.create_alert(event, "camera_save_failed", "warning", f"场景图像保存失败: {exc}", str(exc))
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    camera_packet = is_lora_camera_packet(payload)
    if not camera_packet and isinstance(data, dict):
        rules = await asyncio.to_thread(db.threshold_rules, event["device_id"])
        for rule in rules:
            if not rule.get("enabled", True):
                continue
            field = str(rule.get("field", ""))
            operator = str(rule.get("operator", ""))
            actual = payload_metric(data, field)
            threshold = rule.get("value")
            if not threshold_matches(actual, operator, threshold):
                continue
            label, unit, _ = ALERT_METRICS.get(field, (field, "", (field,)))
            operator_label = ALERT_OPERATORS[operator][0]
            value_text = str(actual).lower() if isinstance(actual, bool) else f"{actual:g}"
            threshold_text = str(threshold).lower() if isinstance(threshold, bool) else f"{threshold:g}"
            message = f"{label}当前为 {value_text}{unit}，达到告警条件（{operator_label} {threshold_text}{unit}）"
            await asyncio.to_thread(
                db.create_alert,
                event,
                f"threshold:{field}:{operator}",
                rule.get("severity", "warning"),
                message,
                {"field": field, "actual": actual, "operator": operator, "threshold": threshold, "unit": unit},
            )
    await hub.broadcast({"type": "event", "data": event})
    now = time.monotonic()
    if now - metrics_broadcast_at >= 2.0:
        metrics_broadcast_at = now
        metrics = await asyncio.to_thread(db.metrics)
        await hub.broadcast({"type": "metrics", "data": metrics})
    return event


class MqttService:
    def __init__(self) -> None:
        self.connections: dict[str, dict[str, Any]] = {}
        self.primary_key = ""
        self.loop: asyncio.AbstractEventLoop | None = None

    @property
    def connected(self) -> bool:
        return any(item["connected"] for item in self.connections.values())

    @property
    def connecting(self) -> bool:
        return any(item["connecting"] for item in self.connections.values())

    def _primary(self) -> dict[str, Any]:
        if self.primary_key in self.connections:
            return self.connections[self.primary_key]
        return next(iter(self.connections.values()), {})

    @property
    def host(self) -> str:
        return str(self._primary().get("host", ""))

    @property
    def port(self) -> int:
        return int(self._primary().get("port", 1883))

    @staticmethod
    def reason_ok(reason_code: Any) -> bool:
        if hasattr(reason_code, "is_failure"):
            return not bool(reason_code.is_failure)
        try:
            return int(reason_code) == 0
        except (TypeError, ValueError):
            return str(reason_code).lower() in ("success", "normal disconnection")

    def connect(
        self,
        host: str,
        port: int,
        loop: asyncio.AbstractEventLoop,
        *,
        name: str = "",
        client_id: str = "",
        username: str = "",
        password: str = "",
        topics: list[str] | None = None,
        qos: int = 1,
    ) -> None:
        if mqtt is None:
            raise RuntimeError("paho-mqtt is not installed")
        key = name.strip() or f"{host}:{port}"
        if key in self.connections:
            self.disconnect(key)
        self.loop = loop
        resolved_client_id = client_id or f"iot-test-console-{uuid.uuid4().hex[:8]}"
        state = {
            "name": key, "client": None, "connected": False, "connecting": True,
            "host": host, "port": port, "client_id": resolved_client_id,
            "username": username, "topics": topics or list(DEFAULT_TOPICS), "qos": qos,
            "reconnects": 0, "error": "",
        }
        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=resolved_client_id,
        )
        client.user_data_set(key)
        if username:
            client.username_pw_set(username, password)
        client.reconnect_delay_set(min_delay=1, max_delay=20)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        state["client"] = client
        self.connections[key] = state
        self.primary_key = key
        try:
            client.connect_async(host, port, keepalive=60)
            client.loop_start()
        except Exception as exc:
            state["connecting"] = False
            state["error"] = str(exc)
            raise

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        del flags, properties
        state = self.connections.get(str(userdata))
        if not state or state.get("client") is not client:
            return
        state["connecting"] = False
        state["connected"] = self.reason_ok(reason_code)
        if not state["connected"]:
            state["error"] = f"connect rejected: {reason_code}"
            return
        state["error"] = ""
        for topic in state["topics"]:
            client.subscribe(topic, qos=state["qos"])

    def _on_disconnect(self, client, userdata, flags, reason_code, properties) -> None:
        del flags, properties
        state = self.connections.get(str(userdata))
        if not state or state.get("client") is not client:
            return
        state["connected"] = False
        if not self.reason_ok(reason_code):
            state["error"] = f"disconnected: {reason_code}"
            state["reconnects"] += 1

    def _on_message(self, client, userdata, message) -> None:
        del client, userdata
        try:
            payload = json.loads(message.payload.decode("utf-8"))
            status = "ok"
            if not isinstance(payload, dict):
                payload = {"value": payload, "parse_error": "消息根节点必须是JSON对象"}
                status = "error"
        except Exception:
            payload = {"raw": message.payload.decode("utf-8", errors="replace")}
            status = "error"
        if self.loop:
            asyncio.run_coroutine_threadsafe(
                record_event("mqtt", "downlink" if "/downlink/" in message.topic else "uplink",
                             message.topic, payload, status=status),
                self.loop,
            )

    def publish(self, topic: str, payload: dict[str, Any]) -> bool:
        candidates = [item for item in self.connections.values() if item["connected"]]
        if mqtt is not None:
            matching = [
                item for item in candidates
                if any(mqtt.topic_matches_sub(subscription, topic) for subscription in item["topics"])
            ]
            if matching:
                candidates = matching
        if not candidates:
            return False
        project_broker = BRIDGE_PROFILE.get("mqtt", {}).get("broker")
        state = next((item for item in candidates if item["host"] == project_broker), candidates[0])
        info = state["client"].publish(topic, json_text(payload), qos=state["qos"])
        return info.rc == mqtt.MQTT_ERR_SUCCESS

    def disconnect(self, name: str | None = None) -> None:
        names = [name] if name is not None else list(self.connections)
        for key in names:
            state = self.connections.pop(key, None)
            client = state.get("client") if state else None
            if not client:
                continue
            try:
                client.disconnect()
                client.loop_stop()
            except Exception:
                pass
        if self.primary_key not in self.connections:
            self.primary_key = next(iter(self.connections), "")

    def status(self) -> dict[str, Any]:
        connections = [
            {key: value for key, value in item.items() if key != "client"}
            for item in self.connections.values()
        ]
        primary = self._primary()
        return {
            "connected": self.connected,
            "connecting": self.connecting,
            "connected_count": sum(item["connected"] for item in self.connections.values()),
            "name": primary.get("name", ""),
            "host": primary.get("host", ""),
            "port": primary.get("port", 1883),
            "client_id": primary.get("client_id", ""),
            "username": primary.get("username", ""),
            "topics": primary.get("topics", list(DEFAULT_TOPICS)),
            "qos": primary.get("qos", 1),
            "reconnects": sum(item["reconnects"] for item in self.connections.values()),
            "error": next((item["error"] for item in self.connections.values() if item["error"]), ""),
            "connections": connections,
        }


class VsoaService:
    def __init__(self) -> None:
        self.client = None
        self.connected = False
        self.url = ""
        self.error = ""
        self.loop: asyncio.AbstractEventLoop | None = None
        self.thread: threading.Thread | None = None
        self.health: dict[str, Any] = {}
        self.health_error = ""
        self._fetch_lock = threading.Lock()
        self._stop_reconnect = threading.Event()

    def connect(self, url: str, loop: asyncio.AbstractEventLoop) -> None:
        if vsoa is None:
            raise RuntimeError("vsoa SDK is not installed")
        self.disconnect()
        self.url, self.loop, self.error = url, loop, ""
        self._stop_reconnect = threading.Event()
        client = self._open_client(url)
        self.client, self.connected = client, True
        self.thread = threading.Thread(
            target=self._run_with_reconnect,
            args=(client, url, self._stop_reconnect),
            daemon=True,
            name="platform-vsoa",
        )
        self.thread.start()

    def _open_client(self, url: str):
        client = vsoa.Client()

        def on_message(cli, message_url, payload, quick):
            del cli, quick
            data = dict(payload.param) if payload and getattr(payload, "param", None) else {}
            url_text = message_url.url if hasattr(message_url, "url") else str(message_url)
            if self.loop:
                asyncio.run_coroutine_threadsafe(
                    record_event("vsoa", "result", url_text, data), self.loop
                )

        def on_connect(cli, connected, server_info):
            del server_info
            if self.client is cli:
                self.connected = bool(connected)
                if not connected:
                    self.health = {}
                    self.health_error = "VSOA connection lost"

        client.onmessage = on_message
        client.onconnect = on_connect
        result = client.connect(url)
        if result != 0:
            self.error = f"connect failed: {result}"
            raise RuntimeError(self.error)
        for item in VSOA_URLS:
            client.subscribe(item)
        return client

    def _run_with_reconnect(
        self,
        client,
        url: str,
        stop_event: threading.Event,
    ) -> None:
        current = client
        while not stop_event.is_set():
            try:
                current.run()
            except Exception as exc:
                if not stop_event.is_set():
                    self.error = f"VSOA receive loop stopped: {exc}"
            if stop_event.is_set():
                return
            if self.client is current:
                self.connected = False
                self.health = {}
                self.health_error = "VSOA disconnected; reconnecting"
            if stop_event.wait(2.0):
                return
            try:
                current = self._open_client(url)
                if stop_event.is_set():
                    current.close()
                    return
                self.client = current
                self.connected = True
                self.error = ""
                self.health_error = ""
            except Exception as exc:
                self.error = f"VSOA reconnect failed: {exc}"
                if stop_event.wait(2.0):
                    return

    def probe_health(self) -> dict[str, Any]:
        if not self.client or not self.connected:
            self.health = {}
            self.health_error = "VSOA client is not connected"
            return {}
        try:
            with self._fetch_lock:
                header, reply, status = self.client.fetch(
                    "/bridge/health", payload=vsoa.Payload(param={}), timeout=2.0
                )
            del header, status
            data = dict(reply.param) if reply and getattr(reply, "param", None) else {}
            self.health = data
            self.health_error = "" if data.get("status") == "running" else "invalid health reply"
        except Exception as exc:
            self.health = {}
            self.health_error = str(exc)
        return self.health

    def send_command(self, command: dict[str, Any]) -> dict[str, Any]:
        if not self.client or not self.connected:
            raise RuntimeError("VSOA bridge is not connected")
        with self._fetch_lock:
            header, reply, status = self.client.fetch(
                "/bridge/send_command",
                payload=vsoa.Payload(param=command),
                timeout=max(2.0, float(command.get("timeout_ms", 10_000)) / 1000 + 1),
            )
        del header, status
        return dict(reply.param) if reply and getattr(reply, "param", None) else {}

    def scene_call(self, operation: str, payload: dict[str, Any] | None = None) -> Any:
        if not self.connected or not self.url:
            raise RuntimeError("请先连接本机协议桥接 VSOA 服务（通常为 vsoa://127.0.0.1:3001）")
        # Scene RPC is isolated from the long-running subscription client. A busy
        # telemetry stream must not block scene list/edit requests in the UI.
        client = vsoa.Client()
        result = client.connect(self.url)
        if result != 0:
            raise RuntimeError(f"场景服务连接失败: {result}")
        runner = threading.Thread(target=client.run, daemon=True, name=f"scene-rpc-{operation}")
        runner.start()
        time.sleep(0.05)
        try:
            header, reply, status = client.fetch(
                f"/scene/{operation}", payload=vsoa.Payload(param=payload or {}), timeout=3.0,
            )
        finally:
            try:
                client.close()
            except Exception:
                pass
        del header
        if status != 0:
            raise RuntimeError(f"场景服务调用超时或失败: {status}")
        response = dict(reply.param) if reply and getattr(reply, "param", None) else {}
        if response.get("error_code", -1) != 0:
            raise RuntimeError(response.get("error_msg") or "场景服务调用失败")
        return response.get("data")

    def disconnect(self) -> None:
        self._stop_reconnect.set()
        client = self.client
        self.connected = False
        self.client = None
        self.health = {}
        if client is not None:
            try:
                client.close()
            except Exception:
                pass

    def status(self) -> dict[str, Any]:
        return {
            "connected": self.connected,
            "url": self.url,
            "error": self.error,
            "health": self.health,
            "health_error": self.health_error,
        }


mqtt_service = MqttService()
vsoa_service = VsoaService()
simulation_tasks: dict[str, asyncio.Task] = {}
performance_tasks: dict[str, asyncio.Task] = {}


class ConnectionRequest(BaseModel):
    name: str = Field(default="", max_length=40)
    host: str = BRIDGE_PROFILE.get("mqtt", {}).get("broker", "")
    port: int = Field(default=BRIDGE_PROFILE.get("mqtt", {}).get("port", 1883), ge=1, le=65535)
    client_id: str = Field(default="", max_length=128)
    username: str = Field(default="", max_length=128)
    password: str = Field(default="", max_length=256)
    topics: list[str] = Field(default_factory=lambda: list(DEFAULT_TOPICS))
    qos: int = Field(default=1, ge=0, le=2)


class ConnectionProfileRequest(ConnectionRequest):
    name: str = Field(min_length=1, max_length=40)


class VsoaRequest(BaseModel):
    url: str = BRIDGE_PROFILE.get("vsoa", {}).get("local_url", "vsoa://127.0.0.1:3001")


class VsoaProfileRequest(VsoaRequest):
    name: str = Field(min_length=1, max_length=40)


class SimulationRequest(BaseModel):
    project: str = Field(pattern="^(lora|zigbee)$")
    device_id: str = Field(min_length=1, max_length=64)
    interval_ms: int = Field(default=800, ge=100, le=60000)
    count: int = Field(default=30, ge=1, le=1000)


class TestRunRequest(BaseModel):
    experiment: str = Field(pattern="^(L5|L6|Z6|ALL|OPS)$")
    scenario: str = "联合链路快速检查"


class PerformanceRunRequest(BaseModel):
    project: str = Field(default="lora", pattern="^(lora|zigbee)$")
    device_count: int = Field(default=5, ge=1, le=100)
    rate: int = Field(default=20, ge=1, le=200)
    duration_seconds: int = Field(default=15, ge=3, le=120)
    pattern: str = Field(default="steady", pattern="^(steady|burst)$")


class ProjectTestRequest(BaseModel):
    scope: str = Field(default="all", pattern="^(all|uplink|downlink)$")


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class UserRequest(BaseModel):
    username: str = Field(pattern=r"^[A-Za-z0-9_.-]{2,64}$")
    display_name: str = Field(min_length=1, max_length=80)
    password: str = Field(default="", max_length=128)
    role: str = Field(pattern="^(user|tester|admin)$")
    active: bool = True


class DeviceProfileRequest(BaseModel):
    device_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=120)
    project: str = Field(pattern="^(lora|zigbee|wifi|generic)$")
    device_type: str = Field(default="environment_sensor", max_length=80)
    capabilities: list[dict[str, Any]] = Field(default_factory=list)
    thresholds: dict[str, Any] = Field(default_factory=dict)
    connection_source: str = Field(default="", max_length=120)


class DeviceAnnotationRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=120)
    note: str = Field(default="", max_length=500)


class ThresholdRuleRequest(BaseModel):
    field: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
    operator: str = Field(pattern="^(gt|gte|lt|lte|eq|neq)$")
    value: float | bool
    severity: str = Field(default="warning", pattern="^(warning|critical)$")
    enabled: bool = True


class DeviceThresholdRequest(BaseModel):
    rules: list[ThresholdRuleRequest] = Field(default_factory=list, max_length=32)


class CameraSaveRequest(BaseModel):
    save_directory: str = Field(default="", max_length=500)


class CommandRequest(BaseModel):
    device_id: str = Field(min_length=1, max_length=128)
    project: str = Field(pattern="^(lora|zigbee|wifi|generic)$")
    command: str = Field(min_length=1, max_length=80)
    parameters: dict[str, Any] = Field(default_factory=dict)
    confirmed: bool = False


class SceneConditionRequest(BaseModel):
    device_id: str = Field(default="", max_length=128)
    sensor: str = Field(min_length=1, max_length=64)
    operator: str = Field(pattern="^(gt|gte|lt|lte|eq|neq)$")
    value: float | bool
    trigger_mode: str = Field(default="level", pattern="^(level|edge)$")
    hold_seconds: int = Field(default=0, ge=0, le=86400)


class SceneActionRequest(BaseModel):
    device_type: str = Field(pattern="^(lora|zigbee|wifi|generic)$")
    device_id: str = Field(min_length=1, max_length=128)
    action: str = Field(default="set", pattern="^(set|reset|capture)$")
    params: dict[str, Any]


class SceneRuleRequest(BaseModel):
    scene_id: str = Field(default="", max_length=128)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    condition_logic: str = Field(default="and", pattern="^(and|or)$")
    conditions: list[SceneConditionRequest] = Field(min_length=1, max_length=10)
    actions: list[SceneActionRequest] = Field(min_length=1, max_length=10)
    enabled: bool = True
    duration_seconds: int = Field(default=0, ge=0, le=86400)
    cooldown_seconds: int = Field(default=60, ge=0, le=86400)
    schedule_start: str | None = Field(default=None, max_length=80)
    schedule_end: str | None = Field(default=None, max_length=80)
    created_at: str | None = None
    updated_at: str | None = None
    last_triggered_at: str | None = None


def mqtt_diagnostic(request: ConnectionRequest) -> dict[str, Any]:
    steps = []
    try:
        address = socket.gethostbyname(request.host)
        steps.append({"name": "地址解析", "ok": True, "detail": address})
    except OSError as exc:
        return {"ok": False, "steps": [{"name": "地址解析", "ok": False, "detail": str(exc)}]}

    started = time.perf_counter()
    try:
        with socket.create_connection((request.host, request.port), timeout=3):
            elapsed = round((time.perf_counter() - started) * 1000, 1)
            steps.append({"name": "TCP 端口", "ok": True, "detail": f"连接成功，{elapsed} ms"})
    except OSError as exc:
        steps.append({"name": "TCP 端口", "ok": False, "detail": str(exc)})
        return {"ok": False, "steps": steps}

    if mqtt is None:
        steps.append({"name": "MQTT 握手", "ok": False, "detail": "paho-mqtt 未安装"})
        return {"ok": False, "steps": steps}

    done = threading.Event()
    result = {"ok": False, "detail": "Broker 未在 4 秒内返回 CONNACK"}
    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=request.client_id or f"iot-diagnostic-{uuid.uuid4().hex[:8]}",
    )
    if request.username:
        client.username_pw_set(request.username, request.password)

    def on_connect(cli, userdata, flags, reason_code, properties):
        del cli, userdata, flags, properties
        result["ok"] = MqttService.reason_ok(reason_code)
        result["detail"] = str(reason_code)
        done.set()

    def on_connect_fail(cli, userdata):
        del cli, userdata
        result["detail"] = "网络连接失败或 Broker 拒绝连接"
        done.set()

    client.on_connect = on_connect
    client.on_connect_fail = on_connect_fail
    try:
        client.connect_async(request.host, request.port, keepalive=15)
        client.loop_start()
        done.wait(4)
    except Exception as exc:
        result["detail"] = str(exc)
    finally:
        try:
            client.disconnect()
            client.loop_stop()
        except Exception:
            pass
    steps.append({"name": "MQTT 握手", **result})
    return {"ok": bool(result["ok"]), "steps": steps}


_project_sim_device_class = None


def project_sim_device(device_id: str, project: str):
    global _project_sim_device_class
    if _project_sim_device_class is None:
        module_path = BRIDGE_ROOT / "tools" / "sim_device.py"
        spec = importlib.util.spec_from_file_location("bridge_project_sim_device", module_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"cannot load project simulator: {module_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _project_sim_device_class = module.SimDevice
    return _project_sim_device_class(device_id, project)


def project_uplink_topic(project: str, device_id: str) -> str:
    candidates = [
        topic for topic in UPLINK_TOPICS
        if f"/{project}/" in topic and "+" in topic
    ]
    if not candidates:
        raise RuntimeError(f"项目 config.yaml 未配置 {project} 上行 topic")
    return candidates[0].replace("+", device_id, 1)


async def run_simulation(task_id: str, request: SimulationRequest) -> None:
    device = project_sim_device(request.device_id, request.project)
    topic = project_uplink_topic(request.project, request.device_id)
    try:
        for _ in range(request.count):
            payload = device.next_payload()
            if not mqtt_service.publish(topic, payload):
                raise RuntimeError("MQTT publish failed")
            await asyncio.sleep(request.interval_ms / 1000)
    finally:
        simulation_tasks.pop(task_id, None)
        await hub.broadcast({"type": "simulation", "data": {"task_id": task_id, "running": False}})


async def execute_test_run(run: dict[str, Any]) -> None:
    started = time.perf_counter()
    run["status"] = "running"
    db.upsert_run(run)
    await hub.broadcast({"type": "run", "data": run})
    await asyncio.sleep(0.25)
    events = db.recent_events(500)
    pairs = db.transformation_pairs(50)
    has_uplink = any(item["direction"] == "uplink" for item in events)
    has_downlink = any(item["direction"] == "downlink" for item in events)
    has_vsoa = any(item["direction"] == "result" for item in events)
    bridge_health = await asyncio.to_thread(vsoa_service.probe_health)
    has_error_visibility = any(item["status"] == "error" for item in events)
    checks = [
        {"name": "平台服务与数据库可用", "ok": True, "detail": f"已留存 {len(events)} 条消息"},
        {"name": "项目配置已载入", "ok": bool(BRIDGE_PROFILE.get("available")), "detail": str(BRIDGE_CONFIG_PATH)},
        {"name": "MQTT Broker 已连接", "ok": mqtt_service.connected, "detail": f"{mqtt_service.host}:{mqtt_service.port}" if mqtt_service.connected else "平台未连接项目 Broker"},
        {"name": "桥接健康检查", "ok": bridge_health.get("status") == "running", "detail": json_text(bridge_health) if bridge_health else vsoa_service.health_error},
        {"name": "上行输入可观测", "ok": has_uplink, "detail": "检测到真实 MQTT 上行消息" if has_uplink else "尚无 MQTT 上行消息"},
        {"name": "VSOA 输出可观测", "ok": has_vsoa, "detail": "检测到桥接项目发布结果" if has_vsoa else "尚无真实 VSOA 结果"},
        {"name": "输入输出可关联", "ok": bool(pairs), "detail": f"已关联 {len(pairs)} 组转换" if pairs else "没有可关联的转换数据"},
        {"name": "异常状态可追踪", "ok": True, "detail": f"已记录 {sum(item['status'] == 'error' for item in events)} 条异常" if has_error_visibility else "当前没有异常消息"},
    ]
    if run["experiment"] in ("L6", "Z6", "ALL") or "下行" in run["scenario"]:
        checks.append({"name": "下行控制可观测", "ok": has_downlink, "detail": "检测到下行消息" if has_downlink else "尚无下行控制消息"})
    for index, check in enumerate(checks):
        await asyncio.sleep(0.12)
        await hub.broadcast(
            {
                "type": "run_progress",
                "data": {"id": run["id"], "current": index + 1, "total": len(checks)},
            }
        )
    passed = sum(check["ok"] for check in checks)
    failed = len(checks) - passed
    run.update(
        {
            "finished_at": now_iso(),
            "status": "passed" if failed == 0 else "failed",
            "passed": passed,
            "failed": failed,
            "details": checks,
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
        }
    )
    db.upsert_run(run)
    await hub.broadcast({"type": "run", "data": run})


async def execute_project_tests(run: dict[str, Any], scope: str) -> None:
    started = time.perf_counter()
    run["status"] = "running"
    db.upsert_run(run)
    await hub.broadcast({"type": "run", "data": run})
    target = "tests" if scope == "all" else f"tests/{scope}"
    try:
        process = await asyncio.create_subprocess_exec(
            str(BRIDGE_PYTHON), "-m", "pytest", target, "-q",
            cwd=str(BRIDGE_ROOT),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        output_bytes, _ = await process.communicate()
        output = output_bytes.decode("utf-8", errors="replace").strip()
        summary = "\n".join(output.splitlines()[-12:])
        ok = process.returncode == 0
        run.update(
            {
                "status": "passed" if ok else "failed",
                "passed": 1 if ok else 0,
                "failed": 0 if ok else 1,
                "details": [{
                    "name": f"bridge_vsoa_mqtt {target}",
                    "ok": ok,
                    "detail": summary or f"pytest exit code {process.returncode}",
                }],
            }
        )
    except Exception as exc:
        run.update(
            {
                "status": "failed", "passed": 0, "failed": 1,
                "details": [{"name": "项目 pytest", "ok": False, "detail": str(exc)}],
            }
        )
    run["finished_at"] = now_iso()
    run["duration_ms"] = round((time.perf_counter() - started) * 1000, 1)
    db.upsert_run(run)
    await hub.broadcast({"type": "run", "data": run})


def percentile(values: list[float], percent: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * percent
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return round(ordered[lower], 1)
    value = ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)
    return round(value, 1)


def build_performance_result(run: dict[str, Any], elapsed: float) -> None:
    events = db.performance_events(run["started_at"], run["config"]["device_prefix"])
    inputs = [
        item for item in events
        if item["source"] == "mqtt" and item["direction"] == "uplink"
    ]
    outputs = [
        item for item in events
        if item["source"] == "vsoa" and item["channel"] == "/device/update"
    ]
    inputs_by_device: dict[str, list[dict[str, Any]]] = {}
    outputs_by_device: dict[str, list[dict[str, Any]]] = {}
    for item in inputs:
        inputs_by_device.setdefault(item["device_id"], []).append(item)
    for item in outputs:
        outputs_by_device.setdefault(item["device_id"], []).append(item)
    latencies = []
    latency_by_output: dict[str, float] = {}
    for device_id, device_inputs in inputs_by_device.items():
        device_outputs = outputs_by_device.get(device_id, [])
        for source, result in zip(device_inputs, device_outputs):
            latency = max(
                0.0,
                (
                    datetime.fromisoformat(result["timestamp"])
                    - datetime.fromisoformat(source["timestamp"])
                ).total_seconds() * 1000,
            )
            latencies.append(latency)
            latency_by_output[result["id"]] = latency

    sent = int(run["metrics"].get("sent", 0))
    received = len(inputs)
    converted = len(outputs)
    duplicates = max(0, converted - sent)
    lost = max(0, sent - converted)
    errors = sum(item["status"] == "error" for item in events)
    buckets: dict[int, dict[str, Any]] = {}
    start_ts = datetime.fromisoformat(run["started_at"]).timestamp()
    for item in events:
        second = max(0, int(datetime.fromisoformat(item["timestamp"]).timestamp() - start_ts))
        bucket = buckets.setdefault(second, {"second": second, "input": 0, "output": 0, "latencies": []})
        if item["direction"] == "uplink":
            bucket["input"] += 1
        elif item["direction"] == "result":
            if item["channel"] == "/device/update":
                bucket["output"] += 1
                if item["id"] in latency_by_output:
                    bucket["latencies"].append(latency_by_output[item["id"]])
    run["series"] = [
        {
            "second": second,
            "input": bucket["input"],
            "output": bucket["output"],
            "latency": round(sum(bucket["latencies"]) / len(bucket["latencies"]), 1)
            if bucket["latencies"] else 0,
        }
        for second, bucket in sorted(buckets.items())
    ]
    run["metrics"].update(
        {
            "received": received,
            "converted": converted,
            "lost": lost,
            "duplicates": duplicates,
            "errors": errors,
            "throughput": round(received / max(run["config"]["duration_seconds"], 0.001), 1),
            "conversion_rate": round(converted * 100 / sent, 2) if sent else 0,
            "avg_latency_ms": round(sum(latencies) / len(latencies), 1) if latencies else 0,
            "p50_latency_ms": percentile(latencies, 0.50),
            "p95_latency_ms": percentile(latencies, 0.95),
            "p99_latency_ms": percentile(latencies, 0.99),
        }
    )


async def execute_performance_run(run: dict[str, Any]) -> None:
    config = run["config"]
    started = time.perf_counter()
    run["status"] = "running"
    db.upsert_performance_run(run)
    await hub.broadcast({"type": "performance", "data": run})
    total = config["rate"] * config["duration_seconds"]
    interval = 1 / config["rate"]
    devices = [
        project_sim_device(f"{config['device_prefix']}{index + 1:03d}", config["project"])
        for index in range(config["device_count"])
    ]
    try:
        for index in range(total):
            target = started + (index // config["rate"] if config["pattern"] == "burst" else index * interval)
            await asyncio.sleep(max(0, target - time.perf_counter()))
            device = devices[index % config["device_count"]]
            topic = project_uplink_topic(config["project"], device.device_id)
            payload = device.next_payload()
            if mqtt_service.publish(topic, payload):
                run["metrics"]["sent"] += 1
            else:
                run["metrics"]["publish_failed"] += 1
            if index % max(1, config["rate"]) == 0:
                run["metrics"]["progress"] = round((index + 1) * 100 / total, 1)
                await hub.broadcast({"type": "performance", "data": run})
        await asyncio.sleep(3.0)
        elapsed = time.perf_counter() - started
        build_performance_result(run, elapsed)
        run["metrics"]["progress"] = 100
        run["status"] = "completed"
        run["finished_at"] = now_iso()
    except asyncio.CancelledError:
        elapsed = time.perf_counter() - started
        build_performance_result(run, elapsed)
        run["status"] = "cancelled"
        run["finished_at"] = now_iso()
        raise
    except Exception as exc:
        run["status"] = "failed"
        run["metrics"]["error"] = str(exc)
        run["finished_at"] = now_iso()
    finally:
        db.upsert_performance_run(run)
        performance_tasks.pop(run["id"], None)
        await hub.broadcast({"type": "performance", "data": run})


@asynccontextmanager
async def lifespan(app: FastAPI):
    del app
    await asyncio.to_thread(backup_platform_database, "startup")
    db.initialize()
    mqtt_profile = BRIDGE_PROFILE.get("mqtt", {})
    mqtt_host = str(mqtt_profile.get("broker", "")).strip()
    if mqtt_host:
        try:
            mqtt_service.connect(
                mqtt_host,
                int(mqtt_profile.get("port", 1883)),
                asyncio.get_running_loop(),
                name="项目默认 Broker",
                username=str(mqtt_profile.get("username", "")),
                password=str(mqtt_profile.get("password", "")),
                topics=list(mqtt_profile.get("uplink_topics") or DEFAULT_TOPICS),
                qos=int(mqtt_profile.get("qos", 1)),
            )
        except Exception as exc:
            # Keep the platform available so the operations page can diagnose or reconnect it.
            state = mqtt_service.connections.get("项目默认 Broker")
            if state is not None:
                state["error"] = f"项目 Broker 自动连接失败: {exc}"
    local_vsoa_url = BRIDGE_PROFILE.get("vsoa", {}).get("local_url", "")
    if local_vsoa_url:
        try:
            await asyncio.to_thread(vsoa_service.connect, local_vsoa_url, asyncio.get_running_loop())
            await asyncio.to_thread(vsoa_service.probe_health)
        except Exception as exc:
            vsoa_service.error = f"本机桥接自动连接失败: {exc}"
    yield
    mqtt_service.disconnect()
    vsoa_service.disconnect()
    for task in list(simulation_tasks.values()):
        task.cancel()
    for task in list(performance_tasks.values()):
        task.cancel()
    await asyncio.to_thread(backup_platform_database, "shutdown")


app = FastAPI(title="Smart Environment IoT Platform", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1|10\.[0-9.]+|192\.168\.[0-9.]+|172\.(1[6-9]|2[0-9]|3[01])\.[0-9.]+)(:[0-9]+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ROLE_RANK = {"user": 1, "tester": 2, "admin": 3}


def required_role(path: str, method: str) -> str:
    if path.startswith("/api/admin") or path.startswith("/api/connection-profiles") or path.startswith("/api/vsoa-connection-profiles"):
        return "admin"
    if path.startswith("/api/mqtt/") or path.startswith("/api/vsoa/"):
        return "admin"
    if path.startswith("/api/simulations") or path.startswith("/api/performance-runs") or path.startswith("/api/test-runs") or path.startswith("/api/project-tests") or path.startswith("/api/transformations"):
        return "tester"
    if path.startswith("/api/device-profiles") and method != "GET":
        return "admin"
    return "user"


@app.middleware("http")
async def authorize_request(request: Request, call_next):
    path = request.url.path
    if request.method == "OPTIONS" or not path.startswith("/api/") or path == "/api/auth/login":
        return await call_next(request)
    authorization = request.headers.get("authorization", "")
    if not authorization.startswith("Bearer "):
        return Response(json_text({"detail": "请先登录"}), status_code=401, media_type="application/json")
    try:
        user = decode_token(authorization[7:])
    except HTTPException as exc:
        return Response(json_text({"detail": exc.detail}), status_code=exc.status_code, media_type="application/json")
    expected = required_role(path, request.method)
    if ROLE_RANK.get(user.get("role", ""), 0) < ROLE_RANK[expected]:
        return Response(json_text({"detail": "当前账号没有此操作权限"}), status_code=403, media_type="application/json")
    request.state.user = user
    response = await call_next(request)
    if request.method not in {"GET", "HEAD", "OPTIONS"} and response.status_code < 400:
        db.audit(user["sub"], request.method.lower(), path, {"status_code": response.status_code}, request.client.host if request.client else "")
    return response


@app.post("/api/auth/login")
async def login(request: LoginRequest, http_request: Request):
    user = db.authenticate(request.username.strip(), request.password)
    if not user:
        db.audit(request.username.strip() or "unknown", "login_failed", "auth", {}, http_request.client.host if http_request.client else "")
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    db.audit(user["username"], "login", "auth", {"role": user["role"]}, http_request.client.host if http_request.client else "")
    return {"token": create_token(user), "user": user, "expires_in": TOKEN_TTL_SECONDS}


@app.get("/api/auth/me")
async def current_user(request: Request):
    return request.state.user


@app.get("/api/status")
def get_status():
    bridge_running = vsoa_service.health.get("status") == "running"
    link_connected = mqtt_service.connected and vsoa_service.connected
    return {
        "platform": {
            "online": True,
            "mode": "ready" if link_connected else "incomplete",
        },
        "mqtt": mqtt_service.status(),
        "vsoa": vsoa_service.status(),
        "bridge": {
            "host": "127.0.0.1",
            "port": int(BRIDGE_PROFILE.get("vsoa", {}).get("local_url", "vsoa://127.0.0.1:3001").rsplit(":", 1)[-1]),
            "connected": vsoa_service.connected,
            "health_verified": bridge_running,
            "health": vsoa_service.health,
            "project_root": str(BRIDGE_ROOT),
        },
        "metrics": db.metrics(),
        "simulations": len(simulation_tasks),
        "performance_runs": len(performance_tasks),
    }


@app.get("/api/project")
def get_project_profile():
    return load_bridge_profile()


@app.get("/api/project/health")
async def get_project_health():
    health = await asyncio.to_thread(vsoa_service.probe_health)
    if not health:
        raise HTTPException(status_code=503, detail=vsoa_service.health_error or "bridge unavailable")
    return health


@app.get("/api/events")
def get_events(limit: int = 100, offset: int = 0):
    return db.recent_events(max(1, min(limit, 500)), max(0, offset))


@app.get("/api/temperature-series")
def get_temperature_series(limit_per_device: int = 500, include_performance: bool = False):
    return db.temperature_series(
        max(10, min(limit_per_device, 1000)), include_performance=include_performance
    )


@app.get("/api/devices")
def get_devices():
    return db.device_summaries()


@app.patch("/api/devices/{device_id}/annotation")
async def update_device_annotation(device_id: str, annotation: DeviceAnnotationRequest, request: Request):
    known = {item["device_id"] for item in db.device_summaries()}
    if device_id not in known:
        raise HTTPException(status_code=404, detail="设备不存在")
    saved = db.upsert_device_annotation(
        device_id, annotation.display_name, annotation.note, request.state.user["sub"]
    )
    db.audit(request.state.user["sub"], "device_annotation_saved", device_id, saved)
    return saved


def save_latest_camera_image(device_id: str, save_directory: str = "") -> dict[str, Any]:
    cameras = db.wifi_dashboard(20).get("devices", [])
    camera = next((item for item in cameras if item["device_id"] == device_id), None)
    if not camera or not camera.get("latest_image"):
        raise HTTPException(status_code=404, detail="该设备暂无可保存图像")
    image = camera["latest_image"]
    if not image.startswith("data:image/") or ";base64," not in image:
        raise HTTPException(status_code=400, detail="当前图像不是可保存的 Base64 帧")
    header, encoded = image.split(",", 1)
    extension = ".png" if "image/png" in header else ".jpg"
    root = Path(save_directory).expanduser() if save_directory.strip() else DATA_DIR / "camera_captures"
    if not root.is_absolute():
        root = (ROOT / root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    safe_device = "".join(char if char.isalnum() or char in "-_" else "_" for char in device_id)
    destination = root / f"{safe_device}-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}{extension}"
    try:
        destination.write_bytes(base64.b64decode(encoded, validate=True))
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=400, detail=f"保存图像失败: {exc}") from exc
    return {"ok": True, "device_id": device_id, "saved_path": str(destination), "bytes": destination.stat().st_size}


@app.post("/api/cameras/{device_id}/save")
async def save_camera_image(device_id: str, payload: CameraSaveRequest, request: Request):
    result = await asyncio.to_thread(save_latest_camera_image, device_id, payload.save_directory)
    db.audit(request.state.user["sub"], "camera_image_saved", device_id, result)
    return result


@app.get("/api/lora-dashboard")
async def get_lora_dashboard(limit_per_device: int = 500):
    return db.lora_dashboard(max(20, min(limit_per_device, 1000)))


@app.get("/api/zigbee-dashboard")
async def get_zigbee_dashboard(limit_per_device: int = 500):
    return db.zigbee_dashboard(max(20, min(limit_per_device, 1000)))


@app.get("/api/wifi-dashboard")
async def get_wifi_dashboard(limit_per_device: int = 500):
    return db.wifi_dashboard(max(20, min(limit_per_device, 1000)))


@app.get("/api/device-profiles")
async def get_device_profiles():
    return db.device_profiles()


@app.post("/api/device-profiles")
async def upsert_device_profile(profile: DeviceProfileRequest, request: Request):
    saved = db.upsert_device_profile(profile.model_dump())
    db.audit(request.state.user["sub"], "device_profile_saved", profile.device_id, saved)
    return saved


@app.put("/api/devices/{device_id}/thresholds")
async def save_device_thresholds(
    device_id: str,
    payload: DeviceThresholdRequest,
    request: Request,
):
    device = next(
        (item for item in db.device_summaries() if item["device_id"] == device_id),
        None,
    )
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")
    rules = [rule.model_dump() for rule in payload.rules]
    saved = db.save_threshold_rules(device, rules)
    db.audit(request.state.user["sub"], "device_thresholds_saved", device_id, {"rules": rules})
    return saved["thresholds"]


@app.get("/api/alerts")
def get_alerts(limit: int = 100):
    return db.alerts(max(1, min(limit, 500)))


@app.post("/api/alerts/{alert_id}/acknowledge")
async def acknowledge_alert(alert_id: str, request: Request):
    username = request.state.user["sub"]
    if not db.acknowledge_alert(alert_id, username):
        raise HTTPException(status_code=404, detail="告警不存在")
    db.audit(username, "alert_acknowledged", alert_id, {})
    return {"ok": True}


@app.get("/api/commands")
def get_commands(limit: int = 100):
    return db.commands(max(1, min(limit, 500)))


def scene_rpc(operation: str, payload: dict[str, Any] | None = None) -> Any:
    try:
        return vsoa_service.scene_call(operation, payload)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def scene_action_conflict(candidate: dict[str, Any], ignored_scene_id: str = "") -> str | None:
    if not candidate.get("enabled"):
        return None
    listed = scene_rpc("list", {}) or {}
    opposite = {
        ("on", "off"), ("off", "on"), ("blink", "off"), ("off", "blink"),
        (True, False), (False, True), ("start", "stop"), ("stop", "start"),
    }
    for scene in listed.get("scenes", []):
        if not scene.get("enabled") or scene.get("scene_id") in {ignored_scene_id, candidate.get("scene_id")}:
            continue
        for current in candidate.get("actions", []):
            if current.get("action") == "capture":
                continue
            for existing in scene.get("actions", []):
                if current.get("device_id") != existing.get("device_id"):
                    continue
                current_params = current.get("params") or {}
                existing_params = existing.get("params") or {}
                control_keys = set(current_params) & set(existing_params) & {"relay", "led", "buzzer", "motor", "state"}
                for key in control_keys:
                    value, old_value = current_params[key], existing_params[key]
                    angle_conflict = key == "motor" and value == old_value == "rotate" and current_params.get("angle") != existing_params.get("angle")
                    if value != old_value or (value, old_value) in opposite or angle_conflict:
                        return f"与场景“{scene.get('name', scene.get('scene_id'))}”冲突：设备 {current.get('device_id')} 的 {key} 动作互斥"
    return None


@app.get("/api/scenes/sensors")
async def get_scene_sensors():
    data = await asyncio.to_thread(scene_rpc, "sensors", {})
    return (data or {}).get("sensors", [])


@app.get("/api/scene-triggers")
async def get_scene_triggers(limit: int = 100):
    return db.scene_triggers(max(1, min(limit, 500)))


@app.get("/api/scenes")
async def get_scenes():
    data = await asyncio.to_thread(scene_rpc, "list", {})
    return (data or {}).get("scenes", [])


@app.post("/api/scenes")
async def create_scene(scene: SceneRuleRequest):
    payload = scene.model_dump(exclude_none=True)
    conflict = await asyncio.to_thread(scene_action_conflict, payload)
    if conflict:
        raise HTTPException(status_code=409, detail=conflict)
    return await asyncio.to_thread(scene_rpc, "add", payload)


@app.put("/api/scenes/{scene_id}")
async def update_scene(scene_id: str, scene: SceneRuleRequest):
    payload = scene.model_dump(exclude_none=True)
    payload["scene_id"] = scene_id
    conflict = await asyncio.to_thread(scene_action_conflict, payload, scene_id)
    if conflict:
        raise HTTPException(status_code=409, detail=conflict)
    return await asyncio.to_thread(scene_rpc, "update", payload)


@app.delete("/api/scenes/{scene_id}")
async def delete_scene(scene_id: str):
    return await asyncio.to_thread(scene_rpc, "delete", {"scene_id": scene_id})


@app.post("/api/scenes/{scene_id}/enable")
async def enable_scene(scene_id: str):
    return await asyncio.to_thread(scene_rpc, "enable", {"scene_id": scene_id})


@app.post("/api/scenes/{scene_id}/disable")
async def disable_scene(scene_id: str):
    return await asyncio.to_thread(scene_rpc, "disable", {"scene_id": scene_id})


@app.post("/api/commands")
async def create_device_command(command_request: CommandRequest, request: Request):
    if not command_request.confirmed:
        raise HTTPException(status_code=400, detail="控制命令必须由用户确认")
    normalized_device_id = command_request.device_id.strip().lower().replace("-", "_")
    is_eora_device = (
        command_request.project == "lora"
        or (
            command_request.project in {"generic", "wifi"}
            and normalized_device_id == "eora_s3_400tb_001"
        )
    )
    if command_request.project not in {"lora", "zigbee"} and not is_eora_device:
        raise HTTPException(status_code=400, detail="该通用设备尚未配置下行适配器")
    trace_id = uuid.uuid4().hex
    action_map = {
        "turn_on": "set", "turn_off": "set", "refresh": "get",
        "motor_on": "set", "motor_off": "set",
        "led_on": "set", "led_off": "set",
        "buzzer_on": "set", "buzzer_off": "set",
        "led_blink_on": "set", "led_blink_off": "set",
    }
    action = action_map.get(command_request.command, command_request.command)
    if action not in {"set", "get", "reset", "config"}:
        raise HTTPException(status_code=400, detail="桥接项目不支持该控制动作")
    parameters = dict(command_request.parameters)
    mqtt_payload: dict[str, Any] | None = None
    if is_eora_device:
        actuator = next((name for name in ("motor", "led") if parameters.get(name) in {"on", "off"}), None)
        requested_value = parameters.get(actuator) if actuator else None
        if actuator is None and parameters.get("cmd") in {"motor", "led"}:
            actuator = parameters.get("cmd")
            requested_value = parameters.get("value")
        if requested_value is None and command_request.command in {"turn_on", "motor_on"}:
            actuator = "motor"
            requested_value = "on"
        if requested_value is None and command_request.command in {"turn_off", "motor_off"}:
            actuator = "motor"
            requested_value = "off"
        if requested_value is None and command_request.command == "led_on":
            actuator, requested_value = "led", "on"
        if requested_value is None and command_request.command == "led_off":
            actuator, requested_value = "led", "off"
        if actuator not in {"motor", "led"} or requested_value not in {"on", "off"}:
            raise HTTPException(status_code=400, detail="EoRa 设备只支持电机或 LED 的开启与关闭")
        parameters = {actuator: requested_value}
        mqtt_payload = {"cmd": actuator, "value": requested_value}
    parameters.setdefault("requested_command", command_request.command)
    bridge_device_type = "generic" if is_eora_device else command_request.project
    bridge_command = {
        "command_id": trace_id,
        "trace_id": trace_id,
        "device_type": bridge_device_type,
        "device_id": command_request.device_id,
        "action": action,
        "params": parameters,
        "timestamp": now_iso(),
        "timeout_ms": 10_000,
    }
    topic_prefix = BRIDGE_PROFILE.get('mqtt', {}).get('downlink_topic_prefix', 'bridge/downlink')
    topic = (f"{topic_prefix}/generic/eora_s3_400tb_001/cmd" if is_eora_device
             else f"{topic_prefix}/{command_request.project}/{command_request.device_id}/{action}")
    username = request.state.user["sub"]
    command = {
        "id": uuid.uuid4().hex, "trace_id": trace_id,
        "device_id": command_request.device_id, "project": command_request.project,
        "command": command_request.command, "parameters": parameters,
        "topic": topic, "status": "pending", "requested_by": username, "requested_at": now_iso(),
    }
    db.create_command(command)
    if is_eora_device:
        if not mqtt_service.connected:
            db.acknowledge_command(trace_id, {
                "success": False,
                "error": "MQTT Broker 未连接",
                "trace_id": trace_id,
            })
            raise HTTPException(status_code=503, detail="MQTT Broker 未连接，无法发布控制命令")
        published = await asyncio.to_thread(mqtt_service.publish, topic, mqtt_payload or {})
        if not published:
            db.acknowledge_command(trace_id, {
                "success": False,
                "error": "MQTT 发布失败",
                "trace_id": trace_id,
            })
            raise HTTPException(status_code=502, detail="控制命令未能发布到 MQTT Broker")
    else:
        if not vsoa_service.connected:
            db.acknowledge_command(trace_id, {
                "success": False,
                "error": "协议桥接 VSOA 未连接",
                "trace_id": trace_id,
            })
            raise HTTPException(status_code=400, detail="协议桥接 VSOA 未连接，无法下发控制命令")
        try:
            result = await asyncio.to_thread(vsoa_service.send_command, bridge_command)
        except Exception as exc:
            result = {"error_code": -1, "error_msg": str(exc), "trace_id": trace_id}
        db.acknowledge_command(trace_id, result)
    command = next(item for item in db.commands(100) if item["trace_id"] == trace_id)
    db.audit(username, "command_sent", command_request.device_id, {"trace_id": trace_id, "command": command_request.command, "parameters": command_request.parameters})
    return command


@app.get("/api/admin/users")
async def get_users():
    return db.users()


@app.post("/api/admin/users")
async def save_user(user: UserRequest, request: Request):
    try:
        saved = db.upsert_user(user.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.audit(request.state.user["sub"], "user_saved", user.username, {"role": user.role, "active": user.active})
    return saved


@app.get("/api/admin/audit-logs")
async def get_audit_logs(limit: int = 100):
    return db.audits(max(1, min(limit, 500)))


@app.get("/api/transformations")
async def get_transformations(limit: int = 30):
    return db.transformation_pairs(max(1, min(limit, 100)))


@app.post("/api/mqtt/connect")
async def connect_mqtt(request: ConnectionRequest):
    try:
        mqtt_service.connect(
            request.host,
            request.port,
            asyncio.get_running_loop(),
            name=request.name,
            client_id=request.client_id,
            username=request.username,
            password=request.password,
            topics=[topic.strip() for topic in request.topics if topic.strip()],
            qos=request.qos,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "status": mqtt_service.status()}


@app.post("/api/mqtt/diagnose")
async def diagnose_mqtt(request: ConnectionRequest):
    return await asyncio.to_thread(mqtt_diagnostic, request)


@app.get("/api/connection-profiles")
async def get_connection_profiles():
    return load_profiles()


@app.post("/api/connection-profiles")
async def upsert_connection_profile(request: ConnectionProfileRequest):
    profile = request.model_dump(exclude={"password"})
    profiles = [item for item in load_profiles() if item.get("name") != request.name]
    profiles.append(profile)
    save_profiles(profiles)
    return profile


@app.delete("/api/connection-profiles/{name}")
async def delete_connection_profile(name: str):
    profiles = [item for item in load_profiles() if item.get("name") != name]
    save_profiles(profiles)
    return {"ok": True}


@app.post("/api/mqtt/disconnect")
async def disconnect_mqtt():
    mqtt_service.disconnect()
    return {"ok": True}


@app.delete("/api/mqtt/connections/{name}")
async def disconnect_mqtt_connection(name: str):
    mqtt_service.disconnect(name)
    return {"ok": True, "status": mqtt_service.status()}


@app.post("/api/vsoa/connect")
async def connect_vsoa(request: VsoaRequest):
    try:
        vsoa_service.connect(request.url, asyncio.get_running_loop())
        await asyncio.sleep(0.2)
        await asyncio.to_thread(vsoa_service.probe_health)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "status": vsoa_service.status()}


@app.get("/api/vsoa-connection-profiles")
async def get_vsoa_connection_profiles():
    return load_vsoa_profiles()


@app.post("/api/vsoa-connection-profiles")
async def upsert_vsoa_connection_profile(request: VsoaProfileRequest):
    profile = request.model_dump()
    profiles = [item for item in load_vsoa_profiles() if item.get("name") != request.name]
    profiles.append(profile)
    save_vsoa_profiles(profiles)
    return profile


@app.delete("/api/vsoa-connection-profiles/{name}")
async def delete_vsoa_connection_profile(name: str):
    profiles = [item for item in load_vsoa_profiles() if item.get("name") != name]
    save_vsoa_profiles(profiles)
    return {"ok": True}


@app.post("/api/simulations")
async def start_simulation(request: SimulationRequest):
    if not mqtt_service.connected:
        raise HTTPException(status_code=400, detail="请先连接项目 config.yaml 中的 MQTT Broker")
    if request.project not in BRIDGE_PROFILE.get("supported_sources", []):
        raise HTTPException(status_code=400, detail=f"项目当前未订阅 {request.project} 上行 topic")
    task_id = uuid.uuid4().hex[:10]
    task = asyncio.create_task(run_simulation(task_id, request))
    simulation_tasks[task_id] = task
    await hub.broadcast({"type": "simulation", "data": {"task_id": task_id, "running": True}})
    return {"task_id": task_id, "running": True}


@app.delete("/api/simulations/{task_id}")
async def stop_simulation(task_id: str):
    task = simulation_tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="simulation not found")
    task.cancel()
    simulation_tasks.pop(task_id, None)
    return {"ok": True}


@app.get("/api/test-runs")
async def get_test_runs():
    return db.recent_runs()


@app.get("/api/performance-runs")
async def get_performance_runs():
    return db.recent_performance_runs()


@app.post("/api/performance-runs")
async def create_performance_run(request: PerformanceRunRequest):
    if not mqtt_service.connected:
        raise HTTPException(status_code=400, detail="请先连接项目 MQTT Broker")
    if not vsoa_service.connected:
        raise HTTPException(status_code=400, detail="请先连接项目 VSOA Server")
    health = await asyncio.to_thread(vsoa_service.probe_health)
    if health.get("status") != "running":
        raise HTTPException(status_code=400, detail="bridge_vsoa_mqtt 健康检查未通过")
    if request.project not in BRIDGE_PROFILE.get("supported_sources", []):
        raise HTTPException(status_code=400, detail=f"项目当前未订阅 {request.project} 上行 topic")
    if request.rate * request.duration_seconds > 20000:
        raise HTTPException(status_code=400, detail="单次测试最多发送 20000 条消息")
    run_id = f"PERF-{datetime.now():%m%d-%H%M%S}"
    config = request.model_dump()
    config["mode"] = "project"
    config["device_prefix"] = f"perf-{run_id.lower()}-"
    run = {
        "id": run_id,
        "started_at": now_iso(),
        "finished_at": None,
        "status": "queued",
        "config": config,
        "metrics": {
            "sent": 0, "received": 0, "converted": 0, "publish_failed": 0,
            "lost": 0, "duplicates": 0, "errors": 0, "progress": 0,
        },
        "series": [],
    }
    db.upsert_performance_run(run)
    task = asyncio.create_task(execute_performance_run(run))
    performance_tasks[run_id] = task
    return run


@app.post("/api/project-tests")
async def create_project_test(request: ProjectTestRequest):
    if not BRIDGE_PROFILE.get("tests", {}).get("available"):
        raise HTTPException(status_code=400, detail="项目 pytest 环境不可用")
    run = {
        "id": f"CODE-{datetime.now():%m%d-%H%M%S}",
        "started_at": now_iso(), "finished_at": None,
        "experiment": request.scope.upper(),
        "scenario": "本机 bridge_vsoa_mqtt pytest",
        "status": "queued", "passed": 0, "failed": 0,
        "duration_ms": 0, "details": [],
    }
    db.upsert_run(run)
    asyncio.create_task(execute_project_tests(run, request.scope))
    return run


@app.delete("/api/performance-runs/{run_id}")
async def stop_performance_run(run_id: str):
    task = performance_tasks.get(run_id)
    if not task:
        raise HTTPException(status_code=404, detail="performance run not found")
    task.cancel()
    return {"ok": True}


@app.post("/api/test-runs")
async def create_test_run(request: TestRunRequest):
    run = {
        "id": f"RUN-{datetime.now():%m%d-%H%M%S}",
        "started_at": now_iso(),
        "finished_at": None,
        "experiment": request.experiment,
        "scenario": request.scenario,
        "status": "queued",
        "passed": 0,
        "failed": 0,
        "duration_ms": 0,
        "details": [],
    }
    db.upsert_run(run)
    asyncio.create_task(execute_test_run(run))
    return run


@app.get("/api/test-runs/{run_id}/report.csv")
async def export_test_run(run_id: str):
    run = next((item for item in db.recent_runs(200) if item["id"] == run_id), None)
    if not run:
        raise HTTPException(status_code=404, detail="test run not found")
    stream = io.StringIO()
    stream.write("\ufeff")
    writer = csv.writer(stream)
    writer.writerow(["任务编号", run["id"]])
    writer.writerow(["实验", run["experiment"]])
    writer.writerow(["场景", run["scenario"]])
    writer.writerow(["开始时间", run["started_at"]])
    writer.writerow(["状态", run["status"]])
    writer.writerow([])
    writer.writerow(["检查项", "结果", "说明"])
    for detail in run.get("details", []):
        writer.writerow([detail["name"], "通过" if detail["ok"] else "失败", detail["detail"]])
    return Response(
        stream.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{run_id}.csv"'},
    )


@app.websocket("/ws")
async def websocket_endpoint(socket: WebSocket):
    token = socket.query_params.get("token", "")
    try:
        decode_token(token)
    except HTTPException:
        await socket.close(code=4401)
        return
    await hub.connect(socket)
    try:
        await socket.send_json({"type": "ready", "data": {"timestamp": now_iso()}})
        while True:
            await socket.receive_text()
    except WebSocketDisconnect:
        hub.disconnect(socket)
