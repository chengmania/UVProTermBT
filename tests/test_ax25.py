from uvprotermbt.ax25 import (
    Address,
    CONTROL_UI,
    PID_NO_LAYER3,
    decode_frame,
    encode_ui_frame,
)

# The exact AX.25 bytes of the message transmitted through the UV-Pro and
# decoded off-air by direwolf on 2026-07-14 (KC3SMW-7 > APZUVT,
# ":KC3SMW-9 :hello from UVProTermBT{1"). This is a regression anchor: if the
# encoder ever stops producing these bytes, on-air behavior has changed.
ONAIR_INFO = b":KC3SMW-9 :hello from UVProTermBT{1"
ONAIR_FRAME_HEX = (
    "82a0b4aaaca8e0"          # APZUVT-0, C=1
    "968666a69aae6f"          # KC3SMW-7, C=0, last
    "03f0"                    # control UI, PID no-L3
    "3a4b4333534d572d39203a68656c6c6f2066726f6d20555650726f5465726d42547b31"
)

# A real Mic-E position beacon captured off the radio the same day.
RX_BEACON_HEX = (
    "a8a0a2a666aa60"          # TPQS3U-0 (Mic-E encoded dest)
    "968666a69aaeee"          # KC3SMW-7
    "ae92888a624062"          # WIDE1-1
    "ae92888a644063"          # WIDE2-1, last
    "03f0"
    "60675e436c21505b2f602234717d5f240d"
)


def test_encode_matches_onair_frame():
    frame = encode_ui_frame(
        source=Address("KC3SMW", 7),
        dest=Address("APZUVT", 0),
        info=ONAIR_INFO,
    )
    assert frame.hex() == ONAIR_FRAME_HEX


def test_encode_with_digipeater_path():
    frame = encode_ui_frame(
        source=Address("KC3SMW", 7),
        dest=Address("APZUVT", 0),
        info=b"test",
        path=[Address("WIDE1", 1), Address("WIDE2", 1)],
    )
    decoded = decode_frame(frame)
    assert str(decoded.source) == "KC3SMW-7"
    assert str(decoded.dest) == "APZUVT-0"
    assert [str(a) for a in decoded.path] == ["WIDE1-1", "WIDE2-1"]
    assert decoded.info == b"test"


def test_encode_decode_roundtrip():
    original = encode_ui_frame(
        source=Address("N0CALL", 15),
        dest=Address("APRS", 0),
        info=b"hello world",
    )
    decoded = decode_frame(original)
    assert decoded.control == CONTROL_UI
    assert decoded.pid == PID_NO_LAYER3
    assert decoded.source == Address("N0CALL", 15)
    assert decoded.dest == Address("APRS", 0)
    assert decoded.path == []
    assert decoded.info == b"hello world"


def test_decode_real_received_beacon():
    decoded = decode_frame(bytes.fromhex(RX_BEACON_HEX))
    assert str(decoded.source) == "KC3SMW-7"
    assert str(decoded.dest) == "TPQS3U-0"  # Mic-E encoded position
    assert decoded.header_str() == "KC3SMW-7 > TPQS3U-0 via WIDE1-1,WIDE2-1"
    assert decoded.control == CONTROL_UI
    assert decoded.pid == PID_NO_LAYER3
    # Info is Mic-E (decoded later by aprs.py); just confirm the type byte.
    assert decoded.info[:1] == b"`"


def test_header_str_no_repeated_marker_when_not_repeated():
    frame = decode_frame(bytes.fromhex(RX_BEACON_HEX))
    # No digipeater in this capture has been repeated (H bit clear):
    assert "*" not in frame.header_str()


def test_ssid_zero_omitted_call_padding():
    frame = encode_ui_frame(
        source=Address("W1AW"),           # ssid defaults to 0
        dest=Address("APRS"),
        info=b"",
    )
    decoded = decode_frame(frame)
    assert decoded.source == Address("W1AW", 0)
    assert decoded.dest == Address("APRS", 0)
    assert decoded.info == b""


def test_decode_truncated_raises():
    import pytest
    with pytest.raises(ValueError):
        decode_frame(b"\x82\xa0\xb4")  # partial address
