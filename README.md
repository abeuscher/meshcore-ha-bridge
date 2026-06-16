# meshcore-ha-bridge

A small, long-running service that connects a **MeshCore LoRa mesh network** to
**Home Assistant**. It listens to a MeshCore radio node over USB serial, and
republishes incoming mesh messages onto an MQTT broker, where Home Assistant can
consume them and act on them (dashboards, automations, notifications).

This is the custom "glue" piece of a larger proof-of-concept. Everything else in
the system is off-the-shelf; this bridge is the one bespoke component.

---

## The larger system

The goal of the overall project is property monitoring over an off-grid radio
network: sensors in places without WiFi (outbuildings, basements) report events
— flooding, freezing, temperature — back to a central brain that can notify a
human, even when the internet is down. It uses LoRa mesh radios so coverage
doesn't depend on WiFi or cellular, and it is fully self-hosted so no data
leaves the property except the final notification.

The end-to-end chain looks like this:

```
physical event (e.g. water)
      │  (voltage / I2C)
      ▼
sensor-equipped MeshCore node          ← field node, battery/solar
      │  (LoRa radio, mesh-routed)
      ▼
MeshCore gateway node                  ← plugged into the host by USB
      │  (USB serial)
      ▼
**this bridge**  (Python)              ← reads serial, publishes to MQTT
      │  (MQTT publish)
      ▼
MQTT broker (Mosquitto)
      │  (MQTT subscribe)
      ▼
Home Assistant                         ← entities + automations
      │
      ▼
notification (phone push / SMS) and/or a LoRa-paired handheld
```

Each link translates one "language" into the next. The radios speak LoRa to each
other; the gateway node speaks USB serial to the host; **this bridge** translates
that serial stream into MQTT messages; Home Assistant speaks MQTT natively.

## Where this code fits

This repository is **only the bridge** — the box labeled "this bridge" above. It
is responsible for exactly one translation:

> MeshCore messages arriving at the gateway node  →  MQTT messages on a broker

It does **not** read sensors directly, does not run the mesh, and does not
contain any Home Assistant logic. It sits in the middle and does one job well, so
that each side can be reasoned about and debugged independently.

## Why it exists as its own piece

MeshCore is a young protocol; its path into Home Assistant is not yet a turnkey
integration the way more established ecosystems are. The bridge is the small
amount of code that fills that gap. Because it is the one custom component, it
lives in version control, is built to run unattended, and is designed to be
redeployed later from a development laptop onto the property's permanent server
with only configuration changes.

## Current status / deployment context

- **Development host:** macOS laptop (Apple Silicon).
- **Eventual host:** a small always-on server on the property (Linux mini-PC or
  similar), running Home Assistant, Mosquitto, and this bridge together, with the
  gateway node plugged into it by USB.
- The surrounding system (Home Assistant in Docker, Mosquitto broker, the
  MeshCore radio mesh, the USB gateway node) is **already standing and verified**.
  A real text message has been confirmed travelling from a phone-paired node,
  across the mesh, into the gateway, and out the gateway's USB serial port as
  structured data on the host. This bridge consumes that stream.

See `SPEC.md` for the precise technical requirements and the captured message
format the bridge must handle.