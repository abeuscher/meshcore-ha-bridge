# Agent Briefing — start here

You are being asked to build **meshcore-ha-bridge**, a small long-running Python
service that connects a MeshCore LoRa mesh radio (over USB serial) to an MQTT
broker, so that Home Assistant can consume mesh messages.

## Before writing any code, read both of these in full:

1. **`README.md`** — explains the larger system this code is part of, the
   end-to-end data chain, and precisely which one job this code is responsible
   for. Read this first for context.

2. **`SPEC.md`** — the technical specification: environment, dependencies and
   package manager, the connections and their (configurable) settings, the exact
   input message format that was empirically captured from real hardware, the
   MQTT output design, the reliability requirements, the deliverables, the
   non-goals, and the definition of done. This is the contract.

## What to do, in order

1. Read `README.md`, then `SPEC.md`.
2. If anything in the spec is ambiguous or you must make a design decision the
   spec leaves open (config format, topic naming specifics, library API details),
   **state the decision and your reasoning briefly, then proceed** — don't stall.
   If something is genuinely blocking and unknowable, ask one focused question.
3. Verify the real library APIs before relying on them. The relevant packages are
   `meshcore` (the MeshCore Python library — use its event/subscription model for
   incoming messages, not raw hex parsing) and `paho-mqtt`. If you're unsure of an
   API signature, check rather than guess — getting the `meshcore` event API right
   is the crux of the whole task.
4. Build the project per the spec: the bridge code, `requirements.txt`, an example
   config with no real secrets, and clear run instructions in the README.
5. Aim for the definition of done in SPEC §10: a message sent from a mesh node
   shows up as a JSON MQTT message on the broker, and the bridge runs unattended
   with reconnect logic.

## Important environment notes

- This is being developed on a macOS laptop but is destined to run on a Linux
  mini-PC on-site. Keep host-specific things (serial port path especially)
  configurable, not hard-coded.
- The surrounding system already exists and works: Home Assistant (Docker), the
  Mosquitto broker, the MeshCore mesh, and a USB-connected gateway node have all
  been verified, and a real text message has been confirmed flowing from the mesh
  out the gateway's serial port. Your code is the next link, consuming that
  stream. You are not building or debugging those other components.
- Never commit real secrets (the MQTT password in particular). Use an example
  config and load actual values from environment/config at runtime.

## Tone of the work

This is monitoring infrastructure whose whole purpose is to reliably report when
something is wrong. Favor robustness and clear logging over cleverness. A bridge
that dies silently is worse than one that's plain. Build it to run for months
untouched, and to be redeployable on the on-site server with only config changes.