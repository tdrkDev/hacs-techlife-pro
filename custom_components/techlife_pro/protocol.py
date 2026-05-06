"""Protocol helper for TechLife Pro.

Ported from https://github.com/Marcoske23/TechLifePro-for-HA. Devices
subscribe on ``dev_sub_<mac>`` (commands in) and publish on
``dev_pub_<mac>`` (status out). All payloads are 16-byte binary frames.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

_LOGGER = logging.getLogger(__name__)


CMD_ON: bytes = bytes.fromhex("fa2300000000000000000000000023fb")
CMD_OFF: bytes = bytes.fromhex("fa2400000000000000000000000024fb")
CMD_REFRESH: bytes = bytes.fromhex("fcf0000000000000000000000000f0fd")

KNOWN_ACTIONS: dict[bytes, str] = {
    CMD_REFRESH: "UPDATE",
    CMD_ON: "ON",
    CMD_OFF: "OFF",
}

LIGHT_TYPE_RGB = "rgb"
LIGHT_TYPE_WHITE = "w"


def _calc_checksum(stream: bytearray) -> bytearray:
    # Frame: [0]=0x28 start, [1..13]=payload, [14]=XOR(1..13), [15]=0x29 end.
    checksum = 0
    for i in range(1, 14):
        checksum ^= stream[i]
    stream[14] = checksum & 0xFF
    return stream


def _to_little(val: str) -> str:
    little = bytearray.fromhex(val)
    little.reverse()
    return "".join(f"{x:02x}" for x in little)


@dataclass
class TechLifeState:
    """Decoded device status from a ``dev_pub_<mac>`` MQTT payload."""

    light_type: Optional[str] = None
    is_on: Optional[bool] = None
    is_available: Optional[bool] = None
    rgb: Optional[tuple[int, int, int]] = None
    brightness: Optional[int] = None
    brightness_white: Optional[int] = None


class TechLifeProtocol:
    """Build commands and parse status frames for TechLife Pro devices."""

    @staticmethod
    def get_on_command() -> bytes:
        return CMD_ON

    @staticmethod
    def get_off_command() -> bytes:
        return CMD_OFF

    @staticmethod
    def get_refresh_command() -> bytes:
        """Ask the device to publish its current state."""
        return CMD_REFRESH

    @staticmethod
    def get_rgb_command(
        red: int,
        green: int,
        blue: int,
        brightness: int = 255,
    ) -> bytes:
        """Build an RGB+brightness command. Inputs are 0..255."""
        red = max(0, min(255, int(red)))
        green = max(0, min(255, int(green)))
        blue = max(0, min(255, int(blue)))
        brightness = max(0, min(255, int(brightness)))

        # Firmware uses 0..10000 per channel and 0..100 brightness.
        scale = brightness / 255.0
        r10k = int((red / 255.0) * 10000 * scale)
        g10k = int((green / 255.0) * 10000 * scale)
        b10k = int((blue / 255.0) * 10000 * scale)
        brn_100 = int(scale * 100)

        payload = bytearray(16)
        payload[0] = 0x28
        payload[13] = 0x0F
        payload[15] = 0x29
        payload[1] = r10k & 0xFF
        payload[2] = (r10k >> 8) & 0xFF
        payload[3] = g10k & 0xFF
        payload[4] = (g10k >> 8) & 0xFF
        payload[5] = b10k & 0xFF
        payload[6] = (b10k >> 8) & 0xFF
        payload[11] = brn_100 & 0xFF
        return bytes(_calc_checksum(payload))

    @staticmethod
    def get_white_command(brightness: int) -> bytes:
        """Build a white-only brightness command. Input is 0..255."""
        brightness = max(0, min(255, int(brightness)))
        value = int(brightness / 255 * 10000)

        payload = bytearray(16)
        payload[0] = 0x28
        payload[13] = 0xF0
        payload[15] = 0x29
        payload[7] = value & 0xFF
        payload[8] = (value >> 8) & 0xFF
        return bytes(_calc_checksum(payload))

    @staticmethod
    def get_brightness_command(brightness: int) -> bytes:
        return TechLifeProtocol.get_white_command(brightness)

    @staticmethod
    def get_change_broker_command(ip_addr: str, port: int = 1883) -> bytes:
        """Build the command that re-points the device at a new MQTT broker.

        Frame layout (16 bytes):
            AF a b c d  pl ph 00 ... 00  cs B0
            indexes 1..4 = IPv4 octets, 5..6 = port (little-endian).
        """
        cmd = bytearray(16)
        cmd[0] = 0xAF
        cmd[7] = 0xF0
        cmd[15] = 0xB0
        octets = [int(x) for x in ip_addr.split(".")]
        if len(octets) != 4:
            raise ValueError(f"Invalid IPv4 address: {ip_addr!r}")
        cmd[1:5] = bytes(octets)
        cmd[5] = port & 0xFF
        cmd[6] = (port >> 8) & 0xFF
        return bytes(_calc_checksum(cmd))

    @staticmethod
    def parse_status(payload: bytes) -> Optional[TechLifeState]:
        """Decode a ``dev_pub_<mac>`` status frame, or return None.

        Valid status frames start with 0x11 and end with 0x22. Observed
        firmware emits 26-byte frames; the on/off byte sits at index 21.
        """
        if not payload or len(payload) < 26:
            return None
        if payload[0] != 0x11 or payload[-1] != 0x22:
            return None

        msg_hex = payload.hex()
        state = TechLifeState()

        if payload[12] == 0x00:
            state.light_type = LIGHT_TYPE_RGB
        elif payload[12] == 0x01:
            state.light_type = LIGHT_TYPE_WHITE

        # byte 21 (hex chars 42..44): 0x23=on, 0x24=off.
        state_hex = msg_hex[42:44]
        if state_hex == "23":
            state.is_on = True
            state.is_available = True
        elif state_hex == "24":
            state.is_on = False
            state.is_available = True

        # bytes 1..6: R/G/B as little-endian uint16, scale 0..10000.
        try:
            r10k = int(_to_little(msg_hex[2:6]), 16)
            g10k = int(_to_little(msg_hex[6:10]), 16)
            b10k = int(_to_little(msg_hex[10:14]), 16)
            state.rgb = (
                max(0, min(255, int(r10k * 255 / 10000))),
                max(0, min(255, int(g10k * 255 / 10000))),
                max(0, min(255, int(b10k * 255 / 10000))),
            )
        except ValueError:
            state.rgb = None

        # byte 11: RGB brightness, scale 0..100.
        try:
            brn_100 = int(msg_hex[22:24], 16)
            state.brightness = max(0, min(255, int(brn_100 * 2.55)))
        except ValueError:
            state.brightness = None

        # bytes 8..9: white brightness as little-endian uint16, scale 0..10000.
        try:
            brn_white_10k = int(_to_little(msg_hex[16:20]), 16)
            state.brightness_white = max(
                0, min(255, int(brn_white_10k / 10000 * 255))
            )
        except ValueError:
            state.brightness_white = None

        return state
