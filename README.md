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

---

## Running the bridge

### 1. Create a virtual environment and install dependencies

```bash
cd meshcore-ha-bridge
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure

```bash
cp config.example.yaml config.yaml
```

Edit `config.yaml` for your host — at minimum the serial port and the MQTT
broker host. **Do not put the MQTT password in the file.** Supply it at runtime
via the environment instead:

```bash
export MESHCORE_MQTT_PASSWORD='your-broker-password'
```

`config.yaml` is git-ignored. Every config value can also be set or overridden
by an environment variable named `MESHCORE_<SECTION>_<FIELD>` (e.g.
`MESHCORE_SERIAL_PORT`, `MESHCORE_MQTT_HOST`, `MESHCORE_MQTT_PASSWORD`), which is
convenient under Docker/systemd.

**Finding the serial port:**
- macOS: `ls /dev/cu.usbmodem*`
- Linux: `ls /dev/ttyACM* /dev/ttyUSB*`

Or set `serial.port: "auto"` to have the bridge scan attached USB serial devices
and connect to the first one that answers as a MeshCore serial companion.

### 3. Run

```bash
python -m meshcore_ha_bridge -c config.yaml
```

Logs go to stdout (timestamped), so it works directly under systemd, Docker, or
a terminal. Stop with Ctrl-C; the bridge publishes its `offline` status and
closes connections cleanly.

### 4. Verify (definition of done)

Subscribe to the broker and send a message from a mesh node:

```bash
mosquitto_sub -h <broker> -u hauser -P "$MESHCORE_MQTT_PASSWORD" -v -t 'meshcore/#'
```

You should see the bridge's `online` status and, when a node sends a message, a
JSON payload on `meshcore/<node>/message`.

### Running unattended on the server

`deploy/meshcore-ha-bridge.service` is a ready-to-edit systemd unit (auto-restart,
SIGTERM clean shutdown, journal logging). See the comments at the top of that file
for install steps. Full containerization is optional for v1.

---

## MQTT output contract

These topic and field names are what Home Assistant entities are configured
against, so they are kept stable (changing them breaks the HA side).

**Topics** (base prefix `meshcore` is configurable):

| Topic | Retain | Meaning |
| --- | --- | --- |
| `meshcore/bridge/status` | yes | `online` / `offline` (offline via MQTT Last Will, so HA can detect a dead bridge) |
| `meshcore/<sender>/message` | no | a direct (private) message; `<sender>` is the contact name, sanitized for MQTT |
| `meshcore/channel-<n>/message` | no | a channel message on channel `<n>` |

Message topics are **not** retained — a flood alert must not replay as "current"
after a restart. Room is left for `meshcore/<node>/telemetry` later.

**Message payload** (JSON):

```json
{
  "sender": "Card One",
  "content": "Testing phone",
  "type": "channel",
  "channel": 0,
  "snr": 16.5,
  "rssi": -35,
  "timestamp": 1781561687,
  "received_at": "2026-06-16T02:13:10Z",
  "path_len": 0,
  "pubkey_prefix": null
}
```

| Field | Notes |
| --- | --- |
| `sender` | Contact name for direct messages (resolved from the node's contact list); the name prefix for channel messages. May fall back to a public-key prefix, or `null`, if unknown. |
| `content` | The message text. |
| `type` | `"direct"` or `"channel"`. |
| `channel` | Channel index for channel messages; `null` for direct messages. |
| `snr`, `rssi` | Signal metadata when the library provides it; otherwise `null`. Useful for later range/signal dashboards. |
| `timestamp` | Sender's timestamp from the packet (Unix epoch seconds). |
| `received_at` | When the bridge received it (ISO-8601 UTC). |
| `path_len` | Mesh hop count for the packet. |
| `pubkey_prefix` | Sender public-key prefix for direct messages; `null` for channel messages. |

Only text messages are bridged in v1 (adverts, telemetry, path/ack packets are
ignored). The design leaves room to add those later without changing this contract.