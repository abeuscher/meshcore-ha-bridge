"""Serial port discovery for the MeshCore gateway node.

The gateway's device path is not stable: it changes between macOS and Linux, and
can change across reboots/reconnects (SPEC §4). The bridge therefore lets the
serial port be set explicitly *or* to ``auto``, in which case we enumerate likely
USB serial devices and let the caller try each one. We deliberately do not try to
positively identify a MeshCore device here — the only reliable test is to open it
and see whether it answers the companion protocol, which the bridge does when it
attempts to connect.
"""

from __future__ import annotations

import logging
from typing import List

from serial.tools import list_ports

logger = logging.getLogger("meshcore_ha_bridge")

# Device-name fragments that indicate a USB CDC/ACM serial adapter on the
# platforms this bridge targets (macOS dev laptop, Linux mini-PC server).
_CANDIDATE_HINTS = ("usbmodem", "usbserial", "ttyacm", "ttyusb", "cu.usb")


def discover_serial_ports() -> List[str]:
    """Return candidate serial device paths, most-likely first.

    Ports whose device name looks like a USB serial adapter are returned ahead
    of anything else, so a typical single-gateway host connects on the first try.
    """
    ports = list(list_ports.comports())
    likely: List[str] = []
    others: List[str] = []
    for p in ports:
        name = (p.device or "").lower()
        if any(hint in name for hint in _CANDIDATE_HINTS):
            likely.append(p.device)
        else:
            others.append(p.device)

    ordered = likely + others
    logger.debug("Discovered serial ports: %s (likely: %s)", ordered, likely)
    return ordered
