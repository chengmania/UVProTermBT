"""Minimal ctypes binding to libsbc for the UV-Pro audio channel.

The radio's Generic Audio channel carries SBC-encoded audio (32 kHz, mono,
16 blocks, 8 subbands, loudness allocation, bitpool 40 — see
docs/GAIA_AUDIO_SSTV.md). libsbc (BlueZ's SBC codec, `libsbc.so.1`) does the
heavy lifting; this wraps just sbc_init/sbc_decode/sbc_encode.

- SbcDecoder is streaming: feed() the SBC bytes carried in audio frames and get
  back 16-bit little-endian mono PCM. Partial trailing frames are buffered until
  the rest arrives (RFCOMM delivers arbitrary chunk sizes). Decode reads the
  codec params from each SBC frame header, so no config is needed to receive.
- SbcEncoder is configured to the radio's exact format for transmit (M3/M4).

Import raises RuntimeError if libsbc isn't present, so callers can degrade.
"""

from __future__ import annotations

import ctypes
import ctypes.util

# SBC parameter constants (from bluez sbc/sbc.h).
# NOTE: allocation Loudness is 0x00 and SNR is 0x01 (getting this backwards
# produces a wrong SBC header allocation bit and the radio rejects the audio —
# confirmed against an HCI capture of HTCommander: its frames are 0x9c 0x71…,
# ours were 0x9c 0x73…, differing only in this bit).
SBC_FREQ_32000 = 0x01
SBC_BLK_16 = 0x03
SBC_MODE_MONO = 0x00
SBC_AM_LOUDNESS = 0x00
SBC_AM_SNR = 0x01
SBC_SB_8 = 0x01
SBC_LE = 0x00

# The radio's audio format.
RADIO_SAMPLE_RATE = 32000
RADIO_BITPOOL = 40


class _Sbc(ctypes.Structure):
    _fields_ = [
        ("flags", ctypes.c_ulong),
        ("frequency", ctypes.c_uint8),
        ("blocks", ctypes.c_uint8),
        ("subbands", ctypes.c_uint8),
        ("mode", ctypes.c_uint8),
        ("allocation", ctypes.c_uint8),
        ("bitpool", ctypes.c_uint8),
        ("endian", ctypes.c_uint8),
        ("priv", ctypes.c_void_p),
        ("priv_alloc_base", ctypes.c_void_p),
    ]


def _load_libsbc() -> ctypes.CDLL:
    for name in ("sbc", "libsbc.so.1", "libsbc.so"):
        path = ctypes.util.find_library(name) or name
        try:
            lib = ctypes.CDLL(path)
        except OSError:
            continue
        lib.sbc_init.argtypes = [ctypes.POINTER(_Sbc), ctypes.c_ulong]
        lib.sbc_init.restype = None
        lib.sbc_finish.argtypes = [ctypes.POINTER(_Sbc)]
        lib.sbc_finish.restype = None
        lib.sbc_get_frame_length.argtypes = [ctypes.POINTER(_Sbc)]
        lib.sbc_get_frame_length.restype = ctypes.c_size_t
        lib.sbc_get_codesize.argtypes = [ctypes.POINTER(_Sbc)]
        lib.sbc_get_codesize.restype = ctypes.c_size_t
        lib.sbc_decode.argtypes = [
            ctypes.POINTER(_Sbc), ctypes.c_void_p, ctypes.c_size_t,
            ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
        lib.sbc_decode.restype = ctypes.c_ssize_t
        lib.sbc_encode.argtypes = [
            ctypes.POINTER(_Sbc), ctypes.c_void_p, ctypes.c_size_t,
            ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_ssize_t)]
        lib.sbc_encode.restype = ctypes.c_ssize_t
        return lib
    raise RuntimeError("libsbc not found (install libsbc1 / libsbc-dev)")


class SbcDecoder:
    """Streaming SBC → 16-bit LE mono PCM decoder."""

    def __init__(self) -> None:
        self._lib = _load_libsbc()
        self._sbc = _Sbc()
        self._lib.sbc_init(ctypes.byref(self._sbc), 0)
        self._sbc.endian = SBC_LE
        self._buf = bytearray()
        self._out = ctypes.create_string_buffer(8192)

    def feed(self, data: bytes) -> bytes:
        """Decode as many complete SBC frames as `data` (plus any buffered
        remainder) contains; return the PCM. Trailing partial frame is kept."""
        self._buf += data
        pcm = bytearray()
        written = ctypes.c_size_t(0)
        while self._buf:
            src = bytes(self._buf)
            consumed = self._lib.sbc_decode(
                ctypes.byref(self._sbc), src, len(src),
                self._out, len(self._out), ctypes.byref(written))
            if consumed <= 0:
                break  # not enough bytes for a full frame yet (or bad data)
            pcm += self._out.raw[:written.value]
            del self._buf[:consumed]
        return bytes(pcm)

    def close(self) -> None:
        self._lib.sbc_finish(ctypes.byref(self._sbc))


class SbcEncoder:
    """16-bit LE mono PCM → SBC encoder, fixed to the radio's format."""

    def __init__(self) -> None:
        self._lib = _load_libsbc()
        self._sbc = _Sbc()
        self._lib.sbc_init(ctypes.byref(self._sbc), 0)
        self._sbc.frequency = SBC_FREQ_32000
        self._sbc.blocks = SBC_BLK_16
        self._sbc.subbands = SBC_SB_8
        self._sbc.mode = SBC_MODE_MONO
        self._sbc.allocation = SBC_AM_LOUDNESS
        self._sbc.bitpool = RADIO_BITPOOL
        self._sbc.endian = SBC_LE
        self._codesize = self._lib.sbc_get_codesize(ctypes.byref(self._sbc))
        self._buf = bytearray()
        self._out = ctypes.create_string_buffer(1024)

    @property
    def pcm_bytes_per_frame(self) -> int:
        return self._codesize

    def encode(self, pcm: bytes) -> bytes:
        """Encode whole SBC frames from PCM; buffers a trailing partial frame."""
        self._buf += pcm
        out = bytearray()
        written = ctypes.c_ssize_t(0)
        while len(self._buf) >= self._codesize:
            chunk = bytes(self._buf[:self._codesize])
            consumed = self._lib.sbc_encode(
                ctypes.byref(self._sbc), chunk, len(chunk),
                self._out, len(self._out), ctypes.byref(written))
            if consumed <= 0:
                break
            out += self._out.raw[:written.value]
            del self._buf[:consumed]
        return bytes(out)

    def close(self) -> None:
        self._lib.sbc_finish(ctypes.byref(self._sbc))
