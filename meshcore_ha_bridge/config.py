"""Configuration loading for the bridge.

Settings come from a YAML file (default ``config.yaml``), with every value
overridable by an environment variable. Environment overrides win so that
secrets — chiefly the MQTT password — can be supplied at runtime without ever
being written to a file. The env var name is the config path upper-cased and
joined with underscores, prefixed with ``MESHCORE_`` (e.g. ``mqtt.password`` ->
``MESHCORE_MQTT_PASSWORD``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import yaml

ENV_PREFIX = "MESHCORE_"


@dataclass
class SerialConfig:
    port: str = "auto"
    baud: int = 115200


@dataclass
class MqttConfig:
    host: str = "127.0.0.1"
    port: int = 1883
    username: str = ""
    password: str = ""
    client_id: str = "meshcore-ha-bridge"
    base_topic: str = "meshcore"
    qos: int = 0


@dataclass
class ReconnectConfig:
    initial_delay: float = 2.0
    max_delay: float = 60.0


@dataclass
class LoggingConfig:
    level: str = "INFO"


@dataclass
class Config:
    serial: SerialConfig = field(default_factory=SerialConfig)
    mqtt: MqttConfig = field(default_factory=MqttConfig)
    reconnect: ReconnectConfig = field(default_factory=ReconnectConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)


def _coerce(current: Any, raw: str) -> Any:
    """Coerce an environment string to the type of the existing default."""
    if isinstance(current, bool):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(current, int) and not isinstance(current, bool):
        return int(raw)
    if isinstance(current, float):
        return float(raw)
    return raw


def _apply_env_overrides(section: str, obj: Any, environ: Dict[str, str]) -> None:
    """Override dataclass fields from MESHCORE_<SECTION>_<FIELD> env vars."""
    for fname in obj.__dataclass_fields__:
        env_name = f"{ENV_PREFIX}{section.upper()}_{fname.upper()}"
        if env_name in environ and environ[env_name] != "":
            setattr(obj, fname, _coerce(getattr(obj, fname), environ[env_name]))


def _apply_file_section(obj: Any, data: Optional[Dict[str, Any]]) -> None:
    """Override dataclass fields from a parsed YAML mapping, ignoring unknowns."""
    if not data:
        return
    for fname in obj.__dataclass_fields__:
        if fname in data and data[fname] is not None:
            setattr(obj, fname, data[fname])


def load_config(path: Optional[str] = None, environ: Optional[Dict[str, str]] = None) -> Config:
    """Load configuration from a YAML file and environment overrides.

    The file is optional: if it is absent, defaults plus environment variables
    are used. This lets the bridge run from environment alone (e.g. in Docker).
    """
    environ = dict(os.environ if environ is None else environ)
    config = Config()

    file_data: Dict[str, Any] = {}
    if path and os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            file_data = yaml.safe_load(fh) or {}

    _apply_file_section(config.serial, file_data.get("serial"))
    _apply_file_section(config.mqtt, file_data.get("mqtt"))
    _apply_file_section(config.reconnect, file_data.get("reconnect"))
    _apply_file_section(config.logging, file_data.get("logging"))

    _apply_env_overrides("serial", config.serial, environ)
    _apply_env_overrides("mqtt", config.mqtt, environ)
    _apply_env_overrides("reconnect", config.reconnect, environ)
    _apply_env_overrides("logging", config.logging, environ)

    return config
