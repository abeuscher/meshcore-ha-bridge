"""meshcore-ha-bridge: relay MeshCore mesh messages onto MQTT for Home Assistant.

See README.md and SPEC.md for the full design. The package is intentionally
small: `config` loads settings, `ports` finds the serial device, `mqtt_publisher`
wraps paho with a Last-Will status topic, and `bridge` ties the MeshCore event
stream to MQTT publishing with reconnect supervision.
"""

__all__ = ["__version__"]

__version__ = "1.0.0"
