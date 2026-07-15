from uvprotermbt.aprs import (
    Kind,
    HeardTable,
    encode_ack,
    encode_message,
    parse_frame,
    parse_kiss_payload,
)
from uvprotermbt.ax25 import Address, decode_frame


def test_encode_message_roundtrip():
    frame = encode_message(Address("KC3SMW", 7), "KC3SMW-9", "hello there", "3")
    pkt = parse_frame(decode_frame(frame))
    assert pkt.kind is Kind.MESSAGE
    assert pkt.source == "KC3SMW-7"
    assert pkt.addressee.strip() == "KC3SMW-9"
    assert pkt.text == "hello there"
    assert pkt.msg_id == "3"


def test_encode_message_truncates_to_67():
    long_text = "x" * 100
    frame = encode_message(Address("N0CALL", 0), "TEST", long_text)
    pkt = parse_frame(decode_frame(frame))
    assert len(pkt.text) == 67


def test_encode_ack_roundtrip():
    frame = encode_ack(Address("KC3SMW", 7), "KC3SMW-9", "3")
    pkt = parse_frame(decode_frame(frame))
    assert pkt.kind is Kind.ACK
    assert pkt.msg_id == "3"
    assert pkt.addressee.strip() == "KC3SMW-9"


def test_parse_real_message_frame():
    # The exact frame direwolf decoded on-air (ax25 regression vector).
    hexbytes = ("82a0b4aaaca8e0968666a69aae6f03f0"
                "3a4b4333534d572d39203a68656c6c6f2066726f6d20555650726f5465726d42547b31")
    pkt = parse_kiss_payload(bytes.fromhex(hexbytes))
    assert pkt is not None
    assert pkt.kind is Kind.MESSAGE
    assert pkt.source == "KC3SMW-7"
    assert pkt.text == "hello from UVProTermBT"
    assert pkt.msg_id == "1"


def test_parse_real_mice_beacon():
    hexbytes = ("a8a0a2a666aa60968666a69aaeeeae92888a624062ae92888a64406303f0"
                "60675e446c21765b2f602234617d5f240d")
    pkt = parse_kiss_payload(bytes.fromhex(hexbytes))
    assert pkt is not None
    assert pkt.kind is Kind.MICE
    assert pkt.source == "KC3SMW-7"


def test_parse_status():
    from uvprotermbt.ax25 import encode_ui_frame
    frame = encode_ui_frame(Address("KC3SMW", 7), Address("APZUVT", 0), b">on the air")
    pkt = parse_frame(decode_frame(frame))
    assert pkt.kind is Kind.STATUS
    assert pkt.text == "on the air"


def test_heard_table_orders_by_recency():
    tbl = HeardTable()
    frame1 = encode_message(Address("AAA", 1), "X", "hi")
    frame2 = encode_message(Address("BBB", 2), "Y", "yo")
    tbl.note(parse_frame(decode_frame(frame1)))
    tbl.note(parse_frame(decode_frame(frame2)))
    tbl.note(parse_frame(decode_frame(frame1)))
    recent = tbl.recent()
    assert recent[0].call == "AAA-1"  # most recently heard
    assert recent[0].count == 2
