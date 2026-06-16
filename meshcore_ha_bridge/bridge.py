"""The bridge: MeshCore serial events in, MQTT JSON out.

Design notes
------------
* Incoming text messages surface as two library events: ``CONTACT_MSG_RECV``
  (direct/private, ``type="PRIV"``) and ``CHANNEL_MSG_RECV`` (channel,
  ``type="CHAN"``). Subscribing to just these two already filters to text
  messages — other packet types (adverts, path updates, acks) are distinct
  event types we never subscribe to — which satisfies SPEC §5's "handle
  TEXT_MSG; ignore the rest."
* The serial-companion firmware does not push message bodies unsolicited; it
  pushes ``MESSAGES_WAITING`` and the host must drain them. ``MeshCore``'s
  ``start_auto_message_fetching()`` runs that drain loop for us.
* Reconnection is supervised here rather than relying on the library's built-in
  auto-reconnect (capped at 3 flat-delay attempts). We rebuild the whole
  connection with exponential backoff and retry forever, because silent death is
  the worst failure mode for monitoring infrastructure (SPEC §7).
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from meshcore import EventType, MeshCore

from .config import Config
from .mqtt_publisher import MqttPublisher
from .ports import discover_serial_ports

logger = logging.getLogger("meshcore_ha_bridge")

# MQTT topic levels may not contain wildcards, '/', or whitespace. Collapse
# anything unsafe to '_' so sender names map to stable, readable topic segments.
_UNSAFE_TOPIC = re.compile(r"[^A-Za-z0-9._-]+")


def _sanitize_topic_segment(value: str) -> str:
    cleaned = _UNSAFE_TOPIC.sub("_", value.strip()).strip("_")
    return cleaned or "unknown"


class Bridge:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.mqtt = MqttPublisher(
            host=config.mqtt.host,
            port=config.mqtt.port,
            username=config.mqtt.username,
            password=config.mqtt.password,
            client_id=config.mqtt.client_id,
            base_topic=config.mqtt.base_topic,
            qos=config.mqtt.qos,
        )
        self._shutdown = asyncio.Event()
        self._disconnected: Optional[asyncio.Event] = None
        self._mc: Optional[MeshCore] = None

    # ----- lifecycle ---------------------------------------------------------

    def request_shutdown(self) -> None:
        """Signal the supervise loop to stop (called from a signal handler)."""
        logger.info("Shutdown requested")
        self._shutdown.set()
        if self._disconnected is not None:
            self._disconnected.set()

    async def run(self) -> None:
        """Top-level supervise loop: connect, serve, reconnect with backoff."""
        self.mqtt.start()
        delay = self.config.reconnect.initial_delay
        try:
            while not self._shutdown.is_set():
                connected = await self._connect_once()
                if not connected:
                    logger.warning("Gateway connection failed; retrying in %.0fs", delay)
                    if await self._sleep_or_shutdown(delay):
                        break
                    delay = min(delay * 2, self.config.reconnect.max_delay)
                    continue

                # Connected: reset backoff and serve until the link drops.
                delay = self.config.reconnect.initial_delay
                await self._serve_until_disconnect()
                await self._teardown_meshcore()

                if not self._shutdown.is_set():
                    logger.info("Gateway link lost; reconnecting in %.0fs", delay)
                    if await self._sleep_or_shutdown(delay):
                        break
                    delay = min(delay * 2, self.config.reconnect.max_delay)
        finally:
            await self._teardown_meshcore()
            self.mqtt.stop()
            logger.info("Bridge stopped")

    async def _sleep_or_shutdown(self, delay: float) -> bool:
        """Sleep for ``delay`` seconds unless shutdown is requested first.

        Returns True if shutdown was requested during the wait.
        """
        try:
            await asyncio.wait_for(self._shutdown.wait(), timeout=delay)
            return True
        except asyncio.TimeoutError:
            return False

    # ----- connection --------------------------------------------------------

    async def _connect_once(self) -> bool:
        """Attempt to open the gateway and wire up subscriptions.

        Returns True if connected and serving, False on failure.
        """
        candidates = self._resolve_ports()
        if not candidates:
            logger.error("No serial ports available to try")
            return False

        for port in candidates:
            try:
                logger.info("Opening MeshCore gateway on %s ...", port)
                mc = await MeshCore.create_serial(
                    port,
                    baudrate=self.config.serial.baud,
                    auto_reconnect=False,  # we supervise reconnection ourselves
                )
            except Exception as exc:  # noqa: BLE001 - any open error => try next/backoff
                logger.warning("Could not open %s: %s", port, exc)
                continue

            if mc is None:
                logger.warning("%s did not answer as a MeshCore serial companion", port)
                continue

            self._mc = mc
            await self._setup_subscriptions(mc)
            logger.info("MeshCore gateway ready on %s", port)
            return True

        return False

    def _resolve_ports(self) -> list[str]:
        configured = (self.config.serial.port or "").strip()
        if configured and configured.lower() != "auto":
            return [configured]
        ports = discover_serial_ports()
        if not ports:
            logger.error("serial.port is 'auto' but no serial ports were found")
        return ports

    async def _setup_subscriptions(self, mc: MeshCore) -> None:
        self._disconnected = asyncio.Event()

        mc.subscribe(EventType.CONTACT_MSG_RECV, self._on_direct_message)
        mc.subscribe(EventType.CHANNEL_MSG_RECV, self._on_channel_message)
        mc.subscribe(EventType.DISCONNECTED, self._on_disconnected)

        # Load contacts so we can resolve sender pubkey prefixes to names, and
        # keep them current as adverts arrive.
        try:
            await mc.ensure_contacts()
            mc.auto_update_contacts = True
        except Exception as exc:  # noqa: BLE001 - name resolution is best-effort
            logger.warning("Could not fetch contacts (sender names may be prefixes): %s", exc)

        # Drain any messages already queued on the node and follow new ones.
        await mc.start_auto_message_fetching()

    async def _serve_until_disconnect(self) -> None:
        """Block until the gateway link drops or shutdown is requested."""
        assert self._disconnected is not None
        waiters = [asyncio.create_task(self._disconnected.wait()),
                   asyncio.create_task(self._shutdown.wait())]
        try:
            await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for w in waiters:
                w.cancel()

    async def _teardown_meshcore(self) -> None:
        if self._mc is not None:
            try:
                await self._mc.disconnect()
            except Exception as exc:  # noqa: BLE001 - already tearing down
                logger.debug("Error during MeshCore disconnect: %s", exc)
            self._mc = None

    async def _on_disconnected(self, event) -> None:
        logger.warning("MeshCore reported disconnect: %s", getattr(event, "payload", event))
        if self._disconnected is not None:
            self._disconnected.set()

    # ----- message handling --------------------------------------------------

    async def _on_direct_message(self, event) -> None:
        try:
            payload = event.payload or {}
            prefix = payload.get("pubkey_prefix", "")
            sender = self._resolve_sender_name(prefix)
            record = self._base_record(
                sender=sender,
                content=payload.get("text", ""),
                msg_type="direct",
                payload=payload,
            )
            record["channel"] = None
            record["pubkey_prefix"] = prefix or None

            node_id = _sanitize_topic_segment(sender or prefix or "unknown")
            self._publish(node_id, record)
        except Exception as exc:  # noqa: BLE001 - never let a bad packet kill the bridge
            logger.error("Failed to handle direct message: %s", exc, exc_info=True)

    async def _on_channel_message(self, event) -> None:
        try:
            payload = event.payload or {}
            channel_idx = payload.get("channel_idx")
            # Channel packets carry no per-sender identity; by MeshCore convention
            # the sender name is prepended to the text as "Name: message".
            sender, content = self._split_channel_text(payload.get("text", ""))
            record = self._base_record(
                sender=sender,
                content=content,
                msg_type="channel",
                payload=payload,
            )
            record["channel"] = channel_idx

            node_id = f"channel-{channel_idx}" if channel_idx is not None else "channel-unknown"
            self._publish(node_id, record)
        except Exception as exc:  # noqa: BLE001 - never let a bad packet kill the bridge
            logger.error("Failed to handle channel message: %s", exc, exc_info=True)

    def _base_record(self, sender: Optional[str], content: str, msg_type: str,
                     payload: Dict[str, Any]) -> Dict[str, Any]:
        """Build the stable JSON shape Home Assistant entities key against.

        Field names here are a contract: changing them breaks the HA side
        (SPEC §6), so keep them stable.
        """
        return {
            "sender": sender,
            "content": content,
            "type": msg_type,
            "snr": payload.get("SNR"),
            "rssi": payload.get("RSSI"),
            "timestamp": payload.get("sender_timestamp"),
            "received_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "path_len": payload.get("path_len"),
        }

    def _resolve_sender_name(self, pubkey_prefix: str) -> Optional[str]:
        if not pubkey_prefix or self._mc is None:
            return pubkey_prefix or None
        contact = self._mc.get_contact_by_key_prefix(pubkey_prefix)
        if contact:
            return contact.get("adv_name") or pubkey_prefix
        return pubkey_prefix

    @staticmethod
    def _split_channel_text(text: str) -> tuple[Optional[str], str]:
        """Split "Name: message" into (sender, content); fall back to (None, text)."""
        if ": " in text:
            name, _, body = text.partition(": ")
            name = name.strip()
            if name:
                return name, body
        return None, text

    def _publish(self, node_id: str, record: Dict[str, Any]) -> None:
        logger.info(
            "Message [%s] from %s on %s: %r",
            record["type"],
            record.get("sender") or "?",
            node_id,
            record["content"],
        )
        self.mqtt.publish_message(node_id, record)
