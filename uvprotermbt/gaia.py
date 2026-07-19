"""Minimal GAIA (radio native control protocol) codec — reverse-engineered from
HTCommander's gaia_protocol.dart.

The UV-Pro's control channel (SPP, UUID 0000110…, the same one we use for KISS)
speaks GAIA framed commands:

    FF 01 <flags> <dataLen> <grp_hi grp_lo> <cmd_hi cmd_lo> <data…>

- FF 01 = start-of-frame + version. flags bit0 = "has checksum" (we send 0).
- dataLen = length of <data> only (not the 4-byte group+command header).
- group/command are big-endian 16-bit. Group `basic` = 2.

This module is just the frame codec plus the handful of command ids we need to
probe whether the radio answers GAIA on the SPP channel (and whether that can
coexist with KISS). See docs/GAIA_AUDIO_SSTV.md — needed for SSTV *transmit*.
"""

from __future__ import annotations

from dataclasses import dataclass

GAIA_SOF = 0xFF
GAIA_VER = 0x01

GROUP_BASIC = 2
GROUP_EXTENDED = 10

# RadioBasicCommand ids we care about.
CMD_GET_DEV_ID = 1
CMD_GET_DEV_INFO = 4
CMD_READ_STATUS = 5
CMD_REGISTER_NOTIFICATION = 6
CMD_EVENT_NOTIFICATION = 9
CMD_GET_HT_STATUS = 20

# RadioNotification ids (events the radio pushes once registered).
NOTIF_HT_STATUS_CHANGED = 1
NOTIF_RADIO_STATUS_CHANGED = 8
NOTIF_FREQ_MODE_STATUS_CHANGED = 14


def handshake_frames() -> list[bytes]:
    """The GAIA connect handshake HTCommander sends, which makes the radio treat
    us as an active controller (registering for HT/radio-status notifications).
    Believed to be what enables the audio transmit path. See radio.dart connect."""
    return [
        encode(GROUP_BASIC, CMD_GET_DEV_INFO, bytes([3])),
        encode(GROUP_BASIC, CMD_REGISTER_NOTIFICATION,
               bytes([NOTIF_HT_STATUS_CHANGED, NOTIF_FREQ_MODE_STATUS_CHANGED])),
        encode(GROUP_BASIC, CMD_REGISTER_NOTIFICATION,
               bytes([NOTIF_RADIO_STATUS_CHANGED])),
        encode(GROUP_BASIC, CMD_GET_HT_STATUS),
    ]


def encode(group: int, command: int, data: bytes = b"") -> bytes:
    """Build a GAIA command frame (no checksum)."""
    if len(data) > 0xFF:
        raise ValueError("GAIA data too long for a single-byte length field")
    header = bytes([GAIA_SOF, GAIA_VER, 0x00, len(data)])
    body = bytes([(group >> 8) & 0xFF, group & 0xFF,
                  (command >> 8) & 0xFF, command & 0xFF])
    return header + body + data


@dataclass
class GaiaFrame:
    group: int
    command: int
    data: bytes
    is_response: bool  # radios OR the command id with 0x8000 in replies


class GaiaDecoder:
    """Streaming GAIA decoder. feed() raw bytes → complete GaiaFrames.

    Resyncs on the FF 01 start-of-frame, so it tolerates interleaved non-GAIA
    bytes (e.g. KISS frames on the same channel) — handy for the coexistence test.
    """

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[GaiaFrame]:
        self._buf += data
        out: list[GaiaFrame] = []
        while True:
            # Find a plausible start-of-frame (FF 01).
            i = 0
            n = len(self._buf)
            while i + 1 < n and not (self._buf[i] == GAIA_SOF and self._buf[i + 1] == GAIA_VER):
                i += 1
            if i:
                del self._buf[:i]
                n = len(self._buf)
            if n < 4:
                break  # need at least the header
            has_checksum = self._buf[2] & 1
            data_len = self._buf[3]
            total = 8 + data_len + has_checksum
            if n < total:
                break  # wait for the rest
            group = (self._buf[4] << 8) | self._buf[5]
            raw_cmd = (self._buf[6] << 8) | self._buf[7]
            payload = bytes(self._buf[8:8 + data_len])
            out.append(GaiaFrame(group=group, command=raw_cmd & 0x7FFF,
                                 data=payload, is_response=bool(raw_cmd & 0x8000)))
            del self._buf[:total]
        return out
