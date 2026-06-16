"""MQTT publishing with a Last-Will availability topic.

Wraps paho-mqtt so the rest of the bridge can publish JSON messages without
caring about reconnection. paho runs its own network thread (``loop_start``) and
reconnects to the broker automatically with backoff; we re-assert the ``online``
status on every (re)connect. A Last Will & Testament publishes ``offline`` to the
status topic if the bridge process dies or loses its broker link, so Home
Assistant can tell when the bridge itself is down (SPEC §6).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict

import paho.mqtt.client as mqtt

logger = logging.getLogger("meshcore_ha_bridge")

STATUS_ONLINE = "online"
STATUS_OFFLINE = "offline"


class MqttPublisher:
    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        client_id: str,
        base_topic: str,
        qos: int = 0,
    ) -> None:
        self.host = host
        self.port = port
        self.base_topic = base_topic.rstrip("/")
        self.qos = qos
        self.status_topic = f"{self.base_topic}/bridge/status"

        self._client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id or None,
        )
        if username:
            self._client.username_pw_set(username, password)

        # LWT: broker publishes this (retained) if we disconnect ungracefully.
        self._client.will_set(self.status_topic, STATUS_OFFLINE, qos=1, retain=True)

        # Reconnect to the broker automatically with capped backoff.
        self._client.reconnect_delay_set(min_delay=1, max_delay=60)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if getattr(reason_code, "is_failure", False):
            logger.error("MQTT connect failed: %s", reason_code)
            return
        logger.info("Connected to MQTT broker %s:%s", self.host, self.port)
        # Assert availability on every (re)connect; retained so HA gets it on subscribe.
        client.publish(self.status_topic, STATUS_ONLINE, qos=1, retain=True)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None):
        # paho's loop will reconnect on its own; just record it.
        if reason_code:
            logger.warning("Disconnected from MQTT broker (%s); will retry", reason_code)
        else:
            logger.info("Disconnected from MQTT broker")

    def start(self) -> None:
        """Begin connecting (non-blocking) and start the network loop."""
        logger.info("Connecting to MQTT broker %s:%s ...", self.host, self.port)
        # connect_async + loop_start never blocks startup on an unreachable broker.
        self._client.connect_async(self.host, self.port, keepalive=60)
        self._client.loop_start()

    def publish_message(self, node_id: str, payload: Dict[str, Any]) -> None:
        """Publish a mesh message as JSON under meshcore/<node_id>/message."""
        topic = f"{self.base_topic}/{node_id}/message"
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        info = self._client.publish(topic, body, qos=self.qos, retain=False)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            logger.warning("MQTT publish to %s returned rc=%s", topic, info.rc)
        else:
            logger.debug("Published to %s: %s", topic, body)

    def stop(self) -> None:
        """Publish offline status and shut the client down cleanly."""
        try:
            self._client.publish(self.status_topic, STATUS_OFFLINE, qos=1, retain=True)
            # Give the offline publish a moment to flush before disconnecting.
            self._client.disconnect()
        finally:
            self._client.loop_stop()
