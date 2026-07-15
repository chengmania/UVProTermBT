# UVProTermBT — Roadmap

## Phase 0 — Scaffold
- [x] Project structure: `uvprotermbt/` package, `tests/`, docs, venv
- [ ] Repo init, push to github.com/chengmania/UVProTermBT
- [ ] `requirements.txt` / packaging metadata
- [ ] GitHub Actions workflow skeleton (Linux build; Windows stretch goal)

## Phase 1 — KISS Codec
- [x] `kiss.py`: encode/decode, FEND/FESC/TFEND/TFESC escaping (ported
      from AXTermPuter's native-tested C++ version)
- [x] Stream reassembly (partial frames across arbitrary-sized reads)
- [x] Unit tests incl. malformed-frame fuzz case (pytest, 8/8 passing)

## Phase 2 — Classic Bluetooth RFCOMM Link to UV-Pro
- [x] Confirm SDP-advertised RFCOMM channels from Linux directly
      (`sdptool records <addr>` — `browse` comes back empty). Found
      channels 2/3/4 via SDP, but see below: none of those are actually
      the right one.
- [x] `RfcommKissLink`: **rewritten (2026-07-14) onto BlueZ's SerialPort
      `org.bluez.Profile1`** (SDP-negotiated), NOT a raw socket — the raw
      `socket.AF_BLUETOOTH`/`BTPROTO_RFCOMM` approach does not work with
      this radio (see below). GLib main-loop thread feeding a queue,
      `send()`/`on_receive()` like AXTermPuter's `RadioLink` interface.
- [x] Reconnect logic (backoff) in `RfcommKissLink`
- [x] Discovered the raw-socket / "channel 1" approach is a dead end:
      the radio refuses bare channel connects and only serves KISS via its
      SerialPort profile (UUID 0x1101). "Channel 1" was an artifact of
      Android's profile negotiation. See docs/PROTOCOL.md §3.
- [x] Root-caused classic-BT connection flakiness to BlueZ's `hfp-hf`
      Hands-Free-unit auto-connect contending with RFCOMM; systemd
      `--noplugin=hfp-hf` drop-in kept in place, though the profile method
      sidesteps the headset contention. See docs/PROTOCOL.md §2.
- [x] Found the real pairing blocker: a **stale link key** in
      `/var/lib/bluetooth` that `bluetoothctl remove` silently skips.
      Symmetric wipe + re-pair is the fix. See docs/PROTOCOL.md §3.
- [x] **HARDWARE TEST PASSED (2026-07-14): decoded live KISS/AX.25
      end-to-end** through the rewritten `RfcommKissLink` — real Mic-E
      beacons `KC3SMW-7 > <Mic-E> via WIDE1-1,WIDE2-1`. Reproducible via
      `scripts/monitor.py`; requires KISS TNC enabled on the radio.
- [ ] BT status surfaced in the UI (no UI yet — Phase 6/7)

## Phase 3 — AX.25 UI Frames + APRS RX
- [x] AX.25 address encode/decode (callsign-SSID, digi path, H-bits) —
      `uvprotermbt/ax25.py`, `tests/test_ax25.py`. Correctly distinguishes
      the C bit (dest/source) from the H/has-been-repeated bit (digis).
- [x] UI frame build/parse — `encode_ui_frame()` / `decode_frame()`.
      **Encoder validated on-air 2026-07-14**: a built frame was
      transmitted through the UV-Pro and decoded by direwolf off-air.
- [ ] APRS packet classifier: position, status, message (needs Mic-E —
      the live beacons are Mic-E encoded)
- [ ] Heard-stations list view (call, last heard, type)

## Phase 4 — APRS Messaging
- [x] Message encode (`:ADDRESSEE:text{NN`) proven end-to-end on-air ahead
      of schedule (KC3SMW-7 message decoded by direwolf as an APRS Message,
      number "1"). Still to formalize in `aprs.py` with 67-char enforcement.
- [ ] Ack send on RX; retry queue with backoff on TX
- [ ] Conversation UI: per-station threads, unread indicator, compose
- [ ] Optional periodic status/position beacon (off by default)
- [ ] ON-AIR TEST: two-way message with another station / digipeater

## Phase 5 — AX.25 Connected Mode
- [ ] LAPB-ish state machine: SABM/UA, I-frames, RR/RNR, REJ, DISC
- [ ] Window size 1 first (simplest), then k>1 if stable
- [ ] Retry/T1 timer tuning for 1200-baud AFSK realities
- [ ] Unit tests with scripted frame exchanges

## Phase 6 — Terminal Mode UI
- [ ] Connect dialog (target call, digi path)
- [ ] Session view: scrollback buffer, line input, disconnect key
- [ ] ON-AIR TEST: connect to ChengmaniaBPQ, read messages on the BBS

## Phase 7 — Settings & Polish
- [ ] Settings screen: callsign/SSID, path, BT target, beacon
- [ ] JSON config persistence (user config dir)
- [ ] README with usage guide
- [ ] PyInstaller executables (Linux primary, Windows stretch) via GitHub
      Actions release workflow

## Future / Post-v1
- [ ] LoRa-APRS backend behind the same link interface, if ever relevant
- [ ] Meshtastic↔APRS bridge experiments
- [ ] Export heard log / messages to a field server
- [ ] Radio-control features (battery level, channel switching, position)
      via the `khusmann/benlink` library's structured GaiaFrame protocol —
      not needed for core APRS/terminal functionality, but a good fit for
      a "radio status" panel later
