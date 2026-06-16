# Technical Specification — meshcore-ha-bridge

This document specifies what the bridge must do, the environment it runs in, the
data it consumes and produces, and the constraints it must satisfy. Read this
together with `README.md` (which explains the larger system and why this piece
exists).

---

## 1. Purpose

A long-running Python service that:

1. Connects to a MeshCore **gateway node** over USB serial.
2. Listens for incoming **mesh messages** (and optionally adverts/telemetry).
3. Publishes each relevant message to an **MQTT broker** as a structured payload.
4. Runs unattended and recovers from disconnects (it is monitoring infrastructure;
   silent death is the worst failure mode).

It is the translation layer between the MeshCore serial world and the MQTT world
that Home Assistant consumes.

## 2. Environment

- **OS (dev):** macOS, Apple Silicon. (Target deployment: Linux mini-PC. Code
  should not hard-code macOS-only assumptions — e.g. serial device paths differ:
  macOS `/dev/cu.usbmodem*`, Linux `/dev/ttyACM*` or `/dev/ttyUSB*`. Make the
  serial port a config value, not a constant.)
- **Python:** 3.11+ (whatever the host Homebrew/system Python provides; assume a
  modern 3.x).
- **Package management:** use a project-local **virtual environment** with
  **pip** and a **`requirements.txt`**. (The companion CLI tool `meshcore-cli`
  was installed system-wide via pipx, but this bridge is an application, not a
  CLI tool, so it should have its own venv. Do not rely on global packages.)
  - A `uv`-based workflow is acceptable if preferred, but pip + venv +
    `requirements.txt` is the assumed baseline and the most portable for the
    eventual server deployment.

## 3. Dependencies

- **`meshcore`** (PyPI) — the official MeshCore Python library. Provides a
  connection to a companion node over Serial / BLE / TCP and an **event model**
  for incoming messages. **Use the library's event subscription API; do not
  hand-parse raw hex packets.** The library exposes parsed message events with
  sender, content, and metadata.
- **`paho-mqtt`** (PyPI) — MQTT client for publishing to Mosquitto.
- Standard library for everything else (logging, asyncio, signal handling, config
  loading). Avoid unnecessary dependencies — this needs to run reliably on a
  small server for a long time.

## 4. Connections & configuration

All of the following must be **configurable** (via a config file such as
`config.yaml`/`.env`, or environment variables — agent's choice, but documented),
never hard-coded, because they change between the dev laptop and the deployed
server:

### Gateway node (input)
- **Connection type:** USB serial.
- **Serial port:** e.g. `/dev/cu.usbmodem14301` on the current dev Mac.
  **NOTE:** this port name is not stable — it can change on reconnect/reboot, and
  differs on Linux. Make it configurable; consider auto-detection as a
  convenience (e.g. scan for a MeshCore device) but always allow an explicit
  override.
- **Baud:** MeshCore serial companions use the library default; let the library
  handle it unless a baud override proves necessary.
- The gateway node is a SenseCAP T1000-E flashed with **USB/Serial Companion**
  MeshCore firmware (v1.16.0 at time of writing). It is **not** a BLE companion;
  it speaks the serial-companion protocol.

### MQTT broker (output)
- **Host:** `192.168.8.209` (the dev Mac's LAN IP on the mesh network's subnet —
  this is DHCP and may change; configurable). On the eventual server this will be
  localhost or the server's static IP.
- **Port:** `1883`
- **Auth:** username `hauser`, password set out-of-band (do **not** commit the
  password; load from env/config/secret).
- The broker is Mosquitto, already running, with `allow_anonymous false`, so
  credentials are required.

### Radio context (informational, not set by the bridge)
The mesh runs the USA/Canada preset: `freq 910.525, bw 62.5, sf 7, cr 5`. The
bridge does not configure the radio; the gateway node is already tuned. Listed
here only so the agent understands the deployment.

## 5. Input data format

When a text message arrives at the gateway, the MeshCore library surfaces it as a
message-received event. For reference, the raw packet log (from the node's
`json_log_rx`) for a confirmed test message looked like this:

```json
{
  "recv_time": 1781561687,
  "snr": 16.5,
  "rssi": -35,
  "payload_length": 38,
  "route_type": 1,
  "route_typename": "FLOOD",
  "payload_type": 2,
  "payload_typename": "TEXT_MSG",
  "path_len": 0,
  "pkt_hash": 301141956
}
```

…and the decoded human-readable form printed as:

```
Card One (0): Testing phone
```

i.e. **sender name** = "Card One", **channel** = 0, **content** = "Testing phone".

Key points for the implementation:
- Filter for actual messages. `payload_typename == "TEXT_MSG"` (`payload_type` 2)
  is the message type of interest. Other packet types (`PATH`, adverts, etc.)
  are not user messages — decide explicitly whether to ignore or separately
  handle them. For the first version, **handle TEXT_MSG; ignore the rest.**
- Prefer the library's parsed event fields (sender, text, channel, snr, rssi)
  over re-parsing `raw_hex`/`payload` yourself.
- Useful metadata to carry through to MQTT: **sender**, **content/text**,
  **channel**, **snr**, **rssi**, **timestamp**. (snr/rssi are valuable later for
  signal-quality dashboards and range diagnostics.)

## 6. Output: MQTT topic & payload design

- **Topic scheme:** hierarchical and human-readable, e.g.
  `meshcore/<sender_or_node_id>/message` for messages, and leave room for
  `meshcore/<node>/telemetry` later. Make the base prefix (`meshcore`)
  configurable.
- **Payload:** JSON, containing at minimum the sender, the message content, and
  the metadata listed above. Keep the structure stable and documented — Home
  Assistant entities will be configured against these field names, so changing
  them later breaks the HA side.
- **QoS / retain:** default QoS 0 is fine for the POC. Do **not** set `retain` on
  message topics (a flood alert shouldn't replay as "current" after restart);
  retain may be appropriate later for status/availability topics.
- Publish a **bridge availability/status** topic (e.g. `meshcore/bridge/status`
  = `online`/`offline`) using MQTT Last Will & Testament, so Home Assistant can
  tell if the bridge itself has died. This matters: the whole product promise is
  "you'll be told when something's wrong," which fails silently if the bridge
  goes down unnoticed.

## 7. Reliability requirements

This is monitoring infrastructure intended to run 24/7 on an unattended server.

- **Reconnect logic:** if the serial connection drops (node unplugged, reboot,
  USB hiccup) the bridge must attempt to reconnect with backoff rather than
  exiting.
- **MQTT reconnect:** likewise handle broker disconnects gracefully and
  re-establish.
- **Logging:** clear, timestamped logs to stdout (so it works under a process
  manager / Docker / systemd). Log connection events, each message handled, and
  all errors. Avoid noisy per-packet spam at info level; use debug for that.
- **Clean shutdown:** handle SIGINT/SIGTERM, publish the offline LWT/status, close
  connections.
- **No crashes on malformed input:** a weird/partial packet should be logged and
  skipped, not fatal.

## 8. Structure & deliverables

- Idiomatic, readable Python. Event-driven (the library is async/event-based);
  do not poll.
- A single well-organized module/package is fine for v1; don't over-engineer.
- Provide: the bridge code, `requirements.txt`, an example config
  (`config.example.yaml` or `.env.example`) with the fields above and **no real
  secrets**, and a short run/usage section in the README (how to create the venv,
  install, configure, run).
- Provide a way to run it as a long-lived service later (a note on systemd unit
  or Docker is enough for now; full containerization is optional for v1).

## 9. Explicit non-goals (for this version)

- **No sensor reading.** The bridge does not talk to sensors; it only relays
  whatever messages arrive over the mesh. (Sensor-equipped nodes are a separate,
  later piece.)
- **No Home Assistant configuration.** Defining HA entities/automations against
  the MQTT topics is done on the HA side, not here.
- **No outbound mesh control** (sending messages back into the mesh) is required
  for v1, though the design shouldn't preclude adding it later.
- **No telemetry/environmental decoding** yet — but choose topic/payload
  conventions that leave room for it.

## 10. Definition of done (v1)

Running the bridge, then sending a text message from a phone-paired mesh node,
results in a corresponding JSON message appearing on the MQTT broker under the
expected topic — verifiable with `mosquitto_sub` or Home Assistant's MQTT
listen tool — with sender, content, and metadata intact, and the bridge keeps
running and reconnects if the node or broker briefly drops.