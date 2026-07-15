# UVProTermBT — Protocol Notes

Living document. Update with evidence when hardware findings change anything.
Carries over confirmed findings from the AXTermPuter project's Phase 2
investigation (2026-07-13) — see that repo's `docs/PROTOCOL.md` and
`LOG.txt` for the full narrative of how these were discovered.

## 1. KISS Framing

- FEND = 0xC0, FESC = 0xDB, TFEND = 0xDC, TFESC = 0xDD
- Frame: FEND, type byte, payload (escaped), FEND
- Type 0x00 = data on port 0 (only port used with UV-Pro)
- Escaping: 0xC0 → 0xDB 0xDC; 0xDB → 0xDB 0xDD
- The RFCOMM socket delivers arbitrary-sized chunks: the decoder MUST
  reassemble across reads and tolerate back-to-back FENDs / empty frames.

Decoder design decisions (`uvprotermbt/kiss.py`, ported from AXTermPuter's
native-tested `lib/kiss/kiss_codec.{h,cpp}` — not reverse-engineered
findings, implementation choices where the KISS spec is silent):
- Back-to-back `FEND FEND` (empty frame) is silently skipped, never
  emitted as a zero-length `Frame`.
- `FESC` followed by anything other than `TFEND`/`TFESC` is treated as a
  protocol violation: the in-progress frame is discarded and decoding
  resyncs on the next `FEND`.

## 2. UV-Pro Bluetooth Link — classic BT RFCOMM, NOT BLE

This is the single most important finding from the AXTermPuter
investigation and the whole reason this project exists on this platform:

- The UV-Pro is a dual-mode (BR/EDR + BLE) radio. It exposes a BLE GATT
  service (`00000001-ba2a-46c9-ae49-01b0961f68bb`, write char `...0002`,
  notify char `...0003`) that looks exactly like a serial/KISS transport
  — but writing well-formed KISS frames to it produces **zero RF
  activity** (verified against direwolf monitoring the channel live).
- Captured real Bluetooth HCI traffic (via an Android bugreport's
  embedded btsnoop log) from two different Android apps talking to the
  radio in TNC mode:
  - The official BTech companion app: classic BT `Create Connection`,
    classic pairing (Link Key Request/Reply), RFCOMM multiplexer
    (channel 0), then a data channel on **RFCOMM channel 4**.
  - A second app ("WoAD"): same classic BT connection pattern, data on
    **RFCOMM channel 1** this time. Direwolf, listening live on the
    frequency, cleanly decoded a real AX.25 SABM frame
    (`KC3SMW-15>KC3SMW-10:(SABM cmd, p=1)`) transmitted during this
    session.
  - Confirmed genuine KISS framing on channel 1: captured payload bytes
    included `C0 00` immediately at the start of a UIH frame — FEND
    followed by command byte 0x00 (data, port 0), exactly matching
    `kiss.py`/`kiss_codec.h`.
- **Conclusion: the UV-Pro's real KISS/TNC data path is classic Bluetooth
  RFCOMM (SPP), not BLE GATT.** The BLE service that looks like a serial
  transport is a red herring for this purpose (its actual function is
  unconfirmed — maybe firmware update, maybe unused in current firmware).
- **SDP records confirmed directly from Linux (2026-07-13)** via `sdptool
  records <addr>` (note: `sdptool browse <addr>` comes back empty —
  `records` is the form that works) after a fresh classic-BT pair
  (`bluetoothctl pair`, no PIN prompt — Just Works style):
  - `PnP Information` (`0x1200`) — no RFCOMM channel (info record only)
  - `"BS AOC"` (custom UUID `39144315-32fa-40db-85ed-fbfeba2d86e6`) →
    RFCOMM channel 2
  - `Voice Gateway` (Handsfree Audio Gateway `0x111f`) → RFCOMM channel 3
  - `"SPP Dev"` (Serial Port, `0x1101`) → RFCOMM channel 4
  - **Correction, superseding the channel-4 recommendation below:**
    channel 4 is SDP-discoverable and accepts a bare socket connection,
    but it is a **red herring** — a generic advertised serial profile,
    not the real TNC data path (see the `benlink` findings just below for
    why). Channel 1, which does *not* appear in this SDP table at all,
    is the one that actually matters.
- **Found the [`khusmann/benlink`](https://github.com/khusmann/benlink)
  project (2026-07-13)** — a Python library specifically reverse-
  engineering this radio family ("Benshi" radios: BTech UV-Pro,
  RadioOddity GA-5WB, Vero VR-N76/N7500, BTech GMRS-Pro). This resolved
  several things we'd been guessing at:
  - The real BLE control service is
    `00001100-d102-11e1-9b23-00025b00a5a5` (write char `...1101`, notify
    char `...1102`) — **not** `00000001-ba2a-46c9-ae49-01b0961f68bb`,
    which we'd identified earlier and is itself a red herring, just like
    RFCOMM channel 4. We had the "which service looks like a serial
    bridge" instinct right, twice, and were wrong both times.
  - Messages are wrapped in a `Message`/`TncDataFragment` structure, and
    for RFCOMM specifically, additionally wrapped in a `GaiaFrame`
    (Qualcomm GAIA framing: starts with `0xFF 0x01` version, then a
    1-byte flags, 1-byte payload-length, then the message bytes). This is
    a real, documented control protocol — not just raw KISS on a serial
    line.
  - `benlink`'s own documented RFCOMM usage connects with **channel 1**,
    matching WoAD's channel, not channel 4.
  - Cross-checking the actual captured bytes settles which channel is
    which: the official app's channel 4 traffic ended in `...ff 01 00` —
    a GaiaFrame header. WoAD's channel 1 traffic started with `c0 00` —
    raw KISS FEND + command byte, no GaiaFrame at all. **Conclusion:
    channel 1 carries genuine raw KISS pass-through (no envelope needed),
    separate from and simpler than the structured Benshi GaiaFrame
    command protocol.** The two are distinguishable by leading byte
    (`0xFF` for a GaiaFrame command, `0xC0` for a raw KISS frame),
    suggesting channel 1 may be multiplexed to carry both, but for our
    purposes (APRS/AX.25 terminal) we only need the raw KISS half.
  - **Decision: `uvprotermbt`'s own `kiss.py` + `RfcommKissLink` remain
    the right approach for the core KISS data path — no need to adopt
    `benlink`'s `Message`/`GaiaFrame` protocol for that.** `benlink` is
    a good reference to come back to later for actual radio-control
    features (battery level, channel switching, position) that aren't
    needed for basic APRS/terminal functionality.
  - `RfcommKissLink.DEFAULT_CHANNEL` is now **1**, not 4.
- **Confirmed live from Linux, twice independently** (2026-07-13): a bare
  `socket.AF_BLUETOOTH`/`BTPROTO_RFCOMM` connect to channel 1 succeeds
  cleanly after a fresh `bluetoothctl pair`. No RF traffic happened to be
  flowing during either test window, so we haven't yet decoded a live
  AX.25 frame end-to-end from this project, but the transport-level
  connection itself works.
- **Known issue, still open: the classic-BT connection is flaky to
  reproduce on demand.** Across many attempts this session we saw, in
  no obvious pattern: clean connect, `TimeoutError`, `ConnectionRefusedError`,
  and (once) `br-connection-refused` at the BlueZ `Device1.Connect()`
  level when a stale/mismatched bond exists on one side only (e.g. we ran
  `bluetoothctl remove` locally but the radio still remembered the old
  bond — symmetric removal on both sides fixes that specific case). Some
  observations, none fully explanatory on their own:
  - The radio's own "paired device" entry for us was observed to appear
    and then disappear within a few seconds on the radio's screen,
    independent of anything we did — suggesting the ACL connection itself
    may only stay up briefly unless something actively uses it soon after
    it forms.
  - A full power cycle (battery pulled) of the radio did restore BLE
    connectivity when it had also gone flaky, suggesting the radio's BT
    stack can get into a bad state that a reset clears.
  - Holding a BLE connection open at the same time does **not** stabilize
    or help classic RFCOMM — confirmed independent by testing both at once.
  - We do not have logs from the radio's own firmware, so root-causing
    this further from software has hit its limit for now.
- **DONE Phase 2 (2026-07-14): live end-to-end AX.25 decode achieved** —
  but NOT via a raw socket on channel 1. Getting there required abandoning
  the raw-socket approach entirely in favour of BlueZ's SerialPort profile.
  Full reproducible recipe in **§3 below**; this is the headline result of
  the whole Phase 2 investigation, read that first.
- **RESOLVED (2026-07-13, later session): root cause of the connection
  flakiness above.** `journalctl -u bluetooth` showed repeated
  `bluetoothd[...]: src/profile.c:ext_connect() Hands-Free unit failed
  connect to <radio MAC>: Connection refused (111)` throughout every
  flaky session. The UV-Pro advertises a Handsfree Audio Gateway service
  (SDP `Voice Gateway`/`0x111f`, channel 3 — see the SDP table above) and
  BlueZ's `hfp-hf` (Hands-Free unit/client) plugin auto-connects to it,
  contending with RFCOMM at the BlueZ/kernel level regardless of which
  client API (raw socket vs `rfcomm bind`) initiates the KISS connection.
  Confirmed independently by two real-world writeups covering this exact
  radio family: a HamRadioTech article (DC6AP, Sep 2025, "VR-N76 (UV-PRO)
  with KISS-TNC under Linux") and the `islandmagic/kiss-tnc-test`
  Raspberry Pi guide — both require disabling the headset/hands-free
  profile as a prerequisite. Their instructions
  (`/etc/bluetooth/input.conf`, `Disable=Headset`) are stale for BlueZ
  5.72 (no such option in that file in this version, confirmed by
  reading it); the working modern equivalent is a systemd drop-in:
  `/etc/systemd/system/bluetooth.service.d/override.conf`:
  ```ini
  [Service]
  ExecStart=
  ExecStart=/usr/libexec/bluetooth/bluetoothd --noplugin=hfp-hf
  ```
  followed by `sudo systemctl daemon-reload && sudo systemctl restart
  bluetooth`. Only `hfp-hf` is disabled (not `hfp-ag`/`hsp-hs`/`hsp-ag`)
  — it's the exact role named in the failing log line; if flakiness
  somehow persists after re-testing, `hsp-hs` (legacy Headset profile
  client) is the next candidate.
- **Pairing procedure addendum:** both external writeups include
  `bluetoothctl trust <MAC>` after `pair <MAC>`, which this project's
  earlier pairing steps had not been using. Updated procedure:
  ```
  bluetoothctl
  scan on
  # note MAC, then:
  pair <MAC>
  trust <MAC>
  quit
  ```
- **External references confirming RFCOMM channel 1 independently:**
  HamRadioTech (DC6AP) and `islandmagic/kiss-tnc-test` both land on
  channel 1 for this radio family via `rfcomm bind /dev/rfcomm0 <MAC> 1`,
  matching our own `khusmann/benlink`-cross-checked finding above. Also
  note: HamRadioTech reports **direwolf's TX path does not work with
  this radio** (RX-only; cause unknown), recommending TFKISS for TX
  verification — relevant for later on-air TX testing (Phase 4+), not
  for this project's current RX-only hardware smoke test.
- **Architecture decision, raw socket vs `rfcomm bind`** — ⚠️ **SUPERSEDED
  2026-07-14, see §3.** This section previously argued for keeping
  `RfcommKissLink`'s raw `socket.AF_BLUETOOTH` approach. Live testing then
  showed the raw socket (AND `rfcomm bind`, which opens the same raw path
  underneath) does **not** work with this radio at all: bare channel
  connects are refused when the radio is awake and time out otherwise, on
  every channel (1–15). Neither raw sockets nor `rfcomm bind`/`pyserial`
  is the answer — the radio only serves KISS through its **SerialPort
  profile**, reached via a BlueZ `org.bluez.Profile1` registration. That
  is now the implemented transport. `RfcommKissLink` was rewritten onto it
  (`uvprotermbt/link.py`); the old channel-1 raw-socket guidance below in
  this section is retained only as investigation history.

## 3. Reproducing the Live KISS Link — the working method

**RESOLVED 2026-07-14: first live end-to-end AX.25 decode through this
project.** Caught real Mic-E position beacons — `KC3SMW-7 > <Mic-E dest>
via WIDE1-1,WIDE2-1`, raw KISS (`c0 00 ... c0`, no GaiaFrame envelope).
`uvprotermbt/link.py` implements the method below; `scripts/monitor.py` is
the smoke test that reproduces it.

### The key finding: use the SerialPort *profile*, not a raw socket
A raw `socket.AF_BLUETOOTH`/`BTPROTO_RFCOMM` connect to a fixed channel
does NOT work with this radio (supersedes the channel-1 raw-socket guidance
in §2). On a clean bond with KISS TNC enabled:
- when the radio's BR/EDR radio is awake, bare channel connects are
  actively **refused** (ConnectionRefused) on every channel tried (1–15);
- the radio power-saves its BR/EDR radio between pages, so on-demand raw
  connects otherwise **time out**, flapping unpredictably.

The radio only serves the KISS session through its advertised **SerialPort
profile** (UUID `0x1101`), reached via an SDP-negotiated *profile*
connection — exactly what macOS (YAAC) and Android (WoAD) do. Our earlier
"channel 1" was just the channel *Android's* profile negotiation landed on,
not something a bare socket can reproduce.

On Linux the equivalent is: register a BlueZ `org.bluez.Profile1` (client
role, SerialPort UUID), call `Device1.Connect()`, and read the RFCOMM file
descriptor BlueZ passes to the profile's `NewConnection` callback. Pure
Python over D-Bus — no `sudo`, no deprecated `rfcomm` tool, no `pyserial`.

### Prerequisites (all required)
1. **On the radio:** Settings (green button) → General Settings → KISS TNC
   → **Enable KISS TNC**. Until this is on the profile connect is refused
   and no channel listens. This KISS TNC menu is a firmware feature *not*
   present in the Rev3 PDF manual in `docs/` — current firmware has it.
   (Confirmed by a YAAC/VR-N76 groups.io guide; the N76 and UV-Pro are the
   same radio/firmware.)
2. **A clean symmetric bond.** The single biggest pairing blocker was a
   **stale link key**: `bluetoothctl remove` silently no-ops when the
   device object isn't currently present, leaving an old key in
   `/var/lib/bluetooth/<adapter>/<radio>/`. Against a radio that has
   forgotten us, that mismatch gives `AuthenticationRejected` on pair and
   `br-connection-refused` on connect. Fix — wipe BOTH sides:
   ```
   # on the radio: forget all Bluetooth connections, then enter Pairing
   sudo rm -rf /var/lib/bluetooth/<ADAPTER-MAC>/38:D2:00:01:38:8F
   sudo systemctl restart bluetooth
   bluetoothctl          # scan on; pair <MAC>; trust <MAC>; quit
   ```
   KDE's bluedevil supplies the pairing agent — do **not** register your
   own; `agent`/`default-agent` in a throwaway `bluetoothctl` session fails
   with "Failed to register agent object" because one already exists.
3. **BlueZ D-Bus bindings:** `python3-dbus` + `python3-gi` (PyGObject).
   They come from the system, so build the venv with
   `--system-site-packages`:
   ```
   python3 -m venv --system-site-packages .venv
   .venv/bin/pip install -r requirements.txt
   ```

### Run it
```
.venv/bin/python scripts/monitor.py          # defaults to the UV-Pro MAC
```
Then generate APRS traffic (another station beaconing, or beacon from the
UV-Pro once it has a GPS lock). Expect:
```
KISS frame  port=0 cmd=0 len=47  KC3SMW-7 > TPQS3U-0 via WIDE1-1,WIDE2-1
```

### Connection gotchas
- `Device1.Connect()` frequently returns `NoReply`/`br-connection-busy`
  while BlueZ finishes in the background — the `NewConnection` callback is
  the real success signal, so those are non-fatal (link.py treats them as
  transient and keeps a backoff retry running).
- `br-connection-busy` also appears when something else is mid-connect:
  KDE auto-connect, a stale `rfcomm bind`, or a prior `ConnectProfile`
  still in flight. link.py issues a `Disconnect()` before each `Connect()`
  to clear it.
- `bluetoothctl connect` may report `br-connection-refused` even when the
  profile path works — it tries the SDP-advertised profiles (incl. the
  Handsfree Audio Gateway, which the radio refuses / shows as a "headset").
  Expected and harmless; the SerialPort profile still connects.
- `sdptool browse`/`records` frequently time out against this radio even
  with the ACL up. Ignore it — BlueZ's own SDP during `Connect()` is what
  matters, and it caches the service list (visible in `bluetoothctl info`).
- The `hfp-hf` systemd `--noplugin` fix from §2 remains in place and does
  no harm, but the profile method is what actually delivered KISS; the
  headset contention is sidestepped by connecting the SerialPort profile.

### Payloads are Mic-E
The live beacons are **Mic-E** encoded (the VR-N76 APRS setup guide
recommends Mic-E for packet efficiency): the AX.25 *destination* address
carries the encoded latitude (looks like gibberish, e.g. `TPQS3U`), and the
info field starts with `` ` `` (0x60) or `'` (0x1C). `aprs.py` must decode
Mic-E, not just the standard `!`/`=` position and `:` message formats.
This is Phase 3 work — flagged here so it isn't a surprise.

## 4. AX.25 Essentials

- Address field: callsign shifted left 1 bit, space-padded to 6 chars;
  SSID byte carries SSID (bits 1-4), H bit (has-been-digipeated), and the
  address-extension bit (last address sets bit 0 = 1).
- UI frame: control 0x03, PID 0xF0 (no layer 3) — all APRS traffic.
- Connected mode subset for Terminal Mode:
  - SABM (0x2F/0x3F w/ P) → expect UA
  - I-frames with N(S)/N(R), RR as ack, REJ on sequence error
  - DISC → UA to tear down
  - T1 retry timer: start ~5 s at 1200 baud w/ digi hops, back off
- FCS: handled by the TNC in KISS mode — do NOT append CRC in KISS payloads.

## 5. APRS Messaging (spec ch. 14)

- Format: `:AAAAAAAAA:message text{NNNNN`
  - Addressee exactly 9 chars, space-padded
  - Message max 67 chars
  - `{NNNNN` = up to 5-char message ID (we use numeric, incrementing)
- Ack: `:AAAAAAAAA:ackNNNNN` — send immediately on RX of ID'd message
- Reject: `rejNNNNN` (rare; handle on RX, never send in v1)
- Retry policy: resend unacked msgs at 30 s, 60 s, 120 s, 240 s, then mark
  failed.
- Default path `WIDE1-1,WIDE2-1`; make configurable (some areas prefer
  WIDE2-1 only).

## 6. Callsign Conventions

- Default station: KC3SMW, suggested SSID -7 (HT/handheld convention).
  On-air smoke testing during the AXTermPuter investigation used -10 and
  -15 for ad hoc test frames — pick real SSIDs deliberately once this
  project is doing genuine testing, don't reuse those test values as if
  they mean anything.
- Terminal mode primary target: ChengmaniaBPQ node (confirm on-air
  call/SSID of the node port before real BBS testing)

## 7. Legal Reminders

- Part 97: no content encryption on RF; ID with callsign (inherent in
  AX.25 source address); no third-party auto-forwarding shenanigans in v1.
