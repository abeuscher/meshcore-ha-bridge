"""Entry point: ``python -m meshcore_ha_bridge``.

Loads config, configures stdout logging (so it works under systemd/Docker), wires
SIGINT/SIGTERM to a clean shutdown, and runs the bridge supervise loop.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys

from .bridge import Bridge
from .config import load_config


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
        stream=sys.stdout,
    )
    # The meshcore library logs under its own "meshcore" logger; let it inherit
    # our root handler but keep its default level unless we're debugging.


def _parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="meshcore-ha-bridge",
        description="Bridge MeshCore mesh messages to MQTT for Home Assistant.",
    )
    parser.add_argument(
        "-c", "--config", default="config.yaml",
        help="Path to YAML config file (default: config.yaml). "
             "Missing file is OK if everything is set via environment.",
    )
    return parser.parse_args(argv)


async def _run(config) -> None:
    bridge = Bridge(config)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, bridge.request_shutdown)
        except NotImplementedError:
            # add_signal_handler is unavailable on some platforms (e.g. Windows).
            signal.signal(sig, lambda *_: bridge.request_shutdown())

    await bridge.run()


def main(argv=None) -> int:
    args = _parse_args(argv)
    config = load_config(args.config)
    _configure_logging(config.logging.level)

    logging.getLogger("meshcore_ha_bridge").info(
        "Starting meshcore-ha-bridge (serial=%s, broker=%s:%s, base_topic=%s)",
        config.serial.port, config.mqtt.host, config.mqtt.port, config.mqtt.base_topic,
    )

    try:
        asyncio.run(_run(config))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
