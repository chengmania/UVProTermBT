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
- [x] BT status surfaced in the UI — GUI status bar shows ●/○ BT (green
      when the SerialPort profile is connected).

## Phase 3 — AX.25 UI Frames + APRS RX
- [x] AX.25 address encode/decode (callsign-SSID, digi path, H-bits) —
      `uvprotermbt/ax25.py`, `tests/test_ax25.py`. Correctly distinguishes
      the C bit (dest/source) from the H/has-been-repeated bit (digis).
- [x] UI frame build/parse — `encode_ui_frame()` / `decode_frame()`.
      **Encoder validated on-air 2026-07-14**: a built frame was
      transmitted through the UV-Pro and decoded by direwolf off-air.
- [x] APRS packet classifier: message/ack/reject/position/status/Mic-E —
      `aprs.py` `parse_frame()`, `tests/test_aprs.py`. (Mic-E is classified
      and its comment surfaced; full lat/lon Mic-E decode still TODO.)
- [x] Heard-stations table (`aprs.HeardTable`) — data layer done; a
      dedicated GUI list view is still TODO.

## Phase 4 — APRS Messaging
- [x] Message encode (`:ADDRESSEE:text{NN`), 67-char enforcement —
      `aprs.encode_message()`, tested; proven on-air 2026-07-14.
- [x] Ack send on RX — GUI auto-acks messages addressed to us
      (`encode_ack()`); ack receipts shown in Chat.
- [ ] Retry queue with backoff on unacked outbound messages
- [x] Basic chat UI (send to a target, live RX). Per-station threads /
      unread indicators still TODO.
- [ ] Optional periodic status/position beacon (off by default)
- [ ] ON-AIR TEST: two-way message with another station / digipeater

## Phase 5 — AX.25 Connected Mode
- [ ] LAPB-ish state machine: SABM/UA, I-frames, RR/RNR, REJ, DISC
- [ ] Window size 1 first (simplest), then k>1 if stable
- [ ] Retry/T1 timer tuning for 1200-baud AFSK realities
- [ ] Unit tests with scripted frame exchanges

## Phase 6 — Desktop GUI (PyQt6, OpenWave-styled)
> Direction change 2026-07-14: the UI is a **standalone PyQt6 windowed app**
> resembling the OpenWave desktop program (not an in-terminal urwid TUI).
> The urwid `ui.py` was built then replaced. Backends are UI-agnostic.
- [x] Main window: OpenWave-style status bar (callsign accent + BT state),
      four mode tabs (Chat / APRS / BBS / Winlink), `[MYCALL]:` input bar.
      `uvprotermbt/gui/main_window.py`.
- [x] Light/dark themes (OpenWave's navy/cyan palette + matched light),
      toggle via View menu / Ctrl-T. `uvprotermbt/gui/theme.py`.
- [x] Chat + APRS Monitor wired to the live link; BBS/Winlink screens ready
      for the connected-mode backend.
- [ ] BBS/Winlink session views once Phase 5 connected mode exists
- [ ] Heard-stations panel; per-station message threads

## Phase 7 — Settings & Polish
- [x] Settings dialog: callsign/SSID, APRS path, BT target, theme —
      `uvprotermbt/gui/settings_dialog.py`.
- [x] JSON config persistence (user config dir) — `uvprotermbt/config.py`.
- [ ] Beacon toggle in settings (field exists; no beacon sender yet)
- [ ] README with usage guide + screenshots
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
